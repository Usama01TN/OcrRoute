# coding=utf-8
"""
Package-metadata aliases for OpenCV.

OpenCV is published as four distributions that all install the same ``cv2`` module: ``opencv-python``,
``opencv-python-headless``, ``opencv-contrib-python`` and ``opencv-contrib-python-headless``. Some libraries check
for one exact *distribution name* through ``importlib.metadata``: PaddleX (PaddleOCR 3) refuses to build its OCR
pipeline unless ``opencv-contrib-python`` is installed, although the headless contrib build provides the identical
``cv2`` API.

The GUI (non-headless) builds bundle their own Qt libraries on Linux, which breaks PyQt5 in the same process, so
OcrRoute uses the headless builds. This finder answers metadata lookups for a missing GUI distribution name with an
installed headless equivalent (contrib names only map to contrib builds). Nothing is written to disk, and a
genuinely installed distribution always wins because this finder runs after the standard ones.
"""
from __future__ import absolute_import, division, print_function

import sys

try:
    from importlib.metadata import DistributionFinder, PackageNotFoundError, distribution
except ImportError:  # pragma: no cover - Python < 3.8
    DistributionFinder = object
    distribution = None
    PackageNotFoundError = Exception

# requested (missing) distribution -> installed equivalents, in order of preference
ALIASES = {
    'opencv-contrib-python': ('opencv-contrib-python-headless',),
    'opencv-python': ('opencv-python-headless', 'opencv-contrib-python-headless', 'opencv-contrib-python'),
    'opencv-python-headless': ('opencv-contrib-python-headless', 'opencv-python', 'opencv-contrib-python'),
}


def _norm(name):
    return (name or '').lower().replace('_', '-').replace('.', '-')


class OpenCvAliasFinder(DistributionFinder):
    """
    OpenCvAliasFinder class: resolves an OpenCV distribution name to an installed equivalent.
    """

    _busy = False  # guards against recursion while we look the real distributions up

    def find_spec(self, *args, **kwargs):  # not a module finder
        return None

    def find_distributions(self, context=None):
        """
        :param context: DistributionFinder.Context
        :return: iterator of Distribution
        """
        name = _norm(getattr(context, 'name', None))
        if not name or name not in ALIASES or OpenCvAliasFinder._busy:
            return iter(())
        OpenCvAliasFinder._busy = True
        try:
            try:
                distribution(name)
                return iter(())  # the real one is installed: never shadow it
            except PackageNotFoundError:
                pass
            for alt in ALIASES[name]:
                try:
                    return iter([distribution(alt)])
                except PackageNotFoundError:
                    continue
            return iter(())
        finally:
            OpenCvAliasFinder._busy = False


def install():
    """
    Register the finder once (appended, so real distributions are always found first).

    :return: bool  True when newly installed
    """
    if distribution is None or any(isinstance(f, OpenCvAliasFinder) for f in sys.meta_path):
        return False
    sys.meta_path.append(OpenCvAliasFinder())
    return True


__all__ = ['ALIASES', 'OpenCvAliasFinder', 'install']
