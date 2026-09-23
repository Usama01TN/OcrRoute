# coding=utf-8
"""
olmOCR library plugin -- in-process inference, no subprocess, no vLLM.
Uses the ``olmocr`` library (https://github.com/allenai/olmocr) the way
the olmOCR-2 model card documents for direct Python usage:
- ``olmocr.data.renderpdf.render_pdf_to_base64png`` renders PDF pages
  given on disk (needs poppler-utils installed); PDFs that arrive in
  memory (bytes, BytesIO, URL) are rendered with pypdfium2 at the same
  target size instead, so nothing is ever written to disk;
- ``olmocr.prompts`` provides the exact prompt the model was trained
  on (``build_no_anchoring_v4_yaml_prompt`` for olmOCR-2; the legacy
  anchor-text prompt for the v1 0225/0725 checkpoints);
- the model itself runs through transformers
  (``allenai/olmOCR-2-7B-1025`` by default, Qwen2.5-VL based).
Install::
    sudo apt-get install poppler-utils
    pip install olmocr torch transformers pillow accelerate
    pip install pypdfium2      # only for PDFs passed as bytes/URL
Hardware: the model is 7B parameters -- ~16 GB VRAM in BF16 (use the
FP8 checkpoint with vLLM, or the subprocess plugin ``olmocrlib.py``,
if that is too much). The first run downloads ~15 GB of weights.
olmOCR-2 replies with YAML front matter (language/rotation/table
flags) followed by the document text; v1 checkpoints reply with JSON
carrying ``natural_text``. Both are parsed. olmOCR outputs no
coordinates by design, so reading order is preserved with synthesized
row boxes in the same unified structure as every other plugin.
"""
from transformers import AutoModelForImageTextToText, AutoProcessor
from torch import float16, bfloat16, float32, inference_mode
from olmocr.data.renderpdf import render_pdf_to_base64png
from re import compile, sub, DOTALL
from torch.version import cuda
from base64 import b64decode
from os.path import dirname
from json import loads
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:  # olmOCR-2 prompt (v4 yaml)
    from olmocr.prompts.prompts import build_no_anchoring_v4_yaml_prompt as _build_prompt
except ImportError:
    try:
        from olmocr.prompts import build_no_anchoring_yaml_prompt as _build_prompt
    except ImportError:
        _build_prompt = None
try:  # legacy v1 prompt pieces (anchor text)
    from olmocr.prompts import build_finetuning_prompt as _build_v1_prompt
    from olmocr.prompts.anchor import get_anchor_text as _get_anchor_text
except ImportError:
    _build_v1_prompt = None
    _get_anchor_text = None

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

#: YAML front matter block at the top of olmOCR-2 replies.
_FRONT_MATTER = compile(r'^\s*---\s*\n.*?\n---\s*\n', DOTALL)


