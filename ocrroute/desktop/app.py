# coding=utf-8
"""OcrRoute Desktop main window: sidebar + stacked pages, embedded/remote server, tray, single instance."""
from __future__ import absolute_import, division, print_function

import sys
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QPixmap
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ocrroute.desktop import i18n as desktopI18n
from ocrroute.desktop import icons
from ocrroute.desktop.pages.dashboard import DashboardPage
from ocrroute.desktop.pages.endpoints import EndpointsPage
from ocrroute.desktop.pages.misc import DoctorPage, SettingsPage, ToolsPage
from ocrroute.desktop.pages.scan import ScanPage
from ocrroute.desktop.pages.tables import BatchPage, EnginesPage, HistoryPage, ProvidersPage, RoutesPage
from ocrroute.desktop.state import AppState
from ocrroute.version import __version__

HERE = Path(__file__).parent
NAV_GLYPHS = {'Dashboard': '▦', 'Scan': '⌕', 'Batch': '▤', 'Engines': '⚙', 'Providers & credentials': '◈', 'Routes': '⇶',
              'History': '≣', 'Tools': '⚒', 'Settings': '⚙', 'Doctor': '✚'}


def applyTheme(app, mode):
    """
    Apply a QSS theme immediately (no restart).

    :param app: QApplication
    :param mode: str  light | dark | system
    """
    theme = mode
    if theme == 'system':
        theme = 'dark' if app.palette().window().color().lightness() < 128 else 'light'
    app.setStyleSheet((HERE / 'styles' / '{}.qss'.format(theme)).read_text(encoding='utf-8'))
INSTANCE_KEY = 'ocrroute-desktop-single-instance'


