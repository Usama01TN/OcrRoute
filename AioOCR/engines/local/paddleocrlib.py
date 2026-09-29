# coding=utf-8
"""
PaddleOCR plugin.
Works with both PaddleOCR generations:
- paddleocr 2.x: ``PaddleOCR(...).ocr(img)`` returning ``[[bbox, (text, confidence)], ...]`` per image.
- paddleocr 3.x: ``PaddleOCR(...).predict(img)`` returning result
  objects with ``rec_texts`` / ``rec_scores`` / ``rec_polys`` (or ``dt_polys``) fields.
"""
from paddleocr import PaddleOCR as _PaddleEngine
from os.path import dirname
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

#: Map common ISO codes to PaddleOCR language identifiers.
_LANG_MAP = {
    'en': 'en', 'fr': 'fr', 'zh': 'ch', 'ch': 'ch', 'de': 'german',
    'es': 'es', 'it': 'it', 'pt': 'pt', 'nl': 'nl', 'ru': 'ru',
    'ar': 'ar', 'ja': 'japan', 'ko': 'korean', 'hi': 'hi',
}



try:
    from .. import languages as _languages
except (ImportError, ValueError):
    from engines import languages as _languages  # type: ignore[no-redef]

#: PaddleOCR 3 language codes (paddleocr/_pipelines/ocr.py, 3.3.0)
_PADDLE_CODES = ['ch', 'en', 'korean', 'japan', 'chinese_cht', 'te', 'ka', 'ta', 'af', 'az', 'bs', 'cs', 'cy', 'da', 'de', 'es', 'et', 'fr', 'ga', 'hr', 'hu', 'id', 'is', 'it', 'ku', 'la', 'lt', 'lv', 'mi', 'ms', 'mt', 'nl', 'no', 'oc', 'pi', 'pl', 'pt', 'ro', 'rs_latin', 'sk', 'sl', 'sq', 'sv', 'sw', 'tl', 'tr', 'uz', 'vi', 'fi', 'eu', 'gl', 'lb', 'rm', 'ca', 'qu', 'ar', 'fa', 'ug', 'ur', 'ps', 'ku', 'sd', 'bal', 'ru', 'rs_cyrillic', 'be', 'bg', 'uk', 'mn', 'abq', 'ady', 'kbd', 'ava', 'dar', 'inh', 'che', 'lbe', 'lez', 'tab', 'kk', 'ky', 'tg', 'mk', 'tt', 'cv', 'ba', 'mhr', 'mo', 'udm', 'kv', 'os', 'bua', 'xal', 'tyv', 'sah', 'kaa', 'hi', 'mr', 'ne', 'bh', 'mai', 'ang', 'bho', 'mah', 'sck', 'new', 'gom', 'sa', 'bgc']
_PADDLE = _languages.fromEngineCodes(_PADDLE_CODES)


