# coding=utf-8
"""
Groq OCR plugin (Groq API vision models).
Groq runs open vision models on custom LPU hardware at extreme speed,
with a genuinely useful FREE tier: no credit card, keys issued
instantly at https://console.groq.com/keys, roughly 30 requests/min
and ~1,000 requests/day per model (Llama 4 Maverick gets half). The
API is OpenAI-compatible::
    POST https://api.groq.com/openai/v1/chat/completions
    Authorization: Bearer <key>
Default model: 'meta-llama/llama-4-scout-17b-16e-instruct' (vision,
high free quota, very fast). Pass
model='meta-llama/llama-4-maverick-17b-128e-instruct' for the larger,
more accurate one (reduced free quota). If a pinned model has been
retired -- Groq rotates its catalog; the old Llama-3.2-vision models
are already gone -- the plugin discovers a current vision model from
GET /openai/v1/models and retries automatically.
The OCR prompt asks for a JSON array of text lines with ``box_2d``
[ymin, xmin, ymax, xmax] on a 0-1000 grid (same convention as the
Gemini/OpenRouter/DeepSeek plugins). Llama models' spatial grounding
is honest-to-goodness variable, so parsing degrades gracefully:
grounded boxes when provided, ordered rows otherwise. Local images go
inline as base64 data URLs (Groq caps base64 requests at ~4 MB);
public URLs are passed through for Groq to fetch (up to 20 MB).
"""
from re import compile, DOTALL, IGNORECASE, sub
from requests import get, post
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

_ENDPOINT = 'https://api.groq.com/openai/v1/chat/completions'
_MODELS_ENDPOINT = 'https://api.groq.com/openai/v1/models'
_DEFAULT_MODEL = 'meta-llama/llama-4-scout-17b-16e-instruct'
#: Substrings that PRIORITIZE likely vision models in the catalog.
_VISION_HINTS = ('vl', 'vision', 'scout', 'maverick', 'llama-4', 'gemma-3', 'pixtral', 'moondream', 'omni')
#: Catalog entries that are certainly not vision chat models.
_NON_CHAT_HINTS = ('whisper', 'tts', 'guard', 'embed', 'rerank', 'moderation', 'transcrib', 'saba', 'allam', 'compound',
                   'prompt-guard')
#: Reasoning models emit <think> monologues and burn the token budget
#: before answering: probe them LAST.
_REASONING_HINTS = ('r1', 'qwq', 'think', 'reason', 'oss')
#: box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
#: Groq rejects base64 request bodies larger than ~4 MB.
_BASE64_LIMIT = 4 * 1024 * 1024
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


