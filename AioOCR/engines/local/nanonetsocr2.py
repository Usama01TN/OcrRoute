# coding=utf-8
"""
Nanonets-OCR2 plugin (local, https://huggingface.co/nanonets/Nanonets-OCR2-3B).
Nanonets-OCR2-3B is an image-to-markdown model (Qwen2.5-VL-3B
fine-tune, 4B params BF16) built for LLM-ready documents. What makes
it distinctive is SEMANTIC TAGGING rather than plain transcription:
- LaTeX equations, inline ($...$) and display ($$...$$)
- ``<img>`` descriptions for figures/logos/charts without captions
- ``<signature>`` isolation (legal/business documents)
- ``<watermark>`` extraction
- ``<page_number>`` tagging
- Unicode checkboxes: (unchecked), (checked), (crossed)
- Complex tables as HTML
- Flow/org charts as MERMAID code
- Handwriting and many languages (English, Chinese, French, Spanish,
  Portuguese, German, Italian, Russian, Japanese, Korean, Arabic...)
- VQA: ask a question about the page; the model answers directly, or
  says "Not mentioned" when the answer is not in the document.
Benchmarks: 69.5 on olmOCR-bench, DocVQA 89.43 (above Qwen2.5-VL-72B
and Gemini 2.5 Flash on that dataset), and in the card's pairwise
Markdown evaluation it beats Gemini 2.5 Flash and GPT-5.
Install::
    pip install transformers torch pillow accelerate
    pip install pypdfium2        # only for PDF input
This plugin follows the card's transformers recipe exactly:
AutoModelForImageTextToText with torch_dtype='auto',
flash_attention_2 tried first (dropped where unavailable), the
official system message, the OFFICIAL PROMPT VERBATIM, and greedy
decoding with prompt trimming per input row.
Options worth knowing:
- ``financial=True`` swaps in the card's financial-documents prompt
  ("Only return HTML table within <table></table>") and sets
  ``repetition_penalty=1``, the card's own tip for table-heavy
  financial pages.
- ``question='...'`` switches to VQA mode.
- ``prompt='...'`` overrides everything.
- Tag handling: ``keepImages`` (default False -- <img> blurbs are
  descriptions, not page text), ``keepWatermarks``/``keepSignatures``/
  ``keepPageNumbers`` (default True, rendered as readable markers).
- Higher input resolution improves accuracy (the card's first tip),
  so PDFs rasterize at ~300 DPI by default (``pdfDpi=``).
Output is markdown/HTML text mapped to ordered rows in the unified
structure (like OCR.Space's shape, rows tier) -- the model returns no
coordinates. For block boxes use DotsOcr/UnlimitedOcr; for word boxes
RapidOcr or the hosted word-box APIs. Note the separate
``nanonetsocr.py`` plugin, which calls Nanonets' HOSTED API instead
(this one runs locally, no key, no quota).
"""
from os.path import dirname
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

_DEFAULT_MODEL = 'nanonets/Nanonets-OCR2-3B'
#: The card's official OCR prompt (verbatim).
_PROMPT = """Extract the text from the above document as if you were reading it naturally. Return the tables in html format. Return the equations in LaTeX representation. If there is an image in the document and image caption is not present, add a small description of the image inside the <img></img> tag; otherwise, add the image caption inside <img></img>. Watermarks should be wrapped in brackets. Ex: <watermark>OFFICIAL COPY</watermark>. Page numbers should be wrapped in brackets. Ex: <page_number>14</page_number> or <page_number>9/22</page_number>. Prefer using \u2610 and \u2611 for check boxes."""
#: The card's alternative prompt for table-heavy financial documents.
_PROMPT_FINANCIAL = """Extract the text from the above document as if you were reading it naturally. Return the tables in HTML format. Return the equations in LaTeX representation. If there is an image in the document and image caption is not present, add a small description of the image inside the <img></img> tag; otherwise, add the image caption inside <img></img>. Watermarks should be wrapped in brackets. Ex: <watermark>OFFICIAL COPY</watermark>. Page numbers should be wrapped in brackets. Ex: <page_number>14</page_number> or <page_number>9/22</page_number>. Prefer using \u2610 and \u2611 for check boxes. Only return HTML table within <table></table>."""
#: The card's system message.
_SYSTEM = 'You are a helpful assistant.'
#: Default PDF rasterization DPI (higher resolution reads better).
_PDF_DPI = 300.0