class PaddleOcr(OCRPlugin):
    """
    PaddleOcr class.
    """

    @classmethod
    def getLanguages(cls, engine=None):
        """:return: list[str]  PaddleOCR's languages as canonical codes (one per request, no auto-detection)"""
        return sorted(_PADDLE)

    @classmethod
    def toEngineLanguage(cls, code, engine=None):
        return _PADDLE.get(code, code)

    #: Engine cache shared by all instances: model loading/downloading
    #: is expensive, so reuse one engine per (language, options) combo.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param language: document language(s); PaddleOCR uses ONE recognition language at a time,
                            so the first entry is used (default 'FR' to match EasyOCR).
        :param image: image source (path, URL, PIL, bytes, ndarray...).
        :param useAngleCls: enable text-angle classification (default True; helps rotated text).
        :param minConfidence: drop words below this score 0..1 (default 0.0).
        :param engineOptions: dict of extra kwargs forwarded to the PaddleOCR constructor (e.g. use_gpu).
        :param kwargs: other settings.
        """
        self.__m_useAngleCls = bool(kwargs.pop('useAngleCls', True))
        self.__m_minConfidence = float(kwargs.pop('minConfidence', 0.0))
        self.__m_engineOptions = dict(kwargs.pop('engineOptions', {}))
        kwargs.setdefault('language', ['fr'])
        super(PaddleOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)

    # ------------------------------------------------------------------ #
    # Engine management                                                  #
    # ------------------------------------------------------------------ #
    def _paddleLang(self):
        """
        PaddleOCR takes one language per pipeline: the first requested one, in PaddleOCR's own code
        ('zh' -> 'ch', 'ja' -> 'japan', 'zh-Hant' -> 'chinese_cht'...).
        """
        return self.getEngineLanguages()[0]

    def _engine(self):
        """
        Return a cached PaddleOCR engine for the current settings.
        """
        key = self._paddleLang(), self.__m_useAngleCls, tuple(sorted(self.__m_engineOptions.items()))
        if key not in PaddleOcr._engines:
            options = dict(self.__m_engineOptions)
            try:
                # paddleocr 2.x signature.
                PaddleOcr._engines[key] = _PaddleEngine(
                    lang=self._paddleLang(), use_angle_cls=self.__m_useAngleCls, show_log=False, **options)
            except (TypeError, ValueError):
                # paddleocr 3.x renamed/removed several options.
                options.pop('show_log', None)
                PaddleOcr._engines[key] = _PaddleEngine(
                    lang=self._paddleLang(), use_textline_orientation=self.__m_useAngleCls, **options)
        return PaddleOcr._engines[key]

    # ------------------------------------------------------------------ #
    # Input normalization                                                #
    # ------------------------------------------------------------------ #
    def _prepareInput(self):
        """
        Return something PaddleOCR accepts natively: a filesystem path,
        a URL string (3.x supports URLs), or an RGB numpy array.
        """
        kind = self.imageKind()
        if kind in ('path', 'url', 'array'):
            return self.getImage()
        if kind in ('pil', 'bytes', 'buffer'):
            from PIL.Image import open
            from numpy import array
            from io import BytesIO
            image = open(BytesIO(self.imageBytes())).convert('RGB')
            # PaddleOCR expects BGR ordering (OpenCV convention).
            return array(image)[:, :, ::-1]
        raise OCRError('Unsupported image source: {}'.format(kind))

    # ------------------------------------------------------------------ #
    # Result extraction (both API generations)                           #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _boxToRect(box):
        """
        Convert a 4-point polygon into (left, top, width, height).
        """
        xs = [float(pt[0]) for pt in box]
        ys = [float(pt[1]) for pt in box]
        left, top = min(xs), min(ys)
        return left, top, max(xs) - left, max(ys) - top

    def _extractV2(self, page):
        """
        Yield (text, confidence, rect) from a 2.x result page.
        """
        for entry in page or []:
            box, (text, confidence) = entry[0], entry[1]
            yield text, float(confidence), self._boxToRect(box)

    def _extractV3(self, page):
        """
        Yield (text, confidence, rect) from a 3.x result object.
        """

        # Result objects behave like dicts; fall back to attributes.
        def field(name):
            """
            :param name: str | unicode
            :return: object
            """
            try:
                return page[name]
            except (TypeError, KeyError):
                return getattr(page, name, None)

        texts = field('rec_texts') or []
        scores = field('rec_scores') or []
        polys = field('rec_polys')
        if polys is None:
            polys = field('dt_polys') or []
        for i, text in enumerate(texts):
            confidence = float(scores[i]) if i < len(scores) else 1.0
            if i < len(polys):
                rect = self._boxToRect(polys[i])
            else:
                rect = (0.0, float(i), 1.0, 1.0)  # keep reading order
            yield text, confidence, rect

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
                 PaddleOCR detects text *lines*; the base class groups
                 them back into lines by geometry, which also merges
                 fragments Paddle split on the same baseline.
        """
        engine = self._engine()
        source = self._prepareInput()
        if hasattr(engine, 'predict'):
            # paddleocr 3.x
            pages = engine.predict(source)
            extractor = self._extractV3
        else:
            # paddleocr 2.x: cls kwarg enables angle classification.
            pages = engine.ocr(source, cls=self.__m_useAngleCls)
            extractor = self._extractV2
        words = []
        for page in pages or []:
            for text, confidence, (left, top, width, height) in extractor(page):
                text = (text or '').strip()
                if not text or confidence < self.__m_minConfidence:
                    continue
                words.append(self.makeWord(text, left, top, width, height))
        return words
