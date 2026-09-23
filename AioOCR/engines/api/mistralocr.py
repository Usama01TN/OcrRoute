# coding=utf-8
"""
Mistral OCR plugin (official ``mistralai`` SDK, mistral-ocr-latest).
Setup::
    pip install mistralai
Create an API key at Mistral AI Studio (https://console.mistral.ai).
The free "Experiment" tier (phone verification required) includes
rate-limited access to the OCR endpoint; paid usage is about $1 per 1,000 pages.
The OCR endpoint takes either a ``document_url`` (PDFs) or an
``image_url`` (images), both accepting public URLs or base64 data
URIs -- so local files, PIL images and raw bytes are sent inline
without any upload step. The reply carries one Markdown string per
page (plus page dimensions); Mistral does not return per-line text
coordinates, so reading order is preserved with synthesized row boxes
in the same unified structure as every other plugin, with multipage
documents stacked in order.
"""
from base64 import b64encode
from os.path import dirname
from os import environ
from re import sub
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from mistralai.client import Mistral
except:
    from mistralai import Mistral

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError


class MistralOcr(OCRPlugin):
    """
    MistralOcr class.
    """
    #: api -> Mistral client (holds connection pools).
    _clients = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: Mistral API key (or ``api=``, or the MISTRAL_API_KEY environment variable).
        :param model: OCR model id (default 'mistral-ocr-latest').
        :param pages: optional list of 0-based page indices to process (PDFs; default: all pages).
        :param docType: force 'document' or 'image' routing when the auto-detection guesses wrong
                        (e.g. an image URL without a file extension).
        :param includeImages: also request embedded images as base64
                                (default False; they are ignored by the unified structure anyway).
        :param includeBlocks: request paragraph-level bounding boxes (OCR 4+ feature; default True). Each
                              block carries pixel coordinates, so the unified structure gets REAL geometry like
                              OCR.Space. Automatically retried without it on models that predate the feature.
        :param minConfidence: drop blocks below this content-confidence (0-1; default 0; needs OCR 4+).
        :param image: PDF or image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (timeout, retries...).
        """
        api = kwargs.pop('api', environ.get('MISTRAL_API_KEY', ''))
        model = kwargs.pop('model', 'mistral-ocr-latest')
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_doc_type = kwargs.pop('docType', None)
        self.__m_include_images = bool(kwargs.pop('includeImages', False))
        self.__m_include_blocks = bool(kwargs.pop('includeBlocks', True))
        self.__m_min_confidence = float(kwargs.pop('minConfidence', 0.0))
        kwargs.setdefault('timeout', 120)  # large PDFs take a while
        super(MistralOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setModel(model)
        self.setApi(api)

    # ------------------------------------------------------------------ #
    # Client                                                             #
    # ------------------------------------------------------------------ #
    def _client(self):
        key = self.getApi()
        if key not in MistralOcr._clients:
            MistralOcr._clients[key] = Mistral(api_key=key)
        return MistralOcr._clients[key]

    # ------------------------------------------------------------------ #
    # Document building                                                  #
    # ------------------------------------------------------------------ #
    def _document(self):
        """
        Build the ``document`` argument for client.ocr.process.
        """
        kind = self.imageKind()
        source = self.getImage()
        if kind == 'url':
            is_pdf = source.lower().split('?')[0].endswith('.pdf')
            if self.__m_doc_type:
                is_pdf = self.__m_doc_type == 'document'
            if is_pdf:
                return {'type': 'document_url', 'document_url': source}
            return {'type': 'image_url', 'image_url': source}
        if kind == 'array':
            from io import BytesIO
            from PIL import Image
            buffer = BytesIO()
            Image.fromarray(source).save(buffer, format='PNG')
            data = buffer.getvalue()
        elif kind in ('path', 'pil', 'bytes', 'buffer'):
            data = self.imageBytes()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(source))
        encoded = b64encode(data).decode('ascii')
        if data[:5] == b'%PDF-':
            return {'type': 'document_url', 'document_url': 'data:application/pdf;base64,' + encoded}
        mime = ('image/jpeg' if data[:3] == b'\xff\xd8\xff' else 'image/png')
        return {'type': 'image_url', 'image_url': 'data:{};base64,{}'.format(mime, encoded)}

    # ------------------------------------------------------------------ #
    # Result mapping                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _field(obj, *names):
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            value = getattr(obj, name, None)
            if value is not None:
                return value
        return None

    @classmethod
    def _markdownToRows(cls, markdown, start_row=0):
        """
        Markdown lines -> ordered rows (markup lightened).
        """
        words = []
        row = start_row
        for line in (markdown or '').splitlines():
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)  # images
            line = sub(r'\|', ' ', line).strip()
            if not line or set(line) <= set('-: '):
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    #: Block types that carry no readable page text.
    _SKIP_BLOCK_TYPES = frozenset(('image', 'figure', 'picture', 'chart'))

    def _wordsFromBlocks(self, page, y_offset):
        """
        OCR 4 blocks -> word dicts with REAL pixel coordinates
        (top_left_x/y, bottom_right_x/y per the OCR docs). Multi-line
        block content is split so the base class rebuilds proper lines.
        """
        words = []
        for block in self._field(page, 'blocks') or []:
            block_type = str(self._field(block, 'type') or '').lower()
            if block_type in self._SKIP_BLOCK_TYPES:
                continue
            content = str(self._field(block, 'content', 'text') or '').strip()
            if not content:
                continue
            if self.__m_min_confidence > 0:
                scores = self._field(block, 'confidence_scores') or {}
                confidence = self._field(scores, 'average_content_confidence_score')
                if confidence is not None and float(confidence) < self.__m_min_confidence:
                    continue
            x1 = self._field(block, 'top_left_x')
            y1 = self._field(block, 'top_left_y')
            x2 = self._field(block, 'bottom_right_x')
            y2 = self._field(block, 'bottom_right_y')
            if None in (x1, y1, x2, y2):
                continue
            x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
            if x2 <= x1 or y2 <= y1:
                continue
            lines = [l for l in (self._cleanLine(raw) for raw in content.splitlines()) if l]
            line_height = (y2 - y1) / max(len(lines), 1)
            for i, line in enumerate(lines):
                words.append(self.makeWord(
                    line, x1, y_offset + y1 + i * line_height, max(x2 - x1, 1.0), max(line_height, 1.0)))
        return words

    @staticmethod
    def _cleanLine(line):
        """
        Lighten Markdown markup inside a text line.
        """
        line = sub(r'^[#>\s]+', '', line)
        line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)
        line = sub(r'\|', ' ', line).strip()
        if not line or set(line) <= set('-: '):
            return ''
        return line

    def _pageHeight(self, page):
        """
        Best-effort page height for multipage vertical stacking.
        """
        dimensions = self._field(page, 'dimensions') or {}
        height = self._field(dimensions, 'height')
        try:
            return float(height) if height else 0.0
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _process(self, include_blocks):
        """
        One client.ocr.process call; returns the SDK response.
        """
        process_kwargs = {'model': self.getModel(), 'document': self._document(),
                          'include_image_base64': self.__m_include_images}
        if include_blocks:
            process_kwargs['include_blocks'] = True
        if self.__m_pages is not None:
            process_kwargs['pages'] = list(self.__m_pages)
        return self._client().ocr.process(**process_kwargs)

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        if not self.getApi():
            raise OCRError(
                'No Mistral API key. Create one at '
                'https://console.mistral.ai (the free Experiment tier '
                'includes OCR) and pass api=... or set the '
                'MISTRAL_API_KEY environment variable.')
        try:
            try:
                response = self._process(self.__m_include_blocks)
            except TypeError as exc:
                # Older SDK/model without the include_blocks parameter:
                # retry plain and fall back to Markdown rows.
                if 'include_blocks' not in str(exc):
                    raise
                response = self._process(False)
        except Exception as exc:  # noqa: BLE001 - SDK classes vary
            text = str(exc)
            lowered = text.lower()
            if 'include_blocks' in lowered or 'unknown parameter' in lowered:
                # API-side rejection (pinned old model): retry plain.
                response = self._process(False)
            elif '401' in text or 'unauthorized' in lowered:
                raise OCRError(
                    'Mistral rejected the API key (401). Check the key '
                    'from https://console.mistral.ai. Original: ' + text[:200])
            elif '429' in text or 'rate limit' in lowered or 'capacity' in lowered:
                raise OCRError(
                    'Mistral rate limit hit (the free Experiment tier '
                    'is rate-limited; paid tiers raise the limits). Original: ' + text[:200])
            else:
                raise OCRError('Mistral OCR failed: ' + text[:300])
        pages = self._field(response, 'pages') or []
        pages = sorted(pages, key=lambda p: self._field(p, 'index') or 0)
        # Preferred path: OCR 4 blocks with real pixel geometry.
        words = []
        y_offset = 0.0
        for page in pages:
            page_words = self._wordsFromBlocks(page, y_offset)
            words.extend(page_words)
            y_offset += self._pageHeight(page) or (max((
                w['Top'] + w['Height'] for w in page_words), default=0.0) - y_offset)
        if words:
            return words
        # Fallback: Markdown per page as ordered rows (pre-OCR-4
        # models, or blocks unavailable for this document type).
        for page in pages:
            markdown = self._field(page, 'markdown') or ''
            words.extend(self._markdownToRows(markdown, start_row=len(words)))
        if not words:
            raise OCRError('Mistral OCR returned no text for this document.')
        return words
