# coding=utf-8
"""
Reserved: post-processing Tools.

This package is a complete, wired, intentionally EMPTY scaffold. No tool ships in this release.
See ``ocrroute/tools/README.md`` and ``docs/TOOLS.md`` for the contract a future tool must follow.
"""
from __future__ import absolute_import, division, print_function

from ocrroute.tools.base import ToolPlugin
from ocrroute.tools.registry import ToolRegistry, getToolRegistry

__all__ = ['ToolPlugin', 'ToolRegistry', 'getToolRegistry']
