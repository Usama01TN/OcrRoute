# coding=utf-8
"""
Google Gemini AI-OCR plugin (free tier available).
Uses the Gemini API's vision capability as an OCR engine. The free
tier from Google AI Studio needs no credit card: create a key at
https://aistudio.google.com/apikey and pass it as ``api=`` (or set
the GEMINI_API_KEY environment variable).
The plugin asks Gemini for a strict JSON array of text items with
``box_2d`` coordinates ([ymin, xmin, ymax, xmax], normalized to a
0-1000 grid -- Gemini's native detection format), rescales them to
real pixels, and feeds them to the base class, so Gemini returns the
exact same result structure as every other plugin. If the model
replies with plain text instead of JSON, reading order is preserved
with synthesized row boxes.
No SDK required: plain ``requests`` against the REST endpoint.
"""
from re import findall, search, sub
from requests import post, get
from base64 import b64encode
from os.path import dirname
from json import loads
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

_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
#: Gemini box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
_OCR_PROMPT = (
    'Perform OCR on this image. Return ONLY a JSON array (no markdown '
    'fences, no commentary). Each element must be: {"text": "<one text '
    'line exactly as written>", "box_2d": [ymin, xmin, ymax, xmax]} '
    'with coordinates normalized to 0-1000. Include every piece of '
    'visible text, one element per text line, in reading order. Ignore '
    'icons, logos and graphics that contain no text.'
)


class GeminiOcr(OCRPlugin):
    """
    GeminiOcr class.
    """
    #: Learned model replacements (old -> new), shared by all instances
    #: so the deprecated-model dance happens at most once per process.
    _model_upgrades = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: Google AI Studio API key (or ``api=``, or the GEMINI_API_KEY / GOOGLE_API_KEY env variables).
        :param model: Gemini model id (default 'gemini-2.5-flash';
                      'gemini-2.5-flash-lite' is faster/cheaper, 'gemini-2.5-pro' most accurate).
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature (default 0 for deterministic OCR).
        :param kwargs: other settings (timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('GEMINI_API_KEY', '') or environ.get('GOOGLE_API_KEY', ''))
        model = kwargs.pop('model', 'gemini-2.5-flash')
        # Apply any replacement learned earlier in this process.
        model = GeminiOcr._model_upgrades.get(model, model)
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = float(kwargs.pop('temperature', 0))
        kwargs.setdefault('timeout', 60)  # vision calls can be slow
        super(GeminiOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setModel(model)
        self.setApi(api)
        self.setEndpoint(_ENDPOINT.format(model=model))

    # ------------------------------------------------------------------ #
    # Model resolution (self-healing on deprecations)                    #
    # ------------------------------------------------------------------ #
    def _switchModel(self, new_model):
        """
        Switch to *new_model*, remember the upgrade process-wide.
        """
        GeminiOcr._model_upgrades[self.getModel()] = new_model
        self.setModel(new_model)
        self.setEndpoint(_ENDPOINT.format(model=new_model))

    def _suggestedReplacement(self, error_text):
        """
        Deprecation 404s name the replacement directly, e.g.
        "...no longer available... Please update your code to use
        models/gemini-3.6-flash...". Extract the first model mentioned
        that differs from the current one.
        """
        for name in findall(r'models/([A-Za-z0-9.\-_]+)', error_text or ''):
            if name != self.getModel():
                return name
        return None

    def _discoverModel(self):
        """
        Fallback: ask the ListModels endpoint for a current 'flash'
        model that supports generateContent. Returns a name or None.
        """
        try:
            reply = get('https://generativelanguage.googleapis.com/v1beta/models',
                        headers={'x-goog-api-key': self.getApi()}, timeout=self.getTimeout())
            if not reply.ok:
                return None
            models = reply.json().get('models') or []
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return None
        candidates = []
        for entry in models:
            name = (entry.get('name') or '').split('/')[-1]
            methods = entry.get('supportedGenerationMethods') or []
            if 'generateContent' in methods and 'flash' in name and 'image' not in name and 'live' not in name and 'tts' not in name:
                candidates.append(name)
        if not candidates:
            return None

        # Prefer the highest version number, plain flash over -lite.
        def sort_key(name):
            version = search(r'(\d+(?:\.\d+)?)', name)
            return float(version.group(1)) if version else 0.0, '-lite' not in name

        return sorted(candidates, key=sort_key)[-1]

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _imagePayloadAndSize(self):
        """
        Return ((mime, base64_data), (width, height)).
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
        return (mime, b64encode(data).decode('ascii')), size

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    @classmethod
    def _parseItems(cls, text, width, height):
        """
        Parse the model's JSON array into word dicts (pixel coords).
        """
        # Strip Markdown fences the model sometimes adds despite the
        # instructions, then find the outermost JSON array.
        cleaned = sub(r'```(?:json)?|```', '', text or '').strip()
        start, end = cleaned.find('['), cleaned.rfind(']')
        if start < 0 or end <= start:
            return []
        try:
            items = loads(cleaned[start:end + 1])
        except ValueError:
            return []
        words = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get('text', '')).strip()
            box = item.get('box_2d') or item.get('box') or []
            if not label or len(box) != 4:
                continue
            try:
                ymin, xmin, ymax, xmax = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            left = xmin / _COORD_SPACE * width
            top = ymin / _COORD_SPACE * height
            words.append(cls.makeWord(
                label, left, top,
                max((xmax - xmin) / _COORD_SPACE * width, 1.0),
                max((ymax - ymin) / _COORD_SPACE * height, 1.0),
            ))
        return words

    @classmethod
    def _plainToRows(cls, text):
        """
        Plain-text fallback: synthetic row boxes keep reading order.
        """
        words = []
        row = 0
        for line in (text or '').splitlines():
            line = line.strip()
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
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No Gemini API key. Create a free one at '
                'https://aistudio.google.com/apikey and pass api=... '
                'or set the GEMINI_API_KEY environment variable.')
        (mime, data), (width, height) = self._imagePayloadAndSize()
        body = {
            'contents': [{'parts': [
                {'text': self.composePrompt(self.__m_prompt)},
                {'inline_data': {'mime_type': mime, 'data': data}},
            ]}],
            'generationConfig': {'temperature': self.__m_temperature},
        }
        request_kwargs = {'json': body, 'timeout': self.getTimeout(), 'headers': {'x-goog-api-key': self.getApi()}}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code == 404:
            # Model deprecated/renamed: the error usually names the
            # replacement; otherwise discover one via ListModels. Retry
            # ONCE with the new model.
            errorText = reply.text or ''
            replacement = (self._suggestedReplacement(errorText) or self._discoverModel())
            if replacement:
                self._switchModel(replacement)
                reply = post(self.getEndpoint(), **request_kwargs)
            if reply.status_code == 404:
                raise OCRError(
                    "Gemini model '{}' not found and no working "
                    "replacement discovered. Pass model=... with a "
                    "current model id (see https://ai.google.dev/"
                    "gemini-api/docs/models). API said: {}".format(self.getModel(), errorText[:300].strip()))
        if reply.status_code == 429:
            raise OCRError(
                'Gemini rate limit hit (free tier is per-minute and per-day limited): ' + (reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from Gemini: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        candidates = payload.get('candidates') or []
        if not candidates:
            raise OCRError('Empty Gemini response: {}'.format(str(payload)[:200]))
        parts = (candidates[0].get('content') or {}).get('parts') or []
        text = ''.join(p.get('text', '') for p in parts)
        words = self._parseItems(text, width, height)
        if not words:
            words = self._plainToRows(text)
        return words
