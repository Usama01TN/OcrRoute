# coding=utf-8
"""
SiliconFlow OCR plugin (Qwen-VL vision models).
SiliconFlow is an OpenAI-compatible inference platform and the main
host for Alibaba's Qwen-VL models -- the strongest open OCR VLMs,
with SOTA multilingual OCR and visual grounding::
    POST https://api.siliconflow.com/v1/chat/completions   (global)
    POST https://api.siliconflow.cn/v1/chat/completions     (China)
    Authorization: Bearer <key>
IMPORTANT -- TWO SEPARATE PLATFORMS: siliconflow.com (global, USD,
$1 new-user credit) and siliconflow.cn (China, RMB) have SEPARATE
accounts and SEPARATE API keys; a key from one does NOT work on the
other. This plugin defaults to the .com host, auto-detects the wrong
one on a 401 (retrying on the other host and caching the result), and
you can pin either explicitly with ``host='cn'`` / ``host='com'`` or a
full ``endpoint=``.
Setup: sign up at https://siliconflow.com (or .cn) and create a key
in the console; new global accounts get $1 of trial credit, enough
for thousands of OCR calls. Model IDs are namespaced 'vendor/model'.
Default model: 'Qwen/Qwen3-VL-8B-Instruct' (strong OCR, cheap, non-reasoning).
Bigger 'Qwen/Qwen3-VL-235B-A22B-Instruct' reads harder
documents; the '-Thinking' variants emit chain-of-thought, so if one
is pinned the plugin strips <think> blocks and retries with a no-
thinking prompt. A rejected/retired model triggers a live /v1/models
catalog probe (vision models first), with the winner cached.
Images are normalized locally before sending: EXIF rotation applied,
exotic formats (TIFF/WEBP/BMP/GIF/CMYK) re-encoded to PNG or JPEG,
and the pixel count clamped to the range Qwen accepts (56*56,
3584*3584) so the declared mime type always matches the bytes and the
payload stays small enough for the gateway.
The OCR prompt asks for a JSON array of text lines with ``box_2d``
[ymin, xmin, ymax, xmax] on a 0-1000 grid (same convention as the
Gemini/Groq/Claude plugins); Qwen3-VL has genuinely strong grounding,
so the boxes are often good. They are scaled to REAL pixels via the
dimensions of the image that was actually sent (0-1 or raw-pixel
coordinates are detected too), degrading gracefully to ordered rows.
Everything lands in the exact unified structure shared by every
plugin (like OCR.Space).
"""
from re import compile, sub, DOTALL, IGNORECASE
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

_HOSTS = {'com': 'https://api.siliconflow.com', 'cn': 'https://api.siliconflow.cn'}
_CHAT_PATH = '/v1/chat/completions'
_MODELS_PATH = '/v1/models'
_DEFAULT_MODEL = 'Qwen/Qwen3-VL-8B-Instruct'
#: Substrings that PRIORITIZE likely vision models in the catalog
#: (most specific first: the index doubles as the ranking score).
_VISION_HINTS = ('qwen3-vl', 'qwen2.5-vl', 'qwen2-vl', 'glm-4.6v', 'glm-4.5v',
                 'glm-4.1v', 'glm-4v', 'internvl', 'deepseek-vl', 'ocr', 'vision', 'vl')
#: Reasoning models: emit <think> monologues; probe LAST.
_REASONING_HINTS = ('thinking', 'reasoner', 'r1', 'qwq')
#: box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
#: Qwen's accepted pixel area on SiliconFlow: 56*56 .. 3584*3584.
_MIN_PIXELS = 3136
_MAX_PIXELS = 12845056
#: Qwen refuses images whose sides differ by more than this factor.
_MAX_RATIO = 100
#: Keep the base64 data URI (and therefore the JSON body) reasonable.
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
#: Hard ceiling for the completion budget (SiliconFlow wants headroom
#: inside the context window; a huge max_tokens is rejected outright).
_MAX_COMPLETION = 8192
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


