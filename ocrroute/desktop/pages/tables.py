# coding=utf-8
"""Shared table-driven pages: engines, providers, routes, history, batch, keys."""
from __future__ import absolute_import, division, print_function

import json
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ocrroute.desktop.pages.base import Page, fmtMs


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return t


def _fill(t, rows, keys=None):
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            it = QTableWidgetItem(str(v))
            if keys is not None and j == 0:
                it.setData(Qt.UserRole, keys[i])
            t.setItem(i, j, it)


def _selectedKey(t):
    r = t.currentRow()
    return t.item(r, 0).data(Qt.UserRole) if r >= 0 else None


# ---------------------------------------------------------------- engines
class EnginesPage(Page):
    title = 'Engines'
    subtitle = 'Every OCRPlugin detected in AioOCR, with availability and install hints.'

    def __init__(self, state):
        super(EnginesPage, self).__init__(state)
        self.button('Rescan', lambda: self.call(lambda c: c.refreshEngines(), lambda _r: self.refresh()))
        self.button('Probe', self.probe)
        self.button('Toggle enabled', self.toggle)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(self.tr('filter…'))
        self.filter.textChanged.connect(self._applyFilter)
        self.root.addWidget(self.filter)
        self.table = _table(
            ['engine', 'kind', 'vendor', 'available', 'enabled', 'PDF', 'handwriting', 'tables', 'cost', 'hint']
        )
        self.root.addWidget(self.table, 1)
        self.rows = []

    def refresh(self):
        self.call(lambda c: c.engines(), self._show)

    def _show(self, engines):
        self.rows = engines
        self.state.engines = engines
        self._applyFilter()

    def _applyFilter(self):
        q = self.filter.text().lower()
        rows = [
            e
            for e in self.rows
            if q in e['id'].lower() or q in (e.get('vendor') or '').lower() or q in (e.get('name') or '').lower()
        ]
        _fill(
            self.table,
            [
                [
                    e['name'] or e['id'],
                    e['kind'],
                    e.get('vendor', ''),
                    '✓' if e['available'] else '✗',
                    'on' if e['enabled'] else 'off',
                    '✓' if e['supports_pdf'] else '',
                    '✓' if e['supports_handwriting'] else '',
                    '✓' if e['supports_tables'] else '',
                    '{} {}'.format(e['cost_model'], e['unit_price'] or ''),
                    '' if e['available'] else e['install_hint'],
                ]
                for e in rows
            ],
            [e['id'] for e in rows],
        )

    def probe(self):
        eid = _selectedKey(self.table)
        if not eid:
            return
        self.call(
            lambda c: c.probeEngine(eid),
            lambda r: QMessageBox.information(
                self,
                'Probe {}'.format(eid),
                '{}  ({})\n\n{}\n\n{} {}'.format(
                    r['status'],
                    fmtMs(r['usage']['duration_ms']),
                    r['result'].get('ParsedText', '')[:400],
                    r.get('error_code') or '',
                    r.get('error_message') or '',
                ),
            ),
        )

    def toggle(self):
        eid = _selectedKey(self.table)
        if not eid:
            return
        e = next(x for x in self.rows if x['id'] == eid)
        self.call(lambda c: c.patchEngine(eid, enabled=not e['enabled']), lambda _r: self.refresh())


