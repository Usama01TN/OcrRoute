# coding=utf-8
"""
None
"""
from os.path import dirname, join, exists
from inspect import getmembers, isclass
from importlib import import_module
from pkgutil import iter_modules

# Import the base class for type checking.
try:
    from .engines.ocrplugin import OCRPlugin
except:
    from engines.ocrplugin import OCRPlugin

# Dictionary to hold all discovered classes: {"ClassName": ClassObject}.
AVAILABLE_PLUGINS = {}


def _discoverOcrPlugins():
    """
    Dynamically loads all OCRPlugin subclasses from api/ and local/ packages.
    """
    for subPkg in ['api', 'local']:
        packagePath = join(dirname(__file__), 'engines', subPkg)
        if not exists(packagePath):
            continue
        # Iterate through all modules inside the subpackage directory.
        for _, moduleName, isPkg in iter_modules([packagePath]):
            if isPkg or moduleName.startswith("_"):
                continue
            fullModuleName = 'engines.{}.{}'.format(subPkg, moduleName)
            try:
                # Import the module relative to the root package.
                try:
                    module = import_module('.engines.{}.{}'.format(subPkg, moduleName), package=__package__)
                except:
                    module = import_module(fullModuleName, package=__package__)
                # Inspect module members for OCRPlugin subclasses.
                for name, obj in getmembers(module, isclass):
                    # Check if it inherits from OCRPlugin (excluding OCRPlugin itself).
                    if issubclass(obj, OCRPlugin) and obj is not OCRPlugin:
                        # Ensure the class was defined in that module (not imported into it).
                        if obj.__module__ == module.__name__:
                            AVAILABLE_PLUGINS[name] = obj
                            # Expose class at root level.
                            globals()[name] = obj
            except Exception as err:
                # Optional: Handle or log import errors (e.g., missing local dependencies like easyocr, tesseract).
                print('Warning: Could not import {}: {}'.format(fullModuleName, err))


# Run plugin discovery
_discoverOcrPlugins()
# Export discovered plugin classes
__all__ = list(AVAILABLE_PLUGINS.keys())
