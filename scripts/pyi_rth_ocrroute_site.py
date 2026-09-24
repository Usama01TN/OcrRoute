# coding=utf-8
"""
PyInstaller runtime hook: make ``site`` describe the bundle (runs before any application code).

PaddlePaddle locates its native libraries (``paddle/libs``: MKL, oneDNN, OpenBLAS...) only by searching
``site.getsitepackages()`` and ``site.USER_SITE``. In a frozen app those point at the build machine's Python, so
Paddle never registers its library directory and later fails to dlopen ``libmklml_intel.so`` by bare name. Paddle
3.0 (the last Intel-Mac build) also joins ``site.USER_SITE`` without checking it, and PyInstaller disables the user
site (``None``), so ``import paddle`` crashes with ``TypeError: sequence item 0: expected str instance``.

Paddle's native loader looks MKL up through the ``FLAGS_mklml_dir`` setting (read from the environment at start-up),
so the bundled ``paddle/libs`` is also exported there; an explicit user setting always wins.
"""
import os
import site
import sys

_base = getattr(sys, '_MEIPASS', None)
if _base:
    _original = getattr(site, 'getsitepackages', None)

    def getsitepackages(prefixes=None):  # the bundle first, then whatever Python reports
        dirs = []
        if _original is not None:
            try:
                dirs = list(_original(prefixes)) if prefixes is not None else list(_original())
            except Exception:  # noqa: BLE001
                dirs = []
        return [_base] + [d for d in dirs if os.path.normcase(d) != os.path.normcase(_base)]

    site.getsitepackages = getsitepackages
    if not getattr(site, 'USER_SITE', None):
        site.USER_SITE = _base

    _paddleLibs = os.path.join(_base, 'paddle', 'libs')
    if os.path.isdir(_paddleLibs):
        os.environ.setdefault('FLAGS_mklml_dir', _paddleLibs)
