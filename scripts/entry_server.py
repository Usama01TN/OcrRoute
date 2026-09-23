# coding=utf-8
"""PyInstaller entry point: the `ocrroute` CLI (serve / setup / ocr / …) as a stand-alone executable."""
from __future__ import absolute_import, division, print_function

import multiprocessing
import os
import sys

if getattr(sys, 'frozen', False):
    base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    sys.path.insert(0, base)
    sys.path.insert(0, os.path.join(base, 'AioOCR'))

from ocrroute.cli.main import app  # noqa: E402

if __name__ == '__main__':
    multiprocessing.freeze_support()
    if len(sys.argv) == 1:
        sys.argv.append('serve')  # double-click on the binary starts the server + panel
    app()
