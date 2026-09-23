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
        self.__m_language = kwargs.pop('language', ['en'])
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
        if not extra:
            return prompt
        return ('{}\n\nAdditional instructions (the output format above '
                'still applies): {}'.format(prompt, extra))

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
