# coding=utf-8
"""Scan - the desktop playground: input (file/clipboard/region), viewer with overlays, results and exports."""
from __future__ import absolute_import, division, print_function

import json
from pathlib import Path

from ManyQt.QtCore import Qt
from ManyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ocrroute.desktop import icons
from ocrroute.desktop.pages.base import Page, fmtMs
from ocrroute.desktop.widgets.image_viewer import ImageViewer
from ocrroute.desktop.widgets.region_capture import RegionCapture


def compatibleCodes(multi, chosen):
    """
    Which languages may still be added, given the engine's combination groups (mirrors OCRPlugin.compatibleLanguages).

    :param multi: dict  {'codes', 'groups', 'universal'}
    :param chosen: list[str]
    :return: list[str]
    """
    codes, universal = multi['codes'], set(multi.get('universal') or ['en'])
    specific = [c for c in chosen if c and c != 'auto' and c not in universal]
    groups = multi.get('groups') or []
    if not groups or not specific:
        return [c for c in codes if c not in chosen]
    grouped = set(c for g in groups for c in g)
    free = set(c for c in codes if c not in grouped and c not in universal)
    allowed = set(universal)
    if set(specific) <= free:
        allowed |= free
    for g in groups:
        if set(specific) <= set(g):
            allowed |= set(g)
    return [c for c in codes if c in allowed and c not in chosen]

EXPORTS = ['json', 'text', 'md', 'hocr', 'alto', 'csv', 'xlsx', 'docx', 'pdf', 'overlay_png']


