# coding=utf-8
"""
Environment defaults for PaddlePaddle / PaddleX, shared by the gateway and the executable builder.

Applied before PaddleX is imported (it reads these variables at import time). Explicit user settings always win.

- ``PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=False``: Paddle 3.3's oneDNN CPU executor rejects some PaddleOCR models
  ("ConvertPirAttribute2RuntimeAttribute not support").
- Paddle 3.0 (the last build for Intel Macs) with PaddleX 3.0.3: Paddle's own model server (BOS) now answers 403 for
  the ``paddle3.0.0`` exports on every network, so models come from Hugging Face (PaddleX's default source), whose
  PP-OCRv5 and auxiliary models were published for Paddle 3.0. Two PaddleX 3.0.3 shortcomings are patched when it is
  imported (``installPaddleXPatches``): ``PP-LCNet_x1_0_textline_ori`` is missing from its Hugging Face list (so it
  fell back to the dead server), and its "is Hugging Face reachable?" probe gives up after 1 second.
- ``PADDLE_PDX_LOCAL_FONT_FILE_PATH``: PaddleX 3.0 downloads two fonts *at import time* (used only to draw
  visualisations), and Paddle's server now answers 403 for that URL on every network, so ``import paddleocr``
  failed. OcrRoute ships DejaVu Sans (``ocrroute/assets/fonts``, Bitstream Vera license) and points PaddleX at it;
  system fonts are only a fallback, so the font never depends on the operating system.
"""
from __future__ import absolute_import, division, print_function

import os

BUNDLED_FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'fonts', 'DejaVuSans.ttf')
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
    :return: str | None  the bundled DejaVu Sans, else a TrueType font the operating system ships
    """
    for font in (BUNDLED_FONT,) + SYSTEM_FONTS:
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
        wanted['PADDLE_PDX_MODEL_SOURCE'] = 'huggingface'  # BOS answers 403 for the paddle3.0.0 exports
    font = systemFont()
    if font:
        wanted['PADDLE_PDX_LOCAL_FONT_FILE_PATH'] = font
    if environ is None or environ is os.environ:
        installPaddleXPatches()
    applied = {}
    for key, value in wanted.items():
        if not env.get(key):
            env[key] = value
            applied[key] = value
    return applied


# Models the PaddleOCR pipeline uses that PaddleX 3.0.3 omits from its Hugging Face list (all present on the Hub).
HF_MODELS_MISSING_IN_PADDLEX_30 = ('PP-LCNet_x1_0_textline_ori', 'PP-LCNet_x0_25_textline_ori', 'PP-OCRv5_mobile_det',
                                   'PP-OCRv5_mobile_rec', 'PP-OCRv5_server_det', 'PP-OCRv5_server_rec',
                                   'PP-LCNet_x1_0_doc_ori', 'UVDoc')
OFFICIAL_MODELS = 'paddlex.inference.utils.official_models'


def huggingFaceReachable(timeout=10.0):
    """
    :return: bool  any HTTP answer from huggingface.co counts (PaddleX 3.0.3 required HTTP 200 within 1 second)
    """
    try:
        import requests

        requests.head('https://huggingface.co', timeout=timeout, allow_redirects=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def patchOfficialModels(module):
    """
    Patch PaddleX 3.0.x ``official_models`` in place (no-op for other versions).

    :param module: the imported ``paddlex.inference.utils.official_models`` module
    :return: bool  True when patched
    """
    models = getattr(module, 'HUGGINGFACE_MODELS', None)
    if models is None or getattr(module, '_ocrroutePatched', False):
        return False
    try:
        from importlib.metadata import version

        if not version('paddlex').startswith('3.0.'):
            return False
    except Exception:  # noqa: BLE001
        return False
    for name in HF_MODELS_MISSING_IN_PADDLEX_30:
        if name not in models:
            models.append(name)
    module.is_huggingface_accessible = huggingFaceReachable
    module._ocrroutePatched = True
    return True


class _PatchOnImport(object):
    """Meta-path finder that runs ``patchOfficialModels`` right after PaddleX's module is executed."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname != OFFICIAL_MODELS:
            return None
        import sys
        from importlib.util import find_spec as _find

        sys.meta_path.remove(self)  # resolve with the regular finders (PyInstaller's included)
        try:
            spec = _find(fullname)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or not hasattr(spec.loader, 'exec_module'):
            return spec
        inner = spec.loader  # keep the real loader: the wrapper below replaces spec.loader

        class _Loader(object):
            def __getattr__(self, name):
                return getattr(inner, name)

            def create_module(self, s):
                return inner.create_module(s)

            def exec_module(self, module):
                inner.exec_module(module)
                patchOfficialModels(module)

        spec.loader = _Loader()
        return spec


def installPaddleXPatches():
    """
    Patch PaddleX 3.0.x's model sources whenever it is imported (or right away if it already is).

    :return: bool  True when the hook was installed or the module patched
    """
    import sys

    if OFFICIAL_MODELS in sys.modules:
        return patchOfficialModels(sys.modules[OFFICIAL_MODELS])
    if not any(isinstance(f, _PatchOnImport) for f in sys.meta_path):
        sys.meta_path.insert(0, _PatchOnImport())
    return True


__all__ = ['HF_MODELS_MISSING_IN_PADDLEX_30', 'installPaddleXPatches', 'patchOfficialModels', 'BUNDLED_FONT', 'SYSTEM_FONTS', 'apply', 'paddleVersion', 'systemFont']
