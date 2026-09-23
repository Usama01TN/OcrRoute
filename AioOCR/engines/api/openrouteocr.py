# coding=utf-8
"""
OpenRouter AI-OCR plugin (free tier available).
OpenRouter exposes many vision-language models through one
OpenAI-compatible API. Models whose id ends in ``:free`` cost $0 per
token; the free tier is request-limited (about 20 requests/minute, and
roughly 50 requests/day on an unfunded account or 1,000/day after a
one-time $10 credit purchase). Create a key at
https://openrouter.ai/settings/keys and pass it as ``api=`` (or set
the OPENROUTER_API_KEY environment variable).
By default, this plugin targets ``openrouter/free``: OpenRouter's own
router that automatically picks an available free model and filters
for the capabilities the request needs (here: image input). That makes
the plugin immune to the constant rotation of individual ``:free``
model ids. You can still pin a specific model with ``model=...``; if a
pinned model has vanished, the plugin falls back to the router, and as
a last resort discovers a current free vision model from the live
/models catalog.
The OCR prompt asks for a JSON array of text lines with ``box_2d``
coordinates on a 0-1000 grid (the same convention as the Gemini and
DeepSeek plugins). Free community models vary in grounding skill, so
the plugin degrades gracefully: JSON items without usable boxes, or
plain-text replies, still produce the unified structure with
synthesized row boxes that preserve reading order.
"""
from requests import post, get
from base64 import b64encode
from os.path import dirname
from json import loads
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

_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
_MODELS_ENDPOINT = 'https://openrouter.ai/api/v1/models'
#: OpenRouter's auto-router for free models (capability-aware).
_FREE_ROUTER = 'openrouter/free'
#: box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
_OCR_PROMPT = (
    'Perform OCR on this image. Return ONLY a JSON array (no markdown '
    'fences, no commentary). Each element must be: {"text": "<one text '
    'line exactly as written>", "box_2d": [ymin, xmin, ymax, xmax]} '
    'with coordinates normalized to 0-1000. Include every piece of '
    'visible text, one element per text line, in reading order. Ignore '
    'icons, logos and graphics that contain no text. If you cannot '
    'estimate coordinates, still return the array with the text fields '
    'in reading order.'
)


