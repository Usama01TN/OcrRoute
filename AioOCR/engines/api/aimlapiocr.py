# coding=utf-8
"""
AI/ML API OCR plugin (api.aimlapi.com/v1/ocr).
AIMLAPI is a model aggregator: one API key gives access to hundreds of
models, including Mistral's OCR line through a dedicated OCR route::
    POST https://api.aimlapi.com/v1/ocr
    Authorization: Bearer <key>
    {"model": "mistral-ocr-latest", "document": {"type": "image_url", "image_url": "..."}}
Setup: create a key at https://aimlapi.com/app/keys (new accounts get
a small free allowance; paid plans unlock volume). Limits per the API
schema: 50 MB per file, up to 1,000 pages.
Available models: 'mistral-ocr-latest' (default), 'mistral-ocr-2512',
'mistral-ocr-3'. The reply carries one markdown string per page plus
page dimensions; the hosted generation returns NO text coordinates
(bounding boxes exist only for extracted figures/images), so reading
order is preserved with synthesized row boxes in the same unified
structure as every other plugin -- if you need true text geometry
from Mistral, use the direct ``mistralocr.py`` plugin whose OCR 4
``include_blocks`` feature provides it.
Local files, PIL images and raw bytes are sent inline as base64 data
URIs; public URLs pass through untouched.
"""
from base64 import b64encode
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

_ENDPOINT = 'https://api.aimlapi.com/v1/ocr'
_MODELS = ('mistral-ocr-latest', 'mistral-ocr-2512', 'mistral-ocr-3')


class AimlApiOcr(OCRPlugin):
    """
    AimlApiOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: AIMLAPI key (or ``api=``, or the AIMLAPI_API_KEY environment variable).
        :param model: OCR model id (default 'mistral-ocr-latest'; see _MODELS).
        :param pages: optional list of 0-based page indices, or a string like '0,2-4' (PDFs).
        :param docType: force 'document' or 'image' routing when the URL auto-detection guesses wrong.
        :param includeImages: request extracted images as base64
                                (default False; ignored by the unified structure anyway).
        :param imageLimit: max images to extract (optional).
        :param imageMinSize: min side of images to extract (optional).
        :param image: image/PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('AIMLAPI_API_KEY', ''))
        model = kwargs.pop('model', 'mistral-ocr-latest')
        if model not in _MODELS:
            raise OCRError('Unknown model {!r}; choose from {}'.format(model, ', '.join(_MODELS)))
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_docType = kwargs.pop('docType', None)
        self.__m_includeImages = bool(kwargs.pop('includeImages', False))
        self.__m_imageLimit = kwargs.pop('imageLimit', None)
        self.__m_imageMinSize = kwargs.pop('imageMinSize', None)
        kwargs.setdefault('timeout', 120)
        kwargs.setdefault('endpoint', _ENDPOINT)
        super(AimlApiOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Document building.                                                 #
    # ------------------------------------------------------------------ #
    def _document(self):
        kind = self.imageKind()
        source = self.getImage()
        if kind == 'url':
            is_pdf = source.lower().split('?')[0].endswith('.pdf')
            if self.__m_docType:
                is_pdf = self.__m_docType == 'document'
            if is_pdf:
                return {'type': 'document_url', 'document_url': source}
            return {'type': 'image_url', 'image_url': source}
        if kind == 'array':
            from io import BytesIO
            from PIL import Image
            buffer = BytesIO()
            Image.fromarray(source).save(buffer, format='PNG')
            data = buffer.getvalue()
        elif kind in ('path', 'pil', 'bytes', 'buffer'):
            data = self.imageBytes()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(source))
        if len(data) > 50 * 1024 * 1024:
            raise OCRError("File exceeds AIMLAPI's 50 MB limit.")
        encoded = b64encode(data).decode('ascii')
        if data[:5] == b'%PDF-':
            return {'type': 'document_url', 'document_url': 'data:application/pdf;base64,' + encoded}
        mime = ('image/jpeg' if data[:3] == b'\xff\xd8\xff' else 'image/png')
        return {'type': 'image_url', 'image_url': 'data:{};base64,{}'.format(mime, encoded)}

    # ------------------------------------------------------------------ #
    # Result mapping                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _field(obj, *names):
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            value = getattr(obj, name, None)
            if value is not None:
                return value
        return None

    @classmethod
    def _markdownToRows(cls, markdown, start_row=0):
        words = []
        row = start_row
        for line in (markdown or '').splitlines():
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)
            line = sub(r'\|', ' ', line).strip()
            if not line or set(line) <= set('-: '):
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No AIMLAPI key. Create one at '
                'https://aimlapi.com/app/keys and pass api=... or set the AIMLAPI_API_KEY environment variable.')
        body = {'model': self.getModel(), 'document': self._document()}
        if self.__m_pages is not None:
            body['pages'] = (list(self.__m_pages) if isinstance(self.__m_pages, (list, tuple)) else self.__m_pages)
        if self.__m_includeImages:
            body['include_image_base64'] = True
        if self.__m_imageLimit is not None:
            body['image_limit'] = int(self.__m_imageLimit)
        if self.__m_imageMinSize is not None:
            body['image_min_size'] = int(self.__m_imageMinSize)
        request_kwargs = {
            'json': body,
            'headers': {
                'Authorization': 'Bearer {}'.format(self.getApi()),
                'Content-Type': 'application/json',
            },
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code in (401, 403):
            raise OCRError(
                'AIMLAPI rejected the key (HTTP {}): check it at '
                'https://aimlapi.com/app/keys. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if reply.status_code == 402:
            raise OCRError('AIMLAPI says the account is out of credits: top up at https://aimlapi.com. API said: ' + (
                    reply.text or '')[:200])
        if reply.status_code == 429:
            raise OCRError('AIMLAPI rate limit hit: ' + (reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from AIMLAPI: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        error = payload.get('error') if isinstance(payload, dict) else None
        if error:
            raise OCRError('AIMLAPI error: {}'.format(str(error)[:300]))
        pages = self._field(payload, 'pages') or []
        pages = sorted(pages, key=lambda p: self._field(p, 'index') or 0)
        words = []
        for page in pages:
            markdown = self._field(page, 'markdown') or ''
            words.extend(self._markdownToRows(markdown, start_row=len(words)))
        if not words:
            raise OCRError('AIMLAPI OCR returned no text for this document.')
        return words
