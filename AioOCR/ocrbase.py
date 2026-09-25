# coding=utf-8
"""
None
"""
from importlib import import_module
from os.path import dirname
from sys import path

path.append(dirname(__file__))


class OcrBase(object):
    """
    OcrBase class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param args: any
        :param kwargs: any
        """
        requests = dict(kwargs)
        for p in args:
            requests.update(p)
        self.__m_requests = requests

    def engines(self):
        """
        :return: list[str | unicode]
        """
        return getattr(import_module('__init__'), '__all__')

    @staticmethod
    def createObject(className, **kwargs):
        cls = getattr(import_module('__init__'), className)
        return cls(**kwargs)

    def parse(self, *args, **kwargs):
        requests = dict(kwargs)
        for p in args:
            requests.update(p)
        if not requests:
            requests = self.__m_requests
        for x in requests:
            for a in requests[x]:
                obj = OcrBase.createObject(x, **a)
                result = obj.parse()
                print(obj.getLastError())
                if not obj.getLastError():
                    return result
        emptyObj = OcrBase.createObject('OCRPlugin')
        return emptyObj.parse()
