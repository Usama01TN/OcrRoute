# coding=utf-8
"""
Calamari-OCR plugin.
Calamari is a *line* recognizer: it expects pre-segmented text-line
images. We segment lines with OpenCV morphology (dilate horizontally so
characters of one line merge into a single contour), sort the boxes in
reading order, and recognize each crop.
"""
from cv2 import CHAIN_APPROX_SIMPLE, IMREAD_GRAYSCALE, MORPH_RECT, RETR_EXTERNAL, THRESH_BINARY_INV, THRESH_OTSU, \
    boundingRect, dilate, findContours, getStructuringElement, imdecode, imread, threshold
from numpy import frombuffer, uint8
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

try:
    # calamari-ocr >= 2.x
    from calamari_ocr.ocr.predict.predictor import Predictor, PredictorParams

    _CALAMARI_V2 = True
except ImportError:
    # calamari-ocr 1.x
    from calamari_ocr.ocr import Predictor

    _CALAMARI_V2 = False


class CalamariOcr(OCRPlugin):
    """
    CalamariOcr class.
    """
    _predictors = {}

    def __init__(self, *args, **kwargs):
        """
        :param checkpoint: path to the Calamari model checkpoint (e.g. 'historical_french.ckpt').
        :param minLineArea: ignore contours smaller than this (px^2).
        :param kwargs: other settings.
        """
        self.__m_checkpoint = kwargs.pop('checkpoint', 'historical_french')
        self.__m_minLineArea = kwargs.pop('minLineArea', 100)
        super(CalamariOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)

    def _predictor(self):
        """
        Cache one predictor per checkpoint (loading is slow).
        """
        ckpt = self.__m_checkpoint
        if ckpt not in CalamariOcr._predictors:
            if _CALAMARI_V2:
                CalamariOcr._predictors[ckpt] = Predictor.from_checkpoint(params=PredictorParams(), checkpoint=ckpt)
            else:
                CalamariOcr._predictors[ckpt] = Predictor(checkpoint=ckpt)
        return CalamariOcr._predictors[ckpt]

    def _loadGray(self):
        """
        Load the current image source as a grayscale numpy array.
        """
        kind = self.imageKind()
        if kind == 'path':
            image = imread(self.getImage(), IMREAD_GRAYSCALE)
        elif kind in ('pil', 'bytes', 'buffer'):
            data = frombuffer(self.imageBytes(), dtype=uint8)
            image = imdecode(data, IMREAD_GRAYSCALE)
        elif kind == 'array':
            image = self.getImage()
            if image.ndim == 3:
                image = image.mean(axis=2).astype(uint8)
        else:
            raise OCRError('Calamari is offline; unsupported source: ' + kind)
        if image is None:
            raise OCRError('Could not decode image.')
        return image

    def _detectLines(self, image):
        """
        Detect text-line bounding boxes, sorted top-to-bottom then left-to-right (reading order).
        """
        _, binary = threshold(image, 0, 255, THRESH_BINARY_INV | THRESH_OTSU)
        # Merge characters of one line into a single blob.
        kernel = getStructuringElement(MORPH_RECT, (25, 3))
        merged = dilate(binary, kernel, iterations=1)
        contours, _ = findContours(merged, RETR_EXTERNAL, CHAIN_APPROX_SIMPLE)
        boxes = [boundingRect(c) for c in contours]
        boxes = [b for b in boxes if b[2] * b[3] >= self.__m_minLineArea]
        boxes.sort(key=lambda b: (b[1], b[0]))
        return boxes

    def _predictText(self, crop):
        """
        Recognize a single line crop, across calamari versions.
        """
        predictor = self._predictor()
        if _CALAMARI_V2:
            for sample in predictor.predict_raw([crop]):
                return sample.outputs.sentence
            return ''
        prediction = predictor.predict_raw([crop], progress_bar=False)
        for p in prediction:
            return p.sentence
        return ''

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts (one per detected line;
                    Calamari outputs whole-line text, which the base class groups back into lines by geometry).
        """
        gray = self._loadGray()
        words = []
        for (x, y, w, h) in self._detectLines(gray):
            crop = gray[y:y + h, x:x + w]
            text = (self._predictText(crop) or '').strip()
            if text:
                words.append(self.makeWord(text, x, y, w, h))
        return words
