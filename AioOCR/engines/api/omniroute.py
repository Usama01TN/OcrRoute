# coding=utf-8
"""
OmniRoute OCR plugin (local AI-gateway, https://omniroute.online).
OmniRoute is a FREE, open-source AI gateway you run locally: one
OpenAI-compatible endpoint in front of 339+ providers (90+ with free
tiers), with quota-aware automatic fallback. It is NOT a hosted cloud
API -- you install and launch it yourself::
    npm install -g omniroute
    omniroute setup      # sign in to 1+ providers in the dashboard
    omniroute            # gateway + dashboard on port 20128
Then this plugin talks to it like any OpenAI endpoint::
    POST http://localhost:20128/v1/chat/completions
    Authorization: Bearer <dashboard key>
Setup: after launching, open http://localhost:20128, connect at
least one vision-capable provider (90+ free tiers -- no credit card),
and copy the dashboard API key. Pass it as ``api=`` or set the
OMNIROUTE_API_KEY environment variable; if your gateway allows local
unauthenticated access you may omit it.
Default model: 'auto' -- OmniRoute picks a provider and falls back if
one fails or is rate-limited, so OCR "just works" across whatever you
connected. Pin a specific routed model with e.g.
model='gemini/gemini-2.5-flash' or model='qwen/qwen3-vl-8b-instruct'.
If 'auto' can't produce a vision answer, the plugin probes the
gateway's /v1/models catalog for a vision model and caches the winner.
Since routes may land on reasoning models that emit <think>, the
plugin strips chain-of-thought and retries with a no-thinking prompt.
The OCR prompt asks for a JSON array of text lines with ``box_2d``
[ymin, xmin, ymax, xmax] on a 0-1000 grid (same convention as the
Gemini/Claude/Groq plugins), scaled to REAL pixels via the local
image dimensions, degrading gracefully to ordered rows. Everything
lands in the exact unified structure shared by every plugin (like
OCR.Space).
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

_DEFAULT_BASE = 'http://localhost:20128'
_CHAT_PATH = '/v1/chat/completions'
_MODELS_PATH = '/v1/models'
_DEFAULT_MODEL = 'auto'
#: Substrings that identify vision-capable routed models.
_VISION_HINTS = ('openrouter', 'vl', 'vision', 'ocr', 'gemini', 'gpt-4o', 'gpt-5', 'claude', 'pixtral', 'llama-4',
                 'internvl', 'qwen2.5-vl', 'qwen3-vl', 'grok', 'llava', 'glm-4v', 'glm-4.1v', 'moondream', 'omni')
#: Reasoning-model markers: emit <think>, probe LAST.
_REASONING_HINTS = ('thinking', 'reasoner', 'r1', 'qwq', 'reasoning')
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


class OmniRouteOcr(OCRPlugin):
    """
    OmniRouteOcr class.
    """
    #: dead/failed model -> discovered replacement (process-wide).
    _model_upgrades = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: OmniRoute dashboard key (or ``api=``, or the OMNIROUTE_API_KEY environment variable).
                        May be empty if the local gateway allows unauthenticated access.
        :param base: gateway base URL (default 'http://localhost:20128'; change host/port if you run it elsewhere).
        :param model: routed model id
                        (default 'auto' -- let OmniRoute choose and fall back). Pin one like 'gemini/gemini-2.5-flash'.
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature (default 0).
        :param maxTokens: completion budget (default 4096).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('OMNIROUTE_API_KEY', ''))
        self.__m_base = str(kwargs.pop('base', _DEFAULT_BASE)).rstrip('/')
        model = kwargs.pop('model', _DEFAULT_MODEL)
        model = OmniRouteOcr._model_upgrades.get(model, model)
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = float(kwargs.pop('temperature', 0))
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 4096))
        self.__m_endpoint_override = kwargs.pop('endpoint', None)
        kwargs.setdefault('timeout', 180)  # routing + fallback is slow
        super(OmniRouteOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)  # Local network, but a server nonetheless
        self.setModel(model)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Endpoints                                                          #
    # ------------------------------------------------------------------ #
    def _chatUrl(self):
        return self.__m_endpoint_override or (self.__m_base + _CHAT_PATH)

    def _headers(self):
        headers = {'Content-Type': 'application/json'}
        if self.getApi():
            headers['Authorization'] = 'Bearer {}'.format(self.getApi())
        return headers

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _imageUrlAndSize(self):
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
                "directory matters for relative paths)."
                .format(self.getImage()))
        if data[:5] == b'%PDF-':
            raise OCRError(
                'This plugin sends images, not PDFs, through '
                'OmniRoute. Convert the page to an image first, or use '
                'a PDF-capable plugin (ClaudeOcr, MistralOcr, GlmOcr, OlmOcr).')
        mime = ('image/jpeg' if data[:3] == b'\xff\xd8\xff'
                else 'image/png')
        size = None
        try:
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(data)) as opened:
                size = opened.size
        except Exception:  # noqa: BLE001 - size is best-effort
            size = None
        return ('data:{};base64,{}'.format(
            mime, b64encode(data).decode('ascii')), size)

    # ------------------------------------------------------------------ #
    # Model discovery                                                    #
    # ------------------------------------------------------------------ #
    def _catalogIds(self):
        try:
            reply = get(self.__m_base + _MODELS_PATH, headers=self._headers(), timeout=self.getTimeout())
            if not reply.ok:
                return []
            return [str(entry.get('id') or '') for entry in (reply.json().get('data') or [])]
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return []

    def _candidateVisionModels(self, exclude):
        candidates = []
        for model_id in self._catalogIds():
            lowered = model_id.lower()
            if not model_id or model_id == exclude or model_id == 'auto':
                continue
            if not any(hint in lowered for hint in _VISION_HINTS):
                continue
            reasoning = any(hint in lowered for hint in _REASONING_HINTS)
            score = min((index for index, hint in enumerate(_VISION_HINTS) if hint in lowered), default=len(_VISION_HINTS))
            candidates.append(((1 if reasoning else 0, score), model_id))
        candidates.sort(key=lambda pair: pair[0])
        return [model_id for _, model_id in candidates]

    # ------------------------------------------------------------------ #
    # Requests                                                           #
    # ------------------------------------------------------------------ #
    def _request(self, model, image_url, prompt=None, budget=None):
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': image_url}},
                {'type': 'text', 'text': prompt or self.composePrompt(self.__m_prompt)},
            ]}],
            'temperature': self.__m_temperature,
            'max_tokens': budget or self.__m_max_tokens,
            'stream': False,
        }
        request_kwargs = {
            'json': body,
            'headers': self._headers(),
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        return post(self._chatUrl(), **request_kwargs)

    @staticmethod
    def _content(payload):
        choices = payload.get('choices') or []
        if not choices:
            return ''
        content = (choices[0].get('message') or {}).get('content') or ''
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content
                              if isinstance(part, dict))
        return content

    _THINK_BLOCK = compile(r'<(think|thinking|reasoning)>.*?</\1>\s*', DOTALL | IGNORECASE)
    _THINK_OPEN = compile(r'<(think|thinking|reasoning)>', IGNORECASE)

    @classmethod
    def _extractAnswer(cls, content):
        content = content or ''
        stripped = cls._THINK_BLOCK.sub('', content)
        match = cls._THINK_OPEN.search(stripped)
        if match:
            stripped = stripped[:match.start()]
        return stripped.strip()

    def _answerFor(self, image_url):
        reply = self._request(self.getModel(), image_url)
        if not reply.ok:
            return reply, ''
        answer = self._extractAnswer(self._content(reply.json()))
        if answer:
            return reply, answer
        no_think = ('Do NOT think out loud. Do NOT output <think> or '
                    'any reasoning. Output ONLY the JSON array, immediately.\n'
                    + self.composePrompt(self.__m_prompt))
        retry = self._request(self.getModel(), image_url, prompt=no_think, budget=self.__m_max_tokens * 2)
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
        return [cls.makeWord(text, 0.0, float(i * 10), 1.0, 8.0)
                for i, text in enumerate(texts)]

    @classmethod
    def _plainToRows(cls, text):
        return cls._rowsFromTexts(
            [line.strip() for line in (text or '').splitlines()
             if line.strip()])

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        imageUrl, size = self._imageUrlAndSize()
        try:
            reply, answer = self._answerFor(imageUrl)
        except Exception as exc:  # noqa: BLE001 - connection failure
            raise OCRError(
                'Could not reach the OmniRoute gateway at {}. Is it '
                'running? Install with "npm install -g omniroute", '
                'launch with "omniroute", then set base=... if it '
                'listens elsewhere. Original: {}'.format(self.__m_base, str(exc)[:200]))
        # 'auto' or a pinned model failed/returned nothing usable:
        # probe the gateway catalog for a concrete vision model.
        needProbe = (not reply.ok and reply.status_code in (
            400, 404, 422, 502)) or (reply.ok and not answer)
        if needProbe:
            failed_model = self.getModel()
            probed = []
            for candidate in self._candidateVisionModels(exclude=failed_model)[:6]:
                probe = self._request(candidate, imageUrl)
                probed.append('{} -> HTTP {}'.format(candidate, probe.status_code))
                if probe.ok:
                    answerTry = self._extractAnswer(self._content(probe.json()))
                    if answerTry:
                        OmniRouteOcr._model_upgrades[failed_model] = candidate
                        self.setModel(candidate)
                        reply, answer = probe, answerTry
                        break
                if probe.status_code == 429:
                    reply = probe
                    break
            else:
                if not answer:
                    catalog = self._catalogIds()
                    vision = [m for m in catalog if any(h in m.lower() for h in _VISION_HINTS)]
                    raise OCRError(
                        "OmniRoute could not fulfil OCR with '{}' and "
                        'no catalog vision model answered (probes: '
                        '{}). Connect a vision provider in the '
                        'dashboard (http://localhost:20128). Vision '
                        'models seen: {}. Pin one with '
                        "model='provider/model'.".format(
                            failed_model, '; '.join(probed) or 'none', ', '.join(vision[:12]) or 'none'))

        if reply.status_code == 401:
            raise OCRError(
                'OmniRoute rejected the request (401): copy the '
                'dashboard API key from http://localhost:20128 and '
                'pass api=... (or configure the gateway to allow '
                'local access). API said: ' + (reply.text or '')[:200])
        if reply.status_code == 429:
            raise OCRError(
                'OmniRoute reports all connected providers are rate/'
                'quota limited (429). Connect more free-tier providers '
                'in the dashboard. API said: ' + (reply.text or '')[:200])
        if reply.status_code in (502, 503):
            raise OCRError(
                'OmniRoute could not route the request (HTTP {}): no '
                'healthy provider for this model. Check provider '
                'status in the dashboard. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from OmniRoute: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('error'):
            raise OCRError('OmniRoute error: {}'.format(str(payload['error'])[:300]))
        if not answer:
            raise OCRError('OmniRoute returned no usable answer for this image.')
        words = self._parseItems(answer, size)
        if not words:
            words = self._plainToRows(answer)
        if not words:
            raise OCRError('OmniRoute returned no text for this image.')
        return words
