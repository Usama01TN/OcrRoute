# coding=utf-8
"""Tools (reserved empty state), Settings and Doctor pages."""
from __future__ import absolute_import, division, print_function

from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
)

from ocrroute.desktop.pages.base import Page, emptyState


class ToolsPage(Page):
    subtitle = 'Reserved for future post-processing extensions.'
    """Reserved section (§12): present in the sidebar, shows an empty state, no behaviour."""

    title = 'Tools'

    def __init__(self, state):
        super(ToolsPage, self).__init__(state)
        self.root.addWidget(
            emptyState(
                self.tr('Tools'),
                self.tr('No tools are installed. This section is reserved for future post-processing extensions.'),
                self.tr('Add tool'),
            ),
            1,
        )

    def refresh(self):
        pass


class SettingsPage(Page):
    title = 'Settings'

    def __init__(self, state):
        super(SettingsPage, self).__init__(state)
        self.button('Save server settings', self.save, primary=True)
        conn = QFrame()
        conn.setObjectName('card')
        cf = QFormLayout(conn)
        self.mode = QComboBox()
        self.mode.addItems([self.tr('Embedded server (this computer)'), self.tr('Remote server')])
        self.url = QLineEdit(str(self.state.settings.value('remote/url', 'http://127.0.0.1:20256')))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText('ocrr_… (stored in the OS keyring when available)')
        self.theme = QComboBox()
        self.theme.addItems(['system', 'light', 'dark'])
        self.theme.setCurrentText(str(self.state.settings.value('ui/theme', 'system')))
        self.language = QComboBox()
        from ocrroute import i18n as sharedI18n
        from ocrroute.desktop.i18n import defaultLanguage

        for code, name in sharedI18n.languages():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(defaultLanguage(self.state.settings))))
        self.tray = QCheckBox(self.tr('Keep running in the system tray when the window is closed'))
        self.tray.setChecked(bool(self.state.settings.value('ui/tray', True, type=bool)))
        connect = QPushButton(self.tr('Connect'))
        connect.clicked.connect(self._connect)
        cf.addRow(self.tr('Mode'), self.mode)
        cf.addRow(self.tr('Server URL'), self.url)
        cf.addRow(self.tr('API key'), self.key)
        cf.addRow('', connect)
        cf.addRow(self.tr('Theme'), self.theme)
        cf.addRow(self.tr('Language'), self.language)
        cf.addRow('', self.tray)
        self.mode.setCurrentIndex(0 if bool(self.state.settings.value('mode/embedded', True, type=bool)) else 1)
        self.mode.currentIndexChanged.connect(lambda i: self.state.settings.setValue('mode/embedded', i == 0))
        self.theme.currentTextChanged.connect(
            lambda t: (self.state.settings.setValue('ui/theme', t), self.state.themeChanged.emit(t))
        )
        self.language.currentIndexChanged.connect(
            lambda _i: (self.state.settings.setValue('ui/language', self.language.currentData()),
                        self.state.languageChanged.emit(self.language.currentData()))
        )
        self.tray.toggled.connect(lambda v: self.state.settings.setValue('ui/tray', v))
        self.root.addWidget(conn)

        srv = QFrame()
        srv.setObjectName('card')
        sf = QFormLayout(srv)
        self.retention = QSpinBox()
        self.retention.setRange(1, 3650)
        self.cache_ttl = QSpinBox()
        self.cache_ttl.setRange(0, 10000000)
        self.breaker = QSpinBox()
        self.breaker.setRange(1, 100)
        self.cooldown = QSpinBox()
        self.cooldown.setRange(1, 86400)
        self.deadline = QSpinBox()
        self.deadline.setRange(1000, 3600000)
        self.store_inputs = QCheckBox('store inputs (enables retry)')
        self.privacy = QCheckBox('privacy mode (no persistence of inputs, API engines refused)')
        for lab, w in (
            ('Log retention (days)', self.retention),
            ('Cache TTL (s)', self.cache_ttl),
            ('Breaker threshold', self.breaker),
            ('Breaker cooldown (s)', self.cooldown),
            ('Run deadline (ms)', self.deadline),
            ('', self.store_inputs),
            ('', self.privacy),
        ):
            sf.addRow(lab, w)
        self.root.addWidget(srv)
        self.paths = QLabel('')
        self.paths.setObjectName('muted')
        self.root.addWidget(self.paths)
        self.root.addStretch(1)

    def refresh(self):
        def show(s):
            e = s['effective']
            self.retention.setValue(int(e['log_retention_days']))
            self.cache_ttl.setValue(int(e['cache_ttl_seconds']))
            self.breaker.setValue(int(e['breaker_threshold']))
            self.cooldown.setValue(int(e['breaker_cooldown_seconds']))
            self.deadline.setValue(int(e['total_deadline_ms']))
            self.store_inputs.setChecked(bool(e['store_inputs']))
            self.privacy.setChecked(bool(e['privacy_mode']))
            self.paths.setText('home {} · bind {}:{}'.format(e['home'], e['host'], e['port']))

        self.call(lambda c: c.settings(), show)

    def save(self):
        values = {
            'log_retention_days': self.retention.value(),
            'cache_ttl_seconds': self.cache_ttl.value(),
            'breaker_threshold': self.breaker.value(),
            'breaker_cooldown_seconds': self.cooldown.value(),
            'total_deadline_ms': self.deadline.value(),
            'store_inputs': self.store_inputs.isChecked(),
            'privacy_mode': self.privacy.isChecked(),
        }
        self.call(lambda c: c.patchSettings(values), lambda _r: self.state.toast.emit(self.tr('Settings saved'), False))

    def _connect(self):
        url = self.url.text().strip().rstrip('/')
        key = self.key.text().strip() or self.state.apiKeyFor(url)
        if not url or not key:
            self.state.toast.emit(self.tr('URL and API key are required'), True)
            return
        from ocrroute.desktop.client import OcrRouteClient
        from ocrroute.desktop.workers import runAsync

        def probe():
            OcrRouteClient(url, key).engines()
            return url

        def ok(_u):
            self.state.settings.setValue('remote/url', url)
            self.state.settings.setValue('mode/embedded', False)
            self.state.rememberServer(url, key)
            self.state.connectTo(url, key, embedded=False)
            self.state.toast.emit(self.tr('Connected to ') + url, False)

        runAsync(probe, ok, lambda m, _d: self.state.toast.emit(self.tr('Connection failed: ') + m, True))


class DoctorPage(Page):
    title = 'Doctor'

    def __init__(self, state):
        super(DoctorPage, self).__init__(state)
        self.button('Refresh', self.refresh)
        self.button('Copy system report', self.copy)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.root.addWidget(self.out, 1)
        self.md = ''

    def refresh(self):
        def show(md):
            self.md = md
            self.out.setPlainText(md)

        self.call(lambda c: c.doctorMarkdown(), show)

    def copy(self):
        QApplication.clipboard().setText(self.md)
        self.state.toast.emit(self.tr('System report copied'), False)
