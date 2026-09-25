# coding=utf-8
"""
EasyOCR plugin.
"""
from os.path import dirname
from easyocr import Reader
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin
except:
    from engines.ocrplugin import OCRPlugin


class EasyOCR(OCRPlugin):
    """
    EasyOCR class.
    """

    #: Reader cache shared by all instances: model loading is expensive,
    #: so reuse one Reader per language combination.
    _readers = {}

    def __init__(self, *args, **kwargs):
        """
        :param language: document language(s), default French.
        :param image: image source.
        :param gpu: bool, use GPU if available (default True).
        :param kwargs: other settings.
        """
        self.__m_gpu = kwargs.pop('gpu', True)
        kwargs.setdefault('language', ['fr'])
        super(EasyOCR, self).__init__(*args, **kwargs)
        self.setOnline(False)

    def _reader(self):
        """
        Return a cached Reader for the current language set.
        """
        langs = self.getLanguage()
        if isinstance(langs, str):
            langs = [langs]
        key = (tuple(sorted(langs)), self.__m_gpu)
        if key not in EasyOCR._readers:
            EasyOCR._readers[key] = Reader(list(langs), gpu=self.__m_gpu)
        return EasyOCR._readers[key]

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        kind = self.imageKind()
        if kind in ('pil', 'buffer'):
            image = self.imageBytes()  # readtext accepts raw bytes
        # 'path', 'url', 'bytes' and numpy arrays go through unchanged:
        # easyocr handles all of them natively.
        results = self._reader().readtext(image)
        words = []
        for bbox, text, confidence in results:
            xs = [pt[0] for pt in bbox]
            ys = [pt[1] for pt in bbox]
            left, top = min(xs), min(ys)
            words.append(self.makeWord(text, left, top, max(xs) - left, max(ys) - top))
        return words
