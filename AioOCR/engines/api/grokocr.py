# coding=utf-8
"""
Grok OCR plugin (xAI API vision models).
xAI's API is OpenAI-compatible and its Grok models accept images::
    POST https://api.x.ai/v1/chat/completions
    Authorization: Bearer <key>
Setup: create a key at https://console.x.ai (usage-based pricing with
prepaid credits; no permanent free tier). Pass it as ``api=`` or
set the XAI_API_KEY (or GROK_API_KEY) environment variable.
Default model: 'grok-4.3' -- vision-capable, moderately priced, and
available in every region (xAI's newest 'grok-4.5' is restricted for
EU accounts; 'grok-4.6' is the pricier flagship). xAI retires model
slugs aggressively -- the grok-4-fast / grok-4-0709 / grok-3 families
were pulled in May 2026 -- so if the default or a pinned model
disappears, the plugin probes the live /v1/models catalog with your
actual request until a vision model answers, and caches the winner.
Grok-specific constraints handled here: images must be JPEG or PNG
(other raster formats are converted automatically), at most 20 MiB
each, sent as base64 data URIs or passed through as public HTTPS
URLs, with detail='high' for dense text. Modern Groks reason
internally and can consume the whole output budget before answering;
an empty reply is retried with a doubled budget and low reasoning
effort (the parameter is dropped automatically on models that reject
it).
The OCR prompt asks for a JSON array of text lines with ``box_2d``
[ymin, xmin, ymax, xmax] on a 0-1000 grid (same convention as the
Gemini/Groq/Claude/ChatGPT plugins), scaled to REAL pixels via the
local image dimensions, degrading gracefully to ordered rows. Like
all VLMs the box estimates are approximate. Everything lands in the
exact unified structure shared by every plugin (like OCR.Space).
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

_ENDPOINT = 'https://api.x.ai/v1/chat/completions'
_MODELS_ENDPOINT = 'https://api.x.ai/v1/models'
_DEFAULT_MODEL = 'grok-4.3'
#: Substrings that PRIORITIZE likely vision chat models in the catalog.
_VISION_HINTS = ('grok-4.3', 'grok-4.6', 'grok-4.5', 'grok-4', 'grok', 'vision')
#: Catalog entries that are certainly not vision chat models.
_NON_CHAT_HINTS = ('imagine', 'image', 'video', 'embed', 'code', 'build', 'tts', 'audio', 'moderation', 'transcrib')
#: box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
#: xAI caps images at 20 MiB, JPEG/PNG only.
_IMAGE_LIMIT = 20 * 1024 * 1024
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


class GrokOcr(OCRPlugin):
    """
    GrokOcr class.
    """
    #: dead model id -> discovered replacement (process-wide).
    _model_upgrades = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: xAI API key (or ``api=``, or the XAI_API_KEY / GROK_API_KEY environment variables),
                        from https://console.x.ai.
        :param model: vision model id (default 'grok-4.3').
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature (default 0; reasoning models may silently ignore it).
        :param maxTokens: completion budget (default 4096).
        :param detail: image detail level ('high' default; 'low' is cheaper but reads small text worse).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('XAI_API_KEY', '') or environ.get('GROK_API_KEY', ''))
        model = kwargs.pop('model', _DEFAULT_MODEL)
        model = GrokOcr._model_upgrades.get(model, model)
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = float(kwargs.pop('temperature', 0))
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 4096))
        self.__m_detail = kwargs.pop('detail', 'high')
        kwargs.setdefault('endpoint', _ENDPOINT)
        kwargs.setdefault('timeout', 180)  # reasoning models are slow
        super(GrokOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setModel(model)
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
            raise OCRError('Grok takes images, not PDFs. Convert the page to an image first, '
                           'or use a PDF-capable plugin (ClaudeOcr, MistralOcr, GlmOcr, OlmOcr).')
        if data[:3] == b'\xff\xd8\xff':
            mime = 'image/jpeg'
        elif data[:8] == b'\x89PNG\r\n\x1a\n':
            mime = 'image/png'
        else:
            # xAI accepts ONLY jpg/png: convert other raster formats.
            from io import BytesIO
            from PIL import Image
            buffer = BytesIO()
            Image.open(BytesIO(data)).convert('RGB').save(
                buffer, format='PNG')
            data = buffer.getvalue()
            mime = 'image/png'
        if len(data) > _IMAGE_LIMIT:
            raise OCRError(
                'Image is {:.1f} MB but xAI accepts at most 20 MiB '
                'per image. Downscale or recompress it first.'
                .format(len(data) / 1048576.0))
        size = None
        try:
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(data)) as opened:
                size = opened.size
        except Exception:  # noqa: BLE001 - size is best-effort
            size = None
        return ('data:{};base64,{}'.format(mime, b64encode(data).decode('ascii')), size)

    # ------------------------------------------------------------------ #
    # Model discovery                                                    #
    # ------------------------------------------------------------------ #
    def _headers(self):
        return {'Authorization': 'Bearer {}'.format(self.getApi()), 'Content-Type': 'application/json'}

    def _catalogIds(self):
        try:
            reply = get(_MODELS_ENDPOINT, headers=self._headers(), timeout=self.getTimeout())
            if not reply.ok:
                return []
            return [str(entry.get('id') or '')
                    for entry in (reply.json().get('data') or [])]
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return []

    def _candidateVisionModels(self, exclude):
        candidates = []
        for model_id in self._catalogIds():
            lowered = model_id.lower()
            if not model_id or model_id == exclude:
                continue
            if any(hint in lowered for hint in _NON_CHAT_HINTS):
                continue
            score = min((index for index, hint in enumerate(_VISION_HINTS) if hint in lowered),
                        default=len(_VISION_HINTS))
            candidates.append(((score, model_id), model_id))
        candidates.sort(key=lambda pair: pair[0])
        return [model_id for _, model_id in candidates]

    # ------------------------------------------------------------------ #
    # Requests with compat ladder                                        #
    # ------------------------------------------------------------------ #
    def _request(self, model, image_url, extra=None, budget=None):
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': self.composePrompt(self.__m_prompt)},
                {'type': 'image_url',
                 'image_url': {'url': image_url,
                               'detail': self.__m_detail}},
            ]}],
            'temperature': self.__m_temperature,
            'max_tokens': budget or self.__m_max_tokens,
        }
        if extra:
            body.update(extra)
        request_kwargs = {
            'json': body,
            'headers': self._headers(),
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code == 400:
            text = (reply.text or '')
            if 'max_tokens' in text and 'max_completion_tokens' not in body:
                body.pop('max_tokens', None)
                body['max_completion_tokens'] = budget or self.__m_max_tokens
                reply = post(self.getEndpoint(), **request_kwargs)
            elif 'temperature' in text and 'temperature' in body:
                body.pop('temperature')
                reply = post(self.getEndpoint(), **request_kwargs)
            elif extra and any(key in text for key in extra):
                for key in list(extra):
                    body.pop(key, None)
                reply = post(self.getEndpoint(), **request_kwargs)
        return reply

    @staticmethod
    def _content(payload):
        choices = payload.get('choices') or []
        if not choices:
            return '', ''
        message = choices[0].get('message') or {}
        content = message.get('content') or ''
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        return content, choices[0].get('finish_reason') or ''

    _THINK_BLOCK = compile(r'<(think|thinking|reasoning)>.*?</\1>\s*', DOTALL | IGNORECASE)
    _THINK_OPEN = compile(r'<(think|thinking|reasoning)>', IGNORECASE)

    @classmethod
    def _extractAnswer(cls, content):
        """
        Strip any leaked <think> monologue (belt and braces).
        """
        content = content or ''
        stripped = cls._THINK_BLOCK.sub('', content)
        match = cls._THINK_OPEN.search(stripped)
        if match:
            stripped = stripped[:match.start()]
        return stripped.strip()

    def _answerFor(self, image_url):
        """
        Get a non-empty answer; when internal reasoning consumed the
        budget (empty content / finish 'length'), retry with a doubled
        budget and low reasoning effort (dropped if unsupported).
        Returns (reply, answer_text).
        """
        reply = self._request(self.getModel(), image_url)
        if not reply.ok:
            return reply, ''
        content, finish = self._content(reply.json())
        answer = self._extractAnswer(content)
        if answer:
            return reply, answer
        retry = self._request(
            self.getModel(), image_url, extra={'reasoning_effort': 'low'}, budget=self.__m_max_tokens * 2)
        if retry.ok:
            answer = self._extractAnswer(self._content(retry.json())[0])
            if answer:
                return retry, answer
        if finish == 'length':
            final = self._request(self.getModel(), image_url, budget=self.__m_max_tokens * 4)
            if final.ok:
                return final, self._extractAnswer(self._content(final.json())[0])
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
            raise OCRError('No xAI API key. Create one at https://console.x.ai and pass api=... '
                           'or set the XAI_API_KEY environment variable.')
        image_url, size = self._imageUrlAndSize()
        reply, answer = self._answerFor(image_url)
        if reply.status_code in (400, 404) and ('model' in (reply.text or '').lower()):
            failed_model = self.getModel()
            probed = []
            for candidate in self._candidateVisionModels(exclude=failed_model)[:6]:
                probe = self._request(candidate, image_url)
                probed.append('{} -> HTTP {}'.format(candidate, probe.status_code))
                if probe.ok:
                    GrokOcr._model_upgrades[failed_model] = candidate
                    self.setModel(candidate)
                    reply, answer = self._answerFor(image_url)
                    break
                if probe.status_code == 429:
                    reply = probe
                    break
            else:
                catalog = self._catalogIds()
                raise OCRError(
                    "xAI has no working vision model under the ids "
                    "this plugin tried (model '{}' is gone; probes: "
                    '{}). Models on your account right now: {}. Note '
                    "that some models (e.g. grok-4.5) are "
                    'region-restricted; grok-4.3 works everywhere. '
                    "Pick one and pass model='...' .".format(
                        failed_model, '; '.join(probed) or 'none available', ', '.join(catalog[:15]) or 'none listed'))
        if reply.status_code == 401:
            raise OCRError(
                'xAI rejected the API key (401): check it at https://console.x.ai. API said: ' + (
                        reply.text or '')[:200])
        if reply.status_code == 403:
            raise OCRError(
                'xAI refused the request (403): the key may lack '
                'access to this model, the account may be out of '
                'credits, or the model is region-restricted (try '
                "model='grok-4.3'). API said: " + (reply.text or '')[:200])
        if reply.status_code == 429:
            raise OCRError(
                'xAI rate limit hit (429); the base class retries '
                'with backoff. API said: ' + (reply.text or '')[:200])
        if reply.status_code in (500, 502, 503):
            raise OCRError(
                'xAI is having trouble (HTTP {}): retried '
                'automatically. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from xAI: {}'.format(
                reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('error'):
            raise OCRError('xAI error: {}'.format(str(payload['error'])[:300]))
        if not answer:
            raise OCRError(
                "Model '{}' returned an empty answer (its internal "
                'reasoning consumed the whole output budget), even '
                'with low reasoning effort and a bigger budget. Pin '
                "another vision model with model='...' .".format(self.getModel()))
        words = self._parseItems(answer, size)
        if not words:
            words = self._plainToRows(answer)
        if not words:
            raise OCRError('Grok returned no text for this image.')
        return words
