# coding=utf-8
"""
Engine catalogue.

The catalogue is built from ``AioOCR.AVAILABLE_PLUGINS`` (the library's own discovery). Modules that
AioOCR could not import are imported once more here, only to capture the error text and derive an
install hint, so an engine is never silently dropped from the UI.
"""
from __future__ import absolute_import, division, print_function

from importlib import import_module
from inspect import getdoc, getsource
from os.path import dirname, exists, join
from pkgutil import iter_modules
from re import M, finditer, fullmatch, search, sub
from re import compile as reCompile
from threading import Lock, RLock

from ocrroute import enginelib
from ocrroute.logsetup import getLogger

try:
    from tomllib import load as tomlLoad  # Python 3.11+
except ImportError:  # pragma: no cover
    from tomli import load as tomlLoad

log = getLogger(__name__)

_KWARG_RE = reCompile(r"kwargs\.pop\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*(?:,\s*(.+?))?\)")
_PARAM_RE = reCompile(r":param\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)")
_RESERVED_BASE = ('endpoint', 'image', 'language', 'engine', 'online', 'payload', 'apiList', 'api', 'proxyList',
                  'proxy', 'timeout', 'retries', 'lastError', 'model', 'prompt')
_PIP_NAMES = {'cv2': 'opencv-python-headless', 'PIL': 'pillow', 'pytesseract': 'pytesseract', 'torch': 'torch',
              'transformers': 'transformers', 'paddleocr': 'paddleocr', 'easyocr': 'easyocr', 'keras_ocr': 'keras-ocr',
              'calamari_ocr': 'calamari-ocr', 'surya': 'surya-ocr', 'rapidocr_onnxruntime': 'rapidocr-onnxruntime',
              'fitz': 'pymupdf', 'pypdfium2': 'pypdfium2', 'nougat': 'nougat-ocr', 'mmocr': 'mmocr',
              'doctr': 'python-doctr', 'openai': 'openai', 'google': 'google-cloud-vision'}


class OptionSpec(object):
    """
    OptionSpec class: one engine constructor option.
    """

    def __init__(self, *args, **kwargs):
        """
        :param name: str | unicode
        :param type: str | unicode  string | integer | number | boolean | array | object
        :param default: any
        :param description: str | unicode
        :param source: str | unicode  introspected | curated | base
        """
        self.__m_name = kwargs.pop('name', '')
        self.__m_type = kwargs.pop('type', 'string')
        self.__m_default = kwargs.pop('default', None)
        self.__m_description = kwargs.pop('description', '')
        self.__m_source = kwargs.pop('source', 'introspected')

    def getName(self):
        """
        :return: str | unicode
        """
        return self.__m_name

    def getType(self):
        """
        :return: str | unicode
        """
        return self.__m_type

    def setType(self, optionType):
        """
        :param optionType: str | unicode
        :return:
        """
        self.__m_type = optionType  # type: str

    def getDefault(self):
        """
        :return: any
        """
        return self.__m_default

    def setDefault(self, default):
        """
        :param default: any
        :return:
        """
        self.__m_default = default

    def getDescription(self):
        """
        :return: str | unicode
        """
        return self.__m_description

    def setDescription(self, description):
        """
        :param description: str | unicode
        :return:
        """
        self.__m_description = description  # type: str

    def getSource(self):
        """
        :return: str | unicode
        """
        return self.__m_source

    def setSource(self, source):
        """
        :param source: str | unicode
        :return:
        """
        self.__m_source = source  # type: str

    def toDict(self):
        """
        :return: dict
        """
        return {'name': self.__m_name, 'type': self.__m_type, 'default': self.__m_default,
                'description': self.__m_description, 'source': self.__m_source}

    name = property(getName)
    type = property(getType, setType)
    default = property(getDefault, setDefault)
    description = property(getDescription, setDescription)
    source = property(getSource, setSource)
    __dict__ = property(toDict)


