# coding=utf-8
"""
Bridge to the AioOCR engine library.

The AioOCR package is shipped **unmodified** next to ``ocrroute``. Its modules were written to run from
inside their own folder: every engine tries ``from .ocrplugin import OCRPlugin`` first and falls back to
``from engines.ocrplugin import OCRPlugin``, and ``AioOCR/__init__.py`` discovers the plugins with
``issubclass(obj, OCRPlugin)``. When the folder is imported as a package those two import paths would
produce two different ``OCRPlugin`` classes and discovery would find nothing.

This module puts the AioOCR folder on ``sys.path`` and registers the top-level ``engines`` package under
its ``AioOCR.engines`` name *before* AioOCR is imported, so there is exactly one ``OCRPlugin`` class and
AioOCR's own ``_discoverOcrPlugins`` works exactly as its author intended.
"""
from __future__ import absolute_import, division, print_function

from os.path import abspath, dirname, exists, join
from sys import modules, path

# The library ships as a top-level package next to ``ocrroute`` (repository root / site-packages).
AIOOCR_ROOT = abspath(join(dirname(dirname(__file__)), 'AioOCR'))
if not exists(AIOOCR_ROOT):  # frozen with PyInstaller: assets live under sys._MEIPASS
    import sys as _sys
    _frozenBase = getattr(_sys, '_MEIPASS', '')
    if _frozenBase and exists(join(_frozenBase, 'AioOCR')):
        AIOOCR_ROOT = join(_frozenBase, 'AioOCR')
if not exists(AIOOCR_ROOT):  # installed as a package: fall back to whatever ``import AioOCR`` resolves to
    try:
        from importlib import util as _util
        _spec = _util.find_spec('AioOCR')
        if _spec is not None and _spec.origin:
            AIOOCR_ROOT = dirname(abspath(_spec.origin))
    except (ImportError, ValueError):
        pass

if AIOOCR_ROOT not in path:
    path.insert(0, AIOOCR_ROOT)


# ---- crash-isolated discovery ---------------------------------------------------------------------------------
# AioOCR catches Python exceptions per engine module, but a compiled dependency that crashes the interpreter
# (segfault / illegal instruction in a wheel built for another CPU) would take the whole gateway down with no
# message. When enabled (frozen builds by default, or OCRROUTE_SAFE_DISCOVERY=1), engine imports are first probed
# in a child process; modules that crash it are blocked in sys.modules so AioOCR skips them like any missing
# dependency. The result is cached per bundle, Python version and CPU architecture.
CRASHED = {}  # module name -> explanation


def _safeDiscoveryEnabled():
    import os as _os
    import sys as _s

    flag = _os.environ.get('OCRROUTE_SAFE_DISCOVERY', '')
    if flag in ('0', 'false', 'no'):
        return False
    return flag in ('1', 'true', 'yes') or bool(getattr(_s, 'frozen', False))


def _probeCommand():
    import sys as _s

    if getattr(_s, 'frozen', False):
        return [_s.executable, '--ocrroute-probe-engines']
    return [_s.executable, '-c', 'from ocrroute.enginelib_probe import main; main()']


def _probeSignature():
    import hashlib
    import platform
    import sys as _s

    from ocrroute import enginelib_probe as _p
    from ocrroute.version import __version__

    parts = [__version__, _s.version, platform.machine(), _s.platform, str(getattr(_s, 'frozen', False))]
    for name in _p.moduleNames(AIOOCR_ROOT):
        sub, mod = name.split('.')[1:]
        path = join(AIOOCR_ROOT, 'engines', sub, mod + '.py')
        try:
            from os.path import getmtime

            parts.append('{}:{}'.format(name, int(getmtime(path))))
        except OSError:
            parts.append(name)
    return hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:16]


