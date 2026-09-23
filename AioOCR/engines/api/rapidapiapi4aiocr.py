# coding=utf-8
"""
API4AI OCR plugin (RapidAPI 'ocr43' API,
https://rapidapi.com/api4ai-api4ai-default/api/ocr43).
API4AI's OCR endpoint on the RapidAPI marketplace::
    POST https://ocr43.p.rapidapi.com/v1/results?algo=simple-words
    x-rapidapi-key: <your RapidAPI key>
    multipart: image=<file>      (or form field url=<public URL>)
Setup:
1. Sign up at https://rapidapi.com, open the API's page and press
   "Subscribe to Test" (free Basic plan available).
2. Pass your key as ``api=`` or set the RAPIDAPI_KEY environment
   variable (shared with the other RapidAPI plugins).
The response (confirmed from a live playground test) is::
    {"results": [{"status": {"code": "ok"}, "width": W, "height": H,
      "entities": [{"kind": "objects", "name": "words", "objects": [
        {"box": [x, y, w, h],       # FRACTIONS of the image
         "entities": [{"kind": "text", "text": "Jane"}]}, ...]}]}]}
WORD-level geometry, and -- unusually nice -- the reply carries the
real image width/height, so boxes are scaled to TRUE pixels even for
URL sources. Everything lands in the exact unified structure shared
by every plugin.
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

_ENDPOINT = 'https://ocr43.p.rapidapi.com/v1/results'


class Api4AiOcr(OCRPlugin):
    """
    Api4AiOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: your RapidAPI key (or ``api=``, or the RAPIDAPI_KEY environment variable). You must be
                       SUBSCRIBED to the ocr43 API on rapidapi.com (free Basic plan works).
        :param algo: OCR algorithm query parameter (default
                     'simple-words': word-level boxes, the confirmed mode; API4AI also documents e.g. 'simple-text').
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('RAPIDAPI_KEY', ''))
        self.__m_algo = kwargs.pop('algo', 'simple-words')
        kwargs.setdefault('endpoint', _ENDPOINT)
        kwargs.setdefault('timeout', 60)
        super(Api4AiOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Request building                                                   #
    # ------------------------------------------------------------------ #
    def _host(self):
        """
        Host header derived from the endpoint URL.
        """
        return self.getEndpoint().split('//', 1)[-1].split('/', 1)[0]

    def _requestOnce(self):
        request_kwargs = {
            'params': {'algo': self.__m_algo} if self.__m_algo else {},
            'headers': {
                'x-rapidapi-key': self.getApi(),
                'x-rapidapi-host': self._host(),
            },
            'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        kind = self.imageKind()
        if kind == 'url':
            return post(self.getEndpoint(), data={'url': self.getImage()}, **request_kwargs)
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
            upload = ('image.jpg', data, 'image/jpeg')
        elif data[:5] == b'%PDF-':
            raise OCRError(
                'The ocr43 API takes images, not PDFs. Convert the '
                'page to an image first, or use a PDF-capable plugin (MistralOcr, GlmOcr, OlmOcr).')
        else:
            upload = ('image.png', data, 'image/png')
        return post(self.getEndpoint(), files={'image': upload}, **request_kwargs)

    # ------------------------------------------------------------------ #
    # Response mapping                                                   #
    # ------------------------------------------------------------------ #
    def _wordsFromResult(self, result, y_offset):
        """
        One results[] entry -> unified word dicts (real pixels).
        """
        width = float(result.get('width') or 1000)
        height = float(result.get('height') or 1000)
        words = []
        for entity in result.get('entities') or []:
            if entity.get('kind') != 'objects':
                continue
            for obj in entity.get('objects') or []:
                box = obj.get('box') or []
                if len(box) != 4:
                    continue
                text = ''
                for inner in obj.get('entities') or []:
                    if inner.get('kind') == 'text':
                        text = str(inner.get('text', '')).strip()
                        break
                if not text:
                    continue
                try:
                    x, y, w, h = (float(v) for v in box)
                except (TypeError, ValueError):
                    continue
                if w <= 0 or h <= 0:
                    continue
                words.append(self.makeWord(
                    text, x * width, y_offset + y * height,
                    max(w * width, 1.0), max(h * height, 1.0)))
        return words, height

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (WORD-level boxes in true pixels; the base class
                 groups them into lines geometrically).
        """
        if not self.getApi():
            raise OCRError(
                'No RapidAPI key. Sign up at https://rapidapi.com, subscribe to the ocr43 API (free Basic plan), and '
                'pass api=... or set the RAPIDAPI_KEY environment variable.')
        reply = self._requestOnce()
        if reply.status_code in (401, 403):
            raise OCRError(
                'RapidAPI rejected the request (HTTP {}). Check the '
                'key AND that you are SUBSCRIBED to the ocr43 API on '
                'rapidapi.com (each API needs its own subscription; '
                'the free plan counts). API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if reply.status_code == 429:
            raise OCRError(
                'RapidAPI quota/rate limit hit (Basic plans have '
                'small monthly quotas; check the pricing tab). API said: ' + (reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from ocr43: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        try:
            payload = reply.json()
        except ValueError:
            raise OCRError('ocr43 returned a non-JSON reply: ' + (reply.text or '')[:200])
        results = (payload or {}).get('results') or []
        if not results:
            raise OCRError('ocr43 returned no results: ' + str(payload)[:200])
        words = []
        y_offset = 0.0
        for result in results:
            status = result.get('status') or {}
            if str(status.get('code', 'ok')).lower() != 'ok':
                raise OCRError('ocr43 reported failure: {} ({})'.format(
                    status.get('message', ''), status.get('code')))
            result_words, height = self._wordsFromResult(result, y_offset)
            words.extend(result_words)
            y_offset += height
        if not words:
            raise OCRError('ocr43 found no text in this image.')
        return words
