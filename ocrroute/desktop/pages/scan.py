# coding=utf-8
"""Scan - the desktop playground: input (file/clipboard/region), viewer with overlays, results and exports."""
from __future__ import absolute_import, division, print_function

import json
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QSizePolicy, 
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ocrroute.desktop.pages.base import Page, fmtMs
from ocrroute.desktop import icons
from ocrroute.desktop.widgets.image_viewer import ImageViewer
from ocrroute.desktop.widgets.region_capture import RegionCapture

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
        self.lang = QLineEdit('en')
        self.pages = QLineEdit()
        self.pages.setPlaceholderText('1-3 (PDF)')
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText('Prompt for VLM engines (optional)')
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
        form.addRow('Language(s)', self.lang)
        form.addRow('Pages', self.pages)
        form.addRow('Prompt', self.prompt)
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
        from PyQt5.QtCore import QBuffer, QIODevice

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

    # ------------------------------------------------------------------ run
    def _payload(self):
        t = self.target.currentData() or ''
        p = {
            'language': self.lang.text(),
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