class EngineInfo(object):
    """
    EngineInfo class: everything the gateway knows about one OCRPlugin subclass.
    """
    _FIELDS = ('id', 'module', 'kind', 'name', 'vendor', 'available', 'import_error', 'install_hint', 'requires_key',
               'supports_pdf', 'supports_handwriting', 'supports_tables', 'supports_overlay', 'languages', 'options',
               'cost_model', 'unit_price', 'quality_score', 'homepage', 'docs_url', 'docstring', 'default_model')

    def __init__(self, *args, **kwargs):
        """
        :param id: str | unicode  the plugin class name
        :param module: str | unicode  dotted module path
        :param kind: str | unicode  api | local
        :param name: str | unicode  display name
        :param vendor: str | unicode
        :param available: bool
        :param import_error: str | unicode
        :param install_hint: str | unicode
        :param requires_key: bool
        :param supports_pdf: bool
        :param supports_handwriting: bool
        :param supports_tables: bool
        :param supports_overlay: bool
        :param languages: list[str | unicode]
        :param options: list[OptionSpec]
        :param cost_model: str | unicode  free | per_page | per_token | per_request | local
        :param unit_price: float  USD cents per unit
        :param quality_score: int  0-100 curated accuracy score
        :param homepage: str | unicode
        :param docs_url: str | unicode
        :param docstring: str | unicode
        :param default_model: str | unicode
        :param cls: type | None  the plugin class object
        """
        self.__m_id = kwargs.pop('id', '')
        self.__m_module = kwargs.pop('module', '')
        self.__m_kind = kwargs.pop('kind', 'local')
        self.__m_name = kwargs.pop('name', '')
        self.__m_vendor = kwargs.pop('vendor', '')
        self.__m_available = kwargs.pop('available', False)
        self.__m_importError = kwargs.pop('import_error', '')
        self.__m_installHint = kwargs.pop('install_hint', '')
        self.__m_requiresKey = kwargs.pop('requires_key', False)
        self.__m_supportsPdf = kwargs.pop('supports_pdf', False)
        self.__m_supportsHandwriting = kwargs.pop('supports_handwriting', False)
        self.__m_supportsTables = kwargs.pop('supports_tables', False)
        self.__m_supportsOverlay = kwargs.pop('supports_overlay', True)
        self.__m_languages = kwargs.pop('languages', [])
        self.__m_options = kwargs.pop('options', [])
        self.__m_costModel = kwargs.pop('cost_model', 'free')
        self.__m_unitPrice = kwargs.pop('unit_price', 0.0)
        self.__m_qualityScore = kwargs.pop('quality_score', 50)
        self.__m_homepage = kwargs.pop('homepage', '')
        self.__m_docsUrl = kwargs.pop('docs_url', '')
        self.__m_docstring = kwargs.pop('docstring', '')
        self.__m_defaultModel = kwargs.pop('default_model', '')
        self.__m_cls = kwargs.pop('cls', None)
        self.heavy = bool(kwargs.pop('heavy', False))  # needs torch/transformers/paddle/onnx at runtime

    # -- accessors (AioOCR style) --------------------------------------------------------------
    def getId(self):
        """
        :return: str | unicode
        """
        return self.__m_id

    def getModule(self):
        """
        :return: str | unicode
        """
        return self.__m_module

    def getKind(self):
        """
        :return: str | unicode
        """
        return self.__m_kind

    def setKind(self, kind):
        """
        :param kind: str | unicode
        :return:
        """
        self.__m_kind = kind  # type: str

    def getName(self):
        """
        :return: str | unicode
        """
        return self.__m_name

    def setName(self, name):
        """
        :param name: str | unicode
        :return:
        """
        self.__m_name = name  # type: str

    def getVendor(self):
        """
        :return: str | unicode
        """
        return self.__m_vendor

    def setVendor(self, vendor):
        """
        :param vendor: str | unicode
        :return:
        """
        self.__m_vendor = vendor  # type: str

    def isAvailable(self):
        """
        :return: bool
        """
        return self.__m_available

    def setAvailable(self, available):
        """
        :param available: bool
        :return:
        """
        self.__m_available = bool(available)

    def getImportError(self):
        """
        :return: str | unicode
        """
        return self.__m_importError

    def setImportError(self, importError):
        """
        :param importError: str | unicode
        :return:
        """
        self.__m_importError = importError  # type: str

    def getInstallHint(self):
        """
        :return: str | unicode
        """
        return self.__m_installHint

    def setInstallHint(self, installHint):
        """
        :param installHint: str | unicode
        :return:
        """
        self.__m_installHint = installHint  # type: str

    def requiresKey(self):
        """
        :return: bool
        """
        return self.__m_requiresKey

    def setRequiresKey(self, requiresKey):
        """
        :param requiresKey: bool
        :return:
        """
        self.__m_requiresKey = bool(requiresKey)

    def supportsPdf(self):
        """
        :return: bool
        """
        return self.__m_supportsPdf

    def setSupportsPdf(self, value):
        self.__m_supportsPdf = bool(value)

    def supportsHandwriting(self):
        """
        :return: bool
        """
        return self.__m_supportsHandwriting

    def setSupportsHandwriting(self, value):
        self.__m_supportsHandwriting = bool(value)

    def supportsTables(self):
        """
        :return: bool
        """
        return self.__m_supportsTables

    def setSupportsTables(self, value):
        self.__m_supportsTables = bool(value)

    def supportsOverlay(self):
        """
        :return: bool
        """
        return self.__m_supportsOverlay

    def setSupportsOverlay(self, value):
        self.__m_supportsOverlay = bool(value)

    def getLanguages(self):
        """
        :return: list[str | unicode]
        """
        return self.__m_languages

    def setLanguages(self, languages):
        """
        :param languages: list[str | unicode]
        :return:
        """
        self.__m_languages = list(languages or [])

    def getOptions(self):
        """
        :return: list[OptionSpec]
        """
        return self.__m_options

    def setOptions(self, options):
        """
        :param options: list[OptionSpec]
        :return:
        """
        self.__m_options = list(options or [])

    def getCostModel(self):
        """
        :return: str | unicode
        """
        return self.__m_costModel

    def setCostModel(self, costModel):
        self.__m_costModel = costModel  # type: str

    def getUnitPrice(self):
        """
        :return: float
        """
        return self.__m_unitPrice

    def setUnitPrice(self, unitPrice):
        self.__m_unitPrice = float(unitPrice)

    def getQualityScore(self):
        """
        :return: int
        """
        return self.__m_qualityScore

    def setQualityScore(self, qualityScore):
        self.__m_qualityScore = int(qualityScore)

    def getHomepage(self):
        """
        :return: str | unicode
        """
        return self.__m_homepage

    def setHomepage(self, homepage):
        self.__m_homepage = homepage  # type: str

    def getDocsUrl(self):
        """
        :return: str | unicode
        """
        return self.__m_docsUrl

    def setDocsUrl(self, docsUrl):
        self.__m_docsUrl = docsUrl  # type: str

    def getDocstring(self):
        """
        :return: str | unicode
        """
        return self.__m_docstring

    def getDefaultModel(self):
        """
        :return: str | unicode
        """
        return self.__m_defaultModel

    def setDefaultModel(self, defaultModel):
        self.__m_defaultModel = defaultModel  # type: str

    def getCls(self):
        """
        :return: type | None
        """
        return self.__m_cls

    def toDict(self):
        """
        :return: dict  JSON-serialisable view (no class object)
        """
        result = {}
        for field in EngineInfo._FIELDS:
            value = getattr(self, field)
            if field == 'options':
                value = [o.toDict() for o in value]
            result[field] = value
        result['heavy'] = self.heavy
        return result

    # snake_case properties so the rest of the gateway can read the fields like plain attributes
    id = property(getId)
    module = property(getModule)
    kind = property(getKind, setKind)
    name = property(getName, setName)
    vendor = property(getVendor, setVendor)
    available = property(isAvailable, setAvailable)
    import_error = property(getImportError, setImportError)
    install_hint = property(getInstallHint, setInstallHint)
    requires_key = property(requiresKey, setRequiresKey)
    supports_pdf = property(supportsPdf, setSupportsPdf)
    supports_handwriting = property(supportsHandwriting, setSupportsHandwriting)
    supports_tables = property(supportsTables, setSupportsTables)
    supports_overlay = property(supportsOverlay, setSupportsOverlay)
    languages = property(getLanguages, setLanguages)
    options = property(getOptions, setOptions)
    cost_model = property(getCostModel, setCostModel)
    unit_price = property(getUnitPrice, setUnitPrice)
    quality_score = property(getQualityScore, setQualityScore)
    homepage = property(getHomepage, setHomepage)
    docs_url = property(getDocsUrl, setDocsUrl)
    docstring = property(getDocstring)
    default_model = property(getDefaultModel, setDefaultModel)
    cls = property(getCls)
    to_dict = toDict


