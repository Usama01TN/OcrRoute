# coding=utf-8
"""
Qianfan-OCR plugin (local, https://huggingface.co/baidu/Qianfan-OCR).
Qianfan-OCR is the Baidu Qianfan team's 4B end-to-end document
intelligence model (Apache-2.0): one prompt-driven VLM covering
parsing to Markdown, layout analysis, tables (HTML), formulas
(LaTeX), chart understanding, document QA, handwriting, scene text,
and key information extraction, across 192 LANGUAGES (Latin,
Cyrillic, Arabic, South/Southeast Asian, CJK...).
The scoreboard is serious: #1 END-TO-END model on OmniDocBench v1.5
(93.12 -- above DeepSeek-OCR-v2's 91.09, Gemini-3 Pro's 90.33 and
dots.ocr's 88.41), #1 on Key Information Extraction (87.9 mean across
five public KIE benchmarks, above Gemini-3.1-Pro and
Qwen3-VL-235B-A22B), and 79.8 on olmOCR-bench. (For raw olmOCR-bench
text fidelity Chandra-2 still leads at 85.8; Qianfan's edge is
structured parsing, charts and KIE.) W8A8 quantization reaches ~1
page/second on a single A100; llama.cpp/Ollama quantizations exist.
Install (GPU recommended; ~5B BF16)::
    pip install transformers torch pillow accelerate
    pip install pypdfium2        # only for PDF input
The plugin follows the card's official recipe exactly:
AutoModelForImageTextToText + AutoProcessor (native architecture, no
custom code), chat-template tokenization, greedy decoding, prompt
trimming. Default prompt is the card's "Parse this document to Markdown."
LAYOUT-AS-THOUGHT: pass ``thinking=True`` to enable the card's
optional thinking phase (``enable_thinking=True`` on the chat
template): the model first reasons through the page layout (bounding
boxes, element types, reading order) before answering -- the card
recommends it for heterogeneous pages (exam papers, newspapers,
technical reports) and plain mode for simple documents (faster and
often better). The thinking segment is stripped from the result; the
generation budget is raised automatically (16384, per the card's thinking example).
KIE CONVENIENCE: pass ``fields=['Name', 'Date', 'Total']`` to run the
model's #1-ranked key-information-extraction with a standard-JSON
prompt; the JSON answer comes back as the parsed text.
Output is markdown/JSON text mapped to ordered rows in the unified
structure (like OCR.Space's shape, rows tier) -- layout geometry
exists inside the thinking phase but its schema is not a stable
contract, so this plugin does not mine it. PDFs are rasterized
locally page by page.
"""
from re import compile, sub, DOTALL, IGNORECASE
from os.path import dirname
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_DEFAULT_MODEL = 'baidu/Qianfan-OCR'
#: The card's official default prompt.
_PROMPT = 'Parse this document to Markdown.'
#: Rasterization scale for PDF pages (~200 DPI over the 72-DPI base).
_PDF_SCALE = 200.0 / 72.0


