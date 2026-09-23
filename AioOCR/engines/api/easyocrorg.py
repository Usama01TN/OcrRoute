# coding=utf-8
"""
easyocr.org hosted OCR plugin.
easyocr.org is a hosted OCR web service (NOT the EasyOCR Python
library that ``easy.py`` wraps -- no install, no models, no GPU).
It has two API generations, both taking a multipart ``file`` upload:
- **Console endpoint** (current, recommended)::
      POST https://console.easyocr.org/api/ocr
      X-Access-Key: eocr_...
  Create the key at https://console.easyocr.org (free tier included).
- **Legacy keyless endpoint** -- no registration, no key::
      POST https://api.easyocr.org/ocr           (international)
      POST https://cn-api.easyocr.org/ocr        (China, faster there)
This plugin uses the console endpoint when an access key is given and
falls back to the legacy keyless endpoint otherwise (handy for quick
tests; the service states images are deleted right after processing,
but as with any keyless third-party API, avoid sensitive documents).
The reply is delightfully direct::
    {"words": [{"text": "...", "left": 53.9, "top": 79.7, "right": 508.6, "bottom": 171.1, "rate": 0.998}]}
-- PIXEL coordinates and per-entry confidence, mapped straight into
the exact unified structure shared by every plugin.
"""
from os.path import dirname
from requests import post
from os import environ
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_CONSOLE_ENDPOINT = 'https://console.easyocr.org/api/ocr'
_LEGACY_ENDPOINTS = {'global': 'https://api.easyocr.org/ocr', 'cn': 'https://cn-api.easyocr.org/ocr'}


class EasyOcrOrg(OCRPlugin):
    """
    EasyOcrOrg class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: console Access Key ('eocr_...'; or ``api=``, or the EASYOCR_ORG_ACCESS_KEY environment variable).
                        WITHOUT a key the legacy keyless endpoint is used.
        :param region: 'global' (default) or 'cn' -- picks the legacy endpoint location (keyless mode only).
        :param minConfidence: drop entries below this 'rate', 0-1 (default 0).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('EASYOCR_ORG_ACCESS_KEY', ''))
        region = str(kwargs.pop('region', 'global')).lower()
        self.__m_min_confidence = float(kwargs.pop('minConfidence', 0.0))
        default_endpoint = (_CONSOLE_ENDPOINT if api else _LEGACY_ENDPOINTS.get(region, _LEGACY_ENDPOINTS['global']))
        kwargs.setdefault('endpoint', default_endpoint)
        kwargs.setdefault('timeout', 60)
        super(EasyOcrOrg, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Request building                                                   #
    # ------------------------------------------------------------------ #
    def _fileTuple(self):
        """
        Build the multipart 'file' from any image source.
        """
        kind = self.imageKind()
        if kind == 'url':
            from requests import get
            reply = get(self.getImage(), timeout=self.getTimeout())
            reply.raise_for_status()
            data = reply.content
        elif kind == 'array':
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
            return 'image.jpg', data, 'image/jpeg'
        if data[:5] == b'%PDF-':
            return 'document.pdf', data, 'application/pdf'
        return 'image.png', data, 'image/png'

    # ------------------------------------------------------------------ #
    # Result mapping                                                     #
    # ------------------------------------------------------------------ #
    def _wordsFromReply(self, payload):
        """
        {'words': [{text,left,top,right,bottom,rate}]} -> words.
        """
        entries = payload.get('words') if isinstance(payload, dict) else None
        if entries is None and isinstance(payload, dict):
            # Be lenient with envelope variants: {'data': {'words': ...}}
            data = payload.get('data')
            if isinstance(data, dict):
                entries = data.get('words')
        words = []
        for entry in entries or []:
            text = entry.get('text', '').strip()
            if not text:
                continue
            rate = entry.get('rate', entry.get('confidence'))
            if rate is not None and float(rate) < self.__m_min_confidence:
                continue
            try:
                left = float(entry['left'])
                top = float(entry['top'])
                right = float(entry['right'])
                bottom = float(entry['bottom'])
            except (KeyError, TypeError, ValueError):
                continue
            if right <= left or bottom <= top:
                continue
            words.append(self.makeWord(text, left, top, right - left, bottom - top))
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (entries are text lines/words with PIXEL boxes; the base class groups them geometrically).
        """
        headers = {}
        if self.getApi():
            headers['X-Access-Key'] = self.getApi()
        request_kwargs = {'files': {'file': self._fileTuple()}, 'headers': headers, 'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code in (401, 403):
            raise OCRError(
                'easyocr.org rejected the request (HTTP {}). If you '
                'use the console endpoint, check the X-Access-Key from '
                'https://console.easyocr.org; without a key the plugin '
                'uses the legacy keyless endpoint instead. API said: '
                '{}'.format(reply.status_code, (reply.text or '')[:200]))
        if reply.status_code == 429:
            raise OCRError('easyocr.org rate limit hit: '
                           + (reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from easyocr.org: {}'.format(
                reply.status_code, (reply.text or '')[:300].strip()))
        try:
            payload = reply.json()
        except ValueError:
            raise OCRError('easyocr.org returned a non-JSON reply: ' + (reply.text or '')[:200])
        if isinstance(payload, dict) and payload.get('error'):
            raise OCRError('easyocr.org error: {}'.format(str(payload['error'])[:250]))
        words = self._wordsFromReply(payload)
        if not words:
            raise OCRError('easyocr.org found no text in this image (reply: {}).'.format(str(payload)[:150]))
        return words