# ---------------------------------------------------------------- providers
class ProviderDialog(QDialog):
    def __init__(self, parent, engines, provider=None):
        super(ProviderDialog, self).__init__(parent)
        self.setWindowTitle('Provider')
        self.engines = engines
        form = QFormLayout(self)
        self.engine = QComboBox()
        for e in engines:
            self.engine.addItem('{} ({})'.format(e['name'], e['kind']), e['id'])
            if not e['available']:
                self.engine.model().item(self.engine.count() - 1).setEnabled(False)
        self.label = QLineEdit()
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText('engine default, or any OpenAI-compatible base URL you operate')
        self.model = QLineEdit()
        self.language = QLineEdit()
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 3600)
        self.timeout.setValue(60)
        self.priority = QSpinBox()
        self.priority.setRange(0, 10000)
        self.priority.setValue(100)
        self.rpm = QSpinBox()
        self.rpm.setRange(0, 100000)
        self.budget = QLineEdit('0')
        self.options = QPlainTextEdit('{}')
        self.options.setMaximumHeight(90)
        self.opt_help = QLabel('')
        self.opt_help.setWordWrap(True)
        self.opt_help.setObjectName('muted')
        for lab, w in (
            ('Engine', self.engine),
            ('Label', self.label),
            ('Endpoint', self.endpoint),
            ('Model', self.model),
            ('Language', self.language),
            ('Timeout (s)', self.timeout),
            ('Priority', self.priority),
            ('RPM limit', self.rpm),
            ('Budget ¢/month', self.budget),
            ('Options JSON', self.options),
            ('', self.opt_help),
        ):
            form.addRow(lab, w)
        self.engine.currentIndexChanged.connect(self._help)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        if provider:
            self.engine.setCurrentIndex(self.engine.findData(provider['engine_id']))
            self.engine.setEnabled(False)
            self.label.setText(provider['label'])
            self.endpoint.setText(provider.get('endpoint', ''))
            self.model.setText(provider.get('model', ''))
            self.language.setText(provider.get('language', ''))
            self.timeout.setValue(int(provider.get('timeout', 60)))
            self.priority.setValue(int(provider.get('priority', 100)))
            self.rpm.setValue(int(provider.get('rpm_limit', 0)))
            self.budget.setText(str(provider.get('monthly_budget_cents', 0)))
            self.options.setPlainText(json.dumps(provider.get('options') or {}, indent=1))
        self._help()

    def _help(self):
        eid = self.engine.currentData()
        e = next((x for x in self.engines if x['id'] == eid), None)
        if e:
            opts = ', '.join('{}={!r}'.format(o['name'], o.get('default')) for o in (e.get('option_schema') or [])[:12])
            self.opt_help.setText('Options for {}: {}'.format(e['id'], opts or '-'))

    def body(self):
        return {
            'engine_id': self.engine.currentData(),
            'label': self.label.text().strip(),
            'endpoint': self.endpoint.text().strip(),
            'model': self.model.text().strip(),
            'language': self.language.text().strip(),
            'timeout': self.timeout.value(),
            'priority': self.priority.value(),
            'rpm_limit': self.rpm.value(),
            'monthly_budget_cents': float(self.budget.text() or 0),
            'options': json.loads(self.options.toPlainText() or '{}'),
        }


class ProvidersPage(Page):
    title = 'Providers & credentials'
    subtitle = 'Engines with connection settings and encrypted credentials.'

    def __init__(self, state):
        super(ProvidersPage, self).__init__(state)
        self.button('New', self.new)
        self.button('Edit', self.edit)
        self.button('Add key', self.addCred)
        self.button('Verify key', self.verifyCred)
        self.button('Test', self.test)
        self.button('Reset circuit', lambda: self._with(lambda c, pid: c.resetCircuit(pid)))
        self.button('Delete', self.delete)
        self.table = _table(['label', 'engine', 'health', 'endpoint', 'model', 'limits', 'credentials'])
        self.root.addWidget(self.table, 1)
        self.rows = []

    def refresh(self):
        self.call(lambda c: (c.providers(), c.engines()), self._show)

    def _show(self, data):
        self.rows, self.state.engines = data
        _fill(
            self.table,
            [
                [
                    p['label'] + ('' if p['enabled'] else ' (off)'),
                    p['engine_id'],
                    p['health'],
                    p['endpoint'] or '-',
                    p['model'] or '-',
                    '{} rpm · {} conc'.format(p['rpm_limit'] or '∞', p['concurrency_limit']),
                    ', '.join(
                        '{} {}{}'.format(c['alias'] or 'key', c['masked'], ' ✗' if c['exhausted_until'] else '')
                        for c in p['credentials']
                    )
                    or '-',
                ]
                for p in self.rows
            ],
            [p['id'] for p in self.rows],
        )

    def _with(self, fn):
        pid = _selectedKey(self.table)
        if pid:
            self.call(lambda c: fn(c, pid), lambda _r: self.refresh())

    def new(self):
        d = ProviderDialog(self, self.state.engines)
        if d.exec_():
            body = d.body()
            self.call(lambda c: c.createProvider(**body), lambda _r: self.refresh())

    def edit(self):
        pid = _selectedKey(self.table)
        if not pid:
            return
        p = next(x for x in self.rows if x['id'] == pid)
        d = ProviderDialog(self, self.state.engines, p)
        if d.exec_():
            body = d.body()
            body.pop('engine_id')
            self.call(lambda c: c.patchProvider(pid, **body), lambda _r: self.refresh())

    def addCred(self):
        pid = _selectedKey(self.table)
        if not pid:
            return
        secret, ok = QInputDialog.getText(
            self, self.tr('Add credential'), self.tr('API key (stored encrypted):'), QLineEdit.Password
        )
        if ok and secret:
            self.call(lambda c: c.createCredential(pid, secret), lambda _r: self.refresh())

    def verifyCred(self):
        pid = _selectedKey(self.table)
        if not pid:
            return
        p = next(x for x in self.rows if x['id'] == pid)
        if not p['credentials']:
            self.state.toast.emit(self.tr('Provider has no credentials'), True)
            return
        cid = p['credentials'][0]['id']
        self.call(
            lambda c: c.verifyCredential(cid),
            lambda r: self.state.toast.emit(
                'Credential {}'.format('OK' if r['ok'] else 'failed: ' + str(r.get('error_code'))), not r['ok']
            ),
        )

    def test(self):
        pid = _selectedKey(self.table)
        if pid:
            self.call(
                lambda c: c.testProvider(pid),
                lambda r: QMessageBox.information(
                    self,
                    'Provider test',
                    '{} ({})\n\n{}\n{} {}'.format(
                        r['status'],
                        fmtMs(r['usage']['duration_ms']),
                        r['result'].get('ParsedText', '')[:300],
                        r.get('error_code') or '',
                        r.get('error_message') or '',
                    ),
                ),
            )

    def delete(self):
        pid = _selectedKey(self.table)
        if pid and QMessageBox.question(self, 'Delete', 'Delete this provider and its credentials?') == QMessageBox.Yes:
            self.call(lambda c: c.deleteProvider(pid), lambda _r: self.refresh())