def _icon():
    """
    :return: QIcon  the same brand mark as the web panel's tab icon (PNG shipped with the panel assets)
    """
    icon = QIcon()
    static = HERE.parent / 'panel' / 'static'
    for name in ('favicon-32.png', 'icon-192.png', 'icon-512.png'):
        path = static / name
        if path.exists():
            icon.addFile(str(path))
    if not icon.isNull():
        return icon
    pm = QPixmap(64, 64)  # fallback when assets are missing
    pm.fill(QColor(37, 99, 235))
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self, state):
        super(MainWindow, self).__init__()
        self.state = state
        self.setWindowTitle('OcrRoute Desktop')
        self.setWindowIcon(_icon())
        self.setMinimumSize(760, 520)
        self.resize(1240, 800)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QWidget()
        side.setObjectName('sidebar')
        side.setMinimumWidth(190)
        side.setMaximumWidth(280)
        side.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 10, 0, 10)
        brand = QLabel('OcrRoute')
        brand.setObjectName('brand')
        sl.addWidget(brand)
        brandSub = QLabel(self.tr('OCR gateway') + ' · v{}'.format(__version__))
        brandSub.setObjectName('brandSub')
        sl.addWidget(brandSub)
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setSpacing(0)
        sl.addWidget(self.nav, 1)
        self.conn_label = QLabel('connecting…')
        self.conn_label.setObjectName('muted')
        self.conn_label.setWordWrap(True)
        self.conn_label.setContentsMargins(14, 0, 14, 0)
        sl.addWidget(self.conn_label)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(side)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        lay.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.pages = [
            DashboardPage(state),
            EndpointsPage(state),
            ScanPage(state),
            BatchPage(state),
            EnginesPage(state),
            ProvidersPage(state),
            RoutesPage(state),
            HistoryPage(state),
            ToolsPage(state),
            SettingsPage(state),
            DoctorPage(state),
        ]
        for p in self.pages:
            scroller = QScrollArea()
            scroller.setWidgetResizable(True)
            scroller.setFrameShape(QScrollArea.NoFrame)
            scroller.setWidget(p)
            self.stack.addWidget(scroller)
            label = self.tr(p.title) + ('  · ' + self.tr('reserved') if isinstance(p, ToolsPage) else '')
            dark = QApplication.instance().styleSheet().find('dark theme') >= 0
            it = QListWidgetItem(icons.pageIcon(p.title, '#94A3B8' if dark else '#64748B'), label)
            it.setToolTip(self.tr(p.title))
            it.setSizeHint(QSize(0, 36))
            self.nav.addItem(it)
        self.scanIndex = next(i for i, pg in enumerate(self.pages) if isinstance(pg, ScanPage))
        self.settingsIndex = next(i for i, pg in enumerate(self.pages) if isinstance(pg, SettingsPage))
        self.nav.currentRowChanged.connect(self._goto)
        self.nav.setCurrentRow(self.scanIndex)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.connPill = QLabel(self.tr('Not connected'))
        self.connPill.setObjectName('pillBad')
        self.status.addPermanentWidget(self.connPill)
        self.state.connected.connect(lambda _u: (self.connPill.setText(self.tr('Connected to') + ' ' + ('embedded' if self.state.embedded else 'remote')), self.connPill.setObjectName('pillOk'), self.connPill.style().unpolish(self.connPill), self.connPill.style().polish(self.connPill)))
        self.state.disconnected.connect(lambda _w: (self.connPill.setText(self.tr('Not connected')), self.connPill.setObjectName('pillBad'), self.connPill.style().unpolish(self.connPill), self.connPill.style().polish(self.connPill)))
        self.state.toast.connect(lambda m, bad: self.status.showMessage(('⚠ ' if bad else '') + m, 6000))
        self.state.connected.connect(
            lambda url: self.conn_label.setText('{} · {}'.format('embedded' if self.state.embedded else 'remote', url))
        )
        self.state.disconnected.connect(lambda why: self.conn_label.setText('disconnected: {}'.format(why)))
        self._menus()
        self._tray()
        geo = self.state.settings.value('ui/geometry')
        if geo is not None:
            self.restoreGeometry(geo)
        split = self.state.settings.value('ui/splitter')
        if split is not None:
            self.splitter.restoreState(split)
        if bool(self.state.settings.value('ui/maximized', False, type=bool)):
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

    def _recolourNav(self, row):
        """
        :param row: int  selected row - its icon inverts to stay visible on the active background
        """
        dark = QApplication.instance().styleSheet().find('dark theme') >= 0
        normal = '#94A3B8' if dark else '#64748B'
        active = '#FFFFFF' if dark else '#1E40AF'
        for i in range(self.nav.count()):
            self.nav.item(i).setIcon(icons.pageIcon(self.pages[i].title, active if i == row else normal))

    def _goto(self, row):
        self._recolourNav(row)
        self.stack.setCurrentIndex(row)
        page = self.pages[row]
        if self.state.client is not None:
            page.refresh()

    def _menus(self):
        m = self.menuBar()
        file = m.addMenu(self.tr('&File'))
        for text, seq, slot in (
            (self.tr('Open file…'), QKeySequence.Open, self.pages[self.scanIndex].openFile),
            (self.tr('Paste image'), QKeySequence('Ctrl+Shift+V'), self.pages[self.scanIndex].pasteClipboard),
            (self.tr('Capture region'), QKeySequence('Ctrl+Shift+R'), self.pages[self.scanIndex].captureRegion),
            (self.tr('Run OCR'), QKeySequence('Ctrl+Return'), self.pages[self.scanIndex].run),
        ):
            a = QAction(text, self)
            a.setShortcut(seq)
            a.triggered.connect(slot)
            file.addAction(a)
        file.addSeparator()
        q = QAction(self.tr('Quit'), self)
        q.setShortcut(QKeySequence.Quit)
        q.triggered.connect(self._quit)
        file.addAction(q)
        view = m.addMenu(self.tr('&View'))
        for i, p in enumerate(self.pages):
            a = QAction(p.title, self)
            a.setShortcut(QKeySequence('Ctrl+{}'.format((i + 1) % 10)))
            a.triggered.connect(lambda _c, i=i: self.nav.setCurrentRow(i))
            view.addAction(a)
        helpm = m.addMenu(self.tr('&Help'))
        a = QAction(self.tr('Open control panel in browser'), self)
        a.triggered.connect(lambda: __import__('webbrowser').open('{}/panel/'.format(self.state.base_url)))
        helpm.addAction(a)
        a = QAction(self.tr('API docs'), self)
        a.triggered.connect(lambda: __import__('webbrowser').open('{}/v1/docs'.format(self.state.base_url)))
        helpm.addAction(a)

    def _tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(_icon(), self)
        menu = QMenu()
        menu.addAction(self.tr('Show'), self._show)
        menu.addAction(
            self.tr('Scan clipboard'),
            lambda: (self._show(), self.nav.setCurrentRow(self.scanIndex), self.pages[self.scanIndex].pasteClipboard(), self.pages[self.scanIndex].run()),
        )
        menu.addAction(self.tr('Scan region'), lambda: (self.nav.setCurrentRow(self.scanIndex), self.pages[self.scanIndex].captureRegion()))
        menu.addSeparator()
        menu.addAction(self.tr('Quit'), self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self._show() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip('OcrRoute Desktop')
        self.tray.show()

    def _show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        self.state.settings.setValue('ui/geometry', self.saveGeometry())
        self.state.settings.setValue('ui/splitter', self.splitter.saveState())
        self.state.settings.setValue('ui/maximized', self.isMaximized())
        if (
            self.tray is not None
            and bool(self.state.settings.value('ui/tray', True, type=bool))
            and not getattr(self, '_quitting', False)
        ):
            e.ignore()
            self.hide()
            self.tray.showMessage('OcrRoute', self.tr('Still running in the tray.'), QSystemTrayIcon.Information, 2000)
            return
        e.accept()

    def _quit(self):
        self._quitting = True
        self.close()
        QApplication.instance().quit()


def _singleInstance():
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.write(b'raise')
        sock.flush()
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(INSTANCE_KEY)
    server = QLocalServer()
    server.listen(INSTANCE_KEY)
    return server


def main(argv=None):
    from ocrroute.stdio import ensureStreams

    ensureStreams('desktop')  # pythonw / windowed builds have no console streams
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(argv or sys.argv)
    app.setApplicationName('OcrRoute Desktop')
    app.setOrganizationName('OcrRoute')
    app.setWindowIcon(_icon())
    server = _singleInstance()
    if server is None:
        return 0
    state = AppState()
    applyTheme(app, str(state.settings.value('ui/theme', 'system')))
    desktopI18n.install(app, desktopI18n.defaultLanguage(state.settings))
    holder = {'win': MainWindow(state)}
    server.newConnection.connect(lambda: holder['win']._show())
    holder['win'].show()

    def rebuild():
        """Recreate the window so every tr() string is re-evaluated in the new language."""
        old = holder['win']
        geometry = old.saveGeometry()
        old._quitting = True
        old.hide()
        new = MainWindow(state)
        new.restoreGeometry(geometry)
        new.show()
        holder['win'] = new
        old.deleteLater()
        if state.client is not None:
            state.connected.emit(state.base_url)

    state.themeChanged.connect(lambda mode: applyTheme(app, mode))
    state.languageChanged.connect(lambda lang: (desktopI18n.install(app, lang), rebuild()))

    embedded = bool(state.settings.value('mode/embedded', True, type=bool))
    if embedded:
        from ocrroute.desktop.server import EmbeddedServer

        srv = EmbeddedServer()
        srv.ready.connect(lambda url, key: state.connectTo(url, key, embedded=True))
        srv.failed.connect(lambda why: state.disconnected.emit(why))
        srv.start()
        app.aboutToQuit.connect(srv.stop)
    else:
        url = str(state.settings.value('remote/url', ''))
        key = state.apiKeyFor(url) if url else ''
        if url and key:
            QTimer.singleShot(0, lambda: state.connectTo(url, key, embedded=False))
        else:
            holder['win'].nav.setCurrentRow(holder['win'].settingsIndex)
            state.toast.emit('Configure the remote server in Settings', True)
    return app.exec_()