def _guessType(defaultSrc):
    """
    :param defaultSrc: str | unicode | None  source text of the default expression
    :return: tuple[str, any]
    """
    if defaultSrc is None:
        return 'string', None
    s = defaultSrc.strip()
    if s in ('True', 'False'):
        return 'boolean', s == 'True'
    if fullmatch(r'-?\d+', s):
        return 'integer', int(s)
    if fullmatch(r'-?\d+\.\d*', s):
        return 'number', float(s)
    if s.startswith(("'", '"')) and s.endswith(("'", '"')) and len(s) >= 2:
        return 'string', s[1:-1]
    if s.startswith('['):
        return 'array', []
    if s.startswith('{'):
        return 'object', {}
    return 'string', None


_HEAVY_MARKERS = ('import torch', 'from torch', 'transformers', 'paddleocr', 'paddle', 'onnxruntime', 'tensorflow', 'keras',
                  'easyocr', 'mmocr', 'calamari', 'surya', 'vllm', 'accelerate')


def isHeavy(cls):
    """
    :param cls: type  engine class
    :return: bool  True when the module pulls a deep-learning runtime (slow cold start, often missing deps)
    """
    try:
        from inspect import getmodule, getsource

        src = getsource(getmodule(cls))
    except Exception:  # noqa: BLE001
        return False
    return any(m in src for m in _HEAVY_MARKERS)


