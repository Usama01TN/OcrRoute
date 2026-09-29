# coding=utf-8
"""Auto-discovery of ToolPlugin subclasses under ``ocrroute.tools.builtin`` - correctly empty today."""
from __future__ import absolute_import, division, print_function

import importlib
import inspect
import pkgutil
import threading
from pathlib import Path

from ocrroute.tools.base import ToolPlugin


class ToolRegistry(object):
    def __init__(self):
        self.__m_tools = {}
        self.__m_discovered = False
        self.__m_lock = threading.Lock()

    def discover(self, force=False):
        with self.__m_lock:
            if self.__m_discovered and not force:
                return self.__m_tools
            import ocrroute.tools.builtin as builtin

            found = {}
            for _f, modname, ispkg in pkgutil.iter_modules([str(Path(builtin.__file__).parent)]):
                if ispkg or modname.startswith('_'):
                    continue
                module = importlib.import_module('ocrroute.tools.builtin.{}'.format(modname))
                for _n, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, ToolPlugin) and obj is not ToolPlugin and obj.__module__ == module.__name__:
                        found[obj.name or obj.__name__] = obj
            self.__m_tools = found
            self.__m_discovered = True
            return found

    def list(self):
        return [cls().describe() for cls in self.discover().values()]

    def get(self, name):
        cls = self.discover().get(name)
        return cls() if cls else None


_registry = None


def getToolRegistry():
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
