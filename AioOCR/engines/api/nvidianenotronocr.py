# coding=utf-8
"""
NVIDIA Nemotron-OCR plugin -- v1 and v2, switchable
(https://build.nvidia.com/nvidia/nemotron-ocr-v2,
https://build.nvidia.com/nvidia/nemotron-ocr-v1).
NVIDIA's own OCR family (not Baidu's PaddleOCR): a hybrid
detector-recognizer with document-level relational modeling -- a
detector for text regions, a recognizer for transcription, and a
RELATIONAL MODEL that analyses layout and READING ORDER. Built for
documents and natural-scene text alike, production-ready and
commercially usable (NVIDIA Open Model License).
MODELS (``model=``), all served through the same request/response
contract but NOT the same URL path:
    'nemotron-ocr-v2'      (default) newest; multilingual variant
                           covers English, Chinese (Simplified and
                           Traditional), Japanese, Korean, Russian.
                           Endpoint path: /v1/ocr
    'nemotron-ocr-v1'      previous generation. Path: /v1/infer
    'nemoretriever-ocr-v1' the original NeMo Retriever OCR NIM
                           (same model line, older naming).
                           Path: /v1/infer
Short aliases work too: 'v2', 'v1', 'nemoretriever', and a
'nvidia/...' prefix is accepted. The plugin sends each model to its
own documented path and auto-falls back to the other path when a
deployment answers 404 (NIM versions differ), caching what works.
Two ways to run, both supported:
1. HOSTED on NVIDIA's cloud (default) -- 'Get API Key' on the model
   page gives an ``nvapi-...`` key with free credits::
       POST https://ai.api.nvidia.com/v1/cv/nvidia/<model>
       Authorization: Bearer nvapi-...
2. SELF-HOSTED NIM container on your own GPU (no key needed)::
       docker run --gpus all -p 8000:8000 \\
           -e NGC_API_KEY=$NGC_API_KEY \\
           nvcr.io/nvidia/nemo-microservices/nemoretriever-ocr-v1:latest
   then ``NemotronOcr(image=..., base='http://localhost:8000')``.
MERGE LEVELS -- the feature that sets these NIMs apart. Requests take
``merge_levels``: 'word', 'sentence' or 'paragraph' (NVIDIA's default
is 'paragraph'). This plugin defaults to 'word', so results carry
TRUE WORD BOXES that the shared line-grouping turns into
OCR.Space-shaped lines; ``mergeLevel='sentence'`` or ``'paragraph'``
gives coarser blocks following the relational model's reading order.
Deployments that reject the field are detected and retried without it.
Geometry: each detection carries a quadrilateral of float [0, 1]
points, scaled here by the local image dimensions to REAL PIXELS
(word/line tier, like OCR.Space -- not approximate VLM boxes).
``minConfidence`` filters on the model's own scores.
Large images upload through the NVCF asset API automatically (and as
the fallback on HTTP 413). Only PNG and JPEG are accepted, so other
formats are converted locally; PDFs are not supported (use a document
engine). Everything lands in the exact unified structure shared by
every plugin (like OCR.Space).
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

#: model -> documented endpoint path.
_MODELS = {
    'nemotron-ocr-v2': '/v1/ocr',
    'nemotron-ocr-v1': '/v1/infer',
    'nemoretriever-ocr-v1': '/v1/infer',
}
#: Convenience spellings.
_ALIASES = {
    'v2': 'nemotron-ocr-v2',
    'v1': 'nemotron-ocr-v1',
    'latest': 'nemotron-ocr-v2',
    'nemotron': 'nemotron-ocr-v2',
    'nemotron-ocr': 'nemotron-ocr-v2',
    'nemoretriever': 'nemoretriever-ocr-v1',
    'nemoretriever-ocr': 'nemoretriever-ocr-v1',
}
#: Order tried when no model is pinned (hosted routes get renamed).
_MODEL_LADDER = ('nemotron-ocr-v2', 'nemotron-ocr-v1', 'nemoretriever-ocr-v1')
#: Hosted CV route prefix.
_HOSTED_PREFIX = 'https://ai.api.nvidia.com/v1/cv/nvidia/'
#: NVCF asset API for large uploads.
_ASSET_URL = 'https://api.nvcf.nvidia.com/v2/nvcf/assets'
#: Inline base64 payloads above this many bytes go through assets.
_ASSET_LIMIT = 180000
#: Supported merge levels (NVIDIA's own default is 'paragraph').
_MERGE_LEVELS = ('word', 'sentence', 'paragraph')
#: Both known paths, for the local auto-detect fallback.
_PATHS = ('/v1/ocr', '/v1/infer')


class NemotronOcr(OCRPlugin):
    """
    NemotronOcr class.
    """
    #: hosted: model -> URL that answered. local: base -> full URL.
    _working_urls = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: 'nemotron-ocr-v2' (default), 'nemotron-ocr-v1',
                      or 'nemoretriever-ocr-v1' (aliases: 'v2', 'v1', 'nemoretriever'; a 'nvidia/' prefix is fine).
        :param api: NVIDIA key 'nvapi-...' (or ``api=``, or the
                       NVIDIA_API_KEY / NGC_API_KEY environment variables). Not needed for a local NIM.
        :param base: base URL of a self-hosted NIM (e.g.
                     'http://localhost:8000'); the model's documented path is appended, with fallback to the other.
        :param mergeLevel: 'word' (default), 'sentence' or 'paragraph'.
        :param minConfidence: drop detections below this confidence, 0-1 (default 0).
        :param assetLimit: inline-payload byte limit before the NVCF asset upload is used (default 180000).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('NVIDIA_API_KEY', '') or environ.get(
            'NGC_API_KEY', ''))
        requested = kwargs.pop('model', None)
        model = self._resolveModel(requested)
        self.__m_pinned = requested is not None
        base = kwargs.pop('base', None)
        self.__m_base = base.rstrip('/') if base else None
        self.__m_local = bool(base)
        self.__m_override = kwargs.pop('endpoint', None)
        level = kwargs.pop('mergeLevel', 'word').lower()
        if level not in _MERGE_LEVELS:
            raise OCRError('mergeLevel must be one of {}, not {!r}'.format(', '.join(_MERGE_LEVELS), level))
        self.__m_merge_level = level
        self.__m_min_confidence = float(kwargs.pop('minConfidence', 0.0))
        self.__m_asset_limit = int(kwargs.pop('assetLimit', _ASSET_LIMIT))
        kwargs.setdefault('timeout', 120)
        super(NemotronOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setModel(model)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Model / endpoint resolution                                        #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolveModel(requested):
        if not requested:
            return _MODEL_LADDER[0]
        name = str(requested).strip().lower()
        if name.startswith('nvidia/'):
            name = name.split('/', 1)[1]
        name = _ALIASES.get(name, name)
        if name not in _MODELS:
            raise OCRError(
                'Unknown model {!r}. Choose from: {} (aliases: {}).'.format(
                    requested, ', '.join(sorted(_MODELS)), ', '.join(sorted(_ALIASES))))
        return name

    def _candidateUrls(self):
        """
        URLs to try, in order, for this configuration.
        """
        if self.__m_override:
            return [self.__m_override]
        if self.__m_local:
            cached = NemotronOcr._working_urls.get(self.__m_base)
            if cached:
                return [cached]
            path = _MODELS[self.getModel()]
            others = [p for p in _PATHS if p != path]
            return [self.__m_base + p for p in [path] + others]
        cached = NemotronOcr._working_urls.get(self.getModel())
        if cached:
            return [cached]
        if self.__m_pinned:  # respect an explicit model choice
            return [_HOSTED_PREFIX + self.getModel()]
        return [_HOSTED_PREFIX + name for name in _MODEL_LADDER]

    def _cacheUrl(self, url):
        key = self.__m_base if self.__m_local else self.getModel()
        NemotronOcr._working_urls[key] = url

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
                'The Nemotron-OCR NIM takes PNG or JPEG images, not '
                'PDFs. Rasterize the pages first, or use a '
                'PDF-capable plugin (MistralOcr, ClaudeOcr, QianfanOcr, UnlimitedOcr).')
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
                'https://build.nvidia.com/nvidia/{}, or resize the '
                'image, or run a local NIM (base=...).'.format(self.getModel()))
        reply = post(
            _ASSET_URL,
            json={'contentType': mime, 'description': 'ocr-input'},
            headers={'Authorization': 'Bearer {}'.format(self.getApi()),
                     'Content-Type': 'application/json', 'accept': 'application/json'},
            timeout=self.getTimeout())
        if not reply.ok:
            raise OCRError(
                'NVCF asset registration failed (HTTP {}): {}'.format(reply.status_code, (reply.text or '')[:200]))
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
    def _request(self, url, url_value, asset_id=None, with_merge=True):
        body = {'input': [{'type': 'image_url', 'url': url_value}]}
        if with_merge:
            body['merge_levels'] = [self.__m_merge_level]
        request_kwargs = {'json': body, 'headers': self._headers(asset_id), 'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        return post(url, **request_kwargs)

    def _send(self, url_value, asset_id=None):
        """
        Walk the candidate URLs; cache whichever answers.
        """
        candidates = self._candidateUrls()
        reply = None
        for index, url in enumerate(candidates):
            reply = self._request(url, url_value, asset_id)
            if reply.status_code == 404 and index < len(candidates) - 1:
                continue  # path/route differs on this deployment
            if reply.ok:
                self._cacheUrl(url)
            return reply, url
        return reply, candidates[-1]

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
                        if float(confidence) < self.__m_min_confidence:
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
                    box_w = (max(xs) - min(xs)) * width
                    box_h = (max(ys) - min(ys)) * height
                    words.append(self.makeWord(text, left, top, max(box_w, 1.0), max(box_h, 1.0)))
                else:  # detection without geometry: keep the text
                    words.append(self.makeWord(text, 0.0, float(len(words) * 10), 1.0, 8.0))
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (REAL pixel boxes scaled from the NIM's normalized quad points; word-level by default).
        """
        if not self.getApi() and not self.__m_local:
            raise OCRError(
                'No NVIDIA API key. Get one at '
                'https://build.nvidia.com/nvidia/{} ("Get API Key", '
                'an nvapi-... key with free credits), then pass '
                'api=... or set the NVIDIA_API_KEY environment '
                'variable -- or run the NIM container yourself and '
                "pass base='http://localhost:8000'.".format(self.getModel()))
        data, mime, size = self._imageData()
        encoded = b64encode(data).decode('ascii')
        asset_id = None
        if not self.__m_local and len(encoded) > self.__m_asset_limit:
            asset_id = self._uploadAsset(data, mime)
            urlValue = 'data:{};asset_id,{}'.format(mime, asset_id)
        else:
            urlValue = 'data:{};base64,{}'.format(mime, encoded)
        reply, url = self._send(urlValue, asset_id)
        # Deployments that don't know merge_levels: retry without it.
        if reply.status_code in (400, 422) and 'merge' in (reply.text or '').lower():
            reply = self._request(url, urlValue, asset_id, with_merge=False)
            if reply.ok:
                self._cacheUrl(url)
        if reply.status_code == 413 and not self.__m_local and asset_id is None:
            asset_id = self._uploadAsset(data, mime)
            reply, url = self._send('data:{};asset_id,{}'.format(mime, asset_id), asset_id)
        if reply.status_code in (401, 403):
            raise OCRError(
                'NVIDIA rejected the API key (HTTP {}). Keys look '
                "like 'nvapi-...' and come from "
                'https://build.nvidia.com/nvidia/{}. API said: {}'.format(
                    reply.status_code, self.getModel(), (reply.text or '')[:200]))
        if reply.status_code == 402:
            raise OCRError(
                'NVIDIA says the account is out of credits: check '
                'your build.nvidia.com balance or move to a '
                'self-hosted NIM. API said: ' + (reply.text or '')[:200])
        if reply.status_code == 404:
            raise OCRError(
                "No '{}' endpoint at {} (HTTP 404). Try another model "
                "(model='nemotron-ocr-v1' / 'nemoretriever-ocr-v1'), "
                'copy the URL from the model page into endpoint=..., '
                "or run a local NIM with base='http://localhost:8000'.".format(self.getModel(), url))
        if reply.status_code == 415:
            raise OCRError('The NIM rejected the content type (415); '
                           'requests must be application/json.')
        if reply.status_code == 422:
            raise OCRError(
                'The NIM could not process the request (422 -- '
                'usually a malformed data URL or bad base64). API said: ' + (reply.text or '')[:250])
        if reply.status_code == 429:
            raise OCRError(
                'Rate limited (429) by the gateway in front of the NIM. API said: ' + (reply.text or '')[:200])
        if reply.status_code == 503:
            retryAfter = ''
            try:
                header = (reply.headers or {}).get('Retry-After')
                if header:
                    retryAfter = ' Retry-After: {}s.'.format(header)
            except Exception:  # noqa: BLE001 - headers are optional.
                retryAfter = ''
            raise OCRError(
                'The Nemotron-OCR NIM is initializing or its queue is '
                'full (503).{} Retried automatically.'.format(retryAfter))
        if reply.status_code == 504:
            raise OCRError(
                'The NIM timed out processing this image (504): try a '
                'smaller image, or raise the deployment timeout (NIM_SERVER_REQUEST_TIMEOUT_S).')
        if reply.status_code in (500, 502):
            raise OCRError(
                'The Nemotron-OCR NIM is having trouble (HTTP {}): '
                'retried automatically. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from Nemotron-OCR ({}): {}'.format(
                reply.status_code, self.getModel(), (reply.text or '')[:300].strip()))
        try:
            payload = reply.json()
        except ValueError:
            raise OCRError('Nemotron-OCR returned a non-JSON response: ' + (reply.text or '')[:200])
        if payload.get('object') == 'error' or payload.get('message'):
            raise OCRError('Nemotron-OCR error: {}'.format(str(payload.get('message') or payload)[:250]))
        words = self._wordsFromPayload(payload, size)
        if not words:
            raise OCRError("Nemotron-OCR ('{}') found no text in this image.".format(self.getModel()))
        return words
