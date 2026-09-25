# coding=utf-8
"""
ChatGPT OCR plugin (OpenAI Chat Completions API).
Drives OpenAI's vision models for OCR::
    POST https://api.openai.com/v1/chat/completions
    Authorization: Bearer <key>
Setup: create a key at https://platform.openai.com/api-keys. The API
is pay-per-token (no permanent free tier; new accounts sometimes get
trial credits) -- with a mini model a screenshot costs a fraction of
a cent.
Default model: 'gpt-5-mini' (the stable, cheap vision mini of the
GPT-5 family that replaced gpt-4o-mini). OpenAI rotates its catalog
aggressively -- the GPT-4-era models are being retired -- so if the
default or a pinned model disappears, the plugin probes the live
/v1/models catalog with your actual request until a vision model
answers, and caches the winner. Modern-model parameter quirks are
handled automatically: ``max_completion_tokens`` vs the legacy
``max_tokens``, restricted ``temperature``, and reasoning effort that
can consume the whole output budget before the answer (retried with
minimal reasoning and a doubled budget).
The OCR prompt asks for a JSON array of text lines with ``box_2d``
[ymin, xmin, ymax, xmax] on a 0-1000 grid (the same convention as the
Gemini/Groq/Claude plugins); images are sent at detail='high' for
better small-text fidelity. Boxes are scaled to REAL pixels using the
local image dimensions; like all VLMs the estimates are approximate,
and parsing degrades gracefully to ordered rows. Everything lands in
the exact unified structure shared by every plugin (like OCR.Space).
"""
from requests import get, post
from base64 import b64encode
from os.path import dirname
from json import loads
from os import environ
from re import sub
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_ENDPOINT = 'https://api.openai.com/v1/chat/completions'
_MODELS_ENDPOINT = 'https://api.openai.com/v1/models'
_DEFAULT_MODEL = 'gpt-5-mini'
#: Substrings that PRIORITIZE likely vision chat models in the catalog.
_VISION_HINTS = ('gpt-5', 'gpt-4o', 'gpt-4.1', 'omni', 'vision', 'terra', 'chatgpt')
#: Catalog entries that are certainly not vision chat models.
_NON_CHAT_HINTS = ('whisper', 'tts', 'dall-e', 'embedding', 'moderation', 'realtime', 'audio', 'transcribe', 'babbage',
                   'davinci', 'instruct', 'sora', 'image', 'search', 'computer-use', 'codex', 'deep-research')
#: box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
#: OpenAI caps image payloads around 20 MB.
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


