# coding=utf-8
"""
Backward-compatible ``OcrBase``.

Legacy scripts call ``OcrBase().parse(EngineName=[{...kwargs...}], Other=[{...}])`` and expect the first engine
whose ``lastError`` stays empty to win. This subclass keeps AioOCR's own ``OcrBase`` as the base class and only
replaces the lookups that relied on ``import_module('__init__')`` and ``sys.path`` mutation with the registry.
"""
from __future__ import absolute_import, division, print_function

from ocrroute.enginelib import AioOCR, OCRPlugin
from ocrroute.catalog.registry import getRegistry
from ocrroute.logsetup import getLogger

log = getLogger(__name__)

_AioOcrBase = AioOCR.ocrbase.OcrBase if hasattr(AioOCR, 'ocrbase') else None
if _AioOcrBase is None:
    from AioOCR.ocrbase import OcrBase as _AioOcrBase


class OcrBase(_AioOcrBase):
    """
    OcrBase class (OcrRoute compatibility layer over AioOCR.ocrbase.OcrBase).
    """

    def __init__(self, *args, **kwargs):
        """
        :param args: any
        :param kwargs: any
        """
        super(OcrBase, self).__init__(*args, **kwargs)
        requests = dict(kwargs)
        for p in args:
            requests.update(p)
        self.__m_requests = requests

    def engines(self):
        """
        :return: list[str | unicode]
        """
        return [e.id for e in getRegistry().all()]

    @staticmethod
    def createObject(className, **kwargs):
        """
        :param className: str - engine class name
        :param kwargs: any
        :return: OCRPlugin
        """
        if className == 'OCRPlugin':
            return OCRPlugin(**kwargs)
        return getRegistry().instantiate(className, **kwargs)

    def parse(self, *args, **kwargs):
        """
        :param args: dict - {EngineName: [kwargs, ...]}
        :param kwargs: same as args
        :return: dict - unified OCR result of the first engine that succeeded, else an empty result
        """
        requests = dict(kwargs)
        for p in args:
            requests.update(p)
        if not requests:
            requests = self.__m_requests
        for name in requests:
            for params in requests[name]:
                try:
                    obj = OcrBase.createObject(name, **params)
                except Exception as err:
                    log.warning('compat engine unavailable', engine=name, error=str(err))
                    continue
                result = obj.parse()
                if obj.getLastError():
                    log.info('compat engine failed', engine=name, error=obj.getLastError())
                    continue
                return result
        return OCRPlugin.emptyResult()
