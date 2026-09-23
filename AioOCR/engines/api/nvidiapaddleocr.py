# coding=utf-8
"""
NVIDIA NIM PaddleOCR plugin
(https://build.nvidia.com/baidu/paddleocr).
Baidu's PaddleOCR served as an NVIDIA NIM microservice: GPU-
accelerated detection + recognition returning text WITH BOUNDING
BOXES. Two ways to run it, both supported here:
1. HOSTED on NVIDIA's cloud (default) -- sign in at
   https://build.nvidia.com/baidu/paddleocr, click "Get API Key"
   (an ``nvapi-...`` key, free trial credits included)::
       POST https://ai.api.nvidia.com/v1/cv/baidu/paddleocr
       Authorization: Bearer nvapi-...
2. SELF-HOSTED NIM container on your own GPU (no key needed for the
   inference call)::
       docker run --gpus all -p 8000:8000 -e NGC_API_KEY=$NGC_API_KEY nvcr.io/nim/baidu/paddleocr:latest
   then ``NvidiaPaddleOcr(image=..., base='http://localhost:8000')``,
   which posts to ``<base>/v1/infer``.
Request format (per NVIDIA's API reference): a JSON body with an
``input`` array of ``{'type': 'image_url', 'url': 'data:image/
<png|jpeg>;base64,<data>'}`` entries. Only PNG and JPEG are
supported, so other formats are converted locally; PDFs are not
accepted (use a document engine).
LARGE IMAGES: NVIDIA's hosted endpoints cap inline base64 payloads,
so images above ``assetLimit`` bytes are uploaded through the NVCF
asset API (POST https://api.nvcf.nvidia.com/v2/nvcf/assets, PUT to
the returned pre-signed URL) and referenced as
``data:image/png;asset_id,<id>`` with the NVCF-INPUT-ASSET-REFERENCES
header -- automatic, and also used as the fallback on HTTP 413.
Geometry: each detection carries a quadrilateral of float [0, 1]
points, which the plugin scales by the local image dimensions to REAL
PIXEL boxes (line/word tier, like RapidOcr or OCR.Space -- not
approximate VLM boxes). ``minConfidence`` filters on the model's own
confidence scores. Everything lands in the exact unified structure
shared by every plugin (like OCR.Space).
"""
from requests import get, post, put
from base64 import b64encode
from os.path import dirname
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

#: Hosted NVIDIA endpoint (full URL -- no /v1/infer suffix).
_HOSTED_URL = 'https://ai.api.nvidia.com/v1/cv/baidu/paddleocr'
#: NVCF asset API for large uploads.
_ASSET_URL = 'https://api.nvcf.nvidia.com/v2/nvcf/assets'
#: Inline base64 payloads above this many bytes go through assets.
_ASSET_LIMIT = 180000