def probeCrashingModules(maxRounds=12, timeout=300):
    """
    :return: dict[str, str]  modules that crash the interpreter when imported, with an explanation
    """
    import json
    import os as _os
    import subprocess

    from ocrroute import enginelib_probe as _p

    cachePath = ''
    try:
        from ocrroute.config import getSettings

        cacheDir = join(str(getSettings().home), 'cache')
        _os.makedirs(cacheDir, exist_ok=True)
        cachePath = join(cacheDir, 'engine-probe-{}.json'.format(_probeSignature()))
        if exists(cachePath):
            with open(cachePath) as fh:
                return json.load(fh)
    except Exception:  # noqa: BLE001 - the cache is an optimisation only
        cachePath = ''
    crashed = {}
    for _round in range(maxRounds):
        env = dict(_os.environ, **{_p.SKIP_ENV: ','.join(crashed), 'OCRROUTE_SAFE_DISCOVERY': '0'})
        try:
            r = subprocess.run(_probeCommand(), env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            break  # a hang is not a crash; let normal discovery proceed
        lines = r.stdout.splitlines()
        if r.returncode == 0 and 'DONE' in lines:
            break
        started = [ln[6:] for ln in lines if ln.startswith('START ')]
        ended = set(ln[4:] for ln in lines if ln.startswith('END '))
        culprit = next((m for m in reversed(started) if m not in ended), None)
        if not culprit:
            break  # the probe itself failed before importing engines; do not guess
        code = r.returncode
        why = 'signal {}'.format(-code) if code < 0 else 'exit code {}'.format(code)
        errLines = [ln.strip() for ln in r.stderr.splitlines() if ln.strip()]
        tail = [ln for ln in errLines if ln.startswith('Fatal Python error')][:1] or \
            [ln for ln in errLines if not ln.startswith(('Extension modules', 'Current thread', 'Thread 0x', 'File '))][-1:]
        crashed[culprit] = 'crashed the interpreter while importing on this platform ({}){}'.format(
            why, (': ' + ' | '.join(tail))[:300] if tail else '')
        _sys.stderr.write('Warning: engine module {} {}; it is disabled.\n'.format(culprit, crashed[culprit]))
    if cachePath:
        try:
            with open(cachePath, 'w') as fh:
                json.dump(crashed, fh)
        except OSError:
            pass
    return crashed


import sys as _sys  # noqa: E402

if _safeDiscoveryEnabled():
    try:
        CRASHED.update(probeCrashingModules())
    except Exception as _exc:  # noqa: BLE001 - never block startup because of the probe itself
        _sys.stderr.write('Warning: crash-isolated engine discovery skipped: {}\n'.format(_exc))
    for _name in CRASHED:  # blocked modules raise ImportError, which AioOCR already handles
        _sys.modules[_name] = None
        _sys.modules['AioOCR.' + _name] = None

import engines  # noqa: E402  (AioOCR/engines, as the library expects)
import engines.api  # noqa: E402
import engines.local  # noqa: E402
import engines.ocrplugin  # noqa: E402

modules.setdefault('AioOCR.engines', engines)
modules.setdefault('AioOCR.engines.api', engines.api)
modules.setdefault('AioOCR.engines.local', engines.local)
modules.setdefault('AioOCR.engines.ocrplugin', engines.ocrplugin)

import io as _io  # noqa: E402
import sys as _sys  # noqa: E402
from contextlib import redirect_stdout as _redirectStdout  # noqa: E402

IMPORT_WARNINGS = []  # AioOCR's "Could not import ..." lines; the Engines page shows the same facts per engine


def _captureWarnings(fn):
    """Run ``fn`` with stdout captured: AioOCR reports missing optional dependencies with print()."""
    buf = _io.StringIO()
    with _redirectStdout(buf):
        fn()
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    del IMPORT_WARNINGS[:]
    IMPORT_WARNINGS.extend(lines)
    import os as _os

    if _os.environ.get('OCRROUTE_VERBOSE_DISCOVERY'):  # opt-in: echo them to stderr like AioOCR does
        for ln in lines:
            _sys.stderr.write(ln + '\n')


def _importAioOcr():
    global AioOCR
    import AioOCR  # noqa: F811  (runs the library's own plugin discovery)


AioOCR = None
_captureWarnings(_importAioOcr)
from AioOCR.engines.ocrplugin import OCRError, OCRPlugin, is_url  # noqa: E402
from AioOCR.ocrbase import OcrBase  # noqa: E402

AVAILABLE_PLUGINS = AioOCR.AVAILABLE_PLUGINS
ENGINES_ROOT = join(AIOOCR_ROOT, 'engines')


def enginesSnapshot():
    """
    :return: dict[str, float]  module file path -> mtime for every engine module (used to detect new/changed engines)
    """
    from os import listdir
    from os.path import getmtime, isdir
    snap = {}
    for sub in enginePackages():
        folder = join(ENGINES_ROOT, sub)
        if not isdir(folder):
            continue
        for fileName in listdir(folder):
            if fileName.endswith('.py') and not fileName.startswith('_'):
                full = join(folder, fileName)
                try:
                    snap[full] = getmtime(full)
                except OSError:
                    pass
    return snap


def rediscover():
    """
    Re-run AioOCR's own plugin discovery so engines added or repaired while the gateway runs are picked up
    without a restart (e.g. after ``pip install`` of a missing dependency, or a new module dropped in
    ``AioOCR/engines/api`` or ``local``).

    :return: dict[str, type]  the refreshed ``AioOCR.AVAILABLE_PLUGINS``
    """
    from importlib import import_module, invalidate_caches, reload
    invalidate_caches()
    # forget modules that failed before so their import is attempted again
    for name in list(modules):
        if name.startswith(('engines.api.', 'engines.local.', 'AioOCR.engines.api.', 'AioOCR.engines.local.')):
            if modules[name] is None:
                del modules[name]
    for sub in enginePackages():
        pkg = modules.get('engines.{}'.format(sub))
        if pkg is not None:
            try:
                reload(pkg)
            except Exception:  # noqa: BLE001
                pass
    discover = getattr(AioOCR, '_discoverOcrPlugins', None)
    if discover is not None:
        _captureWarnings(discover)
    else:  # pragma: no cover - very old library layout
        import_module('AioOCR')
    return AioOCR.AVAILABLE_PLUGINS


def enginePackages():
    """
    :return: list[str | unicode] the sub-packages AioOCR scans for plugins.
    """
    return ['api', 'local']


def pluginNames():
    """
    :return: list[str | unicode] class names discovered by AioOCR itself.
    """
    return sorted(AVAILABLE_PLUGINS.keys())


__all__ = ['AioOCR', 'AVAILABLE_PLUGINS', 'AIOOCR_ROOT', 'ENGINES_ROOT', 'OCRError', 'OCRPlugin', 'OcrBase',
           'enginePackages', 'enginesSnapshot', 'is_url', 'pluginNames', 'rediscover']
