# coding=utf-8
"""
RapidAPI OCR plugin (default: the 'OCR - Extract text' API,
https://rapidapi.com/irrors-apis/api/ocr-extract-text).
RapidAPI is an API marketplace: one account key (x-rapidapi-key)
works across every API you subscribe to, each living at
``<slug>.p.rapidapi.com``. Setup:
1. Sign up at https://rapidapi.com and open the API's page.
2. Press "Subscribe to Test" and pick the free Basic plan.
3. Copy your key from any code snippet in the playground and pass it
   as ``api=`` (or set the RAPIDAPI_KEY environment variable).
RapidAPI playgrounds are JavaScript-only, so the exact endpoint path
and upload field of a given OCR API cannot always be known upfront.
This plugin therefore fixes what IS certain (host + auth headers) and
auto-discovers the rest: it probes the common path/field combinations
(``/`` , ``/ocr``, ``/extract``... with ``image``/``file`` uploads or
a ``url`` form field) until one answers, then caches the working
combination. If your API's playground shows a specific path or
parameter name, skip the probing by passing ``path='/whatever'`` and
``param='image'`` directly.
The response mapper is equally universal: word arrays with bounding
boxes (``bounding_box`` x1/y1/x2/y2 or left/top/right/bottom) become
REAL pixel geometry; otherwise the extracted text (``text``,
``fullText``, ``body.fullText``, ``result``, ``extracted_text``,
``body.pages[].fullText``...) becomes ordered rows. Either way the
result has the exact unified structure shared by every plugin.
Bonus: point ``host=`` at any other RapidAPI OCR service (e.g.
'ocr-wizard.p.rapidapi.com') and the same plugin drives it.
"""
from os.path import dirname
from requests import post
from os import environ
from sys import path
from re import sub

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_DEFAULT_HOST = 'ocr-extract-text.p.rapidapi.com'
#: (path, upload-field) combinations probed in order.
_PROBE_PATHS = ('/', '/ocr', '/extract', '/api/ocr', '/image-to-text')
_PROBE_FIELDS = ('image', 'file')
#: Dict keys that may carry the extracted text, tried in order.
_TEXT_KEYS = ('fullText', 'full_text', 'text', 'extracted_text', 'extractedText', 'ParsedText', 'ocr_text', 'result',
              'content', 'output')


