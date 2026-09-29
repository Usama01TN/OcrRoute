# coding=utf-8
"""
OCRPlugin: common base class for all OCR engines.

Every engine subclass only has to:
  1. call ``super().__init__(*args, **kwargs)``
  2. implement ``_run(image)`` returning a list of word dicts:
         {'WordText': str, 'Left': float, 'Top': float,
          'Width': float, 'Height': float}
The base class turns those words into the unified OCR.Space-style
result (lines, overlay, ParsedText) so every plugin returns the
exact same structure.
"""
from os.path import exists, isfile
from io import BytesIO
from time import sleep

try:
    from urlparse import urlparse  # Python 2
except ImportError:
    from urllib.parse import urlparse  # Python 3


def is_url(text):
    """Return True when *text* looks like an http(s) URL."""
    if not isinstance(text, str):
        return False
    try:
        result = urlparse(text)
        return result.scheme in ('http', 'https') and bool(result.netloc)
    except (ValueError, AttributeError):
        return False


class OCRError(Exception):
    """
    Raised when an OCR engine definitively fails.
    """


try:
    from . import languages as _languages
except (ImportError, ValueError):  # imported as a top-level module (engines on sys.path)
    import languages as _languages  # type: ignore[no-redef]

LANG_AUTO = _languages.AUTO

