# coding=utf-8
"""
RapidOCR plugin (local, https://github.com/RapidAI/RapidOCR).
RapidOCR runs PaddleOCR's detection/classification/recognition models
through ONNX Runtime: fully LOCAL and free, no API key, no GPU
required, fast on CPU, with the models (~20 MB) downloaded
automatically on first use. Install::
    pip install rapidocr onnxruntime
(The plugin also supports the older ``rapidocr_onnxruntime`` package
transparently.)
Geometry is first-class: with ``wordBox=True`` (the default) RapidOCR
returns TRUE WORD-level pixel boxes -- verified on the demo login
screenshot to match OCR.Space almost pixel-for-pixel, including the
word split of "Shital Shah" -- putting this local engine in the same
geometry tier as the best hosted APIs. ``wordBox=False`` returns
line-level boxes instead (slightly faster). Per-word/line confidence
powers ``minConfidence``. Everything lands in the exact unified
structure shared by every plugin (like OCR.Space).
Language: the default PP-OCRv5/v6 models read Latin text, digits and
Chinese out of the box; other scripts are selected via engine params,
e.g. ``engineParams={'Rec.lang_type': 'ar'}`` (see the RapidOCR docs
for available packs).
"""
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

#: Vertical gap when stacking multiple results (not normally needed).
_MIN_BOX = 1.0


