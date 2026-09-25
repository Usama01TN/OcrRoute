# coding=utf-8
"""
Chandra OCR 2 plugin (local, https://huggingface.co/datalab-to/chandra-ocr-2).
Chandra 2 is Datalab's (the Surya/Marker team) OCR model, and as of
this writing the strongest open-weights document OCR available:
85.8 on olmOCR-bench (open SOTA -- above dots.ocr 1.5, olmOCR 2 and
Infinity-Parser), 77.8% on their 43-language benchmark (90+ languages
supported; Arabic doubled vs Chandra 1), with standout handwriting,
form/checkbox reconstruction, tables, and math. 5B params (Qwen 3.5
base), BF16 -- a ~12 GB GPU runs it, and GGUF/Ollama quantizations
exist for smaller machines.
LICENSE NOTE (important): the code is Apache-2.0, but the model
WEIGHTS use a modified OpenRAIL-M license -- free for research,
personal use, and startups under $2M funding/revenue, and NOT usable
to compete with Datalab's API. For broader commercial use see
datalab.to/pricing.
Two official inference paths, both supported (the plugin prefers the
first when available):
1. The ``chandra-ocr`` library (recommended -- it owns the model's
   prompt types and output parsing)::
       pip install chandra-ocr[hf]     # brings transformers + torch
2. Raw transformers (``engineMethod='transformers'``)::
       pip install transformers torch pillow accelerate
PDFs are rasterized locally with pypdfium2 (pip install pypdfium2).
Output is Markdown (mapped to ordered rows in the unified structure,
like OCR.Space's shape). Chandra's layout-aware prompt type
('ocr_layout', the default) preserves reading order on complex pages;
the model does return richer layout/JSON through the chandra library
for advanced use, but block schemas vary by version so this plugin
stays on the stable Markdown contract.
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

_DEFAULT_MODEL = 'datalab-to/chandra-ocr-2'
#: Prompt for the raw-transformers path (the chandra library manages
#: its own prompts via prompt_type).
_PROMPT = ('Convert this document page to Markdown. Preserve the reading order, tables and math. '
           'Output ONLY the markdown.')
#: Rasterization scale for PDF pages (~200 DPI over the 72-DPI base).
_PDF_SCALE = 200.0 / 72.0


class ChandraOcr(OCRPlugin):
    """
    ChandraOcr class.
    """
    #: (model, method) -> loaded engine tuple, loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'datalab-to/chandra-ocr-2';
                      quantized community builds work on the transformers path).
        :param engineMethod: 'auto' (default: the chandra library if
                                installed, else raw transformers), 'chandra', or 'transformers'.
        :param promptType: chandra-library prompt type (default 'ocr_layout').
        :param ocrPrompt: prompt override for the raw-transformers path (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
                       Only the transformers path takes free text, so with engineMethod='auto' an extra
                       prompt selects that path; an explicit engineMethod='chandra' ignores it.
        :param maxTokens: generation budget per page (default 8192).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: optional list of 0-based PDF page indices.
        :param image: page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        method = str(kwargs.pop('engineMethod', 'auto')).lower()
        if method not in ('auto', 'chandra', 'transformers'):
            raise OCRError("engineMethod must be 'auto', 'chandra' or 'transformers', not {!r}".format(method))
        self.__m_method = method
        self.__m_promptType = kwargs.pop('promptType', 'ocr_layout')
        self.__m_prompt = kwargs.pop('ocrPrompt', _PROMPT)  # 'prompt' stays for the base class
        self.__m_maxTokens = int(kwargs.pop('maxTokens', 8192))
        self.__m_device = kwargs.pop('device', None)
        self.__m_pages = kwargs.pop('pages', None)
        super(ChandraOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engines                                                            #
    # ------------------------------------------------------------------ #
    def _resolveMethod(self):
        if self.__m_method != 'auto':
            return self.__m_method
        if (self.getExtraPrompt() or '').strip():
            # The chandra library owns its prompts (prompt_type);
            # only the raw path can carry the extra instructions.
            return 'transformers'
        try:
            import chandra  # noqa: F401 - presence check only
            return 'chandra'
        except ImportError:
            return 'transformers'

    def _loadModel(self):
        """
        Shared transformers model+processor per the official card.
        """
        try:
            from transformers import (AutoModelForImageTextToText, AutoProcessor)
            from torch import cuda, bfloat16, float32
        except ImportError:
            raise OCRError(
                'Chandra needs transformers and torch. Run: pip '
                "install 'chandra-ocr[hf]' (or: pip install "
                'transformers torch pillow accelerate)')
        device = self.__m_device or ('cuda' if cuda.is_available() else 'cpu')
        dtype = bfloat16 if device == 'cuda' else float32
        model = AutoModelForImageTextToText.from_pretrained(self.getModel(), dtype=dtype)
        model = model.to(device)
        model.eval()
        processor = AutoProcessor.from_pretrained(self.getModel())
        try:  # per the official snippet.
            processor.tokenizer.padding_side = 'left'
        except AttributeError:
            pass
        model.processor = processor
        return model, processor, device

    def _engine(self):
        method = self._resolveMethod()
        key = (self.getModel(), method)
        if key in ChandraOcr._engines:
            return ChandraOcr._engines[key]
        model, processor, device = self._loadModel()
        ChandraOcr._engines[key] = (method, model, processor, device)
        return ChandraOcr._engines[key]

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
    # Transcription                                                      #
    # ------------------------------------------------------------------ #
    def _transcribeChandra(self, image, model):
        """
        Official path: the chandra library owns prompts+parsing.
        """
        from chandra.model.schema import BatchInputItem
        from chandra.model.hf import generate_hf
        batch = [BatchInputItem(image=image, prompt_type=self.__m_promptType)]
        result = generate_hf(batch, model)[0]
        markdown = getattr(result, 'markdown', None)
        if markdown:
            return markdown
        raw = getattr(result, 'raw', '') or ''
        try:
            from chandra.output import parse_markdown
            return parse_markdown(raw) or raw
        except Exception:  # noqa: BLE001 - parsing is best-effort
            return raw

    def _transcribeTransformers(self, image, model, processor, device):
        """
        Raw path per the model card's transformers snippet.
        """
        from torch import inference_mode
        messages = [{'role': 'user', 'content': [
            {'type': 'image', 'image': image},
            {'type': 'text', 'text': self.composePrompt(self.__m_prompt)},
        ]}]
        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors='pt')
        inputs = inputs.to(device)
        with inference_mode():
            outputs = model.generate(**inputs, max_new_tokens=self.__m_maxTokens, do_sample=False)
        promptLength = inputs['input_ids'].shape[-1]
        return processor.decode(outputs[0][promptLength:], skip_special_tokens=True) or ''

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
        text = sub(r'(?i)<\s*/?\s*(p|div|table|tbody|thead|form|input|label|span)[^>]*>', ' ', text)
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
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble (ordered rows from Chandra's Markdown output).
        """
        words = []
        try:
            method, model, processor, device = self._engine()
            for pageImage in self._pageImages():
                if method == 'chandra':
                    markdown = self._transcribeChandra(pageImage, model)
                else:
                    markdown = self._transcribeTransformers(pageImage, model, processor, device)
                words.extend(self._markdownToRows(markdown, start_row=len(words)))
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA failures
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'Chandra ran out of GPU memory (5B BF16 params, '
                    '~12 GB recommended): try a GGUF/quantized build '
                    "of 'datalab-to/chandra-ocr-2' or device='cpu' "
                    '(slow). Original: ' + message[:200])
            raise OCRError('Chandra inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        if not words:
            raise OCRError('Chandra produced no text for this document.')
        return words
