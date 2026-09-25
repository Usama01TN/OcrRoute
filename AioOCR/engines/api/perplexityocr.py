# coding=utf-8
"""
Perplexity OCR plugin (Sonar API vision input).
Perplexity's Sonar API is OpenAI-compatible and accepts images::
    POST https://api.perplexity.ai/chat/completions
    Authorization: Bearer <key>
Setup: generate a key in the API tab at
https://www.perplexity.ai/settings/api. The API is pay-per-token
(sonar is ~$1/M tokens); Perplexity Pro subscribers receive $5 of API
credit per month, which covers thousands of OCR calls.
Default model: 'sonar' (cheapest vision-capable). 'sonar-pro' reads
harder documents better. The reasoning variants are NOT recommended
for OCR: they emit <think> chain-of-thought and restrict image use --
if one is pinned anyway, the plugin strips think blocks and retries
with an explicit no-thinking prompt.
Perplexity models are search-grounded by default; for OCR the plugin
sends ``disable_search`` so the model just reads the image (the flag
is dropped automatically if the account/model rejects it). Images go
as base64 data URIs (up to 50 MB -- the most generous VLM limit in
the suite; PNG/JPEG/WEBP/GIF) or pass through as public HTTPS URLs.
The OCR prompt asks for a JSON array of text lines with ``box_2d``
[ymin, xmin, ymax, xmax] on a 0-1000 grid (same convention as the
Gemini/Groq/Claude/ChatGPT plugins), scaled to REAL pixels via the
local image dimensions, degrading gracefully to ordered rows. Like
all VLMs the box estimates are approximate. Everything lands in the
exact unified structure shared by every plugin (like OCR.Space).
"""
from re import sub, compile, DOTALL, IGNORECASE
from base64 import b64encode
from os.path import dirname
from requests import post
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

_ENDPOINT = 'https://api.perplexity.ai/chat/completions'
_DEFAULT_MODEL = 'sonar'
#: Known vision-capable fallbacks tried when a pinned model is
#: rejected (Perplexity has no public /models catalog endpoint).
_FALLBACK_MODELS = ('sonar', 'sonar-pro')
#: box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
#: Perplexity accepts base64 images up to 50 MB.
_IMAGE_LIMIT = 50 * 1024 * 1024
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


