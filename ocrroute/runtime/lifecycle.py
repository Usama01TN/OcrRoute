# coding=utf-8
"""
Process lifecycle: graceful shutdown and restart that work the same for ``ocrroute serve``, the desktop's
embedded server and multi-worker deployments, on Windows, macOS and Linux (no POSIX signals required).

The running uvicorn ``Server`` registers itself here. Shutdown flips ``server.should_exit`` (uvicorn then drains
connections and returns from ``run()``); restart does the same but leaves a flag the caller checks afterwards to
start again (``serve`` loops, the embedded server thread re-creates its server, multi-worker mode re-execs).
"""
from __future__ import absolute_import, division, print_function

import os
import sys
import threading

from ocrroute.logsetup import getLogger

log = getLogger(__name__)

_lock = threading.Lock()
_server = None
_restartRequested = False
_shutdownRequested = False
_mode = 'unknown'  # serve | embedded | external
_transitioning = False  # True from the moment a stop was requested until the next server registers


def register(server, mode='serve'):
    """
    :param server: uvicorn.Server | None
    :param mode: str  serve | embedded | external
    """
    global _server, _mode, _transitioning
    with _lock:
        _server = server
        _mode = mode
        _transitioning = False


def busy():
    """
    :return: bool  a restart or shutdown is in progress; callers should answer 409 and retry later
    """
    return _transitioning or (_server is not None and getattr(_server, 'should_exit', False))


def getMode():
    """
    :return: str
    """
    return _mode


def canRestart():
    """
    :return: bool  restart is supported for serve and embedded modes (anything that registered a server)
    """
    return _server is not None or _mode == 'serve'


def _stopServer():
    global _transitioning
    if _server is not None:
        _transitioning = True
        _server.should_exit = True
        return True
    return False


def requestShutdown(delay=0.4):
    """
    Stop the server gracefully after ``delay`` seconds (so the HTTP response can be delivered first).

    :param delay: float
    :return: str  the mechanism used
    """
    global _shutdownRequested, _transitioning
    with _lock:
        _shutdownRequested = True
        _transitioning = True

    def go():
        from ocrroute.runtime.endpoints import getEndpointManager

        try:
            getEndpointManager().stopAll()
        except Exception:  # noqa: BLE001
            pass
        if not _stopServer():
            if _mode == 'serve':  # multi-worker uvicorn: the master handles SIGTERM gracefully
                import signal

                log.warning('no registered server object, sending SIGTERM to the process')
                os.kill(os.getpid(), getattr(signal, 'SIGTERM', 15))
            else:
                log.warning('shutdown requested but no server is registered; nothing to stop')

    threading.Timer(delay, go).start()
    return 'server.should_exit' if _server is not None else ('sigterm' if _mode == 'serve' else 'noop')


def requestRestart(delay=0.4):
    """
    Restart the server after ``delay`` seconds.

    :param delay: float
    :return: str  the mechanism used
    """
    global _restartRequested, _transitioning
    with _lock:
        _restartRequested = True
        _transitioning = True

    def go():
        if _server is not None:
            _stopServer()  # the owner (serve loop / embedded thread) sees restartRequested() and starts again
        elif _mode == 'serve':
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            log.warning('restart requested but no server is registered; nothing to restart')

    threading.Timer(delay, go).start()
    return 'server.should_exit+loop' if _server is not None else ('os.execv' if _mode == 'serve' else 'noop')


def restartRequested():
    """
    :return: bool  consumed by the owner after ``server.run()`` returns
    """
    return _restartRequested


def shutdownRequested():
    """
    :return: bool
    """
    return _shutdownRequested


def reset():
    """
    Clear the flags before starting again.
    """
    global _restartRequested, _shutdownRequested, _transitioning
    with _lock:
        _restartRequested = False
        _shutdownRequested = False
        _transitioning = False


__all__ = ['busy', 'canRestart', 'getMode', 'register', 'requestRestart', 'requestShutdown', 'reset', 'restartRequested',
           'shutdownRequested']
