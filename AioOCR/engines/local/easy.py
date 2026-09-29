# coding=utf-8
"""
EasyOCR plugin.
"""
from os.path import dirname
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin
except:
    from engines.ocrplugin import OCRPlugin



try:
    from .. import languages as _languages
except (ImportError, ValueError):
    from engines import languages as _languages  # type: ignore[no-redef]

#: EasyOCR's own language codes (easyocr/config.py, 1.7.2)
_EASY_CODES = ['abq', 'ady', 'af', 'ang', 'ar', 'as', 'ava', 'az', 'be', 'bg', 'bgc', 'bh', 'bho', 'bn', 'bs', 'ch_sim', 'ch_tra', 'che', 'cs', 'cy', 'da', 'dar', 'de', 'en', 'es', 'et', 'fa', 'fr', 'ga', 'gom', 'hi', 'hr', 'hu', 'id', 'inh', 'is', 'it', 'ja', 'kbd', 'kn', 'ko', 'ku', 'la', 'lbe', 'lez', 'lt', 'lv', 'mah', 'mai', 'mi', 'mn', 'mni', 'mr', 'ms', 'mt', 'ne', 'new', 'nl', 'no', 'oc', 'pi', 'pl', 'pt', 'ro', 'rs_cyrillic', 'rs_latin', 'ru', 'sa', 'sck', 'sk', 'sl', 'sq', 'sv', 'sw', 'ta', 'tab', 'te', 'th', 'tjk', 'tl', 'tr', 'ug', 'uk', 'ur', 'uz', 'vi']
_EASY = _languages.fromEngineCodes(_EASY_CODES)


class EasyOCR(OCRPlugin):
    """
    EasyOCR class.
    """
    MULTI_LANGUAGE = True  # a list of languages, with EasyOCR's combination rules (easyocr/easyocr.py):
    #: each group may be combined within itself plus English; Latin-script languages (everything else) combine freely
    LANGUAGE_GROUPS = [
        ['zh'], ['zh-Hant'], ['ja'], ['ko'], ['th'], ['ta'], ['te'], ['kn'],
        ['bn', 'as', 'mni'],                                     # Bengali script
        ['ar', 'fa', 'ug', 'ur'],                                # Arabic script
        ['hi', 'mr', 'ne', 'bh', 'mai', 'ang', 'bho', 'mah', 'sck', 'new', 'gom', 'sa', 'bgc'],  # Devanagari
        ['ru', 'sr', 'be', 'bg', 'uk', 'mn', 'abq', 'ady', 'kbd', 'av', 'dar', 'inh', 'ce', 'lbe', 'lez', 'tab', 'tg'],  # Cyrillic
    ]

    @classmethod
    def getLanguages(cls, engine=None):
        """:return: list[str]  EasyOCR's languages as canonical codes (no auto-detection)"""
        return sorted(_EASY)

    @classmethod
    def toEngineLanguage(cls, code, engine=None):
        return _EASY.get(code, code)


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
        langs = self.getEngineLanguages()  # 'zh' -> 'ch_sim', 'auto' -> English...
        key = (tuple(sorted(langs)), self.__m_gpu)
        if key not in EasyOCR._readers:
            from easyocr import Reader

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
