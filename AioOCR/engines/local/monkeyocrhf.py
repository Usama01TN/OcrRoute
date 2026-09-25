# coding=utf-8
"""
MonkeyOCR Hugging Face plugin (local,
https://huggingface.co/echo840/MonkeyOCR).
This plugin loads MonkeyOCR's RECOGNITION model straight from Hugging
Face with transformers -- no repo clone, no pipeline setup. The
weights repo hosts three components (Structure / Recognition / Relation);
the Recognition component is a Qwen2.5-VL-based vision-language model that transcribes a page image on its own,
and it is what the community HF Spaces load directly::
    pip install transformers torch pillow accelerate
    pip install pypdfium2        # only for PDF input
Checkpoints (downloaded automatically on first use):
    echo840/MonkeyOCR            default (3B recognition)
    echo840/MonkeyOCR-pro-1.2B   leaner + faster, beats the 3B
    echo840/MonkeyOCR-pro-3B     strongest (OmniDocBench SOTA-class)
A GPU is strongly recommended (the 1.2B runs on ~8 GB VRAM; CPU works
but is slow). Per the model card, MonkeyOCR targets printed English/
Chinese documents -- photographed text, handwriting, Traditional
Chinese and other languages are not fully supported, and the license is academic/non-commercial.
Tasks (the model card's official prompts):
    task='text'      plain text transcription (default)
    task='table'     table image -> HTML (converted to rows)
    task='formula'   formula image -> LaTeX
PDFs are rasterized locally with pypdfium2 page by page. The
Recognition model alone returns NO coordinates -- ordered rows in the
unified structure. (For block-level boxes use the full-pipeline
monkeyocrhf, which wraps the repo's parse.py and reads its layout JSON.)
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

_DEFAULT_MODEL = 'echo840/MonkeyOCR'
_SUBFOLDER = 'Recognition'
#: Official single-task prompts from the MonkeyOCR model card / repo.
_TASK_PROMPTS = {
    'text': 'Please output the text content from the image.',
    'table': 'This is the image of a table. Please output the table in html format.',
    'formula': 'Please write out the expression of the formula in the image using LaTeX format.',
}
#: Rasterization scale for PDF pages.
_PDF_SCALE = 2.0


class MonkeyOcrHf(OCRPlugin):
    """
    MonkeyOcrHf class.
    """
    #: (model, subfolder) -> (processor, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'echo840/MonkeyOCR'; also 'echo840/MonkeyOCR-pro-1.2B' or '-pro-3B').
        :param subfolder: weights subfolder (default 'Recognition').
        :param task: 'text' (default), 'table', or 'formula'.
        :param ocrPrompt: custom prompt overriding the task prompt (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
        :param maxTokens: generation budget per page (default 4096).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: optional list of 0-based PDF page indices (default: all pages).
        :param image: page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        self.__m_subfolder = kwargs.pop('subfolder', _SUBFOLDER)
        task = kwargs.pop('task', 'text').lower()
        if task not in _TASK_PROMPTS:
            raise OCRError('Unknown task {!r}; choose from {}'.format(task, ', '.join(sorted(_TASK_PROMPTS))))
        self.__m_prompt = kwargs.pop('ocrPrompt', _TASK_PROMPTS[task])  # 'prompt' stays for the base class
        self.__m_task = task
        self.__m_maxTokens = int(kwargs.pop('maxTokens', 4096))
        self.__m_device = kwargs.pop('device', None)
        self.__m_pages = kwargs.pop('pages', None)
        super(MonkeyOcrHf, self).__init__(*args, **kwargs)
        self.setModel(model)
        self.setOnline(False)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        key = (self.getModel(), self.__m_subfolder)
        if key in MonkeyOcrHf._engines:
            return MonkeyOcrHf._engines[key]
        try:
            from torch import cuda, float16, float32
            from transformers import AutoProcessor
        except ImportError:
            raise OCRError(
                'MonkeyOCR needs transformers and torch. Run: pip install transformers torch pillow accelerate')
        device = self.__m_device or ('cuda' if cuda.is_available() else 'cpu')
        dtype = float16 if device == 'cuda' else float32
        load_kwargs = {'trust_remote_code': True}
        if self.__m_subfolder:
            load_kwargs['subfolder'] = self.__m_subfolder
        processor = AutoProcessor.from_pretrained(self.getModel(), **load_kwargs)
        try:
            from transformers import Qwen2_5_VLForConditionalGeneration
            model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                self.getModel(), torch_dtype=dtype, **load_kwargs)
        except Exception:  # noqa: BLE001 - class rename/absence
            from transformers import AutoModelForVision2Seq
            model = AutoModelForVision2Seq.from_pretrained(self.getModel(), torch_dtype=dtype, **load_kwargs)
        model = model.to(device)
        model.eval()
        MonkeyOcrHf._engines[key] = (processor, model, device)
        return MonkeyOcrHf._engines[key]

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
        return self._pdfPages(data) if data[:5] == b'%PDF-' else [Image.open(BytesIO(data)).convert('RGB')]

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
    # Output mapping                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _htmlToText(text):
        """
        Flatten table HTML into readable lines.
        """
        if '<' not in (text or ''):
            return text
        text = sub(r'(?i)</\s*tr\s*>', '\n', text)
        text = sub(r'(?i)<\s*(td|th)[^>]*>', ' ', text)
        text = sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
        text = sub(r'<[^>]+>', '', text)
        return text

    @classmethod
    def _outputToRows(cls, text, start_row=0):
        words = []
        row = start_row
        for line in cls._htmlToText(text or '').splitlines():
            line = sub(r'^[#>\s]+', '', line)
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
        templated = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[templated], images=[image], return_tensors='pt', padding=True)
        inputs = inputs.to(device)
        with inference_mode():
            outputs = model.generate(**inputs, max_new_tokens=self.__m_maxTokens, do_sample=False)
        promptLength = inputs['input_ids'].shape[1]
        generated = outputs[:, promptLength:]
        decoded = processor.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
        return decoded or ''

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (ordered rows; the Recognition model returns no
                 coordinates -- use monkeyocrhf for block boxes).
        """
        words = []
        try:
            for pageImage in self._pageImages():
                output = self._transcribePage(pageImage)
                words.extend(self._outputToRows(output, start_row=len(words)))
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA failures
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'MonkeyOCR ran out of GPU memory: try the leaner '
                    "model='echo840/MonkeyOCR-pro-1.2B' (~8 GB VRAM) "
                    'or device=\'cpu\' (slow). Original: ' + message[:200])
            raise OCRError('MonkeyOCR inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        if not words:
            raise OCRError('MonkeyOCR produced no text for this document.')
        return words
