# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from ManyQt.QtCore import Qt
from ManyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ocrroute.desktop.workers import runAsync


class Page(QWidget):
    title = 'Page'
    subtitle = ''

    def __init__(self, state):
        super(Page, self).__init__()
        self.state = state
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(22, 18, 22, 18)
        self.root.setSpacing(12)
        self.setObjectName('pageRoot')
        head = QHBoxLayout()
        titleBox = QVBoxLayout()
        titleBox.setSpacing(0)
        self.heading = QLabel(self.tr(self.title))
        self.heading.setObjectName('heading')
        titleBox.addWidget(self.heading)
        self.subtitleLabel = QLabel(self.tr(self.subtitle) if self.subtitle else '')
        self.subtitleLabel.setObjectName('subtitle')
        self.subtitleLabel.setVisible(bool(self.subtitle))
        titleBox.addWidget(self.subtitleLabel)
        head.addLayout(titleBox)
        head.addStretch(1)
        self.actions = QHBoxLayout()
        head.addLayout(self.actions)
        self.root.addLayout(head)
        self.state.connected.connect(lambda _u: self.refresh())

    def refresh(self):  # pragma: no cover - overridden
        pass

    def call(self, fn, on_result=None, **kw):
        """Run ``fn(client)`` off-thread; errors go to a toast."""
        if self.state.client is None:
            self.state.toast.emit(self.tr('Not connected'), True)
            return
        client = self.state.client
        return runAsync(lambda: fn(client), on_result, lambda m, _d: self.state.toast.emit(m, True), **kw)

    def button(self, text, slot, primary=False):
        b = QPushButton(self.tr(text))
        if primary:
            b.setProperty('primary', True)
        b.clicked.connect(slot)
        self.actions.addWidget(b)
        return b


def cardTitle(text):
    """
    :param text: str
    :return: QLabel  uppercase card header
    """
    label = QLabel(text.upper())
    label.setObjectName('cardTitle')
    return label


def emptyState(title, text, action=''):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.addStretch(1)
    t = QLabel(title)
    t.setObjectName('emptyTitle')
    t.setAlignment(Qt.AlignCenter)
    s = QLabel(text)
    s.setObjectName('muted')
    s.setAlignment(Qt.AlignCenter)
    s.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(s)
    if action:
        b = QPushButton(action)
        b.setEnabled(False)
        b.setToolTip('Not available in this release')
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(b)
        h.addStretch(1)
        lay.addLayout(h)
    lay.addStretch(1)
    return w


def fmtMs(ms):
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return '-'
    return '{:.2f} s'.format(ms / 1000) if ms >= 1000 else '{} ms'.format(ms)
