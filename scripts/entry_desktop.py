# coding=utf-8
"""PyInstaller entry point: OcrRoute Desktop (PyQt5) with the embedded server."""
from __future__ import absolute_import, division, print_function

import multiprocessing
import os
import sys

if getattr(sys, 'frozen', False):
    base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    sys.path.insert(0, base)
    sys.path.insert(0, os.path.join(base, 'AioOCR'))

from ocrroute.stdio import enableFaultHandler, ensureStreams  # noqa: E402

ensureStreams('desktop')  # windowed builds start without stdout/stderr (sys.stderr is None)
enableFaultHandler()  # a native crash prints the Python stack instead of nothing

if len(sys.argv) > 1 and sys.argv[1] == '--ocrroute-probe-engines':  # child of crash-isolated discovery
    from ocrroute.enginelib_probe import main as _probe  # noqa: E402

    _probe()
    raise SystemExit(0)

from ocrroute.desktop.app import main  # noqa: E402

if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
