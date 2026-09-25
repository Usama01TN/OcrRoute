# coding=utf-8
"""
Infinity-Parser plugin (local, https://huggingface.co/infly/Infinity-Parser-7B).
Infinity-Parser-7B is inftech's scanned-document parser: a
Qwen2.5-VL-7B fine-tuned with LayoutRL -- reinforcement learning
whose rewards combine edit distance, paragraph accuracy and
reading-order preservation. That training pays off: it scores 82.5
overall on olmOCR-bench (the best of the local document parsers in
this suite, above dots.ocr's 79.1 and MonkeyOCR-pro-3B's 75.8) with
SOTA-class results on OmniDocBench, PubTabNet and FinTabNet, in
English and Chinese, under an Apache-2.0 license.
It is a STANDARD qwen2_5_vl checkpoint, so loading needs no custom code::
    pip install transformers torch pillow accelerate
    pip install qwen-vl-utils    # official image preprocessing
    pip install pypdfium2        # only for PDF input
The plugin uses the repo's official transformers recipe: the exact
default prompt ("Please transform the document's contents into
Markdown format."), chat-template generation with qwen_vl_utils (a
direct-images fallback covers environments without it), and
flash_attention_2 tried first and silently dropped where unavailable.
The model is ~8B params in BF16 -- a 16 GB+ GPU is recommended
(community quantizations exist for smaller cards).
Honest limitations, straight from the model card: Infinity-Parser
returns NO layout or bounding-box information by design -- output is
markdown, mapped to ordered rows in the unified structure (like
OCR.Space's shape, rows tier) -- and it does not parse charts or
figures. For block boxes use DotsOcr or the MonkeyOCR pipeline
plugin; for word boxes, RapidOcr/Tesseract or the hosted word-box
APIs. Where Infinity-Parser shines is scanned documents: faithful
text, tables and formulas in correct reading order. PDFs are
rasterized locally page by page.
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

_DEFAULT_MODEL = 'infly/Infinity-Parser-7B'
#: The repo's official default prompt (verbatim, typographic
#: apostrophe included).
_PROMPT = 'Please transform the document\u2019s contents into Markdown format.'
#: Rasterization scale for PDF pages (~200 DPI over the 72-DPI base).
_PDF_SCALE = 200.0 / 72.0
#: Vertical gap inserted between stacked pages.
_PAGE_GAP = 50.0


class InfinityParser(OCRPlugin):
    """
    InfinityParser class.
    """
    #: model path -> (processor, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'infly/Infinity-Parser-7B'; community quantizations work too).
        :param ocrPrompt: override the official markdown prompt (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
        :param maxTokens: generation budget per page (default 8192).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: optional list of 0-based PDF page indices (default: all pages).
        :param image: page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        self.__m_prompt = kwargs.pop('ocrPrompt', _PROMPT)  # 'prompt' stays for the base class
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 8192))
        self.__m_device = kwargs.pop('device', None)
        self.__m_pages = kwargs.pop('pages', None)
        super(InfinityParser, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        name = self.getModel()
        if name in InfinityParser._engines:
            return InfinityParser._engines[name]
        try:
            from torch import bfloat16, float32, cuda
            from transformers import AutoProcessor
        except ImportError:
            raise OCRError(
                'Infinity-Parser needs transformers and torch. Run: '
                'pip install transformers torch pillow accelerate qwen-vl-utils')
        device = self.__m_device or ('cuda' if cuda.is_available() else 'cpu')
        dtype = bfloat16 if device == 'cuda' else float32
        processor = AutoProcessor.from_pretrained(name)
        try:
            from transformers import Qwen2_5_VLForConditionalGeneration as ModelClass
        except ImportError:
            from transformers import AutoModelForVision2Seq as ModelClass
        try:  # official recommendation, dropped where unavailable
            model = ModelClass.from_pretrained(name, torch_dtype=dtype, attn_implementation='flash_attention_2')
        except Exception:  # noqa: BLE001 - no flash-attn
            model = ModelClass.from_pretrained(name, torch_dtype=dtype)
        model = model.to(device)
        model.eval()
        InfinityParser._engines[name] = (processor, model, device)
        return InfinityParser._engines[name]

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
                'PDF input needs pypdfium2 for rasterization. Run: pip install pypdfium2 (or pass page images instead).'
            )
        document = PdfDocument(data)
        try:
            count = len(document)
            wanted = range(count) if self.__m_pages is None else [index for index in self.__m_pages if 0 <= index < count]
            return [document[index].render(scale=_PDF_SCALE).to_pil().convert('RGB') for index in wanted]
        finally:
            document.close()

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
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _transcribePage(self, image):
        from torch import inference_mode
        processor, model, device = self._engine()
        messages = [{'role': 'user', 'content': [
            {'type': 'image', 'image': image}, {'type': 'text', 'text': self.composePrompt(self.__m_prompt)}]}]
        templated = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        try:  # Official path uses qwen_vl_utils preprocessing<
            from qwen_vl_utils import process_vision_info
            imageInputs, videoInputs = process_vision_info(messages)
        except ImportError:
            imageInputs, videoInputs = [image], None
        inputs = processor(text=[templated], images=imageInputs, videos=videoInputs, padding=True, return_tensors='pt')
        inputs = inputs.to(device)
        with inference_mode():
            outputs = model.generate(**inputs, max_new_tokens=self.__m_max_tokens, do_sample=False)
        promptLength = inputs['input_ids'].shape[1]
        generated = outputs[:, promptLength:]
        return processor.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0] or ''

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (ordered rows; the model returns no coordinates by
                 design -- see the module docstring for alternatives).
        """
        words = []
        try:
            for pageImage in self._pageImages():
                markdown = self._transcribePage(pageImage)
                words.extend(self._markdownToRows(markdown, start_row=len(words)))
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA failures
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'Infinity-Parser ran out of GPU memory (~8B BF16 '
                    'params, 16 GB+ recommended): try a community '
                    "quantization of 'infly/Infinity-Parser-7B' or "
                    "device='cpu' (slow). Original: " + message[:200])
            raise OCRError('Infinity-Parser inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        if not words:
            raise OCRError('Infinity-Parser produced no text for this document.')
        return words