class SiliconFlowOcr(OCRPlugin):
    """
    SiliconFlowOcr class.
    """
    #: dead model id -> discovered replacement (process-wide).
    _model_upgrades = {}
    #: api -> host base that authenticated ('com'/'cn' base URL).
    _known_hosts = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: SiliconFlow key (or ``api=``, or the SILICONFLOW_API_KEY environment variable).
        :param host: 'com' (default, global) or 'cn' (China); ignored if a full endpoint= is given.
        :param model: vision model id (default 'Qwen/Qwen3-VL-8B-Instruct').
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param temperature: sampling temperature (default 0).
        :param maxTokens: completion budget (default 4096, capped at 8192).
        :param detail: 'high' (default, full resolution) or 'low' (448x448, 256 tokens, much cheaper).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('SILICONFLOW_API_KEY', ''))
        host = str(kwargs.pop('host', 'com')).lower()
        self.__m_base = _HOSTS.get(host, _HOSTS['com'])
        # A cached known-good host for this key wins over the default.
        if api in SiliconFlowOcr._known_hosts and 'endpoint' not in kwargs:
            self.__m_base = SiliconFlowOcr._known_hosts[api]
        model = kwargs.pop('model', '') or _DEFAULT_MODEL
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_temperature = float(kwargs.pop('temperature', 0))
        self.__m_maxTokens = max(256, min(int(kwargs.pop('maxTokens', 4096)), _MAX_COMPLETION))
        self.__m_detail = str(kwargs.pop('detail', 'high')).lower()
        if self.__m_detail not in ('high', 'low', 'auto'):
            self.__m_detail = 'high'
        self.__m_endpointOverride = kwargs.pop('endpoint', None)
        #: last request summary, used to make HTTP 400 messages useful.
        self.__m_sent = {}
        kwargs.setdefault('timeout', 120)
        super(SiliconFlowOcr, self).__init__(*args, **kwargs)
        # IMPORTANT: after super().__init__(), never before -- the base
        # class initializes its own model/api fields and would reset
        # them to '' (that is what made every request go out with
        # "model": "" and earn a 20015 "parameter is invalid").
        self.setModel(SiliconFlowOcr._model_upgrades.get(model, model))
        self.setOnline(True)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Endpoints / host handling                                          #
    # ------------------------------------------------------------------ #
    def _chatUrl(self):
        if self.__m_endpointOverride:
            return self.__m_endpointOverride
        return self.__m_base + _CHAT_PATH

    def _modelsUrl(self):
        return self.__m_base + _MODELS_PATH

    def _otherBase(self):
        return _HOSTS['cn'] if self.__m_base == _HOSTS['com'] else _HOSTS['com']

    def _headers(self):
        return {'Authorization': 'Bearer {}'.format(self.getApi()), 'Content-Type': 'application/json'}

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _openImage(data):
        """
        Decode raw bytes with PIL so the format is known for certain.
        :param data: (bytes) image bytes of any PIL-readable format.
        :return: PIL.Image.Image
        """
        from io import BytesIO
        from PIL import Image
        try:
            image = Image.open(BytesIO(data))
            image.load()
            return image
        except Exception as error:  # noqa: BLE001 - any decoder failure
            raise OCRError(
                'Cannot decode this image locally ({}). Readable formats '
                'are PNG, JPEG, WEBP, BMP, GIF and TIFF.'.format(error))

    @staticmethod
    def _prepareImage(image):
        """
        Make an image safe for Qwen-VL: upright, 8-bit RGB/greyscale,
        and inside the accepted pixel area.
        :param image: PIL.Image.Image
        :return: PIL.Image.Image
        """
        from PIL import Image, ImageOps
        try:
            image = ImageOps.exif_transpose(image) or image
        except Exception:  # noqa: BLE001 - orientation is best-effort
            pass
        if image.mode not in ('RGB', 'L'):
            # Covers P, RGBA, CMYK, I;16, 1-bit fax TIFF, animated GIF frame 0.
            image = image.convert('RGB')
        width, height = image.size
        if max(width, height) > _MAX_RATIO * max(min(width, height), 1):
            # A sliver of an image (a cropped receipt edge, a bad scan)
            # exceeds Qwen's aspect-ratio limit: pad the short side.
            pad = int(max(width, height) / float(_MAX_RATIO)) + 1
            canvas = Image.new(image.mode, (max(width, pad), max(height, pad)),
                               255 if image.mode == 'L' else (255, 255, 255))
            canvas.paste(image, (0, 0))
            image = canvas
        width, height = image.size
        pixels = max(width * height, 1)
        if pixels > _MAX_PIXELS:
            # Floor, never round: rounding up can land a few hundred
            # pixels past the cap and the gateway then rejects the body.
            scale = (_MAX_PIXELS * 0.995 / float(pixels)) ** 0.5
            image = image.resize((max(int(width * scale), 56),
                                  max(int(height * scale), 56)), Image.LANCZOS)
        elif pixels < _MIN_PIXELS:
            scale = (_MIN_PIXELS * 4.0 / pixels) ** 0.5
            image = image.resize((max(int(round(width * scale)), 56),
                                  max(int(round(height * scale)), 56)), Image.LANCZOS)
        return image

    @staticmethod
    def _encodeImage(image):
        """
        Encode as PNG (lossless, best for text), falling back to JPEG
        when PNG would make the JSON body too big.
        :param image: PIL.Image.Image
        :return: (bytes, str, tuple) payload, its real mime type, and the encoded size.
        """
        from io import BytesIO
        from PIL import Image
        buffer = BytesIO()
        image.save(buffer, format='PNG', optimize=True)
        data = buffer.getvalue()
        if len(data) <= _MAX_IMAGE_BYTES:
            return data, 'image/png', image.size
        # Photographic / noisy pages blow past the budget as PNG. Fall
        # back to JPEG, then to gentler quality and finally to smaller
        # dimensions, so the JSON body always stays sendable.
        candidate = image.convert('RGB')
        for quality in (88, 75, 60):
            buffer = BytesIO()
            candidate.save(buffer, format='JPEG', quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= _MAX_IMAGE_BYTES:
                return data, 'image/jpeg', candidate.size
        for _ in range(4):
            width, height = candidate.size
            if width * height <= _MIN_PIXELS * 16:
                break
            candidate = candidate.resize((max(int(width * 0.75), 56),
                                          max(int(height * 0.75), 56)), Image.LANCZOS)
            buffer = BytesIO()
            candidate.save(buffer, format='JPEG', quality=75, optimize=True)
            data = buffer.getvalue()
            if len(data) <= _MAX_IMAGE_BYTES:
                return data, 'image/jpeg', candidate.size
        return data, 'image/jpeg', candidate.size

    def _imageUrlAndSize(self):
        """
        Build the ``image_url`` value and report the dimensions the
        model will actually see (box_2d scaling depends on them).
        :return: (str, tuple | None) data URI or URL, and (width, height).
        """
        kind = self.imageKind()
        if kind == 'url':
            return self.getImage(), None
        if kind == 'array':
            from PIL import Image
            image = Image.fromarray(self.getImage())
        elif kind in ('path', 'pil', 'bytes', 'buffer'):
            data = self.imageBytes()
            if data[:5] == b'%PDF-':
                raise OCRError(
                    'Qwen-VL takes images, not PDFs. Convert the page to '
                    'an image first, or use a PDF-capable plugin '
                    '(ClaudeOcr, MistralOcr, GlmOcr, OlmOcr).')
            image = self._openImage(data)
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(self.getImage()))
        image = self._prepareImage(image)
        data, mime, size = self._encodeImage(image)
        return 'data:{};base64,{}'.format(mime, b64encode(data).decode('ascii')), size

    # ------------------------------------------------------------------ #
    # Model discovery                                                    #
    # ------------------------------------------------------------------ #
    def _catalogIds(self):
        try:
            reply = get(self._modelsUrl(), headers=self._headers(), timeout=self.getTimeout())
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
            if not any(hint in lowered for hint in _VISION_HINTS):
                continue  # only real vision models
            reasoning = any(hint in lowered for hint in _REASONING_HINTS)
            score = min((index for index, hint in enumerate(_VISION_HINTS) if hint in lowered),
                        default=len(_VISION_HINTS))
            candidates.append(((1 if reasoning else 0, score), model_id))
        candidates.sort(key=lambda pair: pair[0])
        return [model_id for _, model_id in candidates]

    # ------------------------------------------------------------------ #
    # Requests                                                           #
    # ------------------------------------------------------------------ #
    def _request(self, model, image_url, prompt=None, budget=None,
                 base=None):
        url = (base + _CHAT_PATH) if base else self._chatUrl()
        model = str(model or '').strip()
        if not model:
            # Defensive: an empty model is exactly what SiliconFlow
            # answers with 20015 "The parameter is invalid".
            raise OCRError(
                'No SiliconFlow model set. Pass '
                "model='Qwen/Qwen3-VL-8B-Instruct' (or another vision "
                'model from your account).')
        text = prompt or self.composePrompt(self.__m_prompt) or _OCR_PROMPT
        budget = max(256, min(int(budget or self.__m_maxTokens), _MAX_COMPLETION))
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'image_url',
                 'image_url': {'url': image_url, 'detail': self.__m_detail}},
                {'type': 'text', 'text': str(text)},
            ]}],
            'temperature': self.__m_temperature,
            'max_tokens': budget,
            'stream': False,
        }
        self.__m_sent = {
            'model': model, 'temperature': self.__m_temperature, 'max_tokens': budget,
            'detail': self.__m_detail, 'prompt_chars': len(str(text)),
            'image': (image_url.split(',', 1)[0] if image_url.startswith('data:') else image_url)[:60],
            'image_chars': len(image_url),
        }
        request_kwargs = {
            'json': body,
            'headers': self._headers(),
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        return post(url, **request_kwargs)

    def _sentDigest(self):
        """
        Human-readable summary of the last request (no base64 blob).
        :return: str
        """
        if not self.__m_sent:
            return 'nothing sent yet'
        return ', '.join('{}={}'.format(key, self.__m_sent[key]) for key in sorted(self.__m_sent))

    @staticmethod
    def _errorCode(reply):
        """
        SiliconFlow's numeric error code, or 0 when absent.
        20012 = unknown model, 20015 = invalid body parameter.
        :return: int
        """
        try:
            return int((reply.json() or {}).get('code') or 0)
        except Exception:  # noqa: BLE001 - non-JSON error bodies happen
            return 0

    @staticmethod
    def _content(payload):
        choices = payload.get('choices') or []
        if not choices:
            return ''
        message = choices[0].get('message') or {}
        content = message.get('content') or ''
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        # Some Qwen thinking variants expose reasoning separately; we
        # only want the answer content, so ignore reasoning_content.
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
        retry = self._request(self.getModel(), image_url, prompt=no_think, budget=_MAX_COMPLETION)
        if retry.ok:
            return retry, self._extractAnswer(self._content(retry.json()))
        return reply, ''

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    @classmethod
    def _parseItems(cls, text, size):
        """
        Turn the model's JSON array into word dicts in real pixels.
        Coordinates are accepted on the requested 0-1000 grid, as 0-1
        fractions, or already in pixels; the scale is inferred.
        :param text: (str) the model's answer.
        :param size: (tuple | None) dimensions of the image that was sent.
        :return: list[dict]
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
        if not isinstance(items, list):
            return []
        entries, texts = [], []
        for item in items:
            if isinstance(item, str):
                if item.strip():
                    texts.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            label = str(item.get('text') or item.get('line') or '').strip()
            if not label:
                continue
            texts.append(label)
            box = item.get('box_2d') or item.get('box') or item.get('bbox') or []
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                ymin, xmin, ymax, xmax = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            if ymax <= ymin or xmax <= xmin:
                continue
            entries.append((label, ymin, xmin, ymax, xmax))
        if not entries:
            return cls._rowsFromTexts(texts)
        peak = max(max(abs(v) for v in entry[1:]) for entry in entries)
        if peak <= 1.5:
            xScale, yScale = float(width), float(height)  # 0-1 fractions
        elif peak > _COORD_SPACE * 1.02:
            xScale, yScale = 1.0, 1.0  # already pixels
        else:
            xScale, yScale = width / _COORD_SPACE, height / _COORD_SPACE
        boxed = []
        for label, ymin, xmin, ymax, xmax in entries:
            boxed.append(cls.makeWord(
                label,
                xmin * xScale,
                ymin * yScale,
                max((xmax - xmin) * xScale, 1.0),
                max((ymax - ymin) * yScale, 1.0),
            ))
        return boxed or cls._rowsFromTexts(texts)

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
                'No SiliconFlow API key. Sign up at '
                'https://siliconflow.com (global) or '
                'https://siliconflow.cn (China) -- they are SEPARATE '
                'accounts -- create a key, and pass api=... or set '
                'the SILICONFLOW_API_KEY environment variable.')
        image_url, size = self._imageUrlAndSize()
        reply, answer = self._answerFor(image_url)
        # Wrong-host detection: a 401 on one host may mean the key
        # belongs to the OTHER SiliconFlow platform.
        if reply.status_code in (401, 403) and not self.__m_endpointOverride:
            other = self._otherBase()
            probe = self._request(self.getModel(), image_url, base=other)
            if probe.ok or probe.status_code not in (401, 403):
                self.__m_base = other
                SiliconFlowOcr._known_hosts[self.getApi()] = other
                reply, answer = self._answerFor(image_url)
        # Retired / unavailable model: SiliconFlow says so either in
        # words ("Model does not exist") or with code 20012.
        if reply.status_code in (400, 404) and (
                'model' in (reply.text or '').lower() or self._errorCode(reply) == 20012):
            failed_model = self.getModel()
            probed = []
            for candidate in self._candidateVisionModels(exclude=failed_model)[:6]:
                probe = self._request(candidate, image_url)
                probed.append('{} -> HTTP {}'.format(candidate, probe.status_code))
                if probe.ok:
                    SiliconFlowOcr._model_upgrades[failed_model] = candidate
                    self.setModel(candidate)
                    reply, answer = self._answerFor(image_url)
                    break
                if probe.status_code == 429:
                    reply = probe
                    break
            else:
                catalog = self._catalogIds()
                raise OCRError(
                    "SiliconFlow rejected model '{}' and no catalog "
                    'vision model worked (probes: {}). Vision models '
                    'on your account: {}. Pick one and pass '
                    "model='vendor/Model' .".format(
                        failed_model, '; '.join(probed) or 'none', ', '.join(m for m in catalog if any(
                            h in m.lower() for h in _VISION_HINTS))[:250] or 'none listed'))
        if reply.status_code in (401, 403):
            raise OCRError(
                'SiliconFlow rejected the API key (HTTP {}) on both '
                'hosts. Make sure the key matches the platform: '
                'siliconflow.com and siliconflow.cn have SEPARATE '
                'keys. Check the console. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if reply.status_code == 402:
            raise OCRError(
                'SiliconFlow says the account is out of credit: top '
                'up in the console (new global accounts get $1 free). '
                'API said: ' + (reply.text or '')[:200])
        if reply.status_code == 429:
            raise OCRError(
                'SiliconFlow rate limit hit (429; free tier ~1,000 '
                'RPM but low-spend tiers are throttled). API said: ' + (reply.text or '')[:200])
        if reply.status_code in (500, 502, 503):
            raise OCRError(
                'SiliconFlow is having trouble (HTTP {}): retried '
                'automatically. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if reply.status_code == 400:
            # Body validation (usually code 20015). The message never
            # names the field, so report what was actually sent.
            raise OCRError(
                'SiliconFlow rejected the request body (HTTP 400, code {}): {}. '
                'Sent: {}. This is body validation, not a key or credit '
                'problem -- check the model id, max_tokens and the '
                'image.'.format(self._errorCode(reply) or '-', (reply.text or '')[:200], self._sentDigest()))
        if not reply.ok:
            raise OCRError('HTTP {} from SiliconFlow: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('error'):
            raise OCRError('SiliconFlow error: {}'.format(
                str(payload['error'])[:300]))
        if not answer:
            raise OCRError(
                "Model '{}' produced no answer (a -Thinking model may "
                'have spent the budget reasoning). Use a non-thinking '
                "vision model, e.g. model='Qwen/Qwen3-VL-8B-Instruct'.".format(self.getModel()))
        words = self._parseItems(answer, size)
        if not words:
            words = self._plainToRows(answer)
        if not words:
            raise OCRError('SiliconFlow returned no text for this image.')
        return words
