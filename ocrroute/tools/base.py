# coding=utf-8
"""ToolPlugin - abstract contract for future post-processing tools (mirrors the OCRPlugin philosophy)."""
from __future__ import absolute_import, division, print_function

from abc import ABC, abstractmethod


class ToolPlugin(ABC):
    """
    A tool takes a unified OCR result and returns a (possibly transformed) unified result.
    No concrete subclass is provided in this release.
    """

    name = ''
    version = '0.0.0'
    description = ''
    input_kinds = ('unified_result',)
    output_kinds = ('unified_result',)
    option_schema = []

    def __init__(self):
        self.__m_lastError = ''

    @abstractmethod
    def run(self, run_result, **options):
        """Transform ``run_result`` and return a unified result dict."""

    def getLastError(self):  # noqa: N802 - symmetry with OCRPlugin
        return self.__m_lastError

    def setLastError(self, error):  # noqa: N802
        self.__m_lastError = error

    def describe(self):
        return {
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'input_kinds': list(self.input_kinds),
            'output_kinds': list(self.output_kinds),
            'option_schema': list(self.option_schema),
        }