class QianfanOcr(OCRPlugin):
    """
    QianfanOcr class.
    """
    #: model name -> (processor, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: Checkpoint (default 'baidu/Qianfan-OCR'; quantized community builds work too).
        :param thinking: Enable Layout-as-Thought (default False; recommended for complex, mixed layouts).
        :param fields: List of field names for key information extraction (builds the KIE JSON prompt).
        :param ocrPrompt: Custom prompt overriding the defaults (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``.
        :param maxTokens: Generation budget per page (default 4096;
                            automatically 16384 with thinking=True, per the card).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: Optional list of 0-based PDF page indices.
        :param image: Page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: Other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        self.__m_thinking = bool(kwargs.pop('thinking', False))
        fields = kwargs.pop('fields', None)
        prompt = kwargs.pop('ocrPrompt', None)  # 'prompt' stays for the base class
        if prompt:
            self.__m_prompt = prompt
        elif fields:
            self.__m_prompt = (
                'Extract the following fields from the image: {}. '
                'Output in standard JSON format.'.format(', '.join(str(field) for field in fields)))
        else:
            self.__m_prompt = _PROMPT
        default_budget = 16384 if self.__m_thinking or fields else 4096
        self.__m_max_tokens = int(kwargs.pop('maxTokens', default_budget))
        self.__m_device = kwargs.pop('device', None)
        self.__m_pages = kwargs.pop('pages', None)
        super(QianfanOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        name = self.getModel()
        if name in QianfanOcr._engines:
            return QianfanOcr._engines[name]
        try:
            from transformers import AutoModelForImageTextToText, AutoProcessor
            from torch import cuda, bfloat16, float32
        except ImportError:
            raise OCRError(
                'Qianfan-OCR needs transformers and torch. Run: pip '
                'install transformers torch pillow accelerate')
        device = self.__m_device or ('cuda' if cuda.is_available() else 'cpu')
        dtype = bfloat16 if device == 'cuda' else float32
        model = AutoModelForImageTextToText.from_pretrained(name, torch_dtype=dtype)
        model = model.to(device)
        model.eval()
        processor = AutoProcessor.from_pretrained(name)
        QianfanOcr._engines[name] = (processor, model, device)
        return QianfanOcr._engines[name]

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _pageImages(self):
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'pil':
            return [self.getImage().convert('RGB')]
        if kind == 'array':
            from numpy import asarray
            return [Image.fromarray(asarray(self.getImage())).convert('RGB')]
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
        return [Image.open(BytesIO(data)).convert('RGB')]

    def _pdfPages(self, data):
        try:
            from pypdfium2 import PdfDocument
        except ImportError:
            raise OCRError(
                'PDF input needs pypdfium2 for rasterization. Run: '
                'pip install pypdfium2 (or pass page images instead).')
        document = PdfDocument(data)
        try:
            count = len(document)
            wanted = range(count) if self.__m_pages is None else [index for index in self.__m_pages if 0 <= index < count]
            return [document[index].render(scale=_PDF_SCALE).to_pil().convert('RGB') for index in wanted]
        finally:
            document.close()

    # ------------------------------------------------------------------ #
    # Thinking-phase stripping                                           #
    # ------------------------------------------------------------------ #
    _THINK_BLOCK = compile(
        r'[<\u27e8](think|thinking)[>\u27e9].*?[<\u27e8]/(think|thinking)[>\u27e9]\s*', DOTALL | IGNORECASE)
    _THINK_OPEN = compile(r'[<\u27e8](think|thinking)[>\u27e9]', IGNORECASE)

    @classmethod
    def _stripThinking(cls, text):
        text = text or ''
        stripped = cls._THINK_BLOCK.sub('', text)
        match = cls._THINK_OPEN.search(stripped)
        if match:  # unclosed thinking: budget ran out mid-thought.
            stripped = stripped[:match.start()]
        return stripped.strip()

    # ------------------------------------------------------------------ #
    # Markdown -> rows                                                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _htmlToText(text):
        if '<' not in (text or ''):
            return text
        text = sub(r'(?i)</\s*tr\s*>', '\n', text)
        text = sub(r'(?i)<\s*(td|th)[^>]*>', ' ', text)
        text = sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
        text = sub(r'(?i)<\s*/?\s*(p|div|table|tbody|thead)[^>]*>', ' ', text)
        text = sub(r'<[^>]+>', '', text)
        return text

    @classmethod
    def _markdownToRows(cls, markdown, start_row=0):
        words = []
        row = start_row
        for line in cls._htmlToText(markdown or '').splitlines():
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)
            line = sub(r'\|', ' ', line)
            line = sub(r'\s+', ' ', line).strip()
            if not line or set(line) <= set('-: '):
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # Transcription (the card's official recipe)                         #
    # ------------------------------------------------------------------ #
    def _transcribePage(self, image):
        from torch import inference_mode
        processor, model, device = self._engine()
        messages = [{'role': 'user', 'content': [
            {'type': 'image', 'image': image}, {'type': 'text', 'text': self.composePrompt(self.__m_prompt)}]}]
        template_kwargs = {'add_generation_prompt': True, 'tokenize': True, 'return_dict': True, 'return_tensors': 'pt'}
        if self.__m_thinking:
            template_kwargs['enable_thinking'] = True
        try:
            inputs = processor.apply_chat_template(messages, **template_kwargs)
        except TypeError:
            # older template without the enable_thinking kwarg.
            template_kwargs.pop('enable_thinking', None)
            inputs = processor.apply_chat_template(messages, **template_kwargs)
        inputs = inputs.to(device)
        with inference_mode():
            outputs = model.generate(**inputs, max_new_tokens=self.__m_max_tokens, do_sample=False)
        prompt_length = inputs['input_ids'].shape[1]
        generated = outputs[:, prompt_length:]
        decoded = processor.batch_decode(generated, skip_special_tokens=True)[0] or ''
        return self._stripThinking(decoded)

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (ordered rows from Qianfan-OCR's markdown/JSON output).
        """
        words = []
        try:
            for page_image in self._pageImages():
                output = self._transcribePage(page_image)
                words.extend(self._markdownToRows(output, start_row=len(words)))
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA failures
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'Qianfan-OCR ran out of GPU memory (~5B BF16 '
                    'params): try an Ollama/llama.cpp quantization of '
                    "'baidu/Qianfan-OCR' or device='cpu' (slow). Original: " + message[:200])
            raise OCRError('Qianfan-OCR inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        if not words:
            raise OCRError(
                'Qianfan-OCR produced no text for this document (with '
                'thinking=True the budget may have gone to layout '
                'reasoning -- raise maxTokens or disable thinking for simple pages).')
        return words
