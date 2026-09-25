# coding=utf-8
"""
ScanDocFlow OCR plugin (app.scandocflow.com REST API).
ScanDocFlow does OCR plus ML/NLP key-field extraction (invoices,
checks, tax forms...). Setup:
1. Register at https://app.scandocflow.com (Google/Facebook/email).
2. In the app open the "Api Keys" tab and press "+" to generate an
   Access Token (copy it immediately -- it is shown only once).
3. Freemium: up to 50 pages per month; paid averages ~$0.05/page.
The endpoint is ``POST {base}/documents/extract?access_token=...``
(multipart file upload, or JSON with public file URLs). For
``type='ocr'`` (default) the reply carries WORD-level geometry:
``documents[].textAnnotation.Pages[].Words[]`` where each word has
``Text``, ``Confidence``, ``Lang`` and an ``Outline`` of 8 corner
coordinates normalized to the page (width and height taken as 1).
This plugin rescales outlines to REAL pixels when the source is a
local raster image (dimensions known); for PDFs and URLs -- where the
page pixel size is unknown -- coordinates are scaled onto a 1000x1000
per-page grid (relative geometry stays correct) and pages are stacked
vertically. Everything lands in the exact unified structure shared by
every plugin, with ``plainTextBase64`` as the text-only fallback.
Privacy note: ScanDocFlow's own default STORES your documents in
their web app ("retain"). This plugin defaults to ``retain=False``.
"""
from base64 import b64decode
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

#: Default endpoint; override with endpoint=... if yours differs (the
#: docs reference it as {{baseUrl}}/documents/extract).
_ENDPOINT = 'https://backend.scandocflow.com/v1/api/documents/extract'
#: Supported document types per the API docs.
_TYPES = ('ocr', 'ocr.eng', 'xtract', 'financial', 'check', 'acord25', 'taxform.us')
#: Map common ISO-639-1 codes to the API's ISO-639-3 codes.
_LANG_MAP = {'en': 'eng', 'fr': 'fra', 'de': 'deu', 'es': 'spa',
             'it': 'ita', 'pt': 'por', 'nl': 'nld', 'ru': 'rus',
             'ar': 'ara', 'zh': 'chi', 'ja': 'jpn', 'ko': 'kor',
             'tr': 'tur', 'pl': 'pol', 'sv': 'swe', 'da': 'dan',
             'no': 'nor', 'fi': 'fin', 'el': 'ell', 'he': 'heb',
             'uk': 'ukr', 'cs': 'ces', 'auto': 'auto'}
#: Per-page coordinate grid when true pixel dimensions are unknown.
_GRID = 1000.0