class GroqOcr(OCRPlugin):
    """
    GroqOcr class.
    """
    #: dead model id -> discovered replacement (process-wide).
    _model_upgrades = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: Groq API key (or ``api=``, or the GROQ_API_KEY
                       environment variable), free at https://console.groq.com/keys.
        :param model: vision model id (default Llama 4 Scout).
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature (default 0).
        :param maxTokens: generation budget (default 4096).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('GROQ_API_KEY', ''))
        model = kwargs.pop('model', _DEFAULT_MODEL)
        model = GroqOcr._model_upgrades.get(model, model)
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = float(kwargs.pop('temperature', 0))
        self.__m_maxTokens = int(kwargs.pop('maxTokens', 4096))
        kwargs.setdefault('timeout', 60)
        super(GroqOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)
        self.setModel(model)
        self.setEndpoint(_ENDPOINT)

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _imageUrlAndSize(self):
        """
        Return (url_or_data_url, (width, height) or None).
        """
        kind = self.imageKind()
        if kind == 'url':
            # Pass through: Groq fetches it (bigger 20 MB budget), but
            # then the pixel size is unknown -> 1000-grid coordinates.
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
                "directory matters for relative paths)."
                .format(self.getImage()))
        if data[:5] == b'%PDF-':
            raise OCRError(
                'Groq vision models take images, not PDFs. Convert '
                'the page to an image first, or use a PDF-capable plugin (MistralOcr, GlmOcr, OlmOcr).')
        encoded = b64encode(data).decode('ascii')
        if len(encoded) > _BASE64_LIMIT:
            raise OCRError(
                'Image too large for Groq: base64 is {:.1f} MB but '
                'inline requests are capped around 4 MB. Downscale or '
                'recompress the image, or host it and pass a URL (20 MB budget).'.format(len(encoded) / 1048576.0))
        mime = ('image/jpeg' if data[:3] == b'\xff\xd8\xff' else 'image/png')
        size = None
        try:
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(data)) as opened:
                size = opened.size
        except Exception:  # noqa: BLE001 - size is best-effort
            size = None
        return 'data:{};base64,{}'.format(mime, encoded), size

    # ------------------------------------------------------------------ #
    # Model discovery                                                    #
    # ------------------------------------------------------------------ #
    def _headers(self):
        return {'Authorization': 'Bearer {}'.format(self.getApi())}

    def _catalogIds(self):
        """
        All model ids from the live catalog (empty on failure).
        """
        try:
            reply = get(_MODELS_ENDPOINT, headers=self._headers(), timeout=self.getTimeout())
            if not reply.ok:
                return []
            return [str(entry.get('id') or '') for entry in (reply.json().get('data') or [])]
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return []

    def _candidateVisionModels(self, exclude):
        """
        Catalog ids ordered by how likely they are to be vision chat
        models: hint matches first, then the rest of the plausible
        chat models. Obvious non-chat models (whisper/tts/guard/...)
        are dropped; *exclude* (the failed model) is skipped.
        """
        candidates = []
        for modelId in self._catalogIds():
            lowered = modelId.lower()
            if not modelId or modelId == exclude:
                continue
            if any(hint in lowered for hint in _NON_CHAT_HINTS):
                continue
            score = min((
                index for index, hint in enumerate(_VISION_HINTS) if hint in lowered), default=len(_VISION_HINTS))
            reasoning = any(hint in lowered for hint in _REASONING_HINTS)
            candidates.append(((1 if reasoning else 0, score), modelId))
        candidates.sort(key=lambda pair: pair[0])
        return [model_id for _, model_id in candidates]

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    _THINK_BLOCK = compile(r'<(think|thinking|reasoning)>.*?</\1>\s*', DOTALL | IGNORECASE)
    _THINK_OPEN = compile(r'<(think|thinking|reasoning)>', IGNORECASE)

    @classmethod
    def _extractAnswer(cls, content):
        """
        Reasoning models wrap a chain-of-thought monologue in <think>
        tags (sometimes never closing it before the token budget runs
        out). Return only the answer part; '' means the reply was ALL
        thinking and a retry with reasoning suppressed is needed.
        """
        content = content or ''
        stripped = cls._THINK_BLOCK.sub('', content)
        match = cls._THINK_OPEN.search(stripped)
        if match:  # unclosed think block: drop it entirely
            stripped = stripped[:match.start()]
        return stripped.strip()

    @classmethod
    def _parseItems(cls, text, size):
        """
        Model JSON -> word dicts (grounded boxes or ordered rows).
        """
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
                label, xmin / _COORD_SPACE * width, ymin / _COORD_SPACE * height,
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
    def _request(self, model, image_url, extra=None, prompt=None):
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt or self.composePrompt(self.__m_prompt)},
                {'type': 'image_url', 'image_url': {'url': image_url}},
            ]}],
            'temperature': self.__m_temperature,
            'max_tokens': self.__m_maxTokens,
        }
        if extra:
            body.update(extra)
        request_kwargs = {'json': body, 'headers': self._headers(), 'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        return post(self.getEndpoint(), **request_kwargs)

    @staticmethod
    def _content(payload):
        choices = payload.get('choices') or []
        if not choices:
            return ''
        content = (choices[0].get('message') or {}).get('content') or ''
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        return content

    def _answerFor(self, image_url):
        """
        Get a usable (non-thinking) answer from the current model,
        escalating through Groq's reasoning-suppression options when
        the reply drowned in a <think> monologue:
        1. plain request;
        2. + reasoning_format='hidden' (Groq's parameter for
           reasoning models);
        3. + an explicit no-thinking prompt with a doubled budget.
        Returns (reply, answer_text).
        """
        reply = self._request(self.getModel(), image_url)
        if not reply.ok:
            return reply, ''
        answer = self._extractAnswer(self._content(reply.json()))
        if answer:
            return reply, answer
        retry = self._request(self.getModel(), image_url, extra={'reasoning_format': 'hidden'})
        if retry.ok:
            answer = self._extractAnswer(self._content(retry.json()))
            if answer:
                return retry, answer
        no_think_prompt = (
                'Do NOT think out loud. Do NOT output <think> or any '
                'reasoning. Output ONLY the JSON array, immediately.\n' + self.composePrompt(self.__m_prompt))
        final = self._request(
            self.getModel(), image_url, prompt=no_think_prompt, extra={'max_tokens': self.__m_maxTokens * 2})
        if final.ok:
            return final, self._extractAnswer(self._content(final.json()))
        return reply, ''

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No Groq API key. Create a free one (no credit card) '
                'at https://console.groq.com/keys and pass api=... '
                'or set the GROQ_API_KEY environment variable.')
        imageUrl, size = self._imageUrlAndSize()
        reply, answer = self._answerFor(imageUrl)
        if reply.status_code in (400, 404) and ('model' in (reply.text or '').lower()):
            # Pinned/default model retired or renamed (Groq rotates its
            # catalog; preview models get pulled): probe current
            # catalog candidates with THIS request until one works.
            failed_model = self.getModel()
            probed = []
            for candidate in self._candidateVisionModels(exclude=failed_model)[:6]:
                probe = self._request(candidate, imageUrl)
                probed.append('{} -> HTTP {}'.format(candidate, probe.status_code))
                if probe.ok:
                    GroqOcr._model_upgrades[failed_model] = candidate
                    self.setModel(candidate)
                    # Rerun the answer ladder on the discovered model
                    # (it may be a reasoning model needing suppression).
                    reply, answer = self._answerFor(imageUrl)
                    break
                if probe.status_code == 429:
                    reply = probe  # real quota problem, stop probing
                    break
            else:
                catalog = self._catalogIds()
                raise OCRError(
                    "Groq has no working vision model under the ids "
                    "this plugin tried (model '{}' is gone; probes: "
                    '{}). Models on your account right now: {}. Pick '
                    "a vision-capable one and pass model='...' .".format(
                        failed_model, '; '.join(probed) or 'none available', ', '.join(catalog[:15]) or 'none listed'))
        if reply.status_code == 401:
            raise OCRError(
                'Groq rejected the API key (401): check it at '
                'https://console.groq.com/keys. API said: ' + (reply.text or '')[:200])
        if reply.status_code == 413:
            raise OCRError(
                'Groq says the request is too large (413): inline '
                'base64 images are capped ~4 MB. Downscale the image or pass a hosted URL instead.')
        if reply.status_code == 429:
            raise OCRError(
                'Groq rate limit hit. The free tier allows ~30 '
                'requests/min and ~1,000 requests/day per model '
                '(Llama 4 Maverick: half that); limits are per '
                'organization and reset at midnight UTC. API said: ' + (reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from Groq: {}'.format(
                reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('error'):
            raise OCRError('Groq error: {}'.format(
                str(payload['error'])[:300]))
        if not answer:
            raise OCRError(
                "Model '{}' spent its whole reply thinking (<think> "
                'monologue) and produced no answer, even with '
                'reasoning suppressed. Pin a non-reasoning vision '
                "model with model='...' (the catalog is at GET "
                '/openai/v1/models).'.format(self.getModel()))
        words = self._parseItems(answer, size)
        if not words:
            words = self._plainToRows(answer)
        if not words:
            raise OCRError('Groq returned no text for this image.')
        return words