# ---------------------------------------------------------------- routes
class RouteDialog(QDialog):
    def __init__(self, parent, providers, strategies, route=None):
        super(RouteDialog, self).__init__(parent)
        self.setWindowTitle('Route')
        self.providers = providers
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.strategy = QComboBox()
        for k, v in strategies.items():
            self.strategy.addItem(k, k)
            self.strategy.setItemData(self.strategy.count() - 1, v, Qt.ToolTipRole)
        self.default = QCheckBox('default route')
        self.min_chars = QSpinBox()
        self.min_chars.setRange(0, 100000)
        self.deadline = QSpinBox()
        self.deadline.setRange(1000, 3600000)
        self.deadline.setValue(120000)
        form.addRow('Name (slug)', self.name)
        form.addRow('Strategy', self.strategy)
        form.addRow('Stop: min chars', self.min_chars)
        form.addRow('Deadline (ms)', self.deadline)
        form.addRow('', self.default)
        lay.addLayout(form)
        lay.addWidget(QLabel('Members - drag to reorder'))
        self.members = QListWidget()
        self.members.setDragDropMode(QAbstractItemView.InternalMove)
        lay.addWidget(self.members)
        row = QHBoxLayout()
        self.pick = QComboBox()
        for p in providers:
            self.pick.addItem('{} - {}'.format(p['label'], p['engine_id']), p['id'])
        add = QPushButton('Add')
        rm = QPushButton('Remove')
        add.clicked.connect(lambda: self._add(self.pick.currentData()))
        rm.clicked.connect(lambda: self.members.takeItem(self.members.currentRow()))
        row.addWidget(self.pick, 1)
        row.addWidget(add)
        row.addWidget(rm)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if route:
            self.name.setText(route['name'])
            self.name.setEnabled(False)
            self.strategy.setCurrentIndex(self.strategy.findData(route['strategy']))
            self.default.setChecked(route['is_default'])
            self.min_chars.setValue(int((route.get('stop_condition') or {}).get('min_chars', 0)))
            self.deadline.setValue(int(route.get('total_deadline_ms', 120000)))
            for m in route['members']:
                self._add(m['provider_id'])

    def _add(self, pid):
        p = next((x for x in self.providers if x['id'] == pid), None)
        if p:
            it = QListWidgetItem('{} - {}'.format(p['label'], p['engine_id']))
            it.setData(Qt.UserRole, pid)
            self.members.addItem(it)

    def body(self):
        sc = {'min_chars': self.min_chars.value()} if self.min_chars.value() else {}
        return {
            'strategy': self.strategy.currentData(),
            'is_default': self.default.isChecked(),
            'stop_condition': sc,
            'total_deadline_ms': self.deadline.value(),
            'members': [
                {'provider_id': self.members.item(i).data(Qt.UserRole), 'order_index': i}
                for i in range(self.members.count())
            ],
        }


