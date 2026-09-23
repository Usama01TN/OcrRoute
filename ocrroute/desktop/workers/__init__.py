# coding=utf-8
"""QRunnable worker: run a callable off the GUI thread and deliver result/error via signals."""
from __future__ import absolute_import, division, print_function

import traceback

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class _Signals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str, str)  # short message, detail
    finished = pyqtSignal()


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


def runAsync(fn, on_result=None, on_error=None, on_finished=None, *args, **kwargs):
    w = Worker(fn, *args, **kwargs)
    if on_result:
        w.signals.result.connect(on_result)
    if on_error:
        w.signals.error.connect(on_error)
    if on_finished:
        w.signals.finished.connect(on_finished)
    QThreadPool.globalInstance().start(w)
    return w
