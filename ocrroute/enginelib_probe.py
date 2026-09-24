# coding=utf-8
"""
Child-process probe for crash-isolated engine discovery.

Imports every AioOCR engine module one by one and reports progress on stdout (``START name`` / ``END name``).
A module whose compiled dependency crashes the interpreter (segfault, illegal instruction, abort) kills this
child, never the gateway: the parent sees which module was started but not finished and blocks it.
This module deliberately does **not** import ``ocrroute.enginelib`` (that is what triggers a probe).
"""
from __future__ import absolute_import, division, print_function

import faulthandler
import os
import sys
from os.path import abspath, dirname, exists, join
from pkgutil import iter_modules

SKIP_ENV = 'OCRROUTE_PROBE_SKIP'
CRASH_ENV = 'OCRROUTE_PROBE_CRASH'  # test hook: abort when reaching this module, to exercise the parent's recovery


def aioocrRoot():
    """
    :return: str  the AioOCR folder (source tree, installed package, or PyInstaller bundle)
    """
    here = abspath(join(dirname(dirname(__file__)), 'AioOCR'))
    if exists(here):
        return here
    base = getattr(sys, '_MEIPASS', '')
    if base and exists(join(base, 'AioOCR')):
        return join(base, 'AioOCR')
    return here


def moduleNames(root):
    """
    :param root: str
    :return: list[str]  ``engines.api.x`` / ``engines.local.y`` in AioOCR's own discovery order
    """
    names = []
    for sub in ('api', 'local'):
        folder = join(root, 'engines', sub)
        if exists(folder):
            names += ['engines.{}.{}'.format(sub, m) for _, m, isPkg in iter_modules([folder]) if not isPkg and not m.startswith('_')]
    return names


def main():
    faulthandler.enable(file=sys.stderr, all_threads=True)
    root = aioocrRoot()
    if root not in sys.path:
        sys.path.insert(0, root)
    skip = set(filter(None, os.environ.get(SKIP_ENV, '').split(',')))
    crash = os.environ.get(CRASH_ENV, '')
    out = sys.stdout
    import engines.ocrplugin  # noqa: F401  (shared base class, as AioOCR expects)

    for name in moduleNames(root):
        if name in skip:
            continue
        out.write('START {}\n'.format(name))
        out.flush()
        if crash and name == crash:
            os.abort()  # simulated native crash (tests only)
        try:
            __import__(name)
        except BaseException:  # noqa: BLE001 - Python-level failures are AioOCR's business, not a crash
            pass
        out.write('END {}\n'.format(name))
        out.flush()
    out.write('DONE\n')
    out.flush()


if __name__ == '__main__':
    main()
