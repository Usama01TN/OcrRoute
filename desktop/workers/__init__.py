# coding=utf-8
"""QRunnable worker: run a callable off the GUI thread and deliver result/error via signals."""
from __future__ import absolute_import, division, print_function

import traceback

from ManyQt.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    result = Signal(object)
    error = Signal(str, str)  # short message, detail
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.__m_signals = _Signals()
        self.setAutoDelete(True)

    def run(self):
        try:
            res = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            self.__m_signals.error.emit(str(exc)[:300], traceback.format_exc())
        else:
            self.__m_signals.result.emit(res)
        finally:
            self.__m_signals.finished.emit()

    def getSignals(self):
        """
        :return: any
        """
        return self.__m_signals

    def setSignals(self, signals):
        """
        :param signals: any
        """
        self.__m_signals = signals

    signals = property(fget=getSignals, fset=setSignals)


# Workers alive until their ``finished`` signal is delivered. QThreadPool owns the C++ QRunnable, but the Python
# wrapper (and the _Signals QObject it holds) would otherwise be garbage-collected mid-run, silently dropping the
# result. Keyed by id() because QRunnable is not hashable in every PyQt build.
_LIVE = {}


def liveCount():
    """
    :return: int  workers started and not yet finished (diagnostics / tests)
    """
    return len(_LIVE)


def runAsync(fn, on_result=None, on_error=None, on_finished=None, *args, **kwargs):
    """
    Run ``fn`` in the global thread pool and deliver its outcome on the GUI thread.

    :param fn: callable
    :param on_result: callable(result) | None
    :param on_error: callable(message, detail) | None
    :param on_finished: callable() | None  always called last
    :return: Worker
    """
    w = Worker(fn, *args, **kwargs)
    key = id(w)
    _LIVE[key] = w
    w.setAutoDelete(False)  # Python keeps ownership; released below once every signal has been delivered
    if on_result:
        w.signals.result.connect(on_result)
    if on_error:
        w.signals.error.connect(on_error)
    if on_finished:
        w.signals.finished.connect(on_finished)
    w.signals.finished.connect(lambda: _LIVE.pop(key, None))  # connected last: runs after the caller's slots
    QThreadPool.globalInstance().start(w)
    return w
