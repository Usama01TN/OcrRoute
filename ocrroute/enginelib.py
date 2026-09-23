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

import engines  # noqa: E402  (AioOCR/engines, as the library expects)
import engines.api  # noqa: E402
import engines.local  # noqa: E402
import engines.ocrplugin  # noqa: E402

modules.setdefault('AioOCR.engines', engines)
modules.setdefault('AioOCR.engines.api', engines.api)
modules.setdefault('AioOCR.engines.local', engines.local)
modules.setdefault('AioOCR.engines.ocrplugin', engines.ocrplugin)

import sys as _sys  # noqa: E402
from contextlib import redirect_stdout as _redirectStdout  # noqa: E402

with _redirectStdout(_sys.stderr):  # AioOCR prints import warnings with print(); keep stdout clean for --json
    import AioOCR  # noqa: E402  (runs the library's own plugin discovery)
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
        with _redirectStdout(_sys.stderr):
            discover()
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
