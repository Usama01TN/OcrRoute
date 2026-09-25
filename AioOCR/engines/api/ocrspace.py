# coding=utf-8
"""
OCR.Space online OCR plugin.

Improvements over the original:
- No infinite ``while True`` loop: retries are bounded.
- Uses the configured ``timeout`` and ``proxy`` on every request.
- API-key rotation via ``apiList`` when the quota is exhausted.
- A string image that is neither a URL, an existing file, nor valid
  base64 fails IMMEDIATELY with a clear message (previously it was sent
  as garbage base64 and the API answered ``400 Bad Request``).
- Only real API options go into the payload: base-class kwargs such as
  ``image`` or ``timeout`` are no longer leaked as form fields.
- HTTP errors include the response body so the real reason is visible.
"""
from binascii import Error as Base64Error
from base64 import b64decode
from os.path import dirname
from requests import post
from os import environ
from io import BytesIO
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

#: Constructor kwargs consumed by OCRPlugin/OcrSpace themselves: they
#: must never be forwarded to the OCR.Space API payload.
_RESERVED = frozenset(('endpoint', 'image', 'language', 'engine', 'online', 'payload', 'apiList', 'api', 'apikey',
                       'proxyList', 'proxy', 'timeout', 'retries', 'lastError'))


class SourceError(OCRError):
    """
    Local image-source problem: retrying cannot help.
    """


class OcrSpace(OCRPlugin):
    """
    OcrSpace class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: API key string (``api`` also accepted; pass several via apiList for automatic rotation).
        :param language: document language ('eng', 'fre', ...).
        :param engine: OCR.Space engine number (default 2).
        :param kwargs: extra payload settings forwarded to the API (isTable, detectOrientation, scale, ...).
        """
        api = kwargs.pop('api', environ.get('OCR_SPACE_API', 'helloworld'))
        language = kwargs.pop('language', 'eng')
        engine = kwargs.pop('engine', 2)
        # API payload extras = whatever is left that is not reserved.
        extras = {k: v for k, v in kwargs.items() if k not in _RESERVED}
        for key in extras:
            kwargs.pop(key)
        super(OcrSpace, self).__init__(*args, **kwargs)
        self.setOnline(True)
        if not self.getApiList():
            self.setApiList([api])
        self.setApi(self.getApiList()[0])
        self.setEngine(engine)
        self.setEndpoint('https://api.ocr.space/parse/image')
        payload = {
            'detectOrientation': True,
            'scale': True,
            'isTable': True,
            'isOverlayRequired': True,
            'language': language,
            'OCREngine': self.getEngine(),
        }
        payload.update(extras)
        self.setPayload(payload)

    def _rotateKey(self):
        """
        Switch to the next API key in apiList (wraps around).
        """
        keys = self.getApiList()
        if len(keys) > 1:
            self.setApi(keys[(keys.index(self.getApi()) + 1) % len(keys)])

    @staticmethod
    def _validBase64(text):
        """
        Return True when *text* decodes as base64 image data.
        """
        payload = text.split(',', 1)[-1]  # drop a data:...;base64, prefix
        if len(payload) < 16:
            return False
        try:
            b64decode(payload, validate=True)
            return True
        except (Base64Error, ValueError):
            return False

    def _request(self):
        """
        Send one request to OCR.Space and return the JSON reply.
        """
        data = dict(self.getPayload())
        data['apikey'] = self.getApi()
        kwargs = {'timeout': self.getTimeout(), 'data': data}
        if self.getProxy():
            kwargs['proxies'] = self.getProxy()
        kind = self.imageKind()
        source = self.getImage()
        if kind == 'url':
            data['url'] = source
            response = post(self.getEndpoint(), **kwargs)
        elif kind == 'path':
            with open(source, 'rb') as handle:
                response = post(self.getEndpoint(), files={'file': handle}, **kwargs)
        elif kind in ('pil', 'bytes', 'buffer'):
            buffer = BytesIO(self.imageBytes())
            response = post(self.getEndpoint(), files={'file': ('image.png', buffer, 'image/png')}, **kwargs)
        elif isinstance(source, str):
            # Only send string sources that really look like base64;
            # anything else is almost certainly a mistyped/missing path.
            if not self._validBase64(source):
                raise SourceError(
                    "Image source '{}' is not an existing file, a URL, "
                    "or valid base64 data. Check the path (current "
                    "working directory matters for relative paths).".format(
                        source if len(source) < 120 else source[:120]))
            if not source.startswith('data:'):
                source = 'data:image/png;base64,' + source
            data['base64Image'] = source
            response = post(self.getEndpoint(), **kwargs)
        else:
            raise SourceError('Unsupported image source type: {}'.format(kind))
        if not response.ok:
            # Surface OCR.Space's own explanation instead of a bare
            # "400 Client Error".
            raise OCRError('HTTP {} from OCR.Space: {}'.format(
                response.status_code, (response.text or '')[:300].strip()))
        return response.json()

    def _run(self, image, *args, **kwargs):
        """
        Not used directly: OCR.Space already returns the unified shape,
        so ``parse`` is overridden below instead of building from words.
        """
        raise NotImplementedError

    def parse(self, *args, **kwargs):
        """
        Process an image from a local path, base64, bytes, or URL.
        :return: (dict) OCR.Space result *normalized* to the exact same
                 structure as every other plugin, or an errored result
                 dict (FileParseExitCode == -1) after all retries.
        """
        from time import sleep
        self.setLastError('')
        delay = 0.5
        lastExc = None
        for attempt in range(1, self.getRetries() + 1):
            try:
                raw = self._request()
                if isinstance(raw, str):
                    # API returned a plain error string (e.g. quota).
                    self._rotateKey()
                    raise OCRError(raw)
                if raw.get('IsErroredOnProcessing'):
                    messages = raw.get('ErrorMessage') or ['Unknown error']
                    if isinstance(messages, str):
                        messages = [messages]
                    raise OCRError('; '.join(messages))
                parsed = raw.get('ParsedResults') or []
                if not parsed:
                    raise OCRError('Empty ParsedResults.')
                # Enforce the unified structure shared by all plugins.
                return self.normalizeResult(parsed[0])
            except SourceError as exc:
                # Local problem (missing file, bad type): retrying is
                # pointless, fail fast with a clear message.
                lastExc = exc
                self.setLastError('{}: {}'.format(type(exc).__name__, exc))
                break
            except Exception as exc:  # noqa: BLE001
                lastExc = exc
                self.setLastError('{}: {}'.format(type(exc).__name__, exc))
                if attempt < self.getRetries():
                    sleep(delay)
                    delay *= 2
        return self.errorResult(lastExc)
