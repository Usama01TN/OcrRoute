# coding=utf-8
"""Desktop smoke tests (offscreen Qt). Skipped when PyQt5 is not installed."""

import os

import pytest

pytest.importorskip('ManyQt')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(scope='module')
def qapp():
    from ManyQt.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_pages_construct_and_tools_is_empty_state(qapp):
    from ocrroute.desktop.app import MainWindow
    from ocrroute.desktop.pages.misc import ToolsPage
    from ocrroute.desktop.state import AppState

    win = MainWindow(AppState())
    assert [p.title for p in win.pages] == ['Dashboard', 'Endpoints', 'Scan', 'Batch', 'Engines', 'Providers & credentials',
                                            'Routes', 'History', 'Tools', 'Settings', 'Doctor']
    assert isinstance(win.pages[8], ToolsPage)
    assert 'reserved' in win.nav.item(8).text()
    for i in range(win.nav.count()):
        win.nav.setCurrentRow(i)
        assert win.stack.currentIndex() == i


def test_image_viewer_overlays_and_region(qapp, sample_png):

    from ocrroute.desktop.widgets.image_viewer import ImageViewer
    from ocrroute.enginelib import OCRPlugin

    v = ImageViewer()
    v.setImageBytes(sample_png)
    res = OCRPlugin().buildResult([OCRPlugin.makeWord('a', 1, 1, 10, 5), OCRPlugin.makeWord('b', 20, 1, 10, 5)])
    v.setResult(res)
    assert len(v.boxes) == 3  # 1 line box + 2 words
    v.setBoxesVisible(False)
    assert all(not b.isVisible() for b in v.boxes)
    got = []
    v.regionSelected.connect(lambda *r: got.append(r))
    v.setCropMode(True)
    from ManyQt.QtCore import QPointF

    v.beginCrop(QPointF(5, 5))
    v.updateCrop(QPointF(55, 35))
    assert v.endCrop() is True
    assert got and got[0][2] == 50


def test_scan_page_runs_against_fake_engine(qapp, ctx, sample_png):
    """Full Scan flow against a real uvicorn server (as the desktop does), asserting the GUI thread never blocks."""
    import socket
    import threading
    import time

    import requests
    import uvicorn
    from ManyQt.QtCore import QEventLoop, QThreadPool, QTimer

    from ocrroute.api.app import createApp
    from ocrroute.crypto import hashApiKey, newApiKey
    from ocrroute.db.models import ApiKey
    from ocrroute.db.session import sessionScope
    from ocrroute.desktop.client import OcrRouteClient
    from ocrroute.desktop.pages.scan import ScanPage
    from ocrroute.desktop.state import AppState

    raw = newApiKey()
    with sessionScope() as s:
        s.add(ApiKey(name='d', key_hash=hashApiKey(raw), key_prefix=raw[:10], scopes=['admin']))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(createApp(include_panel=False), host='127.0.0.1', port=port, log_level='error'))
    threading.Thread(target=server.run, daemon=True).start()
    base = 'http://127.0.0.1:{}'.format(port)
    for _ in range(100):
        try:
            if requests.get(base + '/v1/health', timeout=0.5).ok:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    try:
        QThreadPool.globalInstance().setMaxThreadCount(8)
        state = AppState()
        page = ScanPage(state)
        state.client = OcrRouteClient(base, raw)
        state.base_url = base
        toasts = []
        state.toast.connect(lambda m, bad: toasts.append((bad, m)))
        state.connected.emit(base)
        loop = QEventLoop()
        QTimer.singleShot(10000, loop.quit)
        tm = QTimer()
        tm.timeout.connect(lambda lp=loop: lp.quit() if page.target.count() > 1 else None)  # bind this loop
        tm.start(50)
        loop.exec_()
        tm.stop()  # a leftover poller would otherwise quit the next event loop early
        assert page.target.findData('engine:FakeOcr') >= 0, toasts
        page.set_input(sample_png, 't.png') if hasattr(page, 'set_input') else page.setInput(sample_png, 't.png')
        page.target.setCurrentIndex(page.target.findData('engine:FakeOcr'))
        # GUI responsiveness probe: a precise 10 ms timer must keep firing while the scan runs in the worker pool.
        # The loop stays open for at least MIN_WINDOW seconds so fast machines (e.g. Windows CI) still collect
        # enough samples; Qt.PreciseTimer avoids Windows' default ~15.6 ms coarse timer resolution.
        from ManyQt.QtCore import Qt

        MIN_WINDOW = 0.4
        ticks = []
        tick = QTimer()
        tick.setTimerType(Qt.PreciseTimer)
        tick.timeout.connect(lambda: ticks.append(time.monotonic()))
        tick.start(10)
        started = time.monotonic()
        page.run()
        loop = QEventLoop()
        QTimer.singleShot(30000, loop.quit)
        tm2 = QTimer()
        tm2.timeout.connect(lambda lp=loop: lp.quit() if page.envelope and time.monotonic() - started >= MIN_WINDOW else None)
        tm2.start(25)
        loop.exec_()
        tm2.stop()
        tick.stop()
        assert page.envelope and page.envelope['status'] == 'succeeded', toasts
        assert page.text.toPlainText().startswith('hello world') and page.lines.rowCount() == 2
        assert len(ticks) >= 5, 'responsiveness probe collected only {} samples'.format(len(ticks))
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        worst = max(gaps)
        assert worst < 0.25, 'GUI thread blocked for {:.0f} ms'.format(worst * 1000)
        from ocrroute.desktop.workers import liveCount

        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec_()
        assert liveCount() == 0, 'workers leaked: {}'.format(liveCount())
    finally:
        server.should_exit = True


def test_worker_results_survive_garbage_collection(qapp):
    """Regression: an unreferenced Worker's signal object used to be collected mid-run, dropping the result."""
    import gc
    import time

    from ManyQt.QtCore import QEventLoop, QTimer

    from ocrroute.desktop.workers import liveCount, runAsync

    got = []
    for i in range(40):
        runAsync(lambda i=i: (time.sleep(0.02), i)[1], got.append)  # no reference kept by the caller
    collector = QTimer()
    collector.timeout.connect(gc.collect)  # force collections while the workers run
    collector.start(1)
    loop = QEventLoop()
    done = QTimer()
    done.timeout.connect(lambda: loop.quit() if len(got) == 40 else None)
    done.start(10)
    QTimer.singleShot(15000, loop.quit)
    loop.exec_()
    collector.stop()
    assert sorted(got) == list(range(40))
    loop = QEventLoop()
    QTimer.singleShot(100, loop.quit)
    loop.exec_()
    assert liveCount() == 0
