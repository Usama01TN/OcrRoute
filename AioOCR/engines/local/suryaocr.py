# coding=utf-8
"""
Surya OCR plugin (https://github.com/datalab-to/surya).
Surya does line-level text detection and recognition in 90+ languages,
fully locally (CPU or GPU). Install::
    pip install surya-ocr
The first run downloads the model weights into the local HF cache;
afterwards it is offline. Torch device is auto-detected (override with
the TORCH_DEVICE environment variable, e.g. TORCH_DEVICE=cuda).
This plugin supports both API generations:
- **Surya 2 (2026+)**: SuryaInferenceManager + RecognitionPredictor;
  the manager auto-spawns a local vllm/llama-server on first use (or
  attaches to one via SURYA_INFERENCE_URL). Results come back as
  ``blocks`` with ``text``, ``bbox`` and ``confidence``.
- **Surya 0.x**: FoundationPredictor + RecognitionPredictor +
  DetectionPredictor; results come back as ``text_lines``.
Either way, Surya's line bboxes are already in PIXEL coordinates
([x1, y1, x2, y2]), which map straight into the unified structure
shared by every plugin. Note: the file is named ``suryaocr.py`` on
purpose -- ``surya.py`` would shadow the installed package.
"""
from surya.recognition import RecognitionPredictor
from os.path import dirname
from re import compile
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:  # Surya 2
    from surya.inference import SuryaInferenceManager

    _SURYA_V2 = True
except ImportError:  # Surya 0.x
    SuryaInferenceManager = None
    _SURYA_V2 = False
    from surya.detection import DetectionPredictor

    try:
        from surya.foundation import FoundationPredictor
    except ImportError:  # Very old releases.
        FoundationPredictor = None

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_TAG = compile(r'<[^>]+>')


class SuryaOcr(OCRPlugin):
    """
    SuryaOcr class.
    """
    #: Predictors are heavy (model loads / spawned inference server):
    #: build once, share across instances.
    _predictors = None

    def __init__(self, *args, **kwargs):
        """
        :param minConfidence: drop lines below this confidence, 0-1
                              (default 0: keep everything; Surya's confidence is per-line).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings.
        """
        self.__m_minConfidence = float(kwargs.pop('minConfidence', 0.0))
        super(SuryaOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)

    # ------------------------------------------------------------------ #
    # Predictors                                                         #
    # ------------------------------------------------------------------ #
    @classmethod
    def _build(cls):
        """
        Build (once) the recognition stack for the installed API.
        """
        if cls._predictors is not None:
            return cls._predictors
        if _SURYA_V2:
            manager = SuryaInferenceManager()
            recognition = RecognitionPredictor(manager)
            cls._predictors = (recognition, None)
        else:
            detection = DetectionPredictor()
            if FoundationPredictor is not None:
                recognition = RecognitionPredictor(FoundationPredictor())
            else:
                recognition = RecognitionPredictor()
            cls._predictors = (recognition, detection)
        return cls._predictors

    # ------------------------------------------------------------------ #
    # Input                                                              #
    # ------------------------------------------------------------------ #
    def _pilImage(self):
        """
        Surya's predictors take PIL images.
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'path':
            return Image.open(self.getImage()).convert('RGB')
        if kind == 'pil':
            return self.getImage().convert('RGB')
        if kind in ('bytes', 'buffer'):
            return Image.open(BytesIO(self.imageBytes())).convert('RGB')
        if kind == 'array':
            return Image.fromarray(self.getImage()).convert('RGB')
        if kind == 'url':
            from requests import get
            reply = get(self.getImage(), timeout=self.getTimeout())
            reply.raise_for_status()
            return Image.open(BytesIO(reply.content)).convert('RGB')
        raise OCRError(
            "Image source '{}' is not an existing file, URL, or "
            "supported type. Check the path (the current working "
            "directory matters for relative paths).".format(self.getImage()))

    # ------------------------------------------------------------------ #
    # Result mapping                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _field(obj, *names):
        """
        Read the first present attribute/key among *names*.
        """
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            value = getattr(obj, name, None)
            if value is not None:
                return value
        return None

    def _wordsFromPrediction(self, prediction):
        """
        One OCRResult (v2 blocks or v0 text_lines) -> word dicts.
        """
        lines = (self._field(prediction, 'blocks')  # Surya 2
                 or self._field(prediction, 'text_lines')  # Surya 0.x
                 or [])
        words = []
        for line in lines:
            if self._field(line, 'skipped'):  # v2 visual blocks
                continue
            text = str(self._field(line, 'text') or '')
            text = _TAG.sub('', text).strip()  # v2 text may carry html
            if not text:
                continue
            confidence = self._field(line, 'confidence')
            if confidence is not None and float(confidence) < self.__m_minConfidence:
                continue
            box = self._field(line, 'bbox') or []
            if len(box) != 4:
                continue
            x1, y1, x2, y2 = (float(v) for v in box)
            if x2 <= x1 or y2 <= y1:
                continue
            words.append(self.makeWord(
                text, x1, y1, x2 - x1, y2 - y1))
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (Surya returns text LINES with pixel bboxes; the base
                 class groups them geometrically like every engine).
        """
        recognition, detection = self._build()
        pil = self._pilImage()
        if detection is None:  # Surya 2
            predictions = recognition([pil])
        else:  # Surya 0.x
            try:
                predictions = recognition([pil], det_predictor=detection)
            except TypeError:
                # Oldest signature required a languages argument.
                langs = self.getLanguage()
                if isinstance(langs, str):
                    langs = [langs]
                predictions = recognition([pil], [langs], detection)
        if not predictions:
            raise OCRError('Surya returned no prediction for this image.')
        return self._wordsFromPrediction(predictions[0])