class RapidOcr(OCRPlugin):
    """
    RapidOcr class.
    """
    #: params-signature -> loaded engine (models load once per process).
    _engines = {}
    #: 'v2' (rapidocr) or 'v1' (rapidocr_onnxruntime), set on first load.
    _generation = None

    def __init__(self, *args, **kwargs):
        """
        :param wordBox: True (default) for WORD-level boxes via return_word_box; False for line-level boxes.
        :param minConfidence: drop words/lines below this recognition confidence, 0-1 (default 0).
        :param useDet: run text detection (default True).
        :param useCls: run angle classification (default True; handles rotated text).
        :param useRec: run recognition (default True).
        :param engineParams: dict passed to the RapidOCR constructor
                             (v2: ``params=...`` -- model choices, languages; v1: keyword arguments).
        :param quiet: silence RapidOCR's INFO logging (default True).
        :param image: image source (path, URL, PIL, bytes, array...).
        :param kwargs: other settings (retries...).
        """
        self.__m_word_box = bool(kwargs.pop('wordBox', True))
        self.__m_min_confidence = float(kwargs.pop('minConfidence', 0.0))
        self.__m_use_det = bool(kwargs.pop('useDet', True))
        self.__m_use_cls = bool(kwargs.pop('useCls', True))
        self.__m_use_rec = bool(kwargs.pop('useRec', True))
        self.__m_engine_params = dict(kwargs.pop('engineParams', {}))
        self.__m_quiet = bool(kwargs.pop('quiet', True))
        super(RapidOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        key = tuple(sorted(self.__m_engine_params.items()))
        if key in RapidOcr._engines:
            return RapidOcr._engines[key]
        if self.__m_quiet:
            from logging import getLogger, WARNING
            getLogger('RapidOCR').setLevel(WARNING)
        try:
            from rapidocr import RapidOCR as Engine
            RapidOcr._generation = 'v2'
            engine = Engine(params=self.__m_engine_params) if self.__m_engine_params else Engine()
        except ImportError:
            try:
                from rapidocr_onnxruntime import RapidOCR as Engine
            except ImportError:
                raise OCRError('RapidOCR is not installed. Run: pip install rapidocr onnxruntime')
            RapidOcr._generation = 'v1'
            engine = Engine(**self.__m_engine_params)
        RapidOcr._engines[key] = engine
        return engine

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _engineInput(self):
        """
        RapidOCR accepts path/bytes/ndarray/PIL natively.
        """
        kind = self.imageKind()
        if kind == 'path':
            return self.getImage()
        if kind == 'url':
            from requests import get
            reply = get(self.getImage(), timeout=self.getTimeout())
            reply.raise_for_status()
            data = reply.content
        elif kind in ('pil', 'array'):
            return self.getImage()
        elif kind in ('bytes', 'buffer'):
            data = self.imageBytes()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(self.getImage()))
        if data[:5] == b'%PDF-':
            raise OCRError(
                'RapidOCR takes images, not PDFs. Convert the page to '
                'an image first, or use a PDF-capable plugin (MistralOcr, ClaudeOcr, GlmOcr, OlmOcr).')
        return data

    # ------------------------------------------------------------------ #
    # Mapping                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _quadToRect(points):
        """
        4-point quad -> (left, top, width, height); None if bad.
        """
        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            return None
        left, top = min(xs), min(ys)
        width, height = max(xs) - left, max(ys) - top
        if width <= 0 or height <= 0:
            return None
        return left, top, width, height

    def _confident(self, score):
        if score is None:
            return True
        try:
            return float(score) >= self.__m_min_confidence
        except (TypeError, ValueError):
            return True

    def _wordsFromWordResults(self, word_results):
        """
        v2 word_results: per-line tuples of (text, score, quad).
        """
        words = []
        if word_results is None:
            return words
        for line in list(word_results):
            if line is None:
                continue
            for entry in list(line):
                try:
                    text, score, points = entry[0], entry[1], entry[2]
                except (TypeError, IndexError):
                    continue
                text = str(text).strip()
                rect = self._quadToRect(points)
                if not text or rect is None or not self._confident(score):
                    continue
                left, top, width, height = rect
                words.append(self.makeWord(text, left, top, max(width, _MIN_BOX), max(height, _MIN_BOX)))
        return words

    def _wordsFromLines(self, boxes, txts, scores):
        """
        Line-level boxes/txts/scores triple -> word dicts.
        """
        words = []
        if boxes is None or txts is None:
            return words
        scores = [] if scores is None else list(scores)
        for index, (box, text) in enumerate(zip(list(boxes), list(txts))):
            text = str(text).strip()
            score = scores[index] if index < len(scores) else None
            rect = self._quadToRect(box.tolist() if hasattr(box, 'tolist') else box)
            if not text or rect is None or not self._confident(score):
                continue
            left, top, width, height = rect
            words.append(self.makeWord(text, left, top, max(width, _MIN_BOX), max(height, _MIN_BOX)))
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (WORD-level pixel boxes by default; line-level with wordBox=False).
        """
        engine = self._engine()
        source = self._engineInput()
        if RapidOcr._generation == 'v2':
            call_kwargs = {'use_det': self.__m_use_det, 'use_cls': self.__m_use_cls, 'use_rec': self.__m_use_rec}
            if self.__m_word_box:
                call_kwargs['return_word_box'] = True
            result = engine(source, **call_kwargs)
            words = []
            if self.__m_word_box:
                words = self._wordsFromWordResults(
                    getattr(result, 'word_results', None))
            if not words:
                words = self._wordsFromLines(
                    getattr(result, 'boxes', None),
                    getattr(result, 'txts', None),
                    getattr(result, 'scores', None))
        else:
            outcome = engine(source, use_det=self.__m_use_det, use_cls=self.__m_use_cls, use_rec=self.__m_use_rec)
            entries = outcome[0] if isinstance(outcome, tuple) else outcome
            words = []
            for entry in entries or []:
                try:
                    box, text, score = entry[0], entry[1], entry[2]
                except (TypeError, IndexError):
                    continue
                text = str(text).strip()
                rect = self._quadToRect(box)
                if not text or rect is None or not self._confident(score):
                    continue
                left, top, width, height = rect
                words.append(self.makeWord(
                    text, left, top,
                    max(width, _MIN_BOX), max(height, _MIN_BOX)))
        if not words:
            raise OCRError('RapidOCR found no text in this image.')
        return words
