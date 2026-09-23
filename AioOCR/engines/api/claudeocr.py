# coding=utf-8
"""
Claude OCR plugin (Anthropic Messages API).
Claude's vision reads text in images very accurately (screenshots,
photos, handwriting, tables) and can also ingest PDFs natively (up to
100 pages). This plugin drives the Messages API directly::
    POST https://api.anthropic.com/v1/messages
    x-api-key: <key>
    anthropic-version: 2023-06-01
Setup: create an API key at https://console.anthropic.com (the API is
pay-per-token -- no permanent free tier -- but OCR-sized calls are
cheap: with the Haiku model a screenshot costs a fraction of a cent).
Default model: 'claude-sonnet-4-6' (good text accuracy and spatial
estimates). Pass model='claude-haiku-4-5-20251001' for the cheapest/
fastest option, or an Opus model for the hardest documents.
The OCR prompt asks for a JSON array of text lines with ``box_2d``
[ymin, xmin, ymax, xmax] on a 0-1000 grid (the same convention as the
Gemini/Groq/OpenRouter plugins). Boxes are scaled to REAL pixels using
the local image dimensions. Like all VLMs, Claude's box estimates are
approximate rather than detector-precise; parsing degrades gracefully
to ordered rows when boxes are missing. Everything lands in the exact
unified structure shared by every plugin (like OCR.Space).
Limits per the API: images up to 5 MB each (jpeg/png/gif/webp), PDFs
up to 32 MB / 100 pages.
"""
from base64 import b64encode
from os.path import dirname
from requests import post
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

_ENDPOINT = 'https://api.anthropic.com/v1/messages'
_API_VERSION = '2023-06-01'
#: box_2d coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
_IMAGE_LIMIT = 5 * 1024 * 1024  # per image
_PDF_LIMIT = 32 * 1024 * 1024  # per request
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


class ClaudeOcr(OCRPlugin):
    """
    ClaudeOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: Anthropic API key (or ``api=``, or the
                       ANTHROPIC_API_KEY environment variable), from https://console.anthropic.com.
        :param model: model id (default 'claude-sonnet-4-6'; e.g.
                      'claude-haiku-4-5-20251001' for cheapest, 'claude-opus-4-8' for hardest documents).
        :param prompt: extra instructions appended to the built-in OCR prompt (base-class feature; e.g.
                       'the text is French', 'read only the handwritten totals'). Change at runtime
                       with ``setExtraPrompt()``.
        :param ocrPrompt: replace the built-in OCR prompt entirely (advanced; keep the JSON contract
                          or parsing falls back to plain rows).
        :param maxTokens: generation budget (default 4096).
        :param image: image/PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('ANTHROPIC_API_KEY', ''))
        model = kwargs.pop('model', 'claude-sonnet-4-6')
        self.__m_prompt = kwargs.pop('ocrPrompt', _OCR_PROMPT)  # 'prompt' stays for the base class
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 4096))
        kwargs.setdefault('endpoint', _ENDPOINT)
        kwargs.setdefault('timeout', 120)  # PDFs take a while.
        super(ClaudeOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setModel(model)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _mediaBlockAndSize(self):
        """
        Build the image/document content block; return (block, size).
        """
        kind = self.imageKind()
        if kind == 'url':
            # Claude fetches URL images itself; pixel size unknown ->
            # 1000-grid coordinates.
            return {'type': 'image', 'source': {'type': 'url', 'url': self.getImage()}}, None
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
            if len(data) > _PDF_LIMIT:
                raise OCRError(
                    'PDF is {:.1f} MB but the Claude API accepts at '
                    'most 32 MB per request. Split the document first.'.format(len(data) / 1048576.0))
            return ({'type': 'document',
                     'source': {'type': 'base64', 'media_type': 'application/pdf',
                                'data': b64encode(data).decode('ascii')}}, None)
        if len(data) > _IMAGE_LIMIT:
            raise OCRError(
                'Image is {:.1f} MB but the Claude API accepts at most '
                '5 MB per image. Downscale or recompress it first.'.format(len(data) / 1048576.0))
        if data[:3] == b'\xff\xd8\xff':
            media_type = 'image/jpeg'
        elif data[:6] in (b'GIF87a', b'GIF89a'):
            media_type = 'image/gif'
        elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            media_type = 'image/webp'
        else:
            media_type = 'image/png'
        size = None
        try:
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(data)) as opened:
                size = opened.size
        except Exception:  # noqa: BLE001 - size is best-effort
            size = None
        return ({'type': 'image',
                 'source': {'type': 'base64', 'media_type': media_type, 'data': b64encode(data).decode('ascii')}}, size)

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    @classmethod
    def _parseItems(cls, text, size):
        """
        Claude's JSON -> word dicts (grounded boxes or ordered rows).
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
                'No Anthropic API key. Create one at '
                'https://console.anthropic.com and pass api=... or '
                'set the ANTHROPIC_API_KEY environment variable.')
        block, size = self._mediaBlockAndSize()
        body = {
            'model': self.getModel(),
            'max_tokens': self.__m_max_tokens,
            'messages': [{'role': 'user', 'content': [
                block,  # media first,
                {'type': 'text', 'text': self.composePrompt(self.__m_prompt)},  # per the docs
            ]}],
        }
        request_kwargs = {
            'json': body,
            'headers': {
                'x-api-key': self.getApi(),
                'anthropic-version': _API_VERSION,
                'Content-Type': 'application/json',
            },
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code == 401:
            raise OCRError(
                'Anthropic rejected the API key (401): check it at '
                'https://console.anthropic.com. API said: ' + (reply.text or '')[:200])
        if reply.status_code == 429:
            raise OCRError(
                'Anthropic rate limit hit (429): the base class '
                'retries with backoff; sustained limits depend on '
                'your usage tier. API said: ' + (reply.text or '')[:200])
        if reply.status_code in (500, 529):
            raise OCRError(
                'Anthropic is overloaded (HTTP {}): retried '
                'automatically. API said: {}'.format(reply.status_code, (reply.text or '')[:200]))
        if not reply.ok:
            raise OCRError('HTTP {} from Anthropic: {}'.format(
                reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        if payload.get('type') == 'error' or payload.get('error'):
            raise OCRError('Anthropic error: {}'.format(str(payload.get('error') or payload)[:300]))
        text = ''.join(
            part.get('text', '')
            for part in payload.get('content') or [] if isinstance(part, dict) and part.get('type') == 'text')
        words = self._parseItems(text, size)
        if not words:
            words = self._plainToRows(text)
        if not words:
            raise OCRError('Claude returned no text for this image.')
        return words
