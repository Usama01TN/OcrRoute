# coding=utf-8
"""
dots.ocr plugin (local, https://huggingface.co/dots-studio/dots.ocr).
dots.ocr is RedNote's multilingual document parser: a single 1.7B-LLM
vision-language model that does layout detection AND content
recognition in one pass, with SOTA results ( the best overall on
OmniDocBench and on olmOCR-bench at 79.1) across ~100 languages,
under an MIT license. What makes it special for this suite: its
layout mode returns a JSON array of elements, each with a PIXEL
bounding box, a category, and the text -- real block-level geometry
from a local model (the tier of Mistral OCR-4 / MonkeyOCR's
pipeline), unlike prompt-estimated VLM boxes.
Install (GPU strongly recommended; ~3B params in BF16)::
    pip install transformers torch pillow accelerate
    pip install qwen-vl-utils    # official image preprocessing
    pip install pypdfium2        # only for PDF input
The plugin loads the checkpoint with trust_remote_code (dots.ocr
ships custom modeling code). flash_attention_2 is tried first and
silently dropped where unavailable. NOTE from the model card: if you
download the weights to a local directory, use a directory name
WITHOUT periods (e.g. ``weights/DotsOCR``) -- a temporary workaround
pending full transformers integration -- and pass that path as
``model=``.
Tasks:
    task='layout'      (default) full parse: bbox + category + text
                       per element, reading order preserved --
                       block-level pixel geometry in the result.
    task='ocr'         text-only transcription (ordered rows); also
                       the model card's recommended fallback when
                       dotted/underscored lines make layout mode repeat endlessly.
    task='grounding'   text inside a given region; pass
                       bbox=[x1, y1, x2, y2] in original image pixels.
Geometry correctness: the model sees the image after Qwen-style
"smart resize" (dimensions rounded to multiples of 28, area capped at
11,289,600 px), and its bboxes live in THAT space -- the plugin
replicates the resize math and rescales every box back to original
image pixels. Tables arrive as HTML (flattened to rows of text),
formulas as LaTeX (kept verbatim), Picture elements carry no text and
are skipped. PDFs are rasterized at the card-recommended ~200 DPI,
pages stacked vertically. Everything lands in the exact unified
structure shared by every plugin (like OCR.Space).
"""
from math import floor, sqrt, ceil
from os.path import dirname
from json import loads
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

_DEFAULT_MODEL = 'dots-studio/dots.ocr'

#: The model card's official full-layout prompt (verbatim).
_PROMPT_LAYOUT = """Please output the layout information from the PDF image, including each layout element's bbox, its category, and the corresponding text content within the bbox.

1. Bbox format: [x1, y1, x2, y2]

2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].

3. Text Extraction & Formatting Rules:
    - Picture: For the 'Picture' category, the text field should be omitted.
    - Formula: Format its text as LaTeX.
    - Table: Format its text as HTML.
    - All Others (Text, Title, etc.): Format their text as Markdown.

4. Constraints:
    - The output text must be the original text from the image, with no translation.
    - All layout elements must be sorted according to human reading order.

5. Final Output: The entire output must be a single JSON object.
"""
_PROMPT_OCR = 'Extract the text content from this image.'
_PROMPT_GROUNDING = 'Extract text from the given bounding box on the image (format: [x1, y1, x2, y2]).\nBounding Box:\n'
_TASKS = ('layout', 'ocr', 'grounding')
#: Qwen-style smart-resize parameters used by dots.ocr.
_RESIZE_FACTOR = 28
_MIN_PIXELS = 3136
_MAX_PIXELS = 11289600
#: PDF rasterization ~200 DPI (card-recommended) over the 72-DPI base.
_PDF_SCALE = 200.0 / 72.0
#: Vertical gap inserted between stacked pages.
_PAGE_GAP = 50.0


