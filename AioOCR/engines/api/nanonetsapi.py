# coding=utf-8
"""
Nanonets OCR plugin (https://nanonets.com).
Nanonets runs two OCR API generations, and this plugin drives both:
- **FullText OCR** (default, ``mode='fulltext'``) -- WORD-level pixel
  boxes, the OCR.Space-style geometry tier::
      POST https://app.nanonets.com/api/v2/OCR/FullText
      Authorization: Basic base64(APIKEY:)     # key as username,
      multipart: file=<binary>  (or urls=<url>)  # blank password
- **Extraction API** (``mode='extract'``) -- Nanonets' flagship
  document model (Nanonets-OCR2, #1 on the IDP leaderboard): superb
  markdown text for PDFs/Word/Excel/images, but no coordinates
  (ordered rows in the unified structure)::
      POST https://extraction-api.nanonets.com/api/v1/extract/sync
      Authorization: Bearer <key>
      multipart: file=<binary>, output_format=markdown
If the legacy FullText endpoint ever disappears (404/410), the plugin
falls through to the extraction API automatically.
Setup: sign up at https://app.nanonets.com and copy the API key from
My Account > API Keys (https://app.nanonets.com/#/keys). The free
developer key includes a limited monthly page allowance; paid plans scale up.
Word boxes arrive as ``results[].page_data[].words[]`` with
``text``/``left``/``top``/``right``/``bottom`` in pixels (plus
per-word confidence when available, powering ``minConfidence``).
Multipage documents stack vertically. Everything lands in the exact
unified structure shared by every plugin (like OCR.Space).
"""
from base64 import b64encode
from os.path import dirname
from requests import post
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

_FULLTEXT_ENDPOINT = 'https://app.nanonets.com/api/v2/OCR/FullText'
_EXTRACT_ENDPOINT = 'https://extraction-api.nanonets.com/api/v1/extract/sync'
#: Vertical gap inserted between stacked pages.
_PAGE_GAP = 50.0
_MODES = ('fulltext', 'extract')