class RoutesPage(Page):
    title = 'Routes'
    subtitle = 'Ordered fallback chains with a balancing strategy.'

    def __init__(self, state):
        super(RoutesPage, self).__init__(state)
        self.button('New', self.new)
        self.button('Edit', self.edit)
        self.button('Simulate', self.simulate)
        self.button('Delete', self.delete)
        self.table = _table(['name', 'strategy', 'default', 'members', 'stop', 'deadline'])
        self.root.addWidget(self.table, 1)
        self.sim_out = QPlainTextEdit()
        self.sim_out.setReadOnly(True)
        self.sim_out.setMaximumHeight(160)
        self.root.addWidget(self.sim_out)
        self.rows = []
        self.strategies = {}
        self.providers = []

    def refresh(self):
        self.call(lambda c: (c.routes(), c.providers()), self._show)

    def _show(self, data):
        routes, self.providers = data
        self.rows, self.strategies = routes['items'], routes['strategies']
        _fill(
            self.table,
            [
                [
                    r['name'],
                    r['strategy'],
                    '✓' if r['is_default'] else '',
                    ' → '.join('{}({})'.format(m['provider_label'], m['engine_id']) for m in r['members']) or '-',
                    json.dumps(r['stop_condition']) if r['stop_condition'] else '-',
                    '{} ms'.format(r['total_deadline_ms']),
                ]
                for r in self.rows
            ],
            [r['id'] for r in self.rows],
        )

    def new(self):
        d = RouteDialog(self, self.providers, self.strategies)
        if d.exec_():
            body = d.body()
            name = d.name.text().strip()
            self.call(lambda c: c.createRoute(name=name, **body), lambda _r: self.refresh())

    def edit(self):
        rid = _selectedKey(self.table)
        if not rid:
            return
        r = next(x for x in self.rows if x['id'] == rid)
        d = RouteDialog(self, self.providers, self.strategies, r)
        if d.exec_():
            body = d.body()
            self.call(lambda c: c.patchRoute(rid, **body), lambda _r: self.refresh())

    def simulate(self):
        rid = _selectedKey(self.table)
        name = next((x['name'] for x in self.rows if x['id'] == rid), '')

        def show(res):
            if not res['ok']:
                self.sim_out.setPlainText(res['error'])
                return
            self.sim_out.setPlainText(
                'route {} · {}\n'.format(res['route'], res['strategy'])
                + '\n'.join(
                    '{}. {} / {} ({}, ~{}¢, q{})'.format(
                        i + 1, c['engine'], c['provider'], c['kind'], c['est_cost_cents'], c['quality']
                    )
                    for i, c in enumerate(res['candidates'])
                )
                + '\n\n'
                + '\n'.join('· ' + x for x in res['explain'])
            )

        self.call(lambda c: c.simulate(route=name), show)

    def delete(self):
        rid = _selectedKey(self.table)
        if rid and QMessageBox.question(self, 'Delete', 'Delete this route?') == QMessageBox.Yes:
            self.call(lambda c: c.deleteRoute(rid), lambda _r: self.refresh())