class DotsOcr(OCRPlugin):
    """
    DotsOcr class.
    """
    #: model path -> (processor, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint or local weights dir (default 'dots-studio/dots.ocr';
                        local dirs must not contain periods in the name).
        :param task: 'layout' (default), 'ocr', or 'grounding'.
        :param bbox: [x1, y1, x2, y2] region for task='grounding'.
        :param ocrPrompt: custom prompt overriding the task prompt (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
        :param maxTokens: generation budget (default 24000, per the model card).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: optional list of 0-based PDF page indices.
        :param image: page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        task = kwargs.pop('task', 'layout').lower()
        if task not in _TASKS:
            raise OCRError('Unknown task {!r}; choose from {}'.format(task, ', '.join(_TASKS)))
        self.__m_task = task
        self.__m_bbox = kwargs.pop('bbox', None)
        if task == 'grounding' and (not isinstance(self.__m_bbox, (list, tuple)) or len(self.__m_bbox) != 4):
            raise OCRError("task='grounding' needs bbox=[x1, y1, x2, y2] in original image pixels.")
        self.__m_prompt = kwargs.pop('ocrPrompt', None)  # 'prompt' stays for the base class
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 24000))
        self.__m_device = kwargs.pop('device', None)
        self.__m_pages = kwargs.pop('pages', None)
        super(DotsOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        name = self.getModel()
        if name in DotsOcr._engines:
            return DotsOcr._engines[name]
        try:
            from transformers import AutoModelForCausalLM, AutoProcessor
            from torch import cuda, bfloat16, float32
        except ImportError:
            raise OCRError(
                'dots.ocr needs transformers and torch. Run: pip '
                'install transformers torch pillow accelerate qwen-vl-utils')
        device = self.__m_device or ('cuda' if cuda.is_available() else 'cpu')
        dtype = bfloat16 if device == 'cuda' else float32
        processor = AutoProcessor.from_pretrained(name, trust_remote_code=True)
        try:  # flash_attention_2 first, drop it where unavailable
            model = AutoModelForCausalLM.from_pretrained(
                name, trust_remote_code=True, torch_dtype=dtype, attn_implementation='flash_attention_2')
        except Exception:  # noqa: BLE001 - no flash-attn / other attn
            model = AutoModelForCausalLM.from_pretrained(name, trust_remote_code=True, torch_dtype=dtype)
        model = model.to(device)
        model.eval()
        DotsOcr._engines[name] = (processor, model, device)
        return DotsOcr._engines[name]

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
    # Smart-resize replication (bbox space -> original pixels)           #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _smartResize(width, height):
        """
        The Qwen2-VL resize dots.ocr applies before inference.
        """
        factor = _RESIZE_FACTOR
        resizedH = max(factor, round(height / factor) * factor)
        resizedW = max(factor, round(width / factor) * factor)
        if resizedH * resizedW > _MAX_PIXELS:
            beta = sqrt((height * width) / _MAX_PIXELS)
            resizedH = floor(height / beta / factor) * factor
            resizedW = floor(width / beta / factor) * factor
        elif resizedH * resizedW < _MIN_PIXELS:
            beta = sqrt(_MIN_PIXELS / (height * width))
            resizedH = ceil(height * beta / factor) * factor
            resizedW = ceil(width * beta / factor) * factor
        return resizedW, resizedH

    # ------------------------------------------------------------------ #
    # Prompt / generation                                                #
    # ------------------------------------------------------------------ #
    def _taskPrompt(self):
        """
        Custom or task prompt, plus the base class's extra prompt.
        """
        if self.__m_prompt:
            prompt = self.__m_prompt
        elif self.__m_task == 'ocr':
            prompt = _PROMPT_OCR
        elif self.__m_task == 'grounding':
            prompt = _PROMPT_GROUNDING + str(list(self.__m_bbox))
        else:
            prompt = _PROMPT_LAYOUT
        return self.composePrompt(prompt)

    def _transcribePage(self, image):
        from torch import inference_mode
        processor, model, device = self._engine()
        messages = [{'role': 'user', 'content': [
            {'type': 'image', 'image': image}, {'type': 'text', 'text': self._taskPrompt()}]}]
        templated = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        try:  # official path: qwen_vl_utils handles the smart resize
            from qwen_vl_utils import process_vision_info
            imageInputs, videoInputs = process_vision_info(messages)
        except ImportError:
            imageInputs, videoInputs = [image], None
        inputs = processor(text=[templated], images=imageInputs, videos=videoInputs, padding=True, return_tensors='pt')
        inputs = inputs.to(device)
        with inference_mode():
            outputs = model.generate(**inputs, max_new_tokens=self.__m_max_tokens, do_sample=False)
        prompt_length = inputs['input_ids'].shape[1]
        generated = outputs[:, prompt_length:]
        return processor.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0] or ''

    # ------------------------------------------------------------------ #
    # Output mapping                                                     #
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

    def _wordsFromLayout(self, output, original_size, y_offset):
        """
        Layout JSON (bboxes in smart-resize space) -> word dicts.
        """
        cleaned = sub(r'```(?:json)?|```', '', output or '').strip()
        start = min((i for i in (cleaned.find('['), cleaned.find('{')) if i >= 0), default=-1)
        if start < 0:
            return []
        end = max(cleaned.rfind(']'), cleaned.rfind('}'))
        if end <= start:
            return []
        try:
            payload = loads(cleaned[start:end + 1])
        except ValueError:
            return []
        if isinstance(payload, dict):
            for key in ('elements', 'layout', 'items', 'blocks', 'result'):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
            else:
                payload = [payload]
        if not isinstance(payload, list):
            return []
        width, height = original_size
        resized_w, resized_h = self._smartResize(width, height)
        scale_x = width / float(resized_w)
        scale_y = height / float(resized_h)
        words = []
        row = 0
        for element in payload:
            if not isinstance(element, dict):
                continue
            if str(element.get('category', '')).lower() == 'picture':
                continue
            text = self._htmlToText(str(element.get('text', '')).strip())
            if not text:
                continue
            bbox = element.get('bbox') or []
            rect = None
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                try:
                    x1, y1, x2, y2 = (float(v) for v in bbox)
                    if x2 > x1 and y2 > y1:
                        rect = (x1 * scale_x, y1 * scale_y, (x2 - x1) * scale_x, (y2 - y1) * scale_y)
                except (TypeError, ValueError):
                    rect = None
            lines = [l for l in text.splitlines() if l.strip()]
            for line_index, line in enumerate(lines):
                line = sub(r'\s+', ' ', line).strip()
                if rect:
                    left, top, box_w, box_h = rect
                    line_height = box_h / max(len(lines), 1)
                    words.append(self.makeWord(
                        line, left, y_offset + top + line_index * line_height, box_w, max(line_height, 1.0)))
                else:
                    words.append(self.makeWord(line, 0.0, y_offset + row * 10.0, 1.0, 8.0))
                row += 1
        return words

    @classmethod
    def _textToRows(cls, text, start_row=0):
        words = []
        row = start_row
        for line in (text or '').splitlines():
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
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble (task='layout': block-level ORIGINAL-PIXEL boxes;
                    other tasks: ordered rows).
        """
        words = []
        yOffset = 0.0
        try:
            for pageImage in self._pageImages():
                output = self._transcribePage(pageImage)
                if self.__m_task == 'layout':
                    pageWords = self._wordsFromLayout(
                        output, pageImage.size, yOffset)
                    if not pageWords:  # JSON failed: keep the text
                        pageWords = self._textToRows(output, start_row=len(words))
                else:
                    pageWords = self._textToRows(output, start_row=len(words))
                words.extend(pageWords)
                yOffset = max((w['Top'] + w['Height'] for w in pageWords), default=yOffset) + _PAGE_GAP
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA failures
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'dots.ocr ran out of GPU memory (the model is ~3B '
                    'BF16 params): use a quantized build or a bigger GPU. Original: ' + message[:200])
            raise OCRError('dots.ocr inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        if not words:
            raise OCRError(
                'dots.ocr produced no text. Per the model card, dense '
                'dotted/underscored lines can make layout mode repeat '
                "or fail -- try task='ocr', enlarge the image, or "
                'raise the PDF DPI (200 recommended).')
        return words
