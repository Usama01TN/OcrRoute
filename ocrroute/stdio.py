# coding=utf-8
"""
Standard streams for GUI (windowed) processes.

A PyInstaller ``--windowed`` executable, or ``pythonw.exe`` on Windows, starts with ``sys.stdout`` and
``sys.stderr`` set to ``None``. Anything that writes to them then fails: ``faulthandler.enable()`` raises
``RuntimeError: sys.stderr is None``, ``print()`` calls from AioOCR engines are dropped or raise, and loggers
crash. ``ensureStreams()`` gives such a process real streams (a rotating log file) before anything else runs.
"""
from __future__ import absolute_import, division, print_function

import io
import os
import sys


def logDir():
    """
    :return: str  ``$OCRROUTE_HOME/logs`` (default ``~/.ocrroute/logs``), created on demand
    """
    home = os.environ.get('OCRROUTE_HOME') or os.path.join(os.path.expanduser('~'), '.ocrroute')
    path = os.path.join(home, 'logs')
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(os.environ.get('TEMP') or os.environ.get('TMPDIR') or '/tmp', 'ocrroute-logs')
        os.makedirs(path, exist_ok=True)
    return path


def _usable(stream):
    if stream is None:
        return False
    try:
        stream.write('')
        return True
    except (AttributeError, OSError, ValueError):
        return False


def ensureStreams(name='desktop', maxBytes=2 * 1024 * 1024):
    """
    Replace missing or broken ``sys.stdout`` / ``sys.stderr`` with a UTF-8 log file.

    :param name: str  log file stem (``desktop`` -> ``~/.ocrroute/logs/desktop.log``)
    :param maxBytes: int  the previous log is kept as ``.1`` once it grows past this size
    :return: str | None  path of the log file, or None when the console streams were usable
    """
    if _usable(sys.stdout) and _usable(sys.stderr):
        return None
    path = os.path.join(logDir(), name + '.log')
    try:
        if os.path.exists(path) and os.path.getsize(path) > maxBytes:
            os.replace(path, path + '.1')
    except OSError:
        pass
    stream = open(path, 'a', encoding='utf-8', errors='replace', buffering=1)  # line-buffered
    if not _usable(sys.stdout):
        sys.stdout = stream
    if not _usable(sys.stderr):
        sys.stderr = stream
    return path


def enableFaultHandler():
    """
    Enable ``faulthandler`` on whatever stderr we have; never raises (a crash dump is a diagnostic, not a
    requirement for starting the app).

    :return: bool
    """
    import faulthandler

    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
        return True
    except (RuntimeError, AttributeError, ValueError, io.UnsupportedOperation):
        return False


__all__ = ['enableFaultHandler', 'ensureStreams', 'logDir']
