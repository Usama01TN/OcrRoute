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


# Start-up lines native libraries print directly to file descriptor 2 while engines are imported. They are not errors,
# and newer TensorFlow prints them through absl, which ignores TF_CPP_MIN_LOG_LEVEL.
NATIVE_NOISE = (
    'oneDNN custom operations are on',
    'All log messages before absl::InitializeLog() is called are written to STDERR',
    'This TensorFlow binary is optimized to use available CPU instructions',
    'To enable the following instructions:',
    'Unable to register cuDNN factory', 'Unable to register cuFFT factory', 'Unable to register cuBLAS factory',
    'computation placer already registered',
    'Could not find cuda drivers on your machine',
    'TF-TRT Warning: Could not find TensorRT',
)


class filteredNativeStderr(object):
    """
    Context manager: while it is active, everything written to file descriptor 2 (Python *and* C/C++ code) is captured;
    on exit the known start-up noise (``NATIVE_NOISE``) is dropped and every other line is written back to stderr, so real
    warnings and errors are never lost. Does nothing when stderr has no file descriptor (windowed apps, test capture) or
    when ``OCRROUTE_VERBOSE_DISCOVERY`` is set.
    """

    def __init__(self, noise=NATIVE_NOISE):
        self.noise = noise
        self.saved = None

    def __enter__(self):
        import tempfile

        if os.environ.get('OCRROUTE_VERBOSE_DISCOVERY'):
            return self
        try:
            if sys.stderr is not None:
                sys.stderr.flush()
        except (OSError, ValueError, AttributeError):
            pass
        try:
            self.saved = os.dup(2)  # native libraries write to descriptor 2 itself, whatever sys.stderr is
        except OSError:  # no descriptor 2 (windowed app): nothing to filter
            self.saved = None
            return self
        self.tmp = tempfile.TemporaryFile()
        os.dup2(self.tmp.fileno(), 2)
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            return False
        try:
            sys.stderr.flush()
        except (OSError, ValueError, AttributeError):
            pass
        os.dup2(self.saved, 2)
        os.close(self.saved)
        self.saved = None
        self.tmp.seek(0)
        text = self.tmp.read().decode('utf-8', 'replace')
        self.tmp.close()
        kept = [ln for ln in text.splitlines() if ln.strip() and not any(n in ln for n in self.noise)]
        if kept:
            try:
                os.write(2, ('\n'.join(kept) + '\n').encode('utf-8', 'replace'))
            except OSError:
                pass
        return False


__all__ = ['NATIVE_NOISE', 'enableFaultHandler', 'ensureStreams', 'filteredNativeStderr', 'logDir']
