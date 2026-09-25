# coding=utf-8
"""
Keras-OCR plugin.
"""
from keras_ocr import pipeline, tools
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


class KerasOcr(OCRPlugin):
    """
    KerasOcr class.
    """
    #: One pipeline for the whole process: constructing it downloads and
    #: loads the detector + recognizer models, which is very slow.
    _pipeline = None

    def __init__(self, *args, **kwargs):
        """
        :param language: document language.
        :param image: image source.
        :param kwargs: other settings.
        """
        super(KerasOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)

    @classmethod
    def _pipe(cls):
        if cls._pipeline is None:
            cls._pipeline = pipeline.Pipeline()
        return cls._pipeline

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        Bug fixed vs. the original version: it accessed
        ``self.__m_image`` from the subclass, which Python name-mangles
        to ``_KerasOcr__image`` and therefore always raised
        AttributeError. We now use the base-class accessor.
        """
        kind = self.imageKind()
        if kind in ('path', 'url'):
            image = tools.read(self.getImage())
        elif kind in ('pil', 'bytes', 'buffer'):
            from numpy import array
            from io import BytesIO
            from PIL import Image
            image = array(Image.open(BytesIO(self.imageBytes())).convert('RGB'))
        # numpy arrays pass through unchanged.
        results = self._pipe().recognize([image])
        words = []
        for text, bbox in results[0]:
            xs = bbox[:, 0]
            ys = bbox[:, 1]
            left, top = float(xs.min()), float(ys.min())
            words.append(self.makeWord(text, left, top, float(xs.max()) - left, float(ys.max()) - top))
        return words
