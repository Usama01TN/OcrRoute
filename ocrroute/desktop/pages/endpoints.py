# coding=utf-8
"""
Endpoints page: where the gateway is reachable, tunnels, public URL and the global OCR prompt.
"""
from __future__ import absolute_import, division, print_function

from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ocrroute.desktop.pages.base import Page, cardTitle


class EndpointsPage(Page):
    """
    EndpointsPage class.
    """
    title = 'Endpoints'
    subtitle = 'Your OCR connection URLs'

    def __init__(self, state):
        super(EndpointsPage, self).__init__(state)
        self.button('Refresh', self.refresh)
        self.__m_active = QFrame()
        self.__m_active.setObjectName('card')
        al = QVBoxLayout(self.__m_active)
        al.addWidget(cardTitle(self.tr('Active endpoints')))
        self.__m_activeBox = QVBoxLayout()
        al.addLayout(self.__m_activeBox)
        self.root.addWidget(self.__m_active)
        self.__m_local = QFrame()
        self.__m_local.setObjectName('card')
        ll = QVBoxLayout(self.__m_local)
        ll.addWidget(cardTitle(self.tr('Local server')))
        self.__m_localBox = QVBoxLayout()
        ll.addLayout(self.__m_localBox)
        self.root.addWidget(self.__m_local)
        self.__m_tunnels = QFrame()
        self.__m_tunnels.setObjectName('card')
        tl = QVBoxLayout(self.__m_tunnels)
        tl.addWidget(cardTitle(self.tr('Tunnels')))
        self.__m_tunnelBox = QVBoxLayout()
        tl.addLayout(self.__m_tunnelBox)
        self.root.addWidget(self.__m_tunnels)
        extra = QFrame()
        extra.setObjectName('card')
        el = QVBoxLayout(extra)
        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr('Public URL')))
        self.__m_public = QLineEdit()
        self.__m_public.setPlaceholderText('https://ocr.example.com')
        row.addWidget(self.__m_public, 1)
        save = QPushButton(self.tr('Save'))
        save.setProperty('primary', True)
        save.clicked.connect(self._savePublic)
        row.addWidget(save)
        el.addLayout(row)
        self.__m_promptToggle = QCheckBox(self.tr('Inject a custom prompt into every VLM engine request'))
        self.__m_promptToggle.toggled.connect(self._togglePrompt)
        el.addWidget(self.__m_promptToggle)
        self.__m_prompt = QPlainTextEdit()
        self.__m_prompt.setPlaceholderText('Return only the visible text, preserving line breaks.')
        self.__m_prompt.setMaximumHeight(90)
        el.addWidget(self.__m_prompt)
        savePrompt = QPushButton(self.tr('Save'))
        savePrompt.clicked.connect(self._savePrompt)
        el.addWidget(savePrompt, 0)
        self.root.addWidget(extra)
        self.root.addStretch(1)

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _urlRow(self, label, url, ok=True):
        """
        :return: QWidget  label + monospace url + copy button
        """
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        pill = QLabel(label)
        pill.setObjectName('pillOk' if ok else 'pill')
        h.addWidget(pill)
        u = QLabel(url)
        u.setTextInteractionFlags(u.textInteractionFlags() | 1)  # selectable
        u.setStyleSheet('font-family: Menlo, Consolas, monospace;')
        h.addWidget(u, 1)
        copy = QPushButton(self.tr('Copy'))
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(url), self.state.toast.emit(self.tr('Copied'), False)))
        h.addWidget(copy)
        return w

    def refresh(self):
        self.call(lambda c: c._req('GET', '/v1/endpoints'), self._show)

    def _show(self, ep):
        self._clear(self.__m_activeBox)
        for a in ep['active']:
            self.__m_activeBox.addWidget(self._urlRow(a['label'], a['url']))
        self._clear(self.__m_localBox)
        head = QLabel('{} · {}'.format(self.tr('Running'), ep['server_id']))
        head.setObjectName('pillOk')
        from PyQt5.QtCore import Qt

        self.__m_localBox.addWidget(head, 0, Qt.AlignLeft)
        for u in ep['local']:
            self.__m_localBox.addWidget(self._urlRow('LAN', u, ok=False))
        self._clear(self.__m_tunnelBox)
        for t in ep['tunnels']:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 4, 0, 4)
            name = QLabel('<b>{}</b>{}'.format(t['title'], ('<br><span style="color:#8B93A7">{}/v1</span>'.format(t['url']) if t['url'] else '')))
            h.addWidget(name, 1)
            if t['running']:
                st = QLabel(self.tr('active'))
                st.setObjectName('pillOk')
                h.addWidget(st)
                b = QPushButton(self.tr('Disable'))
                b.clicked.connect(lambda _c, n=t['name']: self._tunnel(n, 'disable'))
            elif not t['installed']:
                st = QLabel(self.tr('Not installed'))
                st.setObjectName('pill')
                h.addWidget(st)
                b = QPushButton(self.tr('Install & enable').replace('&', '&&'))
                b.setProperty('primary', True)
                b.setToolTip(t.get('install_command') or t.get('homepage', ''))
                b.clicked.connect(lambda _c, n=t['name']: self._tunnel(n, 'install'))
            else:
                if not t['authenticated']:
                    st = QLabel(self.tr('Needs auth'))
                    st.setObjectName('pillBad')
                    h.addWidget(st)
                b = QPushButton(self.tr('Enable tunnel'))
                b.setProperty('primary', True)
                b.clicked.connect(lambda _c, n=t['name']: self._tunnel(n, 'enable'))
            h.addWidget(b)
            self.__m_tunnelBox.addWidget(w)
        self.__m_public.setText(ep.get('public_url', ''))
        self.__m_promptToggle.blockSignals(True)
        self.__m_promptToggle.setChecked(bool(ep.get('custom_prompt_enabled')))
        self.__m_promptToggle.blockSignals(False)
        self.__m_prompt.setPlainText(ep.get('custom_prompt', ''))
        self.__m_prompt.setVisible(bool(ep.get('custom_prompt_enabled')))

    def _tunnel(self, name, action):
        def done(r):
            if action == 'install':
                self.state.toast.emit(self.tr('Saved') if r.get('ok') else (r.get('output') or 'install failed')[-200:], not r.get('ok'))
            elif r.get('url'):
                self.state.toast.emit(r['url'], False)
            self.refresh()

        self.call(lambda c: c._req('POST', '/v1/endpoints/tunnels/{}/{}'.format(name, action)), done)

    def _savePublic(self):
        url = self.__m_public.text().strip()
        self.call(lambda c: c._req('PATCH', '/v1/endpoints', json={'public_base_url': url}), lambda _r: (self.state.toast.emit(self.tr('Saved'), False), self.refresh()))

    def _togglePrompt(self, on):
        self.__m_prompt.setVisible(on)
        self.call(lambda c: c._req('PATCH', '/v1/endpoints', json={'custom_prompt_enabled': bool(on)}), lambda _r: self.state.toast.emit(self.tr('Saved'), False))

    def _savePrompt(self):
        text = self.__m_prompt.toPlainText()
        self.call(lambda c: c._req('PATCH', '/v1/endpoints', json={'custom_prompt': text}), lambda _r: self.state.toast.emit(self.tr('Saved'), False))
