# coding=utf-8
"""
Frozen-bundle self-test: import modules *inside* a built executable and report full tracebacks.

    ocrroute-server --ocrroute-selftest easyocr,paddleocr,engines.local.easy

A package can import fine in the build environment yet fail inside the bundle when PyInstaller left out a module
it could not see (for example ``six``, which EasyOCR imports through the virtual ``six.moves`` package without
declaring it). ``scripts/build_executable.py`` runs this after every Full build and fails the build on any error.
"""
from __future__ import absolute_import, division, print_function

import importlib
import sys
import traceback


def run(modules):
    """
    :param modules: list[str]
    :return: int  number of modules that failed to import
    """
    root = None
    try:  # AioOCR engine modules (engines.*) resolve like the gateway resolves them
        from ocrroute import enginelib  # noqa: F401

        root = enginelib.AIOOCR_ROOT
    except Exception:  # noqa: BLE001
        pass
    if root and root not in sys.path:
        sys.path.insert(0, root)
    import os

    os.environ.setdefault('TORCHVISION_WARN_WHEN_EXTENSION_LOADING_FAILS', '1')  # surface swallowed load errors
    failed = 0
    for name in modules:
        try:
            importlib.import_module(name)
            print('SELFTEST OK   {}'.format(name), flush=True)
        except BaseException:  # noqa: BLE001 - report everything, including SystemExit from broken packages
            failed += 1
            print('SELFTEST FAIL {}'.format(name), flush=True)
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
    return failed


def main(argv):
    """
    :param argv: list[str]  ``[--ocrroute-selftest, "mod1,mod2"]``
    """
    modules = [m for m in (argv[1] if len(argv) > 1 else '').split(',') if m]
    raise SystemExit(run(modules) if modules else 2)
