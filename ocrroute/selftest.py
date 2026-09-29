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


def _paddleCompute():
    """Run Paddle's own installation check: a real CPU computation that loads its native math libraries (MKL,
    oneDNN), which an import alone does not."""
    import paddle

    paddle.utils.run_check()


def _torchCompute():
    import torch

    a = torch.ones(64, 64)
    assert float((a @ a).sum()) == 64.0 ** 3


def _torchvisionOps():
    import torch
    import torchvision

    boxes = torch.tensor([[0.0, 0.0, 10.0, 10.0], [1.0, 1.0, 11.0, 11.0]])
    keep = torchvision.ops.nms(boxes, torch.tensor([0.9, 0.8]), 0.5)  # a compiled torchvision operator
    assert keep.tolist() == [0]


def _paddleOcrRequirements():
    """PaddleX refuses to build its OCR pipeline unless its `ocr` (or `ocr-core`) extra is satisfied, judged by
    distribution *metadata*; a missing .dist-info in the bundle only shows up at the first OCR request."""
    from paddlex.utils.deps import EXTRAS, is_dep_available, is_extra_available

    names = [e for e in ('ocr-core', 'ocr') if e in EXTRAS]
    if not any(is_extra_available(e) for e in names):
        missing = sorted(set(d for e in names for d in EXTRAS[e] if not is_dep_available(d)))
        raise RuntimeError('PaddleX OCR requirements not met in the bundle; missing metadata: {}'.format(missing))


def _paddleModelSources():
    """PaddleX 3.0.x (Intel Macs) must download every OCR-pipeline model from Hugging Face: Paddle's own server
    answers 403 for its paddle3.0.0 exports. Verifies OcrRoute's import-time patch took effect inside the bundle."""
    from importlib.metadata import version

    import paddlex.inference.utils.official_models as om

    if not version('paddlex').startswith('3.0.'):
        return
    from ocrroute import paddleenv

    missing = [m for m in paddleenv.HF_MODELS_MISSING_IN_PADDLEX_30 if m not in om.HUGGINGFACE_MODELS]
    if missing or not getattr(om, '_ocrroutePatched', False):
        raise RuntimeError('PaddleX 3.0 model sources not patched (would use the dead BOS server for {})'.format(missing))


CHECKS = {'paddleocr:model-sources': _paddleModelSources, 'paddleocr:requirements': _paddleOcrRequirements, 'paddle:compute': _paddleCompute, 'torch:compute': _torchCompute, 'torchvision:ops': _torchvisionOps}


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
            if name in CHECKS:
                CHECKS[name]()
            else:
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