# ---------------------------------------------------------------- history
class HistoryPage(Page):
    title = 'History'
    subtitle = 'Every run with its attempts, timings and results.'

    def __init__(self, state):
        super(HistoryPage, self).__init__(state)
        self.button('Refresh', self.refresh)
        self.button('Purge', self.purge)
        self.filter = QComboBox()
        self.filter.addItems(['any status', 'succeeded', 'cached', 'failed'])
        self.filter.currentIndexChanged.connect(self.refresh)
        self.actions.insertWidget(0, self.filter)
        self.table = _table(['time', 'id', 'status', 'route', 'engine', 'attempts', 'pages', 'chars', 'ms', 'error'])
        self.table.itemSelectionChanged.connect(self._detail)
        self.root.addWidget(self.table, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(200)
        self.root.addWidget(self.detail)

    def refresh(self):
        st = self.filter.currentText()
        params = {'limit': 200}
        if st != 'any status':
            params['status'] = st
        self.call(
            lambda c: c.runs(**params),
            lambda runs: _fill(
                self.table,
                [
                    [
                        r['created_at'][:19].replace('T', ' '),
                        r['id'][-8:],
                        r['status'],
                        r['route'],
                        r['winning_engine'],
                        r['attempt_count'],
                        r['page_count'],
                        r['chars'],
                        r['duration_ms'],
                        '{} {}'.format(r['error_code'], r['error_message'][:60]) if r['error_code'] else '',
                    ]
                    for r in runs
                ],
                [r['id'] for r in runs],
            ),
        )

    def _detail(self):
        rid = _selectedKey(self.table)
        if rid:
            self.call(
                lambda c: c.run(rid),
                lambda r: self.detail.setPlainText(
                    '\n'.join(
                        '{}. {} {} - {} {} ms {} {}'.format(
                            a['order'],
                            a['engine'],
                            a['provider'],
                            a['status'],
                            a['duration_ms'],
                            a['error_code'] or '',
                            a['error_message'][:80] if a['error_message'] else '',
                        )
                        for a in r.get('attempts', [])
                    )
                    + '\n\n'
                    + (r.get('result') or {}).get('ParsedText', '').replace('\r\n', '\n')[:1500]
                ),
            )

    def purge(self):
        rid = _selectedKey(self.table)
        if rid and QMessageBox.question(self, 'Purge', 'Delete this run and its artifacts?') == QMessageBox.Yes:
            self.call(lambda c: c.deleteRun(rid), lambda _r: self.refresh())


# ---------------------------------------------------------------- batch
class BatchPage(Page):
    title = 'Batch'
    subtitle = 'Queue many files and watch them process.'

    def __init__(self, state):
        super(BatchPage, self).__init__(state)
        self.button('Add files…', self.addFiles)
        self.button('Add folder…', self.addFolder)
        self.button('Clear', lambda: self.queue.clear())
        self.button('Start', self.start, primary=True)
        row = QHBoxLayout()
        self.route = QComboBox()
        self.outputs = QLineEdit('json,text')
        row.addWidget(QLabel('Route'))
        row.addWidget(self.route)
        row.addWidget(QLabel('Outputs'))
        row.addWidget(self.outputs)
        row.addStretch(1)
        self.root.addLayout(row)
        self.queue = QListWidget()
        self.root.addWidget(self.queue, 1)
        self.jobs = _table(['created', 'name', 'status', 'done', 'failed', 'total'])
        self.root.addWidget(self.jobs, 1)
        self.button('Cancel job', self.cancel)

    def refresh(self):
        def show(data):
            routes, jobs = data
            self.route.clear()
            self.route.addItem('auto', '')
            for r in routes['items']:
                self.route.addItem(r['name'], r['name'])
            _fill(
                self.jobs,
                [
                    [j['created_at'][:19].replace('T', ' '), j['name'], j['status'], j['done'], j['failed'], j['total']]
                    for j in jobs
                ],
                [j['id'] for j in jobs],
            )

        self.call(lambda c: (c.routes(), c.jobs()), show)

    def addFiles(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, 'Add files', '', 'Images and PDF (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.pdf)'
        )
        for f in files:
            self.queue.addItem(f)

    def addFolder(self):
        d = QFileDialog.getExistingDirectory(self, 'Add folder')
        if d:
            for p in sorted(Path(d).iterdir()):
                if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.pdf'):
                    self.queue.addItem(str(p))

    def start(self):
        files = [Path(self.queue.item(i).text()) for i in range(self.queue.count())]
        if not files:
            self.state.toast.emit(self.tr('Queue is empty'), True)
            return
        spec = {
            'name': 'desktop batch ({} files)'.format(len(files)),
            'route': self.route.currentData() or '',
            'output': [x.strip() for x in self.outputs.text().split(',') if x.strip()],
        }
        self.call(lambda c: c.createBatch(files, **spec), lambda _r: (self.queue.clear(), self.refresh()))

    def cancel(self):
        jid = _selectedKey(self.jobs)
        if jid:
            self.call(lambda c: c.cancelJob(jid), lambda _r: self.refresh())