class ScanDocFlow(OCRPlugin):
    """
    ScanDocFlow class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: Access Token from the app's "Api Keys" tab
                       (or ``api=``, or the SCANDOCFLOW_ACCESS_TOKEN environment variable).
        :param type: document type (default 'ocr'; see _TYPES --
                     'xtract'/'financial'/'check' etc. add key-field
                     extraction, but this plugin maps the OCR words either way).
        :param language: ISO code ('en', 'fr'...) or the API's 639-3 code ('eng', 'fra'...); default 'auto'.
        :param pageCount: optional number of pages to process.
        :param pageOffset: optional 0-based first page.
        :param retain: keep the document stored in the ScanDocFlow web
                       app (default False -- privacy first; their own default is true).
        :param minConfidence: drop words below this confidence, 0-1 (default 0).
        :param image: image/PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('SCANDOCFLOW_ACCESS_TOKEN', ''))
        docType = str(kwargs.pop('type', 'ocr'))
        if docType not in _TYPES:
            raise OCRError('Unknown type {!r}; choose from {}'.format(docType, ', '.join(_TYPES)))
        self.__m_type = docType
        language = kwargs.pop('language', 'auto')
        if isinstance(language, (list, tuple)):
            language = language[0] if language else 'auto'
        self.setLanguage(_LANG_MAP.get(str(language).lower(), str(language)))
        self.__m_page_count = kwargs.pop('pageCount', None)
        self.__m_page_offset = kwargs.pop('pageOffset', None)
        self.__m_retain = bool(kwargs.pop('retain', False))
        self.__m_minConfidence = float(kwargs.pop('minConfidence', 0.0))
        kwargs.setdefault('timeout', 120)  # ML extraction takes a bit
        kwargs.setdefault('endpoint', _ENDPOINT)
        super(ScanDocFlow, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Request building                                                   #
    # ------------------------------------------------------------------ #
    def _formFields(self):
        fields = {'type': self.__m_type, 'lang': self.getLanguage(), 'retain': 'true' if self.__m_retain else 'false'}
        if self.__m_page_count is not None:
            fields['pageCount'] = str(int(self.__m_page_count))
        if self.__m_page_offset is not None:
            fields['pageOffset'] = str(int(self.__m_page_offset))
        return fields

    def _requestOnce(self):
        """
        One extract call; returns parsed JSON.
        """
        request_kwargs = {'params': {'access_token': self.getApi()}, 'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        kind = self.imageKind()
        size = None
        if kind == 'url':
            body = dict(self._formFields())
            body['files'] = [{'url': self.getImage()}]
            reply = post(self.getEndpoint(), json=body, **request_kwargs)
        else:
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
                    "Image source '{}' is not an existing file, URL, "
                    "or supported type. Check the path (the current "
                    "working directory matters for relative paths).".format(self.getImage()))
            if len(data) > 50 * 1024 * 1024:
                raise OCRError('Files exceed ScanDocFlow\'s 50 MB per-request limit.')
            if data[:5] == b'%PDF-':
                name, mime = 'document.pdf', 'application/pdf'
            elif data[:3] == b'\xff\xd8\xff':
                name, mime = 'image.jpg', 'image/jpeg'
                size = self._rasterSize(data)
            else:
                name, mime = 'image.png', 'image/png'
                size = self._rasterSize(data)
            reply = post(
                self.getEndpoint(), files={'files': (name, data, mime)}, data=self._formFields(), **request_kwargs)
        if reply.status_code in (401, 403):
            raise OCRError(
                'ScanDocFlow rejected the Access Token (HTTP {}). '
                'Generate one in the "Api Keys" tab at '
                'https://app.scandocflow.com. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if reply.status_code in (402, 429):
            raise OCRError('ScanDocFlow quota/rate limit hit (freemium allows 50 pages/month). API said: ' + (
                    reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from ScanDocFlow: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        return reply.json(), size

    @staticmethod
    def _rasterSize(data):
        """
        (width, height) of a raster image, or None.
        """
        try:
            from PIL.Image import open
            from io import BytesIO
            with open(BytesIO(data)) as image:
                return image.size
        except Exception:  # noqa: BLE001 - size is best-effort
            return None

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    def _wordsFromAnnotation(self, annotation, size):
        """
        textAnnotation.Pages[].Words[] -> unified word dicts.
        """
        width, height = size if size else (_GRID, _GRID)
        words = []
        yOffset = 0.0
        for page in (annotation or {}).get('Pages') or []:
            for word in page.get('Words') or []:
                text = str(word.get('Text', '')).strip()
                if not text:
                    continue
                confidence = word.get('Confidence')
                if confidence is not None and float(confidence) < self.__m_minConfidence:
                    continue
                outline = word.get('Outline') or []
                if len(outline) < 8:
                    continue
                xs = [float(outline[i]) * width for i in range(0, 8, 2)]
                ys = [float(outline[i]) * height for i in range(1, 8, 2)]
                left, top = min(xs), min(ys)
                boxWidth, boxHeight = max(xs) - left, max(ys) - top
                if boxWidth <= 0 or boxHeight <= 0:
                    continue
                words.append(self.makeWord(text, left, yOffset + top, boxWidth, boxHeight))
            yOffset += height
        return words

    @classmethod
    def _plainTextRows(cls, encoded):
        """
        plainTextBase64 fallback -> ordered rows.
        """
        try:
            text = b64decode(encoded or '').decode('utf-8', 'replace')
        except Exception:  # noqa: BLE001
            return []
        words = []
        row = 0
        for line in text.splitlines():
            line = sub(r'\s+', ' ', line).strip()
            if not line:
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (WORD-level boxes; the base class groups into lines).
        """
        if not self.getApi():
            raise OCRError(
                'No ScanDocFlow Access Token. Register at '
                'https://app.scandocflow.com, generate a token in the '
                '"Api Keys" tab, and pass api=... or set the '
                'SCANDOCFLOW_ACCESS_TOKEN environment variable.')
        payload, size = self._requestOnce()
        if str(payload.get('status', '')).lower() not in ('success', 'ok'):
            raise OCRError('ScanDocFlow returned status {!r}: {}'.format(payload.get('status'), str(payload)[:250]))
        documents = payload.get('documents') or []
        words = []
        for document in documents:
            words.extend(self._wordsFromAnnotation(document.get('textAnnotation'), size))
        if not words:
            for document in documents:
                words.extend(self._plainTextRows(document.get('plainTextBase64')))
        if not words:
            raise OCRError('ScanDocFlow returned no text for this document.')
        return words