class OCRPlugin(object):
    """
    OCRPlugin base class.
    """
    #: Vertical tolerance (fraction of word height) used when grouping
    #: words into lines. Words whose vertical centers differ by less
    #: than ``LINE_TOLERANCE * height`` belong to the same line.
    LINE_TOLERANCE = 0.6

    def __init__(self, *args, **kwargs):
        """
        :param endpoint: str
        :param image: path | URL | base64 str | bytes | BytesIO | PIL.Image
        :param language: list[str] | str
        :param engine: int
        :param online: bool
        :param payload: dict
        :param apiList: list[str | unicode]
        :param api: str | unicode
        :param proxyList: list[dict[str, str]]
        :param proxy: dict[str, str]
        :param timeout: (int) Seconds for network requests.
        :param retries: (int) Max attempts for ``parse``.
        :param lastError: str | unicode
        :param model: str | unicode
        :param prompt: (str) Extra instructions appended to the engine's built-in
                       OCR prompt (vision-language-model engines only; ignored by
                       classic OCR APIs). Change at runtime with ``setExtraPrompt``.
        """
        self.__m_endpoint = kwargs.pop('endpoint', '')
        self.__m_image = kwargs.pop('image', '')
        # no language given: the engine's own default (``'auto'`` for engines that detect it, e.g. the vision-language
        # engines, which would otherwise get an "English" hint; ``'en'`` for engines that need one, e.g. Tesseract)
        self.__m_language = kwargs.pop('language', None)
        if self.__m_language in (None, '', []):
            self.__m_language = [self.defaultLanguage()]
        self.__m_engine = kwargs.pop('engine', 1)
        self.__m_online = kwargs.pop('online', False)
        self.__m_payload = kwargs.pop('payload', {})
        self.__m_apiList = kwargs.pop('apiList', [])
        self.__m_api = kwargs.pop('api', '')
        self.__m_proxyList = kwargs.pop('proxyList', [{}])
        self.__m_proxy = kwargs.pop('proxy', {})
        self.__m_timeout = kwargs.pop('timeout', 10)
        self.__m_retries = kwargs.pop('retries', 3)
        self.__m_lastError = kwargs.pop('lastError', '')
        self.__m_model = kwargs.pop('model', '')
        self.__m_extraPrompt = kwargs.pop('prompt', '')

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    def parse(self, *args, **kwargs):
        """
        Process an image from a local path, base64/bytes, or URL.
        Retries up to ``retries`` times with exponential backoff and
        stores the last failure in ``lastError``.
        :return: (dict) unified result (one ParsedResults entry).
        """
        self.setLastError('')
        delay = 0.5
        last_exc = None
        for attempt in range(1, self.getRetries() + 1):
            try:
                return self.buildResult(self._run(self.getImage(), *args, **kwargs))
            except Exception as exc:  # noqa: BLE001 - engines raise anything
                last_exc = exc
                self.setLastError('{}: {}'.format(type(exc).__name__, exc))
                if attempt < self.getRetries():
                    sleep(delay)
                    delay *= 2
        # All attempts failed: return an "errored" result instead of
        # looping forever, so callers can inspect FileParseExitCode.
        return self.errorResult(last_exc)

    def errorResult(self, exc=None):
        """
        Build the unified *errored* result. ``lastError`` must already
        have been recorded via ``setLastError``.

        :param exc: optional exception for ErrorDetails.
        :return: dict with FileParseExitCode == -1.
        """
        result = self.emptyResult()
        result['FileParseExitCode'] = -1
        result['ErrorMessage'] = self.getLastError()
        result['ErrorDetails'] = repr(exc) if exc is not None else ''
        return result

    def _run(self, image, *args, **kwargs):
        """
        Engine-specific OCR. Subclasses override this and return a list of word dicts (see module docstring).
        :param image: whatever ``getImage()`` currently holds.
        :return: list[dict]
        """
        raise NotImplementedError('Subclasses must implement _run().')

    # ------------------------------------------------------------------ #
    # Shared result construction                                         #
    # ------------------------------------------------------------------ #
    @staticmethod
    def emptyResult():
        """
        Return a fresh, empty ParsedResults[0]-style dict.
        """
        return {
            'TextOverlay': {
                'Lines': [],
                'HasOverlay': True,
                'Message': 'Total lines: 0',
            },
            'TextOrientation': '0',
            'FileParseExitCode': 1,
            'ParsedText': '',
        }

    def buildResult(self, words):
        """
        Group *words* into lines and build the unified result dict.
        Words are grouped by vertical proximity (not exact pixel match),
        then sorted top-to-bottom and left-to-right, so the output reads
        in natural order regardless of the engine's detection order.
        :param words: list of word dicts.
        :return: dict (ParsedResults[0] shape).
        """
        result = self.emptyResult()
        if not words:
            return result
        # Sort words by vertical center first so grouping is stable.
        words = sorted(words, key=lambda w: (w['Top'] + w['Height'] / 2.0, w['Left']))
        lines = []
        for word in words:
            center = word['Top'] + word['Height'] / 2.0
            placed = False
            for line in lines:
                # Tolerance from the SMALLER of the two heights: one
                # oversized box (e.g. a graphic detected as a "word")
                # must not swallow neighbouring text lines.
                avgHeight = line['_heightSum'] / len(line['Words'])
                tolerance = self.LINE_TOLERANCE * max(min(avgHeight, word['Height']), 1.0)
                if abs(center - line['_center']) <= tolerance:
                    line['Words'].append(word)
                    line['MaxHeight'] = max(line['MaxHeight'], word['Height'])
                    line['MinTop'] = min(line['MinTop'], word['Top'])
                    line['_heightSum'] += word['Height']
                    # Running average keeps the line center representative.
                    n = len(line['Words'])
                    line['_center'] += (center - line['_center']) / n
                    placed = True
                    break
            if not placed:
                lines.append({
                    'LineText': '',
                    'Words': [word],
                    'MaxHeight': word['Height'],
                    'MinTop': word['Top'],
                    '_center': center,
                    '_heightSum': word['Height'],
                })
        # Order lines top-to-bottom, words left-to-right, build texts.
        lines.sort(key=lambda l: l['MinTop'])
        parsed_text = []
        for line in lines:
            line['Words'].sort(key=lambda w: w['Left'])
            line['LineText'] = ' '.join(w['WordText'] for w in line['Words']).strip()
            del line['_center']
            del line['_heightSum']
            parsed_text.append(line['LineText'])
        result['TextOverlay']['Lines'] = lines
        result['TextOverlay']['Message'] = 'Total lines: {}'.format(len(lines))
        result['ParsedText'] = '\r\n'.join(parsed_text)
        return result

    @classmethod
    def normalizeResult(cls, raw):
        """
        Coerce *raw* (e.g. an online API's ParsedResults[0]) into the
        exact structure every plugin returns: all keys present, all
        geometry values as floats, no engine-specific extras dropped
        silently onto lines/words.
        Guaranteed shape::
            {'TextOverlay': {'Lines': [{'LineText': str,
                                        'Words': [{'WordText': str,
                                                   'Left': float,
                                                   'Top': float,
                                                   'Width': float,
                                                   'Height': float}],
                                        'MaxHeight': float,
                                        'MinTop': float}],
                             'HasOverlay': bool,
                             'Message': str},
             'TextOrientation': str,
             'FileParseExitCode': int,
             'ParsedText': str}
        :param raw: dict from any engine or API.
        :return: normalized dict.
        """
        result = cls.emptyResult()
        if not isinstance(raw, dict):
            return result

        def _float(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        overlay = raw.get('TextOverlay') or {}
        lines = []
        for line in overlay.get('Lines') or []:
            words = []
            for word in line.get('Words') or []:
                words.append({
                    'WordText': str(word.get('WordText', '')),
                    'Left': _float(word.get('Left')),
                    'Top': _float(word.get('Top')),
                    'Width': _float(word.get('Width')),
                    'Height': _float(word.get('Height')),
                })
            min_top = line.get('MinTop')
            max_height = line.get('MaxHeight')
            if min_top is None:
                min_top = min((w['Top'] for w in words), default=0.0)
            if max_height is None:
                max_height = max((w['Height'] for w in words), default=0.0)
            text = line.get('LineText')
            if not text:
                text = ' '.join(w['WordText'] for w in words).strip()
            lines.append({
                'LineText': text,
                'Words': words,
                'MaxHeight': _float(max_height),
                'MinTop': _float(min_top, float('inf') if not words else 0.0),
            })
        result['TextOverlay']['Lines'] = lines
        result['TextOverlay']['HasOverlay'] = bool(overlay.get('HasOverlay', bool(lines)))
        result['TextOverlay']['Message'] = str(overlay.get('Message') or 'Total lines: {}'.format(len(lines)))
        result['TextOrientation'] = str(raw.get('TextOrientation', '0'))
        try:
            result['FileParseExitCode'] = int(raw.get('FileParseExitCode', 1))
        except (TypeError, ValueError):
            result['FileParseExitCode'] = 1
        result['ParsedText'] = str(raw.get('ParsedText', ''))
        return result

    @staticmethod
    def makeWord(text, left, top, width, height):
        """
        Convenience constructor for a word dict.
        """
        return {
            'WordText': text, 'Left': float(left), 'Top': float(top), 'Width': float(width), 'Height': float(height)
        }

    # ------------------------------------------------------------------ #
    # Image normalization helpers                                        #
    # ------------------------------------------------------------------ #
    def imageKind(self):
        """
        Classify the current image source.
        :return: one of 'path', 'url', 'pil', 'bytes', 'buffer', 'array', 'unknown'.
        """
        img = self.getImage()
        name = type(img).__name__
        if name == 'Image' or hasattr(img, 'save'):
            return 'pil'
        if isinstance(img, BytesIO):
            return 'buffer'
        if isinstance(img, (bytes, bytearray)):
            return 'bytes'
        if name == 'ndarray':
            return 'array'
        if isinstance(img, str):
            if is_url(img):
                return 'url'
            if exists(img) and isfile(img):
                return 'path'
        return 'unknown'

    def imageBytes(self):
        """
        Return the image as raw PNG/original bytes whatever the source
        (path, PIL image, BytesIO, bytes). URLs are not downloaded here;
        online engines can pass them through instead.
        :return: bytes
        """
        kind = self.imageKind()
        img = self.getImage()
        if kind == 'bytes':
            return bytes(img)
        if kind == 'buffer':
            return img.getvalue()
        if kind == 'pil':
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            return buffer.getvalue()
        if kind == 'path':
            with open(img, 'rb') as handle:
                return handle.read()
        raise OCRError('Cannot convert image source ({}) to bytes.'.format(kind))

    # ------------------------------------------------------------------ #
    # Getters / setters (backward compatible)                            #
    # ------------------------------------------------------------------ #
    def getImage(self):
        """
        :return: any
        """
        return self.__m_image

    def setImage(self, image):
        """
        :param image: any
        :return:
        """
        self.__m_image = image

    def getLanguage(self):
        """
        :return: str | unicode
        """
        return self.__m_language

    def setLanguage(self, language):
        """
        :param language: str | unicode
        :return:
        """
        self.__m_language = language  # type: str

    # ------------------------------------------------------------------ #
    # Languages                                                          #
    # ------------------------------------------------------------------ #
    @classmethod
    def getLanguages(cls, engine=None):
        """
        Languages this engine accepts, as canonical codes (see ``engines.languages``): ``'auto'`` first when the
        engine can detect the language itself, then ``'en'``, ``'ar'``, ``'fr'``... Engines override it.

        The base implementation returns an empty list: the engine takes no language setting (it detects the
        language itself, e.g. a vision-language model, or reads one script only).

        :param engine: the engine variant whose languages are wanted (e.g. OCR.Space engine 1, 2 or 3);
                       ``None`` for the default variant
        :return: list[str]
        """
        return []

    @classmethod
    def getEngines(cls):
        """
        Engine variants whose supported languages differ (e.g. OCR.Space engines 1, 2 and 3). Engines override it.

        :return: list[dict]  ``{'value': ..., 'label': ..., 'default': bool}``; empty when there are none
        """
        return []

    #: The model this engine uses when none is given, and the models it is known to work with (ids; a free string is
    #: always accepted too: providers add models faster than plugins). See getModels / listModels.
    DEFAULT_MODEL = ''
    MODELS = ()

    @classmethod
    def defaultModel(cls):
        """:return: str  the default model id ('' for engines without a model setting)"""
        return cls.DEFAULT_MODEL or (cls.MODELS[0] if cls.MODELS else '')

    @classmethod
    def getModels(cls):
        """
        :return: list[dict]  known models, {'id', 'label', 'source': 'known'}, the default first; [] when the engine has
                 no model setting
        """
        ids = []
        for m in (cls.defaultModel(),) + tuple(cls.MODELS):
            if m and m not in ids:
                ids.append(m)
        return [{'id': m, 'label': m, 'source': 'known'} for m in ids]

    def listModels(self):
        """
        :return: list[dict]  the models the configured account can use right now ({'id', 'label', 'source': 'live'}),
                 from the provider's catalogue when the plugin knows how to ask (needs the API key); the known list
                 otherwise. Engines override ``_liveModels``.
        """
        live = []
        try:
            live = list(self._liveModels() or [])
        except Exception:  # noqa: BLE001 - no key, offline, provider down: the known list still helps
            live = []
        if not live:
            return self.getModels()
        return [{'id': m, 'label': m, 'source': 'live'} for m in live]

    def _liveModels(self):
        """:return: list[str]  model ids from the provider (plugins with a models endpoint reuse their discovery)"""
        if hasattr(self, '_candidateVisionModels'):  # ChatGPT, Grok, Groq, OmniRoute, SiliconFlow
            vision = list(self._candidateVisionModels(exclude=()))
            rest = [m for m in (self._catalogIds() if hasattr(self, '_catalogIds') else []) if m not in vision]
            return vision + rest  # likely vision models first, then the rest of the account's catalogue
        return []

    #: True when the engine reads several languages in one run (Tesseract ``eng+fra``, EasyOCR's list, Google Vision
    #: hints, a language hint naming several languages); False: one language per run (OCR.Space, Baidu, PaddleOCR...).
    MULTI_LANGUAGE = False
    #: Optional: groups of canonical codes that may be combined with each other (plus ``UNIVERSAL_LANGUAGES``); an
    #: empty list means any combination. See EasyOCR.
    LANGUAGE_GROUPS = []
    UNIVERSAL_LANGUAGES = ['en']

    @classmethod
    def compatibleLanguages(cls, chosen, engine=None):
        """
        :param chosen: list[str]  canonical codes already selected
        :return: list[str]  the codes that can still be added (all of ``getLanguages`` when the engine has no
                 combination rule; a single-language engine returns [] once one is chosen)
        """
        codes = cls.getLanguages(engine)
        chosen = [c for c in _languages.normalize(list(chosen or [])) if c != LANG_AUTO]
        if not cls.MULTI_LANGUAGE:
            return [] if chosen else codes
        if not cls.LANGUAGE_GROUPS:
            return [c for c in codes if c not in chosen]
        universal = set(cls.UNIVERSAL_LANGUAGES)
        specific = [c for c in chosen if c not in universal]
        if not specific:
            return [c for c in codes if c not in chosen]
        grouped = set(c for g in cls.LANGUAGE_GROUPS for c in g)
        free = set(c for c in codes if c not in grouped and c not in universal)  # e.g. Latin script: combine freely
        allowed = set(universal)
        if set(specific) <= free:
            allowed |= free
        for group in cls.LANGUAGE_GROUPS:
            if set(specific) <= set(group):
                allowed |= set(group)
        return [c for c in codes if c in allowed and c not in chosen]

    @classmethod
    def defaultLanguage(cls, engine=None):
        """
        :return: str  ``'auto'`` when the engine can detect the language, else ``'en'`` when supported, else the first
                 supported language (``'auto'`` for engines without a language setting)
        """
        codes = cls.getLanguages(engine)
        if not codes or LANG_AUTO in codes:
            return LANG_AUTO
        return 'en' if 'en' in codes else codes[0]

    @classmethod
    def toEngineLanguage(cls, code, engine=None):
        """
        Translate one canonical code into this engine's own format (``'ar'`` -> ``'ara'`` for Tesseract,
        ``'zh'`` -> ``'chs'`` for OCR.Space...). Engines override it; the base keeps the canonical code.

        :param code: str  canonical code
        :param engine: engine variant, as in ``getLanguages``
        :return: str
        """
        return code

    @classmethod
    def describeLanguages(cls, engine=None):
        """
        :return: dict  everything a user interface needs: ``engines`` (variants), ``engine`` (the one described),
                 ``languages`` (``{'code', 'name'}``), ``default``, ``fixed`` (True: no language setting)
        """
        variants = cls.getEngines()
        if engine is None and variants:
            engine = next((v['value'] for v in variants if v.get('default')), variants[0]['value'])
        codes = cls.getLanguages(engine)
        return {'engines': variants, 'engine': engine, 'languages': _languages.catalog(codes),
                'hint': bool(getattr(cls, 'LANGUAGE_HINT', False)),
                'multiple': bool(getattr(cls, 'MULTI_LANGUAGE', False)) and not (fixed if 'fixed' in dir() else not codes),
                'groups': [list(g) for g in (getattr(cls, 'LANGUAGE_GROUPS', None) or [])],
                'universal': list(getattr(cls, 'UNIVERSAL_LANGUAGES', ['en'])),
                'reads': _languages.catalog(list(getattr(cls, 'READS', []) or [])),
                'default': cls.defaultLanguage(engine), 'fixed': not codes}

    def getEngineLanguages(self):
        """
        The requested languages (``getLanguage()``: any spelling) in this engine's own format: normalised to
        canonical codes, restricted to what the current engine variant accepts (``'auto'`` is dropped when the
        engine cannot detect languages), falling back to ``defaultLanguage()`` when nothing usable is left, then
        translated with ``toEngineLanguage``.

        :return: list[str]
        """
        engine = self.getEngine()
        supported = self.getLanguages(engine)
        wanted = _languages.normalize(self.getLanguage())
        if supported:
            usable = [c for c in wanted if c in supported]
            if not usable:
                usable = [self.defaultLanguage(engine)]
        else:
            usable = [c for c in wanted if c != LANG_AUTO] or [LANG_AUTO]
        return [self.toEngineLanguage(c, engine) for c in usable]

    def getEngine(self):
        """
        :return: int
        """
        return self.__m_engine

    def setEngine(self, engine):
        """
        :param engine: int
        :return:
        """
        self.__m_engine = int(engine)  # type: int

    def isOnline(self):
        """
        :return: bool
        """
        return self.__m_online

    def setOnline(self, online):
        """
        :param online: bool
        :return:
        """
        self.__m_online = bool(online)  # type: bool

    def getEndpoint(self):
        """
        :return: str | unicode
        """
        return self.__m_endpoint

    def setEndpoint(self, endpoint):
        """
        :param endpoint: str | unicode
        :return:
        """
        self.__m_endpoint = endpoint  # type: str

    def getPayload(self):
        """
        :return: dict
        """
        return self.__m_payload

    def setPayload(self, payload):
        """
        :param payload: dict
        :return:
        """
        self.__m_payload = payload

    def getApi(self):
        """
        :return: str | unicode
        """
        return self.__m_api

    def setApi(self, api):
        """
        :param api: str | unicode
        """
        self.__m_api = api  # type: str

    def getProxy(self):
        """
        :return: dict[str, str]
        """
        return self.__m_proxy

    def setProxy(self, proxy):
        """
        :param proxy: dict[str, str]
        """
        self.__m_proxy = proxy  # type: dict[str, str]

    def getTimeout(self):
        """
        :return: int
        """
        return self.__m_timeout

    def setTimeout(self, timeout):
        """
        :param timeout: int
        :return:
        """
        self.__m_timeout = int(timeout)  # type: int

    def getRetries(self):
        """
        :return: int
        """
        return self.__m_retries

    def setRetries(self, retries):
        """
        :param retries: int
        :return:
        """
        self.__m_retries = max(1, int(retries))  # type: int

    def getProxyList(self):
        """
        :return: list[str | unicode]
        """
        return self.__m_proxyList

    def setProxyList(self, proxyList):
        """
        :param proxyList: list[str | unicode]
        :return:
        """
        self.__m_proxyList = proxyList  # type: list[str]

    def getApiList(self):
        """
        :return: list[str | unicode]
        """
        return self.__m_apiList

    def setApiList(self, apiList):
        """
        :param apiList: list[str | unicode]
        :return:
        """
        self.__m_apiList = apiList  # type: list[str]

    def getLastError(self):
        """
        :return: str | unicode
        """
        return self.__m_lastError

    def setLastError(self, error):
        """
        :param error: str | unicode
        :return:
        """
        self.__m_lastError = error  # type: str

    def getModel(self):
        """
        :return: str | unicode
        """
        return self.__m_model

    def setModel(self, model):
        """
        :param model: str | unicode
        :return:
        """
        self.__m_model = model  # type: str

    def composePrompt(self, prompt):
        """
        Append the extra prompt (``prompt=`` / ``setExtraPrompt``) to an
        engine's built-in instructions. VLM engines call this right before
        sending the request, so a runtime ``setExtraPrompt`` takes effect on
        the next ``parse``. Classic OCR APIs have no prompt and never call it.
        :param prompt: (str) the engine's built-in OCR instructions.
        :return: str
        """
        extra = (self.getExtraPrompt() or '').strip()  # type: str
        hint = self.languageHint() if getattr(self, 'LANGUAGE_HINT', False) else ''
        if hint:
            extra = (extra + ' ' + hint).strip() if extra else hint
        if not extra:
            return prompt
        return ('{}\n\nAdditional instructions (the output format above '
                'still applies): {}'.format(prompt, extra))

    def languageHint(self):
        """
        :return: str  a sentence telling a vision-language model which language(s) the document is in (from the
                 canonical language setting; empty for ``'auto'``), e.g. "The text is in Arabic and French."
        """
        names = [_languages.name(c) for c in _languages.normalize(self.getLanguage()) if c != _languages.AUTO]
        if not names:
            return ''
        joined = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
        return 'The text is in {}: read it in that language and do not translate it.'.format(joined)

    def getExtraPrompt(self):
        """
        :return: str | unicode
        """
        return self.__m_extraPrompt

    def setExtraPrompt(self, prompt):
        """
        :param prompt: str | unicode
        :return:
        """
        self.__m_extraPrompt = prompt  # type: str

    image = property(fget=getImage, fset=setImage)
    language = property(fget=getLanguage, fset=setLanguage)
    engine = property(fget=getEngine, fset=setEngine)
    endpoint = property(fget=getEndpoint, fset=setEndpoint)
    payload = property(fget=getPayload, fset=setPayload)
    api = property(fget=getApi, fset=setApi)
    proxy = property(fget=getProxy, fset=setProxy)
    timeout = property(fget=getTimeout, fset=setTimeout)
    retries = property(fget=getRetries, fset=setRetries)
    proxyList = property(fget=getProxyList, fset=setProxyList)
    apiList = property(fget=getApiList, fset=setApiList)
    lastError = property(fget=getLastError, fset=setLastError)
    model = property(fget=getModel, fset=setModel)
    extraPrompt = property(fget=getExtraPrompt, fset=setExtraPrompt)


class LanguageHintPlugin(object):
    MULTI_LANGUAGE = True  # "The text is in English, French and Arabic"

    """
    Mixin for vision-language engines: they detect the language themselves (``'auto'``), and a chosen language is sent
    to the model as a hint in the prompt (see ``OCRPlugin.languageHint``), which helps on ambiguous scripts. List it
    before ``OCRPlugin`` in the bases: ``class GeminiOcr(LanguageHintPlugin, OCRPlugin)``.
    """
    LANGUAGE_HINT = True

    @classmethod
    def getLanguages(cls, engine=None):
        """:return: list[str]  ``'auto'`` first, then every known language (as a hint)"""
        return [LANG_AUTO] + sorted(_languages.LANGUAGES)

    @classmethod
    def toEngineLanguage(cls, code, engine=None):
        """:return: str  the language name, as it appears in the prompt hint"""
        return LANG_AUTO if code == LANG_AUTO else _languages.name(code)