class RapidApiOcr(OCRPlugin):
    """
    RapidApiOcr class.
    """
    #: host -> (path, field_or_None-for-url-mode) that worked.
    _discovered = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: your RapidAPI key (or ``api=``, or the RAPIDAPI_KEY environment variable). You must be
                       SUBSCRIBED to the API on rapidapi.com (free Basic plan works).
        :param host: RapidAPI host (default 'ocr-extract-text.p.rapidapi.com';
                      point it at any other OCR API on the marketplace).
        :param path: exact endpoint path if you know it (skips probing), e.g. '/ocr'.
        :param param: upload field name if you know it (e.g. 'image').
        :param minConfidence: drop boxed words below this confidence, 0-1, when the API provides one.
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('RAPIDAPI_KEY', ''))
        self.__m_host = kwargs.pop('host', _DEFAULT_HOST)
        self.__m_path = kwargs.pop('path', None)
        self.__m_param = kwargs.pop('param', None)
        self.__m_minConfidence = float(kwargs.pop('minConfidence', 0.0))
        kwargs.setdefault('timeout', 60)
        super(RapidApiOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)
        self.setEndpoint('https://' + self.__m_host)

    # ------------------------------------------------------------------ #
    # Request building                                                   #
    # ------------------------------------------------------------------ #
    def _headers(self):
        return {'x-rapidapi-key': self.getApi(), 'x-rapidapi-host': self.__m_host}

    def _imageData(self):
        """
        (bytes, filename, mime) for upload; None if source is URL.
        """
        kind = self.imageKind()
        if kind == 'url':
            return None
        if kind == 'array':
            from io import BytesIO
            from PIL import Image
            buffer = BytesIO()
            Image.fromarray(self.getImage()).save(buffer, format='PNG')
            data = buffer.getvalue()
        elif kind in ('path', 'pil', 'bytes', 'buffer'):
            data = self.imageBytes()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(self.getImage()))
        if data[:3] == b'\xff\xd8\xff':
            return data, 'image.jpg', 'image/jpeg'
        if data[:5] == b'%PDF-':
            return data, 'document.pdf', 'application/pdf'
        return data, 'image.png', 'image/png'

    def _attempts(self):
        """
        Yield (path, field) combos: pinned, cached, then probes.
        """
        if self.__m_path is not None:
            yield self.__m_path, self.__m_param or 'image'
            return
        cached = RapidApiOcr._discovered.get(self.__m_host)
        if cached:
            yield cached
        for path in _PROBE_PATHS:
            for field in ((self.__m_param,) if self.__m_param else _PROBE_FIELDS):
                combo = (path, field)
                if combo != cached:
                    yield combo

    def _call(self, path, field, upload):
        """
        One POST; upload=None means url-mode form field.
        """
        requestKwargs = {'headers': self._headers(), 'timeout': self.getTimeout()}
        if self.getProxy():
            requestKwargs['proxies'] = self.getProxy()
        url = 'https://{}{}'.format(self.__m_host, path)
        if upload is None:
            return post(url, data={'url': self.getImage(), 'image_url': self.getImage()}, **requestKwargs)
        data, name, mime = upload
        return post(url, files={field: (name, data, mime)}, **requestKwargs)

    # ------------------------------------------------------------------ #
    # Response mapping                                                   #
    # ------------------------------------------------------------------ #
    def _fractionalWords(self, paragraphs, size):
        """
        The 'OCR - Extract text' dialect (confirmed from a live
        playground response): paragraphs[].words[] where each word has
        ``boxCoordinates: [left, top, width, height]`` as FRACTIONS of
        the image. Scaled to real pixels when the source dimensions
        are known, else onto a 1000x1000 grid.
        """
        width, height = size if size else (1000.0, 1000.0)
        words = []
        for paragraph in paragraphs or []:
            if not isinstance(paragraph, dict):
                continue
            entries = paragraph.get('words')
            if not entries:  # degrade to the paragraph-level box
                entries = [paragraph]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = str(entry.get('text', '')).strip()
                box = entry.get('boxCoordinates') or []
                if not text or len(box) != 4:
                    continue
                try:
                    x, y, w, h = (float(v) for v in box)
                except (TypeError, ValueError):
                    continue
                if w <= 0 or h <= 0:
                    continue
                words.append(self.makeWord(
                    text, x * width, y * height,
                    max(w * width, 1.0), max(h * height, 1.0)))
        return words

    def _boxedWords(self, entries):
        """
        Word arrays with boxes -> real-geometry word dicts.
        """
        words = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get('text', entry.get('word', ''))).strip()
            if not text:
                continue
            confidence = entry.get('confidence', entry.get('rate'))
            if confidence is not None:
                try:
                    if float(confidence) < self.__m_minConfidence:
                        continue
                except (TypeError, ValueError):
                    pass
            box = entry.get('bounding_box', entry.get('bbox', entry.get('boundingBox'))) or entry
            try:
                if all(k in box for k in ('x1', 'y1', 'x2', 'y2')):
                    x1, y1 = float(box['x1']), float(box['y1'])
                    x2, y2 = float(box['x2']), float(box['y2'])
                elif all(k in box for k in ('left', 'top', 'right', 'bottom')):
                    x1, y1 = float(box['left']), float(box['top'])
                    x2, y2 = float(box['right']), float(box['bottom'])
                elif all(k in box for k in ('left', 'top', 'width', 'height')):
                    x1, y1 = float(box['left']), float(box['top'])
                    x2 = x1 + float(box['width'])
                    y2 = y1 + float(box['height'])
                else:
                    continue
            except (TypeError, ValueError):
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            words.append(self.makeWord(text, x1, y1, x2 - x1, y2 - y1))
        return words

    @classmethod
    def _textToRows(cls, text, start_row=0):
        words = []
        row = start_row
        for line in (text or '').splitlines():
            line = sub(r'\s+', ' ', line).strip()
            if not line:
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    def _mapPayload(self, payload, size=None):
        """
        Universal extractor across RapidAPI OCR response dialects.
        """
        if isinstance(payload, list):
            words = self._boxedWords(payload)
            if words:
                return words
            texts = [str(e) for e in payload if isinstance(e, str)]
            return self._textToRows('\n'.join(texts))
        if isinstance(payload, str):
            return self._textToRows(payload)
        if not isinstance(payload, dict):
            return []
        if payload.get('status') is False:
            raise OCRError('The API reported failure: {}'.format(
                str(payload.get('message') or payload.get('error') or payload)[:250]))
        # Confirmed 'OCR - Extract text' dialect: fractional word boxes.
        words = self._fractionalWords(payload.get('paragraphs'), size)
        if words:
            return words
        # Boxed word arrays under common keys (top level or in body/data).
        for container in (payload, payload.get('body') or {}, payload.get('data') or {}):
            if not isinstance(container, dict):
                continue
            for key in ('words', 'annotations', 'detections', 'items'):
                words = self._boxedWords(container.get(key))
                if words:
                    return words
        # Text under common keys.
        for container in (payload, payload.get('body') or {}, payload.get('data') or {}):
            if not isinstance(container, dict):
                continue
            for key in _TEXT_KEYS:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return self._textToRows(value)
            pages = container.get('pages')
            if isinstance(pages, list):
                texts = []
                for page in pages:
                    if isinstance(page, dict):
                        for key in _TEXT_KEYS:
                            value = page.get(key)
                            if isinstance(value, str) and value.strip():
                                texts.append(value)
                                break
                if texts:
                    return self._textToRows('\n'.join(texts))
        return []

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No RapidAPI key. Sign up at https://rapidapi.com, '
                'subscribe to the API (free Basic plan), and pass '
                'api=... or set the RAPIDAPI_KEY environment variable.')
        upload = self._imageData()
        size = None
        if upload is not None and upload[2] in ('image/jpeg', 'image/png'):
            try:
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(upload[0])) as opened:
                    size = opened.size
            except Exception:  # noqa: BLE001 - fall back to grid
                size = None
        lastError = None
        for path, field in self._attempts():
            try:
                reply = self._call(path, field, upload)
            except Exception as exc:  # noqa: BLE001 - keep probing
                lastError = str(exc)
                continue
            if reply.status_code in (401, 403):
                raise OCRError(
                    'RapidAPI rejected the request (HTTP {}). Check '
                    'the key AND that you are SUBSCRIBED to this API '
                    'on rapidapi.com (each API needs its own '
                    'subscription, the free plan counts). API said: '
                    '{}'.format(reply.status_code, (reply.text or '')[:200]))
            if reply.status_code == 429:
                raise OCRError(
                    'RapidAPI quota/rate limit hit (Basic plans have '
                    'small monthly quotas; check the pricing tab). '
                    'API said: ' + (reply.text or '')[:200])
            if reply.status_code in (404, 405):
                lastError = 'HTTP {} at {}'.format(reply.status_code, path)
                continue  # wrong path guess: keep probing
            if not reply.ok:
                lastError = 'HTTP {} at {}: {}'.format(reply.status_code, path, (reply.text or '')[:150])
                continue
            try:
                payload = reply.json()
            except ValueError:
                payload = reply.text
            try:
                words = self._mapPayload(payload, size)
            except OCRError as exc:
                # e.g. status:false ("no image provided"): could be a
                # wrong field guess, so keep probing; the message
                # surfaces in the final error if nothing works.
                lastError = str(exc)
                continue
            if words:
                RapidApiOcr._discovered[self.__m_host] = (path, field)
                return words
            lastError = 'unrecognized reply at {}: {}'.format(path, str(payload)[:150])
        raise OCRError(
            'Could not get OCR text from {}. Last error: {}. Open the '
            "API's playground on rapidapi.com, check the endpoint "
            'path and upload field in its code snippet, and pass them '
            "as path='/...' and param='...' .".format(self.__m_host, lastError))
