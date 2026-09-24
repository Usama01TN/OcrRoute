# coding=utf-8
"""
Environment defaults for PaddlePaddle / PaddleX, shared by the gateway and the executable builder.

Applied before PaddleX is imported (it reads these variables at import time). Explicit user settings always win.

- ``PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=False``: Paddle 3.3's oneDNN CPU executor rejects some PaddleOCR models
  ("ConvertPirAttribute2RuntimeAttribute not support").
- ``PADDLE_PDX_MODEL_SOURCE=BOS`` with Paddle 3.0 (the last build for Intel Macs): Hugging Face serves only the latest
  model exports, which Paddle 3.0 cannot load ("Type of attribute: strides is not right"); BOS serves the exports
  made for Paddle 3.0.
- ``PADDLE_PDX_LOCAL_FONT_FILE_PATH``: PaddleX 3.0 downloads two fonts *at import time* (used only to draw
  visualisations). Offline, that made the engine unavailable; during a build it made PyInstaller's module scan of
  PaddleX fail, so PaddleX's own imports (``colorlog``...) were never bundled. A system font is used instead.
"""
from __future__ import absolute_import, division, print_function

import os

SYSTEM_FONTS = (
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf', '/System/Library/Fonts/Supplemental/Arial.ttf',
    '/Library/Fonts/Arial Unicode.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf', 'C:\\Windows\\Fonts\\arial.ttf',
)


def paddleVersion():
    """
    :return: tuple[int, int] | None  installed PaddlePaddle version, from metadata (paddle is not imported)
    """
    try:
        from importlib.metadata import version

        major, minor = version('paddlepaddle').split('.')[:2]
        return int(major), int(minor)
    except Exception:  # noqa: BLE001 - not installed / unexpected version string
        return None


def systemFont():
    """
    :return: str | None  a TrueType font the operating system ships
    """
    for font in SYSTEM_FONTS:
        if os.path.isfile(font):
            return font
    return None


def apply(environ=None):
    """
    Set the defaults in ``environ`` (``os.environ`` by default) without overriding explicit values.

    :return: dict  the variables that were set
    """
    env = os.environ if environ is None else environ
    wanted = {'PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT': 'False'}
    version = paddleVersion()
    if version is not None and version < (3, 1):
        wanted['PADDLE_PDX_MODEL_SOURCE'] = 'BOS'
    font = systemFont()
    if font:
        wanted['PADDLE_PDX_LOCAL_FONT_FILE_PATH'] = font
    applied = {}
    for key, value in wanted.items():
        if not env.get(key):
            env[key] = value
            applied[key] = value
    return applied


__all__ = ['SYSTEM_FONTS', 'apply', 'paddleVersion', 'systemFont']