class NvidiaPaddleOcr(OCRPlugin):
    """
    NvidiaPaddleOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: NVIDIA key 'nvapi-...' (or ``api=``, or the
                       NVIDIA_API_KEY / NGC_API_KEY environment variables). Not needed for a local NIM.
        :param base: base URL of a self-hosted NIM (e.g.
                     'http://localhost:8000'); the plugin posts to
                     '<base>/v1/infer'. Default: NVIDIA's hosted endpoint.
        :param minConfidence: drop detections below this confidence, 0-1 (default 0).
        :param assetLimit: inline-payload byte limit before the NVCF asset upload is used (default 180000).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('NVIDIA_API_KEY', '') or environ.get('NGC_API_KEY', ''))
        base = kwargs.pop('base', None)
        self.__m_endpoint = kwargs.pop('endpoint', None) or (str(base).rstrip('/') + '/v1/infer' if base else _HOSTED_URL)
        self.__m_local = bool(base)
        self.__m_minConfidence = float(kwargs.pop('minConfidence', 0.0))
        self.__m_assetLimit = int(kwargs.pop('assetLimit', _ASSET_LIMIT))
        kwargs.setdefault('timeout', 120)
        super(NvidiaPaddleOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Headers                                                            #
    # ------------------------------------------------------------------ #
    def _headers(self, asset_id=None):
        headers = {'Content-Type': 'application/json', 'accept': 'application/json'}
        if self.getApi():
            headers['Authorization'] = 'Bearer {}'.format(self.getApi())
        if asset_id:
            headers['NVCF-INPUT-ASSET-REFERENCES'] = asset_id
        return headers

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _imageData(self):
        """
        (png/jpeg bytes, mime, (width, height)).
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'url':
            reply = get(self.getImage(), timeout=self.getTimeout())
            reply.raise_for_status()
            data = reply.content
        elif kind == 'array':
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
                'The PaddleOCR NIM takes PNG or JPEG images, not '
                'PDFs. Rasterize the pages first, or use a '
                'PDF-capable plugin (MistralOcr, ClaudeOcr, '
                'QianfanOcr, UnlimitedOcr).')
        if data[:3] == b'\xff\xd8\xff':
            mime = 'image/jpeg'
        elif data[:8] == b'\x89PNG\r\n\x1a\n':
            mime = 'image/png'
        else:  # NIM accepts png/jpeg only: convert whatever this is
            with Image.open(BytesIO(data)) as opened:
                converted = BytesIO()
                opened.convert('RGB').save(converted, format='PNG')
                data, mime = converted.getvalue(), 'image/png'
        with Image.open(BytesIO(data)) as opened:
            size = opened.size
        return data, mime, size

    # ------------------------------------------------------------------ #
    # NVCF asset upload (large images)                                   #
    # ------------------------------------------------------------------ #
    def _uploadAsset(self, data, mime):
        """
        Upload bytes to NVCF; returns the asset id.
        """
        if not self.getApi():
            raise OCRError(
                'This image needs the NVCF asset upload, which '
                'requires an NVIDIA API key. Get one at '
                'https://build.nvidia.com/baidu/paddleocr, or resize '
                'the image, or run a local NIM (base=...).')
        reply = post(
            _ASSET_URL,
            json={'contentType': mime, 'description': 'ocr-input'},
            headers={'Authorization': 'Bearer {}'.format(self.getApi()),
                     'Content-Type': 'application/json', 'accept': 'application/json'},
            timeout=self.getTimeout())
        if not reply.ok:
            raise OCRError(
                'NVCF asset registration failed (HTTP {}): {}'.format(
                    reply.status_code, (reply.text or '')[:200]))
        payload = reply.json()
        upload_url = payload.get('uploadUrl')
        asset_id = str(payload.get('assetId') or '')
        if not upload_url or not asset_id:
            raise OCRError('NVCF asset response missing uploadUrl or assetId: {}'.format(str(payload)[:200]))
        uploaded = put(
            upload_url, data=data,
            headers={'x-amz-meta-nvcf-asset-description': 'ocr-input', 'content-type': mime}, timeout=self.getTimeout())
        if not uploaded.ok:
            raise OCRError('NVCF asset upload failed (HTTP {}).'.format(uploaded.status_code))
        return asset_id

    # ------------------------------------------------------------------ #
    # Requests                                                           #
    # ------------------------------------------------------------------ #
    def _request(self, url_value, asset_id=None):
        body = {'input': [{'type': 'image_url', 'url': url_value}]}
        request_kwargs = {'json': body, 'headers': self._headers(asset_id), 'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        return post(self.__m_endpoint, **request_kwargs)

    # ------------------------------------------------------------------ #
    # Response mapping                                                   #
    # ------------------------------------------------------------------ #
    def _wordsFromPayload(self, payload, size):
        width, height = size
        words = []
        for entry in (payload.get('data') or []):
            if not isinstance(entry, dict):
                continue
            for detection in (entry.get('text_detections') or []):
                if not isinstance(detection, dict):
                    continue
                prediction = detection.get('text_prediction') or {}
                text = str(prediction.get('text', '')).strip()
                if not text:
                    continue
                confidence = prediction.get('confidence')
                if confidence is not None:
                    try:
                        if float(confidence) < self.__m_minConfidence:
                            continue
                    except (TypeError, ValueError):
                        pass
                points = ((detection.get('bounding_box') or {}).get('points') or [])
                xs, ys = [], []
                for point in points:
                    if not isinstance(point, dict):
                        continue
                    try:
                        xs.append(float(point.get('x')))
                        ys.append(float(point.get('y')))
                    except (TypeError, ValueError):
                        continue
                if len(xs) >= 2 and len(ys) >= 2:
                    left, top = min(xs) * width, min(ys) * height
                    boxW = (max(xs) - min(xs)) * width
                    boxH = (max(ys) - min(ys)) * height
                    words.append(self.makeWord(text, left, top, max(boxW, 1.0), max(boxH, 1.0)))
                else:  # detection without geometry: keep the text
                    words.append(self.makeWord(text, 0.0, float(len(words) * 10), 1.0, 8.0))
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (REAL pixel boxes scaled from the NIM's normalized quad points).
        """
        if not self.getApi() and not self.__m_local:
            raise OCRError(
                'No NVIDIA API key. Get one at '
                'https://build.nvidia.com/baidu/paddleocr ("Get API '
                "Key\", an nvapi-... key with free credits), then "
                'pass api=... or set the NVIDIA_API_KEY '
                'environment variable -- or run the NIM container '
                "yourself and pass base='http://localhost:8000'.")
        data, mime, size = self._imageData()
        encoded = b64encode(data).decode('ascii')
        asset_id = None
        if not self.__m_local and len(encoded) > self.__m_assetLimit:
            asset_id = self._uploadAsset(data, mime)
            url_value = 'data:{};asset_id,{}'.format(mime, asset_id)
        else:
            url_value = 'data:{};base64,{}'.format(mime, encoded)
        reply = self._request(url_value, asset_id)

        if reply.status_code == 413 and not self.__m_local and asset_id is None:
            asset_id = self._uploadAsset(data, mime)
            reply = self._request('data:{};asset_id,{}'.format(mime, asset_id), asset_id)
        if reply.status_code in (401, 403):
            raise OCRError(
                'NVIDIA rejected the API key (HTTP {}). Keys look '
                "like 'nvapi-...' and come from "
                'https://build.nvidia.com/baidu/paddleocr. API said: '
                '{}'.format(reply.status_code, (reply.text or '')[:200]))
        if reply.status_code == 402:
            raise OCRError(
                'NVIDIA says the account is out of credits: check '
                'your build.nvidia.com balance or move to a '
                'self-hosted NIM. API said: '
                + (reply.text or '')[:200])
        if reply.status_code == 429:
            raise OCRError(
                'NVIDIA rate limit hit (429) -- the hosted trial '
                'endpoint is throttled. API said: ' + (reply.text or '')[:200])
        if reply.status_code == 404 and self.__m_local:
            raise OCRError(
                'No NIM at {}. Is the container running? docker run '
                '--gpus all -p 8000:8000 -e NGC_API_KEY=$NGC_API_KEY '
                'nvcr.io/nim/baidu/paddleocr:latest'.format(self.__m_endpoint))
        if reply.status_code in (500, 502, 503):
            raise OCRError(
                'The PaddleOCR NIM is having trouble (HTTP {}): '
                'retried automatically. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from the PaddleOCR NIM: {}'.format(
                reply.status_code, (reply.text or '')[:300].strip()))
        try:
            payload = reply.json()
        except ValueError:
            raise OCRError('The PaddleOCR NIM returned a non-JSON response: ' + (reply.text or '')[:200])
        if payload.get('error') or payload.get('detail'):
            raise OCRError('PaddleOCR NIM error: {}'.format(str(payload.get('error') or payload.get('detail'))[:250]))
        words = self._wordsFromPayload(payload, size)
        if not words:
            raise OCRError('The PaddleOCR NIM found no text in this image.')
        return words
