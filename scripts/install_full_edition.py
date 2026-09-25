# coding=utf-8
"""
Install the dependencies of the OcrRoute *Full* edition (EasyOCR + PaddleOCR) into the current environment.

    python scripts/install_full_edition.py            # what CI runs before `build_executable.py --edition full`
    python scripts/install_full_edition.py --no-easyocr

Why a script: the two engines need care to coexist with the rest of OcrRoute.
- PyTorch (EasyOCR): Linux uses the CPU-only index (the PyPI Linux wheel pulls ~2.5 GB of CUDA libraries);
  macOS x86_64 has no PyTorch build newer than 2.2 (NumPy 1 only), so EasyOCR is skipped there.
- PaddleX (PaddleOCR 3) caps NumPy below 2.4 and pins OpenCV 4.10.0.84 (contrib). Exactly one OpenCV distribution
  must remain, and it must be *headless* (GUI builds ship Qt libraries that break PyQt5 on Linux). OcrRoute's
  ``ocrroute.opencv_alias`` makes the headless contrib build satisfy PaddleX's distribution-name check.
"""
from __future__ import absolute_import, division, print_function

import argparse
import os
import platform
import subprocess
import sys

OPENCV = 'opencv-contrib-python-headless==4.10.0.84'
# Surya 0.17.1: the last release that runs OCR entirely in PyTorch (Surya 2, 0.20+, needs a vLLM or llama.cpp
# server). Installed without pip's resolver: its exact pins (opencv-python-headless 4.11, pypdfium2 4.30, pillow<11,
# pre-commit) would replace the single OpenCV PaddleX needs and OcrRoute's pypdfium2; it only imports cv2 and never
# checks those versions. These are the dependencies it actually uses.
SURYA = 'surya-ocr==0.17.1'
SURYA_DEPS = ('transformers>=4.56.1,<5', 'einops>=0.8.1,<0.9', 'filetype>=1.2,<2', 'platformdirs>=4.3.6,<5',
              'pydantic>=2.5.3,<3', 'pydantic-settings>=2.1,<3', 'python-dotenv>=1,<2', 'click>=8.1.8,<9')
NUMPY = 'numpy>=1.24,<2.4'


def pip(*args):
    cmd = [sys.executable, '-m', 'pip'] + list(args)
    print('+', ' '.join(cmd), flush=True)
    subprocess.check_call(cmd)


def easyocrSupported():
    """
    :return: bool  False on macOS x86_64 (no PyTorch >= 2.3 wheels for Intel Macs)
    """
    return not (sys.platform == 'darwin' and platform.machine().lower() in ('x86_64', 'amd64'))


def legacyPaddle():
    """
    :return: bool  True where PaddlePaddle stops at 3.0.0 (macOS x86_64). The newest PaddleOCR there downloads models
             exported for Paddle >= 3.3 ("Type of attribute: strides is not right" when loading them), so the
             releases made for Paddle 3.0 are pinned: paddlex 3.0.3 + paddleocr 3.0.3 (PP-OCRv5 models).
    """
    return sys.platform == 'darwin' and platform.machine().lower() in ('x86_64', 'amd64')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--no-easyocr', action='store_true', help='PaddleOCR only')
    ap.add_argument('--no-paddle', action='store_true', help='EasyOCR only')
    ap.add_argument('--no-surya', action='store_true', help='skip Surya')
    ap.add_argument('--legacy-paddle', action='store_true', help='force the Paddle 3.0 pins (testing on other platforms)')
    args = ap.parse_args()
    withEasy = easyocrSupported() and not args.no_easyocr
    withPaddle = not args.no_paddle
    withSurya = easyocrSupported() and not args.no_surya  # needs PyTorch >= 2.7: not on Intel Macs
    if withEasy:
        if sys.platform.startswith('linux'):
            pip('install', '--prefer-binary', 'torch', 'torchvision', '--index-url', 'https://download.pytorch.org/whl/cpu')
        else:
            pip('install', '--prefer-binary', 'torch', 'torchvision')
        pip('install', '--prefer-binary', 'easyocr>=1.7', NUMPY)
    if withSurya:
        if not withEasy:  # PyTorch comes with the EasyOCR step; install it here when EasyOCR is skipped
            if sys.platform.startswith('linux'):
                pip('install', '--prefer-binary', 'torch', 'torchvision', '--index-url', 'https://download.pytorch.org/whl/cpu')
            else:
                pip('install', '--prefer-binary', 'torch', 'torchvision')
        pip('install', '--no-deps', SURYA)
        pip('install', '--prefer-binary', *(SURYA_DEPS + (NUMPY,)))
    if withPaddle and (legacyPaddle() or args.legacy_paddle):
        pip('install', '--prefer-binary', 'paddlepaddle==3.0.0', 'paddlex[ocr]==3.0.3', NUMPY)
        # paddleocr 3.0.3 asks for paddlex[ie,multimodal,ocr]>=3.0.3: no upper bound (pip would fetch the newest
        # PaddleX and its v6 models) and two extras that pull large unrelated stacks. Install it without deps.
        pip('install', '--no-deps', 'paddleocr==3.0.3')
        pip('install', 'PyYAML>=6', 'typing-extensions>=4.12', 'setuptools')  # Paddle 3.0 imports it undeclared
    elif withPaddle:
        pip('install', '--prefer-binary', 'paddlepaddle>=3.0', 'paddleocr>=3.0', NUMPY)
    # exactly one OpenCV, headless, at the version PaddleX pins
    pip('uninstall', '-y', 'opencv-python', 'opencv-python-headless', 'opencv-contrib-python', 'opencv-contrib-python-headless')
    pip('install', '--prefer-binary', OPENCV, NUMPY)
    probe = ['import sys; sys.path.insert(0, {!r}); from ocrroute import paddleenv; paddleenv.apply()'.format(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
             'import cv2, numpy; print("cv2", cv2.__version__, "numpy", numpy.__version__)']
    if withEasy:
        probe.append('import easyocr, torch; print("easyocr", easyocr.__version__, "torch", torch.__version__)')
    if withPaddle:
        probe.append('import paddle, paddleocr; print("paddle", paddle.__version__)')
    if withSurya:
        probe.append('import surya, transformers; from surya.recognition import RecognitionPredictor; '
                     'from surya.detection import DetectionPredictor; print("surya ok, transformers", transformers.__version__)')
    # The check imports PaddleX in a fresh interpreter, so it needs OcrRoute's Paddle settings too: PaddleX 3.0
    # downloads a font at import time from a URL that now answers 403 everywhere.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    from ocrroute import paddleenv

    env = dict(os.environ)
    print('Paddle settings for the check:', paddleenv.apply(env), flush=True)
    subprocess.check_call([sys.executable, '-c', '; '.join(probe)], env=env)
    with open('full-edition.txt', 'w') as fh:
        fh.write('easyocr={} paddleocr={} surya={}\n'.format(int(withEasy), int(withPaddle), int(withSurya)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
