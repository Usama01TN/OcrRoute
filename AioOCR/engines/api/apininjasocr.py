# coding=utf-8
"""
API Ninjas Image-to-Text OCR plugin
(https://api-ninjas.com/api/imagetotext).
API Ninjas is an API marketplace; its Image to Text endpoint does OCR
with word-level bounding boxes::
    POST https://api.api-ninjas.com/v1/imagetotext
    X-Api-Key: <key>
    multipart: image=<JPEG or PNG file>
Setup: sign up free at https://api-ninjas.com to get an API key
instantly, then pass it as ``api=`` or set the API_NINJAS_API_KEY
environment variable.
Know the limits going in: the endpoint accepts ONLY JPEG/PNG (this
plugin auto-converts other raster formats and rejects PDFs), free
accounts can upload images up to 200 KB each (5 MB on premium), and
commercial use requires a premium subscription.
The reply is a JSON array of detections::
    [{"text": "API", "bounding_box": {"x1": 60, "y1": 72, "x2": 163, "y2": 118}}, ...]
-- word-level PIXEL coordinates (OCR.Space granularity), mapped
straight into the exact unified structure shared by every plugin.
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

_ENDPOINT = 'https://api.api-ninjas.com/v1/imagetotext'
_FREE_LIMIT = 200 * 1024  # 200 KB on the free tier.
_PREMIUM_LIMIT = 5 * 1024 * 1024  # 5 MB on premium.


class ApiNinjasOcr(OCRPlugin):
    """
    ApiNinjasOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: API Ninjas key (or ``api=``, or the API_NINJAS_API_KEY environment variable).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('API_NINJAS_API_KEY', ''))
        kwargs.setdefault('endpoint', _ENDPOINT)
        kwargs.setdefault('timeout', 60)
        super(ApiNinjasOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Request building                                                   #
    # ------------------------------------------------------------------ #
    def _fileTuple(self):
        """
        Multipart 'image' as JPEG/PNG (the only accepted formats).
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
        if data[:5] == b'%PDF-':
            raise OCRError(
                'API Ninjas imagetotext accepts only JPEG/PNG images, '
                'not PDFs. Convert the page to an image first, or use '
                'a PDF-capable plugin (MistralOcr, GlmOcr, OlmOcr).')
        if data[:3] == b'\xff\xd8\xff':
            name, mime = 'image.jpg', 'image/jpeg'
        elif data[:8] == b'\x89PNG\r\n\x1a\n':
            name, mime = 'image.png', 'image/png'
        else:
            # Other raster formats (webp, bmp, gif...): convert to PNG.
            from io import BytesIO
            from PIL import Image
            buffer = BytesIO()
            Image.open(BytesIO(data)).convert('RGB').save(buffer, format='PNG')
            data = buffer.getvalue()
            name, mime = 'image.png', 'image/png'
        if len(data) > _PREMIUM_LIMIT:
            raise OCRError(
                'Image is {:.1f} MB but API Ninjas accepts at most '
                '5 MB (premium) / 200 KB (free tier). Downscale or recompress it first.'.format(len(data) / 1048576.0))
        return name, data, mime

    # ------------------------------------------------------------------ #
    # Result mapping                                                     #
    # ------------------------------------------------------------------ #
    def _wordsFromReply(self, payload):
        """
        [{text, bounding_box:{x1,y1,x2,y2}}] -> unified word dicts.
        Coordinates are coerced with float(): sister endpoints have
        been seen returning them as strings.
        """
        words = []
        for entry in payload if isinstance(payload, list) else []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get('text', '')).strip()
            if not text:
                continue
            box = entry.get('bounding_box') or {}
            try:
                x1 = float(box['x1'])
                y1 = float(box['y1'])
                x2 = float(box['x2'])
                y2 = float(box['y2'])
            except (KeyError, TypeError, ValueError):
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            words.append(self.makeWord(
                text, x1, y1, x2 - x1, y2 - y1))
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble (WORD-level pixel boxes; the base class groups them
                 into lines geometrically).
        """
        if not self.getApi():
            raise OCRError(
                'No API Ninjas key. Sign up free at https://api-ninjas.com to get one instantly, then '
                'pass api=... or set the API_NINJAS_API_KEY environment variable.')
        name, data, mime = self._fileTuple()
        request_kwargs = {'files': {'image': (name, data, mime)}, 'headers': {'X-Api-Key': self.getApi()},
                          'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code in (401, 403):
            raise OCRError(
                'API Ninjas rejected the key (HTTP {}): check it in '
                'your account at https://api-ninjas.com. API said: {}'.format(
                    reply.status_code, (reply.text or '')[:200]))
        if reply.status_code == 400 and len(data) > _FREE_LIMIT:
            raise OCRError(
                'API Ninjas rejected the upload (HTTP 400). The image '
                'is {:.0f} KB; free accounts are limited to 200 KB '
                'per image (5 MB on premium) -- recompress/downscale '
                'it or upgrade. API said: {}'.format(len(data) / 1024.0, (reply.text or '')[:200]))
        if reply.status_code == 429:
            raise OCRError('API Ninjas rate limit hit: ' + (reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from API Ninjas: {}'.format(
                reply.status_code, (reply.text or '')[:300].strip()))
        try:
            payload = reply.json()
        except ValueError:
            raise OCRError('API Ninjas returned a non-JSON reply: ' + (reply.text or '')[:200])
        if isinstance(payload, dict) and payload.get('error'):
            raise OCRError('API Ninjas error: {}'.format(str(payload['error'])[:250]))
        words = self._wordsFromReply(payload)
        if not words:
            raise OCRError('API Ninjas found no text in this image.')
        return words