class NanonetsOcr(OCRPlugin):
    """
    NanonetsOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: Nanonets API key (or ``api=``, or the
                       NANONETS_API_KEY environment variable), from https://app.nanonets.com/#/keys.
        :param mode: 'fulltext' (default; word pixel boxes) or
                     'extract' (flagship Nanonets-OCR2 Markdown, rows only).
        :param outputFormat: extraction-mode output (default 'markdown').
        :param minConfidence: drop words below this confidence, 0-1 (fulltext mode; default 0).
        :param image: image/PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (endpoint override for fulltext,
                       extractEndpoint for the extraction API, timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('NANONETS_API_KEY', ''))
        mode = str(kwargs.pop('mode', 'fulltext')).lower()
        if mode not in _MODES:
            raise OCRError('Unknown mode {!r}; choose from {}'.format(mode, ', '.join(_MODES)))
        self.__m_mode = mode
        self.__m_output_format = kwargs.pop('outputFormat', 'markdown')
        self.__m_min_confidence = float(kwargs.pop('minConfidence', 0.0))
        self.__m_extract_endpoint = kwargs.pop('extractEndpoint', _EXTRACT_ENDPOINT)
        kwargs.setdefault('endpoint', _FULLTEXT_ENDPOINT)
        kwargs.setdefault('timeout', 120)
        super(NanonetsOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _fileTuple(self):
        """
        (name, bytes, mime) for multipart upload; None for URLs.
        """
        kind = self.imageKind()
        if kind == 'url':
            return None
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
            return 'document.pdf', data, 'application/pdf'
        if data[:3] == b'\xff\xd8\xff':
            return 'image.jpg', data, 'image/jpeg'
        return 'image.png', data, 'image/png'

    def _downloadedFileTuple(self):
        """
        Download a URL source for APIs that want the bytes.
        """
        from requests import get
        reply = get(self.getImage(), timeout=self.getTimeout())
        reply.raise_for_status()
        data = reply.content
        if data[:5] == b'%PDF-':
            return 'document.pdf', data, 'application/pdf'
        if data[:3] == b'\xff\xd8\xff':
            return 'image.jpg', data, 'image/jpeg'
        return 'image.png', data, 'image/png'

    # ------------------------------------------------------------------ #
    # FullText mode (word pixel boxes)                                   #
    # ------------------------------------------------------------------ #
    def _fulltextRequest(self):
        request_kwargs = {
            'headers': {'Authorization': 'Basic ' + b64encode((self.getApi() + ':').encode('ascii')).decode('ascii')},
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        upload = self._fileTuple()
        if upload is None:
            return post(self.getEndpoint(),
                        data={'urls': self.getImage()},
                        **request_kwargs)
        return post(self.getEndpoint(), files={'file': upload},
                    **request_kwargs)

    def _wordsFromFulltext(self, payload):
        """
        results[].page_data[].words[] -> unified word dicts.
        """
        words = []
        y_offset = 0.0
        for result in (payload or {}).get('results') or []:
            for page in result.get('page_data') or []:
                page_words = []
                for word in page.get('words') or []:
                    text = str(word.get('text', '')).strip()
                    if not text:
                        continue
                    confidence = word.get('confidence', word.get('score'))
                    if confidence is not None:
                        try:
                            if float(confidence) < self.__m_min_confidence:
                                continue
                        except (TypeError, ValueError):
                            pass
                    try:
                        left = float(word['left'])
                        top = float(word['top'])
                        right = float(word['right'])
                        bottom = float(word['bottom'])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if right <= left or bottom <= top:
                        continue
                    page_words.append(self.makeWord(text, left, y_offset + top, right - left, bottom - top))
                if not page_words:
                    page_words = self._textToRows(page.get('raw_text', ''), start_row=len(words))
                words.extend(page_words)
                size = page.get('size') or {}
                page_height = float(size.get('height', 0) or 0)
                if page_height <= 0:
                    page_height = max((w['Top'] + w['Height'] for w in page_words), default=y_offset) - y_offset
                y_offset += page_height + _PAGE_GAP
        return words

    # ------------------------------------------------------------------ #
    # Extraction mode (flagship model, Markdown rows)                    #
    # ------------------------------------------------------------------ #
    def _extractRequest(self):
        request_kwargs = {
            'headers': {'Authorization': 'Bearer {}'.format(self.getApi())},
            'data': {'output_format': self.__m_output_format},
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        upload = self._fileTuple() or self._downloadedFileTuple()
        return post(self.__m_extract_endpoint, files={'file': upload}, **request_kwargs)

    @classmethod
    def _extractText(cls, payload):
        """
        Find the extracted text across response envelope variants.
        """
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return ''
        for key in ('content', 'markdown', 'text', 'result', 'output', 'extracted_text'):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = cls._extractText(value)
                if nested:
                    return nested
        data = payload.get('data')
        if isinstance(data, (dict, str)):
            return cls._extractText(data)
        if isinstance(data, list):
            parts = [cls._extractText(item) for item in data]
            return '\n'.join(part for part in parts if part)
        return ''

    @classmethod
    def _textToRows(cls, text, start_row=0):
        words = []
        row = start_row
        for line in (text or '').splitlines():
            line = sub(r'^[#>*\s|-]+', '', line)
            line = sub(r'\|', ' ', line)
            line = sub(r'\s+', ' ', line).strip()
            if not line or set(line) <= set('-: '):
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # Shared error triage                                                #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _triage(reply, which):
        if reply.status_code in (401, 403):
            raise OCRError(
                'Nanonets rejected the API key (HTTP {} from the {} '
                'API): copy it from https://app.nanonets.com/#/keys. '
                'API said: {}'.format(reply.status_code, which, (reply.text or '')[:200]))
        if reply.status_code in (402, 429):
            raise OCRError(
                'Nanonets quota/rate limit hit (HTTP {}; the free '
                'developer key has a limited monthly page allowance). '
                'API said: {}'.format(reply.status_code, (reply.text or '')[:200]))

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No Nanonets API key. Sign up at https://app.nanonets.com, copy the key from My '
                'Account > API Keys, and pass api=... or set the NANONETS_API_KEY environment variable.')
        if self.__m_mode == 'fulltext':
            reply = self._fulltextRequest()
            if reply.status_code in (404, 410):
                # Legacy endpoint retired: fall through to the
                # flagship extraction API (rows only).
                reply = self._extractRequest()
                self._triage(reply, 'extraction')
                if not reply.ok:
                    raise OCRError(
                        'HTTP {} from Nanonets extraction: {}'.format(reply.status_code, (reply.text or '')[:250]))
                words = self._textToRows(self._extractText(self._json(reply)))
                if not words:
                    raise OCRError('Nanonets returned no text for this document.')
                return words
            self._triage(reply, 'FullText')
            if not reply.ok:
                raise OCRError('HTTP {} from Nanonets FullText: {}'.format(reply.status_code, (reply.text or '')[:250]))
            payload = self._json(reply)
            message = str(payload.get('message', ''))
            if message and message.lower() not in ('success', 'ok', ''):
                raise OCRError('Nanonets reported: ' + message[:250])
            words = self._wordsFromFulltext(payload)
            if not words:
                raise OCRError('Nanonets found no text in this document.')
            return words
        # extract mode
        reply = self._extractRequest()
        self._triage(reply, 'extraction')
        if not reply.ok:
            raise OCRError('HTTP {} from Nanonets extraction: {}'.format(reply.status_code, (reply.text or '')[:250]))
        words = self._textToRows(self._extractText(self._json(reply)))
        if not words:
            raise OCRError('Nanonets returned no text for this document.')
        return words

    @staticmethod
    def _json(reply):
        try:
            return reply.json()
        except ValueError:
            raise OCRError('Nanonets returned a non-JSON reply: ' + (reply.text or '')[:200])