class PerplexityOcr(OCRPlugin):
    """
    PerplexityOcr class.
    """
    #: dead model id -> working replacement (process-wide).
    _model_upgrades = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: Perplexity API key (or ``api=``, or the PERPLEXITY_API_KEY / PPLX_API_KEY environment variables),
                        from https://www.perplexity.ai/settings/api.
        :param model: model id (default 'sonar'; 'sonar-pro' for harder documents).
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature (default 0).
        :param maxTokens: generation budget (default 4096).
        :param disableSearch: skip Perplexity's web search grounding
                              (default True -- OCR needs the image read, not searched;
                                auto-dropped if the API rejects the flag).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('PERPLEXITY_API_KEY', '') or environ.get('PPLX_API_KEY', ''))
        model = kwargs.pop('model', _DEFAULT_MODEL)
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = float(kwargs.pop('temperature', 0))
        self.__m_maxTokens = int(kwargs.pop('maxTokens', 4096))
        self.__m_disableSearch = bool(kwargs.pop('disableSearch', True))
        kwargs.setdefault('endpoint', _ENDPOINT)
        kwargs.setdefault('timeout', 120)
        super(PerplexityOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setModel(PerplexityOcr._model_upgrades.get(model, model))
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _imageUrlAndSize(self):
        """
        Return (url_or_data_url, (width, height) or None).
        """
        kind = self.imageKind()
        if kind == 'url':
            # Public HTTPS image URLs pass through; pixel size unknown
            # -> 1000-grid coordinates.
            return self.getImage(), None
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
        if data[:5] == b'%PDF-':
            raise OCRError(
                'This plugin sends images, not PDFs, to Perplexity. '
                'Convert the page to an image first, or use a '
                'PDF-capable plugin (ClaudeOcr, MistralOcr, GlmOcr, OlmOcr).')
        if len(data) > _IMAGE_LIMIT:
            raise OCRError(
                'Image is {:.1f} MB but Perplexity accepts at most '
                '50 MB per image. Downscale or recompress it first.'
                .format(len(data) / 1048576.0))
        if data[:3] == b'\xff\xd8\xff':
            mime = 'image/jpeg'
        elif data[:6] in (b'GIF87a', b'GIF89a'):
            mime = 'image/gif'
        elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            mime = 'image/webp'
        else:
            mime = 'image/png'
        size = None
        try:
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(data)) as opened:
                size = opened.size
        except Exception:  # noqa: BLE001 - size is best-effort
            size = None
        return 'data:{};base64,{}'.format(mime, b64encode(data).decode('ascii')), size

    # ------------------------------------------------------------------ #
    # Requests                                                           #
    # ------------------------------------------------------------------ #
    def _headers(self):
        return {'Authorization': 'Bearer {}'.format(self.getApi()), 'Content-Type': 'application/json'}

    def _request(self, model, image_url, prompt=None, budget=None):
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt or self.composePrompt(self.__m_prompt)},
                {'type': 'image_url', 'image_url': {'url': image_url}},
            ]}],
            'temperature': self.__m_temperature,
            'max_tokens': budget or self.__m_maxTokens,
            'stream': False,
        }
        if self.__m_disableSearch:
            body['disable_search'] = True
        request_kwargs = {'json': body, 'headers': self._headers(), 'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code == 400 and 'disable_search' in (
                reply.text or ''):
            # The account/model doesn't know the flag: drop and retry.
            body.pop('disable_search', None)
            reply = post(self.getEndpoint(), **request_kwargs)
        return reply

    @staticmethod
    def _content(payload):
        choices = payload.get('choices') or []
        if not choices:
            return ''
        content = (choices[0].get('message') or {}).get('content') or ''
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        return content

    # ------------------------------------------------------------------ #
    # Reasoning-model hygiene                                            #
    # ------------------------------------------------------------------ #
    _THINK_BLOCK = compile(r'<(think|thinking|reasoning)>.*?</\1>\s*', DOTALL | IGNORECASE)
    _THINK_OPEN = compile(r'<(think|thinking|reasoning)>', IGNORECASE)

    @classmethod
    def _extractAnswer(cls, content):
        """
        Strip <think> monologues (sonar-reasoning emits them).
        """
        content = content or ''
        stripped = cls._THINK_BLOCK.sub('', content)
        match = cls._THINK_OPEN.search(stripped)
        if match:  # unclosed think block: drop it entirely
            stripped = stripped[:match.start()]
        return stripped.strip()

    def _answerFor(self, image_url):
        """
        (reply, answer): retries with a no-thinking prompt when the whole reply was chain-of-thought.
        """
        reply = self._request(self.getModel(), image_url)
        if not reply.ok:
            return reply, ''
        answer = self._extractAnswer(self._content(reply.json()))
        if answer:
            return reply, answer
        no_think = ('Do NOT think out loud. Do NOT output <think> or '
                    'any reasoning. Output ONLY the JSON array, immediately.\n'
                    + self.composePrompt(self.__m_prompt))
        retry = self._request(self.getModel(), image_url, prompt=no_think, budget=self.__m_maxTokens * 2)
        if retry.ok:
            return retry, self._extractAnswer(self._content(retry.json()))
        return reply, ''

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    @classmethod
    def _parseItems(cls, text, size):
        width, height = size if size else (_COORD_SPACE, _COORD_SPACE)
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
        return [cls.makeWord(text, 0.0, float(i * 10), 1.0, 8.0) for i, text in enumerate(texts)]

    @classmethod
    def _plainToRows(cls, text):
        return cls._rowsFromTexts([line.strip() for line in (text or '').splitlines() if line.strip()])

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No Perplexity API key. Generate one at '
                'https://www.perplexity.ai/settings/api (Pro accounts '
                'include $5/month of API credit) and pass api=... '
                'or set the PERPLEXITY_API_KEY environment variable.')
        imageUrl, size = self._imageUrlAndSize()
        reply, answer = self._answerFor(imageUrl)
        if reply.status_code in (400, 404) and (
                'model' in (reply.text or '').lower()):
            # Pinned model rejected; Perplexity has no catalog endpoint,
            # so walk the known vision models.
            failedModel = self.getModel()
            probed = []
            for candidate in _FALLBACK_MODELS:
                if candidate == failedModel:
                    continue
                probe = self._request(candidate, imageUrl)
                probed.append('{} -> HTTP {}'.format(candidate, probe.status_code))
                if probe.ok:
                    PerplexityOcr._model_upgrades[failedModel] = candidate
                    self.setModel(candidate)
                    reply, answer = self._answerFor(imageUrl)
                    break
                if probe.status_code == 429:
                    reply = probe
                    break
            else:
                raise OCRError(
                    "Perplexity rejected model '{}' and the known "
                    'vision models failed too (probes: {}). Check '
                    'https://docs.perplexity.ai for current model '
                    "names and pass model='...' .".format(failedModel, '; '.join(probed) or 'none'))
        if reply.status_code == 401:
            raise OCRError(
                'Perplexity rejected the API key (401): check it at '
                'https://www.perplexity.ai/settings/api. API said: ' + (reply.text or '')[:200])
        if reply.status_code == 402:
            raise OCRError(
                'Perplexity says the account is out of credit: top up '
                'at https://www.perplexity.ai/settings/api (Pro includes $5/month). API said: ' + (
                        reply.text or '')[:200])
        if reply.status_code == 429:
            raise OCRError(
                'Perplexity rate limit hit (429); the base class '
                'retries with backoff. API said: ' + (reply.text or '')[:200])
        if reply.status_code in (500, 502, 503):
            raise OCRError(
                'Perplexity is having trouble (HTTP {}): retried '
                'automatically. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from Perplexity: {}'.format(
                reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('error'):
            raise OCRError('Perplexity error: {}'.format(str(payload['error'])[:300]))
        if not answer:
            raise OCRError(
                "Model '{}' spent its whole reply thinking and "
                'produced no answer. Use a non-reasoning model '
                "(model='sonar' or 'sonar-pro') for OCR.".format(self.getModel()))
        words = self._parseItems(answer, size)
        if not words:
            words = self._plainToRows(answer)
        if not words:
            raise OCRError('Perplexity returned no text for this image.')
        return words