class OlmOcrLib(OCRPlugin):
    """
    OlmOcrLib class.
    """
    #: (model_name, device) -> (model, processor).
    _models = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: HF checkpoint (default 'allenai/olmOCR-2-7B-1025';
                      v1 checkpoints like 'allenai/olmOCR-7B-0225-preview'
                      also work and switch to the anchor-text prompt).
        :param processorSource: processor repo (default 'Qwen/Qwen2.5-VL-7B-Instruct', per the model card;
                                use 'Qwen/Qwen2-VL-7B-Instruct' for v1 models).
        :param device: 'cuda', 'mps', 'cpu', or None for device_map='auto'.
        :param targetDim: longest page side when rendering PDFs (default 1288, the pipeline default).
        :param maxPages: cap on PDF pages to process (default: all).
        :param maxNewTokens: generation budget per page (default 8192).
        :param temperature: sampling temperature (default 0.1, per the model card; 0 for greedy).
        :param prompt: extra instructions appended to the olmocr library's training prompt (base-class
                       feature, e.g. 'the document is in German'); change at runtime with ``setExtraPrompt()``.
                       olmOCR is tuned to its exact prompt, so keep additions short.
        :param image: PDF or image source (path, URL, PIL, bytes...).
        :param kwargs: other settings.
        """
        model = kwargs.pop('model', 'allenai/olmOCR-2-7B-1025')
        self.__m_processor_source = kwargs.pop('processorSource', 'Qwen/Qwen2.5-VL-7B-Instruct')
        self.__m_device = kwargs.pop('device', None)
        self.__m_target_dim = int(kwargs.pop('targetDim', 1288))
        self.__m_max_pages = kwargs.pop('maxPages', None)
        self.__m_max_new_tokens = int(kwargs.pop('maxNewTokens', 8192))
        self.__m_temperature = float(kwargs.pop('temperature', 0.1))
        super(OlmOcrLib, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Model management                                                   #
    # ------------------------------------------------------------------ #
    def _pickDtype(self):
        device = self.__m_device
        if device == 'cuda' or (device is None and cuda.is_available()):
            supported = getattr(cuda, 'is_bf16_supported', None)
            return bfloat16 if supported and supported() else float16
        if device == 'mps':
            return float16
        if device is None:
            return 'auto'
        return float32

    def _loadModel(self):
        key = (self.getModel(), self.__m_device)
        if key in OlmOcrLib._models:
            return OlmOcrLib._models[key]
        dtype = self._pickDtype()
        load_kwargs = {'low_cpu_mem_usage': True}
        if self.__m_device is None:
            load_kwargs['device_map'] = 'auto'
        try:
            processor = AutoProcessor.from_pretrained(self.__m_processor_source)
            for dtype_kwarg in ('torch_dtype', 'dtype'):
                try:
                    model = AutoModelForImageTextToText.from_pretrained(
                        self.getModel(), **dict(load_kwargs, **{dtype_kwarg: dtype}))
                    break
                except TypeError:
                    continue
            else:
                model = AutoModelForImageTextToText.from_pretrained(self.getModel(), **load_kwargs)
        except (KeyError, ValueError, OSError) as exc:
            raise OCRError('Could not load {}: {}'.format(self.getModel(), exc))
        model = model.eval()
        if self.__m_device is not None:
            model = model.to(self.__m_device)
        OlmOcrLib._models[key] = (model, processor)
        return OlmOcrLib._models[key]

    # ------------------------------------------------------------------ #
    # Prompting (via the olmocr library)                                 #
    # ------------------------------------------------------------------ #
    def _prompt(self, pdf_path=None, page=1):
        """
        The training prompt for the selected checkpoint family.
        """
        is_v1 = '-0225' in self.getModel() or '-0725' in self.getModel()
        if is_v1 and _build_v1_prompt is not None:
            anchor = ''
            if pdf_path and _get_anchor_text is not None:
                try:
                    anchor = _get_anchor_text(pdf_path, page, pdf_engine='pdfreport', target_length=4000)
                except Exception:  # noqa: BLE001 - anchor is best-effort
                    anchor = ''
            return _build_v1_prompt(anchor)
        if _build_prompt is not None:
            return _build_prompt()
        raise OCRError('No prompt builder found in this olmocr version: pip install -U olmocr')

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _pageImages(self):
        """
        Yield (PIL image, pdf_path_or_None, page_number). PDFs on disk
        are rendered page by page with the olmocr library's renderer;
        in-memory PDFs are rendered with pypdfium2; plain images pass
        through directly. Nothing is written to disk.
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        source = self.getImage()
        data = None
        if kind == 'url':
            from requests import get
            reply = get(source, timeout=min(self.getTimeout(), 120))
            reply.raise_for_status()
            data = reply.content
        elif kind in ('pil',):
            yield source.convert('RGB'), None, 1
            return
        elif kind == 'array':
            yield Image.fromarray(source).convert('RGB'), None, 1
            return
        elif kind in ('bytes', 'buffer'):
            data = self.imageBytes()
        elif kind != 'path':
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(source))
        if kind == 'path':
            with open(source, 'rb') as handle:
                head = handle.read(5)
            if head == b'%PDF-':
                for item in self._pdfPagesFromPath(source):
                    yield item
            else:
                yield Image.open(source).convert('RGB'), None, 1
            return
        if data[:5] == b'%PDF-':
            for item in self._pdfPagesFromBytes(data):
                yield item
        else:
            yield Image.open(BytesIO(data)).convert('RGB'), None, 1

    def _pageLimit(self, total):
        if self.__m_max_pages:
            return min(total, int(self.__m_max_pages))
        return total

    def _pdfPagesFromPath(self, pdfPath):
        """
        PDF on disk: the olmocr library's own renderer (pdftoppm).
        """
        from io import BytesIO
        from PIL import Image
        try:
            from pypdf import PdfReader
            total = len(PdfReader(pdfPath).pages)
        except Exception:  # noqa: BLE001 - renderer will fail past the end
            total = 1
        for page in range(1, self._pageLimit(total) + 1):
            encoded = render_pdf_to_base64png(pdfPath, page, target_longest_image_dim=self.__m_target_dim)
            yield Image.open(BytesIO(b64decode(encoded))).convert('RGB'), pdfPath, page

    def _pdfPagesFromBytes(self, data):
        """
        PDF in memory: pypdfium2, scaled so the longest side matches
        ``targetDim`` (what the library renderer does for files). No
        path exists, so the v1 anchor text is skipped (best-effort).
        """
        try:
            from pypdfium2 import PdfDocument
        except ImportError:
            raise OCRError(
                'In-memory PDF input needs pypdfium2 (pip install '
                'pypdfium2), or pass the PDF as a file path instead.')
        document = PdfDocument(data)
        try:
            for index in range(self._pageLimit(len(document))):
                page = document[index]
                longest = max(page.get_width(), page.get_height()) or 1.0
                yield (page.render(scale=self.__m_target_dim / longest).to_pil().convert('RGB'),
                       None, index + 1)
        finally:
            document.close()

    # ------------------------------------------------------------------ #
    # Output parsing                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extractText(reply):
        """
        olmOCR-2 yaml front matter, or v1 JSON with natural_text.
        """
        reply = (reply or '').strip()
        if reply.startswith('{'):
            try:
                return str(loads(reply).get('natural_text') or '')
            except ValueError:
                pass
        return _FRONT_MATTER.sub('', reply, count=1).strip()

    @classmethod
    def _textToRows(cls, text, start_row=0):
        words = []
        row = start_row
        for line in (text or '').splitlines():
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'\|', ' ', line).strip()
            if not line or set(line) <= set('-: '):
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _generate(self, model, processor, pil_image, prompt):
        """
        One page through the model, per the model card recipe.
        """
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': prompt},
                                                 {'type': 'image', 'image': pil_image}]}]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[text], images=[pil_image], padding=True, return_tensors='pt').to(model.device)
        generate_kwargs = {'max_new_tokens': self.__m_max_new_tokens}
        if self.__m_temperature > 0:
            generate_kwargs.update(do_sample=True, temperature=self.__m_temperature)
        with inference_mode():
            output = model.generate(**inputs, **generate_kwargs)
        prompt_length = inputs['input_ids'].shape[1]
        return processor.decode(output[0][prompt_length:], skip_special_tokens=True)

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        model, processor = self._loadModel()
        words = []
        for pil_image, pdf_path, page in self._pageImages():
            prompt = self.composePrompt(self._prompt(pdf_path, page))
            reply = self._generate(model, processor, pil_image, prompt)
            text = self._extractText(reply)
            words.extend(self._textToRows(text, start_row=len(words)))
        if not words:
            raise OCRError('olmOCR produced no text for this document.')
        return words