class ChatGptOcr(OCRPlugin):
    """
    ChatGptOcr class.
    """
    #: dead model id -> discovered replacement (process-wide).
    _model_upgrades = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: OpenAI API key (or ``api=``, or the OPENAI_API_KEY environment variable),
                        from https://platform.openai.com/api-keys.
        :param model: vision model id (default 'gpt-5-mini').
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature; OMITTED by default because GPT-5-era models reject custom values
                            (auto-retried without it if the model complains).
        :param maxTokens: completion budget (default 4096).
        :param detail: image detail level for vision ('high' default; 'low' is cheaper but reads small text worse).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('OPENAI_API_KEY', ''))
        model = kwargs.pop('model', _DEFAULT_MODEL)
        model = ChatGptOcr._model_upgrades.get(model, model)
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = kwargs.pop('temperature', None)
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 4096))
        self.__m_detail = kwargs.pop('detail', 'high')
        kwargs.setdefault('endpoint', _ENDPOINT)
        kwargs.setdefault('timeout', 120)
        super(ChatGptOcr, self).__init__(*args, **kwargs)
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
            raise OCRError(
                'The Chat Completions vision path takes images, not '
                'PDFs. Convert the page to an image first, or use a '
                'PDF-capable plugin (ClaudeOcr, MistralOcr, GlmOcr, OlmOcr).')
        if len(data) > _IMAGE_LIMIT:
            raise OCRError(
                'Image is {:.1f} MB but OpenAI accepts about 20 MB per image. Downscale or recompress it first.'.format(
                    len(data) / 1048576.0))
        mime = ('image/jpeg' if data[:3] == b'\xff\xd8\xff' else 'image/png')
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
    # Model discovery                                                    #
    # ------------------------------------------------------------------ #
    def _headers(self):
        return {'Authorization': 'Bearer {}'.format(self.getApi())}

    def _catalogIds(self):
        try:
            reply = get(_MODELS_ENDPOINT, headers=self._headers(), timeout=self.getTimeout())
            if not reply.ok:
                return []
            return [str(entry.get('id') or '') for entry in (reply.json().get('data') or [])]
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
            cheap = 0 if 'mini' in lowered or 'nano' in lowered else 1
            candidates.append(((score, cheap, model_id), model_id))
        candidates.sort(key=lambda pair: pair[0])
        return [model_id for _, model_id in candidates]

    # ------------------------------------------------------------------ #
    # Requests with parameter-compat ladder                              #
    # ------------------------------------------------------------------ #
    def _request(self, model, image_url, extra=None, budget=None):
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': self.composePrompt(self.__m_prompt)},
                {'type': 'image_url', 'image_url': {'url': image_url, 'detail': self.__m_detail}},
            ]}],
            'max_completion_tokens': budget or self.__m_max_tokens,
        }
        if self.__m_temperature is not None:
            body['temperature'] = float(self.__m_temperature)
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
            # Legacy models want max_tokens instead.
            if 'max_completion_tokens' in text:
                body.pop('max_completion_tokens', None)
                body['max_tokens'] = budget or self.__m_max_tokens
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
        return content, str(choices[0].get('finish_reason') or '')

    def _answerFor(self, image_url):
        """
        Get a non-empty answer, escalating when reasoning consumed the
        whole budget (empty content + finish_reason 'length'):
        1. plain request;
        2. reasoning_effort='minimal' + doubled budget;
        3. reasoning_effort='none' variant.
        Returns (reply, answer_text).
        """
        reply = self._request(self.getModel(), image_url)
        if not reply.ok:
            return reply, ''
        content, finish = self._content(reply.json())
        if content.strip():
            return reply, content
        if finish != 'length':
            return reply, content
        for effort in ('minimal', 'none'):
            retry = self._request(
                self.getModel(), image_url, extra={'reasoning_effort': effort}, budget=self.__m_max_tokens * 2)
            if not retry.ok:
                continue
            content, _ = self._content(retry.json())
            if content.strip():
                return retry, content
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
            label = item.get('text', '').strip()
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
                'No OpenAI API key. Create one at https://platform.openai.com/api-keys and pass api=... or set the '
                'OPENAI_API_KEY environment variable.')
        image_url, size = self._imageUrlAndSize()
        reply, answer = self._answerFor(image_url)
        if reply.status_code in (400, 404) and ('model' in (reply.text or '').lower()):
            failed_model = self.getModel()
            probed = []
            for candidate in self._candidateVisionModels(exclude=failed_model)[:6]:
                probe = self._request(candidate, image_url)
                probed.append('{} -> HTTP {}'.format(candidate, probe.status_code))
                if probe.ok:
                    ChatGptOcr._model_upgrades[failed_model] = candidate
                    self.setModel(candidate)
                    reply, answer = self._answerFor(image_url)
                    break
                if probe.status_code == 429:
                    reply = probe
                    break
            else:
                catalog = self._catalogIds()
                raise OCRError(
                    "OpenAI has no working vision model under the ids "
                    "this plugin tried (model '{}' is gone; probes: "
                    '{}). Models on your account right now: {}. Pick '
                    "a vision-capable one and pass model='...' .".format(
                        failed_model, '; '.join(probed) or 'none available', ', '.join(catalog[:15]) or 'none listed'))
        if reply.status_code == 401:
            raise OCRError(
                'OpenAI rejected the API key (401): check it at https://platform.openai.com/api-keys. API said: ' + (
                        reply.text or '')[:200])
        if reply.status_code == 429:
            text = (reply.text or '')
            if 'insufficient_quota' in text:
                raise OCRError(
                    'OpenAI says the account has no credit '
                    '(insufficient_quota): add billing/credits at '
                    'https://platform.openai.com/settings/organization/'
                    'billing. API said: ' + text[:200])
            raise OCRError('OpenAI rate limit hit (429); the base class retries with backoff. API said: ' + text[:200])
        if reply.status_code in (500, 502, 503):
            raise OCRError(
                'OpenAI is having trouble (HTTP {}): retried '
                'automatically. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from OpenAI: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('error'):
            raise OCRError('OpenAI error: {}'.format(str(payload['error'])[:300]))
        if not answer:
            raise OCRError(
                "Model '{}' returned an empty answer (its reasoning "
                'consumed the whole output budget), even with minimal '
                'reasoning. Pin another vision model with '
                "model='...' .".format(self.getModel()))
        words = self._parseItems(answer, size)
        if not words:
            words = self._plainToRows(answer)
        if not words:
            raise OCRError('ChatGPT returned no text for this image.')
        return words
