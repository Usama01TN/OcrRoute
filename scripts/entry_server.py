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

import faulthandler  # noqa: E402

faulthandler.enable(all_threads=True)  # a native crash prints the Python stack (which module) instead of nothing

if len(sys.argv) > 1 and sys.argv[1] == '--ocrroute-probe-engines':  # child of crash-isolated discovery
    from ocrroute.enginelib_probe import main as _probe  # noqa: E402

    _probe()
    raise SystemExit(0)

from ocrroute.cli.main import app  # noqa: E402

if __name__ == '__main__':
    multiprocessing.freeze_support()
    if len(sys.argv) == 1:
        sys.argv.append('serve')  # double-click on the binary starts the server + panel
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    try:
        app()
    except OSError as exc:
        import errno

        if exc.errno in (errno.EPIPE, errno.EINVAL):  # reader closed the pipe (EINVAL is how Windows says it)
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except OSError:
                pass
            raise SystemExit(0)
        raise