def introspectOptions(cls):
    """
    Harvest ``kwargs.pop('name', default)`` calls and ``:param name:`` docs from an engine class.

    :param cls: type
    :return: list[OptionSpec]
    """
    specs = {}
    try:
        src = getsource(cls)
    except (OSError, TypeError):
        src = ''
    docs = {}
    for m in finditer(_PARAM_RE, src):
        docs.setdefault(m.group(1), m.group(2).strip())
    for m in finditer(_KWARG_RE, src):
        name = m.group(1)
        if name in _RESERVED_BASE:
            continue
        optType, default = _guessType(m.group(2))
        specs.setdefault(name, OptionSpec(name=name, type=optType, default=default, description=docs.get(name, '')))
    for name in ('language', 'model', 'prompt', 'timeout', 'retries'):
        if name in docs and name not in specs:
            specs[name] = OptionSpec(name=name, description=docs[name], source='base')
    return sorted(specs.values(), key=lambda o: o.getName())


def installHint(error, kind):
    """
    :param error: str | unicode  import or runtime error text
    :param kind: str | unicode  api | local
    :return: str | unicode  a copy-paste install command
    """
    stated = search(r'(pip install [A-Za-z0-9_.\-\[\] ]+)', error)
    if stated:
        return stated.group(1).strip().rstrip('.')
    m = search(r"No module named '([^'.]+)", error)
    if m:
        mod = m.group(1)
        return 'pip install {}'.format(_PIP_NAMES.get(mod, mod.replace('_', '-')))
    if 'tesseract' in error.lower():
        return 'Install the Tesseract binary (apt install tesseract-ocr / brew install tesseract) and pytesseract'
    return 'pip install ocrroute[local]' if kind == 'local' else 'pip install ocrroute[api]'