class ScanPage(Page):
    title = 'Scan'
    subtitle = 'Drop, paste or capture a document and route it through your engines.'

    def __init__(self, state):
        super(ScanPage, self).__init__(state)
        self.data = None
        self.filename = 'clipboard.png'
        self.envelope = None
        self.region = None
        self._capture = None
        self.setAcceptDrops(True)

        # actions
        self.button('Open file…', self.openFile)
        self.button('Paste', self.pasteClipboard)
        self.button('Capture region', self.captureRegion)
        self.run_btn = self.button('Run OCR', self.run, primary=True)
        self.run_btn.setProperty('accent', True)
        self.run_btn.setProperty('primary', False)
        self.run_btn.setIcon(icons.icon('play', '#FFFFFF', 14))
        self.run_btn.setEnabled(False)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ctl = QFrame()
        ctl.setObjectName('card')
        form = QFormLayout(ctl)
        self.target = QComboBox()
        self.lang = QComboBox()  # the languages the chosen engine accepts ('auto' preselected when it can detect)
        self.lang.setMinimumContentsLength(18)
        self.variant = QComboBox()  # engine variants whose languages differ (OCR.Space engines 1-3)
        # several languages (Tesseract eng+fra+ara, EasyOCR, Google Vision hints, VLM hints): "Also:" chips + an adder
        self.extraRow = QWidget()
        self._extraLayout = QHBoxLayout(self.extraRow)
        self._extraLayout.setContentsMargins(0, 0, 0, 0)
        self._extraLayout.setSpacing(4)
        self.extraAdd = QComboBox()
        self.extraAdd.setMinimumContentsLength(14)
        self._extraLangs = []   # canonical codes chosen in addition to the first language
        self._multi = None      # {'codes', 'groups', 'universal', 'names'} when the engine accepts several languages
        self._langSeq = 0
        self.pages = QLineEdit()
        self.pages.setPlaceholderText('1-3 (PDF)')
        self.model = QComboBox()  # the engine's known models as suggestions; any model id can be typed
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.NoInsert)
        self.model.setMinimumContentsLength(22)
        self.modelRefresh = QPushButton('Refresh')
        self.modelRefresh.setToolTip('Ask the provider for its current models (needs an enabled credential)')
        self.modelRow = QWidget()
        _ml = QHBoxLayout(self.modelRow)
        _ml.setContentsMargins(0, 0, 0, 0)
        _ml.addWidget(self.model, 1)
        _ml.addWidget(self.modelRefresh)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText('Extra prompt for vision-language engines (optional)')
        self.options = QLineEdit()
        self.options.setPlaceholderText('{"minConfidence": 60}')
        pre = QHBoxLayout()
        self.pre_up, self.pre_gray, self.pre_rot, self.nocache = (
            QCheckBox('upscale'),
            QCheckBox('grayscale'),
            QCheckBox('auto-rotate'),
            QCheckBox('bypass cache'),
        )
        for c in (self.pre_up, self.pre_gray, self.pre_rot, self.nocache):
            pre.addWidget(c)
        pre.addStretch(1)
        form.addRow('Target', self.target)
        form.addRow('Language', self.lang)
        form.addRow('Also', self.extraRow)
        self._extraLabel = form.labelForField(self.extraRow)
        form.addRow('Engine variant', self.variant)
        self._variantLabel = form.labelForField(self.variant)
        self._showVariant(False)
        self.target.currentIndexChanged.connect(lambda _i: self._loadLanguages(False))
        self.variant.activated.connect(lambda _i: self._loadLanguages(True))
        self._pickedLang = None  # a language the user chose: kept across engines when the new one supports it
        self.lang.activated.connect(self._firstLanguageChanged)
        self.extraAdd.activated.connect(self._addExtraLanguage)
        form.addRow('Pages', self.pages)
        form.addRow('Model', self.modelRow)
        self._modelLabel = form.labelForField(self.modelRow)
        form.addRow('Prompt', self.prompt)
        self._promptLabel = form.labelForField(self.prompt)
        self.modelRefresh.clicked.connect(lambda: self._loadModels(True))
        form.addRow('Options', self.options)
        form.addRow('', pre)
        ll.addWidget(ctl)
        vbar = QHBoxLayout()
        self.info = QLabel(self.tr('Drop an image or PDF here, open a file, paste, or capture a screen region.'))
        self.info.setObjectName('muted')
        vbar.addWidget(self.info, 1)
        self.boxes_cb = QCheckBox('boxes')
        self.boxes_cb.setChecked(True)
        crop = QPushButton('Crop')
        crop.setToolTip('Drag a rectangle to OCR only that region')
        zi, zo, fit = QPushButton('+'), QPushButton('−'), QPushButton('Fit')
        for b in (self.boxes_cb, crop, zo, zi, fit):
            vbar.addWidget(b)
        ll.addLayout(vbar)
        self.viewer = ImageViewer()
        self.viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.viewer.setMinimumHeight(220)
        ll.addWidget(self.viewer, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.meta = QLabel(self.tr('No run yet.'))
        self.meta.setObjectName('muted')
        self.meta.setWordWrap(True)
        rl.addWidget(self.meta)
        self.tabs = QTabWidget()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.lines = QTableWidget(0, 6)
        self.lines.setHorizontalHeaderLabels(['page', 'text', 'x', 'y', 'w', 'h'])
        self.lines.horizontalHeader().setStretchLastSection(False)
        self.lines.setColumnWidth(1, 320)
        self.json_view = QPlainTextEdit()
        self.json_view.setReadOnly(True)
        self.routing_view = QPlainTextEdit()
        self.routing_view.setReadOnly(True)
        self.tabs.addTab(self.text, 'Text')
        self.tabs.addTab(self.lines, 'Lines')
        self.tabs.addTab(self.json_view, 'JSON')
        self.tabs.addTab(self.routing_view, 'Routing')
        rl.addWidget(self.tabs, 1)
        ex = QHBoxLayout()
        copy = QPushButton('Copy text')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        ex.addWidget(copy)
        self.export_kind = QComboBox()
        self.export_kind.addItems(EXPORTS)
        self.save_btn = QPushButton('Save as…')
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.saveExport)
        ex.addWidget(self.export_kind)
        ex.addWidget(self.save_btn)
        ex.addStretch(1)
        rl.addLayout(ex)
        split.addWidget(right)
        split.setSizes([620, 520])
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)
        self.root.addWidget(split, 1)

        self.boxes_cb.toggled.connect(self.viewer.setBoxesVisible)
        crop.clicked.connect(lambda: self.viewer.setCropMode(True))
        zi.clicked.connect(lambda: self.viewer.zoom(1.25))
        zo.clicked.connect(lambda: self.viewer.zoom(0.8))
        fit.clicked.connect(self.viewer.fit)
        self.viewer.regionSelected.connect(self._region)
        self.viewer.wordClicked.connect(lambda li: (self.tabs.setCurrentWidget(self.lines), self.lines.selectRow(li)))

    # ------------------------------------------------------------------ inputs
    def refresh(self):
        def load(c):
            return c.engines(), c.routes()['items']

        def fill(res):
            engines, routes = res
            self.state.engines, self.state.routes = engines, routes
            cur = self.target.currentData()
            self.target.clear()
            self.target.addItem('auto (default route)', '')
            for r in routes:
                if r['enabled']:
                    self.target.addItem('route: {}'.format(r['name']), 'route:{}'.format(r['name']))
            for e in engines:
                self.target.addItem(
                    '{}{}'.format(e['name'], '' if e['available'] else '  (unavailable)'), 'engine:{}'.format(e['id'])
                )
                if not e['available']:
                    self.target.model().item(self.target.count() - 1).setEnabled(False)
            if cur:
                idx = self.target.findData(cur)
                if idx >= 0:
                    self.target.setCurrentIndex(idx)
            self._loadLanguages(False)

        self.call(load, fill)

    def setInput(self, data, name):
        self.data, self.filename, self.region, self.envelope = data, name, None, None
        self.viewer.setResult(None)
        if data[:4] == b'%PDF':
            self.viewer.clear()
            self.info.setText('{} · PDF · {:.1f} KB (preview after OCR)'.format(name, len(data) / 1024))
        else:
            self.viewer.setImageBytes(data)
            self.info.setText('{} · {:.1f} KB'.format(name, len(data) / 1024))
        self.run_btn.setEnabled(True)

    def openFile(self):
        start = str(self.state.settings.value('scan/last_dir', str(Path.home())))
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr('Open image or PDF'),
            start,
            'Images and PDF (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.gif *.pdf)',
        )
        if path:
            self.state.settings.setValue('scan/last_dir', str(Path(path).parent))
            self.setInput(Path(path).read_bytes(), Path(path).name)

    def pasteClipboard(self):
        cb = QApplication.clipboard()
        img = cb.image()
        if img.isNull():
            self.state.toast.emit(self.tr('Clipboard has no image'), True)
            return
        from ManyQt.QtCore import QBuffer, QIODevice

        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, 'PNG')
        self.setInput(bytes(buf.data()), 'clipboard.png')

    def captureRegion(self):
        self._capture = RegionCapture()
        self._capture.captured.connect(lambda b: (self.setInput(b, 'region.png'), self.run()))
        self._capture.show()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_file():
                self.setInput(p.read_bytes(), p.name)
                break

    def _region(self, x, y, w, h):
        self.region = [x, y, w, h]
        self.info.setText('{} · region {},{} {}×{} (Run OCR to apply)'.format(self.filename, x, y, w, h))

    # ------------------------------------------------------------------ languages
    def _showVariant(self, visible):
        self._variantShown = bool(visible)  # isVisible() is False whenever the page itself is hidden
        self.variant.setVisible(visible)
        if self._variantLabel is not None:
            self._variantLabel.setVisible(visible)

    def _loadModels(self, live=False):
        """Known models of the chosen engine (live from the provider on Refresh); hidden for engines without a model."""
        target = self.target.currentData() or ''
        if not target.startswith('engine:'):
            self._showModel(False)
            return
        engine = target[7:]
        self._modelSeq = getattr(self, '_modelSeq', 0) + 1
        seq = self._modelSeq

        def load(c):
            pid = ''
            if live:
                try:
                    pid = next((p['id'] for p in c.providers().get('items', []) if p.get('engine_id') == engine and p.get('enabled', True)), '')
                except Exception:  # noqa: BLE001
                    pid = ''
            return c.engineModels(engine, pid if live else '', live)

        def fill(d):
            if seq != self._modelSeq:
                return
            if not d.get('has_model'):
                self._showModel(False)
                return
            self._showModel(True)
            typed = self.model.currentText().strip()
            self.model.clear()
            for m in d.get('models') or []:
                self.model.addItem(m.get('label') or m['id'], m['id'])
            self.model.setCurrentIndex(-1)
            self.model.setEditText(typed if typed and typed != d.get('default') else '')
            self.model.lineEdit().setPlaceholderText(d.get('default') or 'engine default')
            self.model.setToolTip(('Live list from the provider' if d.get('source') == 'live' else 'Known models') + (('. ' + d['error']) if d.get('error') else ''))

        self.call(load, fill)

    def _showModel(self, visible):
        self.modelRow.setVisible(visible)
        if getattr(self, '_modelLabel', None) is not None:
            self._modelLabel.setVisible(visible)

    def _showPrompt(self, visible):
        self.prompt.setVisible(visible)
        if getattr(self, '_promptLabel', None) is not None:
            self._promptLabel.setVisible(visible)

    def modelValue(self):
        """:return: str  the typed or chosen model id ('' = the engine's default)"""
        i = self.model.currentIndex()
        text = self.model.currentText().strip()
        if i >= 0 and self.model.itemText(i) == text:
            return self.model.itemData(i) or text
        return text

    def _loadLanguages(self, keepVariant):
        """Fill the language list for the chosen target (engine languages, or every language for routes)."""
        if not keepVariant:
            self._loadModels(False)
        self._langSeq += 1
        seq, target = self._langSeq, self.target.currentData() or ''
        variant = self.variant.currentData() if keepVariant else None

        def load(c):
            if target.startswith('engine:'):
                return c.engineLanguages(target[7:], variant)
            return dict(c.languages(), engines=[], fixed=False)

        def fill(d):
            if seq != self._langSeq:  # a newer choice is already loading
                return
            keep = self._pickedLang
            vs = d.get('engines') or []
            if vs and not keepVariant:
                self.variant.clear()
                for v in vs:
                    self.variant.addItem(v['label'], v['value'])
            if vs:
                i = self.variant.findData(d.get('engine'))
                if i >= 0:
                    self.variant.setCurrentIndex(i)
            self._showVariant(bool(vs))
            self._showPrompt(bool(d.get('hint')) or not target.startswith('engine:'))
            self.lang.clear()
            if target != getattr(self, '_lastLangTarget', None):
                self._extraLangs = []
            self._lastLangTarget = target
            self._multi = None
            if d.get('fixed'):
                reads = ', '.join(x['name'] for x in d.get('reads') or [])
                self.lang.addItem('No language setting (reads {})'.format(reads) if reads else 'Automatic (detected by the engine)', 'auto')
                self.lang.setEnabled(False)
                self.lang.setToolTip("This engine's models read {}: it has no language setting.".format(reads) if reads
                                     else 'This engine detects the language itself.')
                return
            self.lang.setEnabled(True)
            self.lang.setToolTip('This engine detects the language itself; a chosen language is sent to the model as a hint.'
                                 if d.get('hint') else '')
            for item in d.get('languages') or []:
                label = 'Automatic (detect the language)' if item['code'] == 'auto' else '{} ({})'.format(item['name'], item['code'])
                self.lang.addItem(label, item['code'])
            for wanted in (keep, d.get('default'), 'auto', 'en'):
                i = self.lang.findData(wanted)
                if i >= 0:
                    self.lang.setCurrentIndex(i)
                    break
            if d.get('multiple') or not target.startswith('engine:'):  # routes: each engine takes what it supports
                items = d.get('languages') or []
                self._multi = {'codes': [x['code'] for x in items if x['code'] != 'auto'], 'groups': d.get('groups') or [],
                               'universal': d.get('universal') or ['en'], 'names': {x['code']: x['name'] for x in items}}
            self._renderExtra()

        self.call(load, fill)

    # ------------------------------------------------------------------ several languages
    def _firstLanguageChanged(self, _i=None):
        self._pickedLang = self.lang.currentData()
        if self._multi:
            ok = set(compatibleCodes(self._multi, [self.lang.currentData()]))
            self._extraLangs = [c for c in self._extraLangs if c in ok]
        self._renderExtra()

    def _addExtraLanguage(self, _i=None):
        code = self.extraAdd.currentData()
        if code and code not in self._extraLangs:
            self._extraLangs.append(code)
        self._renderExtra()

    def _removeExtraLanguage(self, code):
        self._extraLangs = [c for c in self._extraLangs if c != code]
        self._renderExtra()

    def _renderExtra(self):
        """Chips for the extra languages and an adder limited to what the engine can combine."""
        while self._extraLayout.count():
            item = self._extraLayout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self.extraAdd:  # chips are rebuilt; the adder is permanent
                w.deleteLater()
        show = self._multi is not None and self.lang.isEnabled()
        self.extraRow.setVisible(show)
        if self._extraLabel is not None:
            self._extraLabel.setVisible(show)
        if not show:
            return
        first = self.lang.currentData()
        self._extraLangs = [c for c in self._extraLangs if c != first]
        for code in self._extraLangs:
            chip = QPushButton('{}  \u00d7'.format(self._multi['names'].get(code, code)))
            chip.setToolTip('Remove {}'.format(code))
            chip.setFlat(True)
            chip.clicked.connect(lambda _c=False, c=code: self._removeExtraLanguage(c))
            self._extraLayout.addWidget(chip)
        self.extraAdd.clear()
        self.extraAdd.addItem('+ Add a language\u2026', '')
        for code in compatibleCodes(self._multi, [first] + self._extraLangs):
            self.extraAdd.addItem('{} ({})'.format(self._multi['names'].get(code, code), code), code)
        self.extraAdd.setEnabled(self.extraAdd.count() > 1)
        self._extraLayout.addWidget(self.extraAdd)
        self._extraLayout.addStretch(1)

    def languages(self):
        """:return: str | list[str]  the chosen languages (a list when several; an 'auto' first language steps aside)"""
        first = self.lang.currentData() or 'auto'
        chosen = [first] + [c for c in self._extraLangs if c != first]
        real = [c for c in chosen if c != 'auto']
        if len(real) > 1:
            return real
        return real[0] if real else chosen[0]

    # ------------------------------------------------------------------ run
    def _payload(self):
        t = self.target.currentData() or ''
        p = {
            'language': self.languages(),
            'pages': self.pages.text(),
            'prompt': self.prompt.text(),
            'output': ['json', 'text'],
            'cache': not self.nocache.isChecked(),
            'preprocess': {
                'upscale': self.pre_up.isChecked(),
                'grayscale': self.pre_gray.isChecked(),
                'auto_rotate': self.pre_rot.isChecked(),
                'region': self.region,
            },
            'metadata': {'origin': 'desktop'},
        }
        if t.startswith('route:'):
            p['route'] = t[6:]
        elif t.startswith('engine:'):
            p['engine'] = t[7:]
        if self.options.text().strip():
            p['options'] = json.loads(self.options.text())
        if getattr(self, '_variantShown', False) and self.variant.currentData() not in (None, ''):
            p.setdefault('options', {})['engine'] = self.variant.currentData()
        if self.modelRow.isVisibleTo(self) and self.modelValue():
            p.setdefault('options', {})['model'] = self.modelValue()
        return p

    def run(self):
        if not self.data:
            return
        try:
            payload = self._payload()
        except json.JSONDecodeError:
            self.state.toast.emit(self.tr('Options must be valid JSON'), True)
            return
        self.run_btn.setEnabled(False)
        self.run_btn.setText(self.tr('Running…'))
        data, name = self.data, self.filename
        self.call(
            lambda c: c.ocrBytes(data, name, **payload),
            self._show,
            on_finished=lambda: (self.run_btn.setEnabled(True), self.run_btn.setText(self.tr('Run OCR'))),
        )

    def _show(self, env):
        self.envelope = env
        res = env.get('result', {})
        r, u = env.get('routing', {}), env.get('usage', {})
        status = env.get('status')
        err = '  ·  {}: {}'.format(env.get('error_code'), env.get('error_message', '')) if env.get('error_code') else ''
        self.meta.setText(
            '<b>{}</b> · {}{} · {} · {} chars · {} lines · ~${:.4f}{}{}'.format(
                status,
                r.get('winning_engine') or '-',
                ' / ' + r['winning_provider'] if r.get('winning_provider') else '',
                fmtMs(u.get('duration_ms')),
                u.get('chars', 0),
                u.get('lines', 0),
                (u.get('cost_cents', 0) or 0) / 100,
                ' · cached' if env.get('cached') else '',
                err,
            )
        )
        self.text.setPlainText((res.get('ParsedText') or '').replace('\r\n', '\n'))
        self.json_view.setPlainText(json.dumps(res, indent=2, ensure_ascii=False))
        self.routing_view.setPlainText(json.dumps(r, indent=2, ensure_ascii=False))
        lines = res.get('TextOverlay', {}).get('Lines', [])
        self.lines.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            words = ln.get('Words', [])
            x = min((w['Left'] for w in words), default=0)
            w_ = max((w['Left'] + w['Width'] for w in words), default=0) - x
            for col, val in enumerate(
                (
                    ln.get('Page', 1),
                    ln.get('LineText', ''),
                    round(x),
                    round(ln.get('MinTop', 0)),
                    round(w_),
                    round(ln.get('MaxHeight', 0)),
                )
            ):
                item = QTableWidgetItem(str(val))
                if col == 1:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.lines.setItem(i, col, item)
        self.viewer.setResult(res if status in ('succeeded', 'cached') else None)
        self.save_btn.setEnabled(status in ('succeeded', 'cached'))

    def saveExport(self):
        if not self.envelope:
            return
        kind = self.export_kind.currentText()
        run_id = self.envelope['run_id']
        ext = {
            'json': '.json',
            'text': '.txt',
            'md': '.md',
            'hocr': '.hocr.html',
            'alto': '.alto.xml',
            'csv': '.csv',
            'xlsx': '.xlsx',
            'docx': '.docx',
            'pdf': '.pdf',
            'overlay_png': '.png',
        }[kind]
        start = str(
            Path(str(self.state.settings.value('scan/last_dir', str(Path.home())))) / (Path(self.filename).stem + ext)
        )
        path, _ = QFileDialog.getSaveFileName(self, self.tr('Save export'), start)
        if not path:
            return

        def save(c):
            Path(path).write_bytes(c.artifact(run_id, kind))
            return path

        self.call(save, lambda p: self.state.toast.emit(self.tr('Saved ') + str(p), False))