class NanonetsOcr2(OCRPlugin):
    """
    NanonetsOcr2 class.
    """
    #: model name -> (processor, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'nanonets/Nanonets-OCR2-3B';
                      also 'nanonets/Nanonets-OCR2-1.5B-exp', or a llama.cpp/Ollama quantization).
        :param financial: use the card's financial-documents prompt and repetition_penalty=1 (default False).
        :param question: ask a question about the page (VQA mode); the model answers or says 'Not mentioned'.
        :param ocrPrompt: custom prompt overriding the above (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``.
        :param keepImages: keep <img> description text (default False).
        :param keepWatermarks: keep watermark text (default True).
        :param keepSignatures: keep signature markers (default True).
        :param keepPageNumbers: keep page numbers (default True).
        :param maxTokens: generation budget per page (default 8192; the card uses up to 15000 for dense pages).
        :param repetitionPenalty: override the repetition penalty.
        :param pdfDpi: PDF rasterization DPI (default 300).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: optional list of 0-based PDF page indices.
        :param image: page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        financial = bool(kwargs.pop('financial', False))
        question = kwargs.pop('question', None)
        prompt = kwargs.pop('ocrPrompt', None)  # 'prompt' stays for the base class
        if prompt:
            self.__m_prompt = prompt
        elif question:
            self.__m_prompt = str(question)
        elif financial:
            self.__m_prompt = _PROMPT_FINANCIAL
        else:
            self.__m_prompt = _PROMPT
        self.__m_keep_images = bool(kwargs.pop('keepImages', False))
        self.__m_keep_watermarks = bool(kwargs.pop('keepWatermarks', True))
        self.__m_keep_signatures = bool(kwargs.pop('keepSignatures', True))
        self.__m_keep_page_numbers = bool(kwargs.pop('keepPageNumbers', True))
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 8192))
        penalty = kwargs.pop('repetitionPenalty', 1 if financial else None)
        self.__m_penalty = penalty
        self.__m_pdf_dpi = float(kwargs.pop('pdfDpi', _PDF_DPI))
        self.__m_device = kwargs.pop('device', None)
        self.__m_pages = kwargs.pop('pages', None)
        super(NanonetsOcr2, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        name = self.getModel()
        if name in NanonetsOcr2._engines:
            return NanonetsOcr2._engines[name]
        try:
            from torch import cuda
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError:
            raise OCRError(
                'Nanonets-OCR2 needs transformers and torch. Run: pip '
                'install transformers torch pillow accelerate')
        device = self.__m_device or ('cuda' if cuda.is_available() else 'cpu')
        try:  # the card's recommended attention, dropped if missing
            model = AutoModelForImageTextToText.from_pretrained(
                name, torch_dtype='auto', attn_implementation='flash_attention_2')
        except Exception:  # noqa: BLE001 - no flash-attn installed
            model = AutoModelForImageTextToText.from_pretrained(name, torch_dtype='auto')
        model = model.to(device)
        model.eval()
        processor = AutoProcessor.from_pretrained(name)
        NanonetsOcr2._engines[name] = (processor, model, device)
        return NanonetsOcr2._engines[name]

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _pageImages(self):
        from PIL.Image import fromarray, open
        from io import BytesIO
        kind = self.imageKind()
        if kind == 'pil':
            return [self.getImage().convert('RGB')]
        if kind == 'array':
            from numpy import asarray
            return [fromarray(asarray(self.getImage())).convert('RGB')]
        if kind == 'url':
            from requests import get
            reply = get(self.getImage(), timeout=self.getTimeout())
            reply.raise_for_status()
            data = reply.content
        elif kind in ('path', 'bytes', 'buffer'):
            data = self.imageBytes()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(self.getImage()))
        if data[:5] == b'%PDF-':
            return self._pdfPages(data)
        return [open(BytesIO(data)).convert('RGB')]

    def _pdfPages(self, data):
        try:
            from pypdfium2 import PdfDocument
        except ImportError:
            raise OCRError(
                'PDF input needs pypdfium2 for rasterization. Run: pip install pypdfium2 (or pass page images instead).'
            )
        scale = self.__m_pdf_dpi / 72.0
        document = PdfDocument(data)
        try:
            count = len(document)
            wanted = range(count) if self.__m_pages is None else [index for index in self.__m_pages if 0 <= index < count]
            return [document[index].render(scale=scale).to_pil().convert('RGB') for index in wanted]
        finally:
            document.close()

    # ------------------------------------------------------------------ #
    # Semantic-tag handling                                              #
    # ------------------------------------------------------------------ #
    def _applyTags(self, text):
        """
        Resolve Nanonets' semantic tags per the keep* settings.
        """
        text = text or ''
        if self.__m_keep_images:
            text = sub(r'(?is)<img>\s*(.*?)\s*</img>', lambda m: '[image: {}]'.format(m.group(1)), text)
        else:
            text = sub(r'(?is)<img>.*?</img>', '', text)
        if self.__m_keep_watermarks:
            text = sub(r'(?is)<watermark>\s*(.*?)\s*</watermark>', lambda m: '[watermark: {}]'.format(m.group(1)), text)
        else:
            text = sub(r'(?is)<watermark>.*?</watermark>', '', text)
        if self.__m_keep_signatures:
            text = sub(
                r'(?is)<signature>\s*(.*?)\s*</signature>',
                lambda m: ('[signature: {}]'.format(m.group(1)) if m.group(1) else '[signature]'), text)
        else:
            text = sub(r'(?is)<signature>.*?</signature>', '', text)
        if self.__m_keep_page_numbers:
            text = sub(
                r'(?is)<page_number>\s*(.*?)\s*</page_number>',
                lambda m: '[page {}]'.format(m.group(1)), text)
        else:
            text = sub(r'(?is)<page_number>.*?</page_number>', '', text)
        return text

    @staticmethod
    def _htmlToText(text):
        """
        Flatten HTML tables (and stray tags) into readable lines.
        """
        if '<' not in (text or ''):
            return text
        text = sub(r'(?i)</\s*tr\s*>', '\n', text)
        text = sub(r'(?i)<\s*(td|th)[^>]*>', ' ', text)
        text = sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
        text = sub(r'(?i)<\s*/?\s*(p|div|table|tbody|thead|caption|span|ul|ol)[^>]*>', ' ', text)
        text = sub(r'(?i)<\s*li[^>]*>', '\n', text)
        text = sub(r'<[^>]+>', '', text)
        return text

    def _toRows(self, text, start_row=0):
        words = []
        row = start_row
        text = self._applyTags(text)
        text = self._htmlToText(text)
        for line in text.splitlines():
            line = sub(r'^\s*```[a-zA-Z]*\s*$', '', line)  # fences
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)
            line = sub(r'\|', ' ', line)
            line = sub(r'\s+', ' ', line).strip()
            if not line or set(line) <= set('-:*_ '):
                continue
            words.append(self.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # Transcription (the card's official recipe)                         #
    # ------------------------------------------------------------------ #
    def _transcribePage(self, image):
        from torch import inference_mode
        processor, model, device = self._engine()
        messages = [
            {'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': [
                {'type': 'image', 'image': image},
                {'type': 'text', 'text': self.composePrompt(self.__m_prompt)},
            ]},
        ]
        templated = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[templated], images=[image], padding=True, return_tensors='pt')
        inputs = inputs.to(device)
        generate_kwargs = {'max_new_tokens': self.__m_max_tokens, 'do_sample': False}
        if self.__m_penalty is not None:
            generate_kwargs['repetition_penalty'] = self.__m_penalty
        with inference_mode():
            output_ids = model.generate(**inputs, **generate_kwargs)
        trimmed = [output[len(source):] for source, output in zip(inputs['input_ids'], output_ids)]
        decoded = processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=True)
        return (decoded[0] if decoded else '') or ''

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (ordered rows; Nanonets-OCR2 returns markdown/HTML
                 with semantic tags, not coordinates).
        """
        words = []
        try:
            for page_image in self._pageImages():
                output = self._transcribePage(page_image)
                words.extend(self._toRows(output, start_row=len(words)))
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA failures
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'Nanonets-OCR2 ran out of GPU memory (4B BF16 '
                    "params): try model='nanonets/"
                    "Nanonets-OCR2-1.5B-exp', a llama.cpp/Ollama "
                    "quantization, a lower pdfDpi, or device='cpu' "
                    '(slow). Original: ' + message[:200])
            raise OCRError(
                'Nanonets-OCR2 inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        if not words:
            raise OCRError(
                'Nanonets-OCR2 produced no text for this document '
                '(the card notes higher input resolution reads better -- try a larger image or pdfDpi=400).')
        return words