class OpenRouterOcr(OCRPlugin):
    """
    OpenRouterOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: OpenRouter API key (or ``api=``, or the OPENROUTER_API_KEY environment variable).
        :param model: model id (default 'openrouter/free',
                        the capability-aware free auto-router; or pin any vision model such as a current ':free' id).
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature (default 0).
        :param appName: optional X-Title header shown in OpenRouter usage stats.
        :param kwargs: other settings (timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('OPENROUTER_API_KEY', ''))
        model = kwargs.pop('model', _FREE_ROUTER)
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = float(kwargs.pop('temperature', 0))
        self.__m_app_name = kwargs.pop('appName', 'ocr-plugin')
        kwargs.setdefault('timeout', 90)  # free routes can be slow
        super(OpenRouterOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)
        self.setModel(model)
        self.setEndpoint(_ENDPOINT)

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _imagePayloadAndSize(self):
        """
        Return (data_url, (width, height)).
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'url':
            from requests import get
            reply = get(self.getImage(), timeout=self.getTimeout())
            reply.raise_for_status()
            data = reply.content
        elif kind in ('path', 'pil', 'bytes', 'buffer'):
            data = self.imageBytes()
        elif kind == 'array':
            buffer = BytesIO()
            Image.fromarray(self.getImage()).save(buffer, format='PNG')
            data = buffer.getvalue()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(self.getImage()))
        with Image.open(BytesIO(data)) as img:
            size = img.size
            fmt = (img.format or 'PNG').lower()
        mime = 'image/jpeg' if fmt in ('jpg', 'jpeg') else 'image/webp' if fmt == 'webp' else 'image/png'
        data_url = 'data:{};base64,{}'.format(mime, b64encode(data).decode('ascii'))
        return data_url, size

    # ------------------------------------------------------------------ #
    # Model fallback / discovery                                         #
    # ------------------------------------------------------------------ #
    def _headers(self):
        return {'Authorization': 'Bearer {}'.format(self.getApi()), 'X-Title': self.__m_app_name}

    def _discoverFreeVisionModel(self):
        """
        Last-resort: fetch the live model catalog and return the id of
        a ':free' model that accepts image input, or None.
        """
        try:
            reply = get(_MODELS_ENDPOINT, headers=self._headers(), timeout=self.getTimeout())
            if not reply.ok:
                return None
            models = reply.json().get('data') or []
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return None
        for entry in models:
            model_id = entry.get('id') or ''
            if not model_id.endswith(':free'):
                continue
            modalities = ((entry.get('architecture') or {}).get('input_modalities') or [])
            if 'image' in modalities:
                return model_id
        return None

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    @classmethod
    def _parseItems(cls, text, width, height):
        """
        Parse the model's JSON array into word dicts. Items carrying a
        usable box_2d get real pixel coordinates; if NO item has a
        usable box (weak grounding is common on free models), the item
        texts become ordered rows instead.
        """
        cleaned = sub(r'```(?:json)?|```', '', text or '').strip()
        start, end = cleaned.find('['), cleaned.rfind(']')
        if start < 0 or end <= start:
            return []
        try:
            items = loads(cleaned[start:end + 1])
        except ValueError:
            return []
        boxed, texts = [], []
        for item in items:
            if isinstance(item, str):
                if item.strip():
                    texts.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            label = str(item.get('text', '')).strip()
            if not label:
                continue
            texts.append(label)
            box = item.get('box_2d') or item.get('box') or []
            if len(box) != 4:
                continue
            try:
                ymin, xmin, ymax, xmax = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            if ymax <= ymin or xmax <= xmin:
                continue
            boxed.append(cls.makeWord(
                label,
                xmin / _COORD_SPACE * width,
                ymin / _COORD_SPACE * height,
                max((xmax - xmin) / _COORD_SPACE * width, 1.0),
                max((ymax - ymin) / _COORD_SPACE * height, 1.0),
            ))
        if boxed:
            return boxed
        return cls._rowsFromTexts(texts)

    @classmethod
    def _rowsFromTexts(cls, texts):
        """
        Synthetic stacked boxes: keep reading order without geometry.
        """
        return [cls.makeWord(text, 0.0, float(i * 10), 1.0, 8.0) for i, text in enumerate(texts)]

    @classmethod
    def _plainToRows(cls, text):
        """
        Plain-text reply: one row per non-empty line.
        """
        return cls._rowsFromTexts([line.strip() for line in (text or '').splitlines() if line.strip()])

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _request(self, model, data_url):
        """
        Send one chat-completions request for *model*.
        """
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': self.composePrompt(self.__m_prompt)},
                {'type': 'image_url', 'image_url': {'url': data_url}},
            ]}],
            'temperature': self.__m_temperature,
        }
        kwargs = {'json': body, 'headers': self._headers(), 'timeout': self.getTimeout()}
        if self.getProxy():
            kwargs['proxies'] = self.getProxy()
        return post(self.getEndpoint(), **kwargs)

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No OpenRouter API key. Create a free one at https://openrouter.ai/settings/keys and pass '
                'api=... or set the OPENROUTER_API_KEY environment variable.')
        dataUrl, (width, height) = self._imagePayloadAndSize()
        reply = self._request(self.getModel(), dataUrl)
        if reply.status_code in (400, 404) and self.getModel() != _FREE_ROUTER:
            # Pinned model gone or invalid (free ids rotate constantly):
            # fall back to the capability-aware free router, then to
            # live catalog discovery.
            fallback = _FREE_ROUTER
            retry = self._request(fallback, dataUrl)
            if retry.status_code in (400, 404):
                discovered = self._discoverFreeVisionModel()
                if discovered:
                    fallback = discovered
                    retry = self._request(fallback, dataUrl)
            if retry.ok:
                self.setModel(fallback)
                reply = retry
        if reply.status_code == 429:
            raise OCRError(
                'OpenRouter rate limit hit. Free models allow ~20 '
                'requests/minute and ~50 requests/day on an unfunded '
                'account (a one-time $10 credit purchase raises the '
                'daily cap to ~1,000). API said: ' + (reply.text or '')[:200])
        if reply.status_code == 402:
            raise OCRError(
                "OpenRouter says this request needs credits (the model "
                "'{}' is not free, or free capacity is exhausted). Use "
                "model='openrouter/free' or a current ':free' vision "
                "model id. API said: {}".format(self.getModel(), (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from OpenRouter: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('error'):
            raise OCRError('OpenRouter error: {}'.format(str(payload['error'])[:300]))
        choices = payload.get('choices') or []
        if not choices:
            raise OCRError('Empty OpenRouter response: {}'.format(str(payload)[:200]))
        content = (choices[0].get('message') or {}).get('content') or ''
        if isinstance(content, list):  # some providers return parts
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        words = self._parseItems(content, width, height)
        if not words:
            words = self._plainToRows(content)
        return words