class EngineRegistry(object):
    """
    EngineRegistry class: the catalogue of engines, taken from AioOCR's own discovery.
    """

    def __init__(self, *args, **kwargs):
        """
        :param curatedPath: str | unicode  path of engines.toml
        """
        curatedPath = kwargs.pop('curatedPath', None) or kwargs.pop('curated_path', None)
        self.__m_lock = RLock()
        self.__m_engines = {}
        self.__m_curated = self._loadCurated(curatedPath or join(dirname(__file__), 'engines.toml'))
        self.__m_discovered = False
        self.__m_snapshot = {}
        self.__m_registered = {}  # engines added with register(); kept across rediscovery

    def changed(self):
        """
        :return: bool  True when engine modules were added, removed or modified since the last discovery
        """
        return enginelib.enginesSnapshot() != self.__m_snapshot

    def rescanIfChanged(self):
        """
        Re-discover only when the engines folder changed on disk.

        :return: bool  True when a rescan happened
        """
        if self.__m_discovered and not self.changed():
            return False
        self.discover(force=True)
        return True

    @staticmethod
    def _loadCurated(path):
        """
        :param path: str | unicode
        :return: dict[str, dict]
        """
        if not exists(path):
            return {}
        with open(path, 'rb') as fh:
            data = tomlLoad(fh)
        return dict(data.get('engines', {}))

    @staticmethod
    def _moduleKind(moduleName):
        """
        :param moduleName: str | unicode  e.g. AioOCR.engines.api.ocrspace or engines.local.tesseract
        :return: str | unicode  api | local
        """
        return 'api' if '.api.' in moduleName else 'local'

    @staticmethod
    def _classNameHint(path):
        """
        :param path: str | unicode  module file path
        :return: str | unicode  first class name declared in the file, or ''
        """
        try:
            with open(path, encoding='utf-8', errors='ignore') as fh:
                m = search(r'^class\s+(\w+)\s*\(', fh.read(), M)
            return m.group(1) if m else ''
        except OSError:
            return ''

    # ------------------------------------------------------------------ discovery
    def discover(self, force=False):
        """
        Build the catalogue: available classes from ``AioOCR.AVAILABLE_PLUGINS`` plus a diagnostic entry for
        every module the library could not import.

        :param force: bool  rebuild even if already discovered
        :return: dict[str, EngineInfo]
        """
        with self.__m_lock:
            if self.__m_discovered and not force:
                return self.__m_engines
            if force and self.__m_discovered:
                enginelib.rediscover()
            self.__m_snapshot = enginelib.enginesSnapshot()
            found = {}
            base = enginelib.OCRPlugin
            for name, cls in enginelib.AVAILABLE_PLUGINS.items():
                if not issubclass(cls, base):
                    continue
                info = EngineInfo(id=name, module=cls.__module__, kind=self._moduleKind(cls.__module__), available=True,
                                  cls=cls, docstring=(getdoc(cls) or '').strip(), options=introspectOptions(cls),
                                  heavy=isHeavy(cls))
                self._applyCurated(info)
                found[name] = info
            importedModules = set(cls.__module__.split('.')[-1] for cls in enginelib.AVAILABLE_PLUGINS.values())
            for kind in enginelib.enginePackages():
                pkgDir = join(enginelib.ENGINES_ROOT, kind)
                if not exists(pkgDir):
                    continue
                for _finder, modName, isPkg in iter_modules([pkgDir]):
                    if isPkg or modName.startswith('_') or modName in importedModules:
                        continue
                    fullName = 'engines.{}.{}'.format(kind, modName)
                    err = enginelib.CRASHED.get(fullName) or self._importError(fullName)
                    if not err:
                        continue  # imported fine now (e.g. registered by a test) - nothing to report
                    classId = self._classNameHint(join(pkgDir, modName + '.py')) or modName
                    hint = ('Disabled automatically: a compiled dependency crashes on this CPU/OS. Try reinstalling it '
                            'from source or another version.') if fullName in enginelib.CRASHED else installHint(err, kind)
                    info = EngineInfo(id=classId, module='AioOCR.engines.{}.{}'.format(kind, modName), kind=kind,
                                      available=False, import_error=err[:500], install_hint=hint)
                    self._applyCurated(info)
                    found.setdefault(classId, info)
            found.update(self.__m_registered)
            self.__m_engines = dict(sorted(found.items()))
            self.__m_discovered = True
            log.info('engines discovered', total=len(found), available=sum(1 for e in found.values() if e.isAvailable()))
            return self.__m_engines

    @staticmethod
    def _importError(moduleName):
        """
        :param moduleName: str | unicode
        :return: str | unicode  '' when the module imports, otherwise the error text
        """
        try:
            import_module(moduleName)
        except BaseException as exc:  # noqa: BLE001 - engines raise anything at import time
            return '{}: {}'.format(type(exc).__name__, exc)
        return ''

    def _applyCurated(self, info):
        """
        :param info: EngineInfo
        :return:
        """
        if not info.getName():
            pretty = sub(r'(?<!^)(?=[A-Z])', ' ', info.getId()).replace('O C R', 'OCR').replace('Ocr', 'OCR')
            info.setName(pretty)
        cur = self.__m_curated.get(info.getId())
        if not cur:
            if info.getKind() == 'api':
                info.setRequiresKey(True)
                info.setCostModel('per_request')
            else:
                info.setCostModel('local')
            return
        for key, value in cur.items():
            if key == 'options':
                names = dict((o.getName(), o) for o in info.getOptions())
                for name, spec in value.items():
                    if name in names:
                        o = names[name]
                        o.setDescription(spec.get('description', o.getDescription()))
                        o.setType(spec.get('type', o.getType()))
                        if 'default' in spec:
                            o.setDefault(spec['default'])
                        o.setSource('curated')
                    else:
                        info.getOptions().append(OptionSpec(name=name, type=spec.get('type', 'string'),
                                                            default=spec.get('default'),
                                                            description=spec.get('description', ''), source='curated'))
                info.getOptions().sort(key=lambda o: o.getName())
            elif key in EngineInfo._FIELDS:
                setattr(info, key, value)

    # ------------------------------------------------------------------ access
    def all(self):
        """
        :return: list[EngineInfo]
        """
        return list(self.discover().values())

    def get(self, engineId):
        """
        :param engineId: str | unicode
        :return: EngineInfo | None
        """
        return self.discover().get(engineId)

    def available(self):
        """
        :return: list[EngineInfo]
        """
        return [e for e in self.all() if e.isAvailable()]

    def instantiate(self, engineId, **kwargs):
        """
        :param engineId: str | unicode
        :param kwargs: OCRPlugin constructor kwargs
        :return: OCRPlugin
        """
        info = self.get(engineId)
        if info is None:
            raise LookupError("Unknown engine '{}'".format(engineId))
        if not info.isAvailable() or info.getCls() is None:
            raise ImportError("Engine '{}' is not available: {} ({})".format(engineId, info.getImportError(),
                                                                          info.getInstallHint()))
        return info.getCls()(**kwargs)

    def register(self, cls, kind='local', **meta):
        """
        Register an engine class programmatically (tests and future plugins).

        :param cls: type
        :param kind: str | unicode
        :param meta: EngineInfo fields to override
        :return: EngineInfo
        """
        with self.__m_lock:
            self.discover()
            info = EngineInfo(id=cls.__name__, module=cls.__module__, kind=kind, available=True, cls=cls,
                              options=introspectOptions(cls))
            self._applyCurated(info)
            for k, v in meta.items():
                setattr(info, k, v)
            self.__m_registered[cls.__name__] = info
            self.__m_engines[cls.__name__] = info
            return info


_registry = None
_registryLock = Lock()


def getRegistry():
    """
    :return: EngineRegistry  the process-wide catalogue
    """
    global _registry
    with _registryLock:
        if _registry is None:
            _registry = EngineRegistry()
        return _registry


def resetRegistry():
    """
    Drop the process-wide catalogue (tests).
    """
    global _registry
    with _registryLock:
        _registry = None


# snake_case aliases kept for external callers
introspect_options = introspectOptions
_install_hint = installHint
get_registry = getRegistry
reset_registry = resetRegistry
