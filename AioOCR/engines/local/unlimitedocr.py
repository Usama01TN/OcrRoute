# coding=utf-8
"""
Unlimited-OCR plugin (local,
https://huggingface.co/baidu/Unlimited-OCR).
Unlimited-OCR is Baidu's "one-shot long-horizon parsing" model (June
2026): a 3B document parser, MIT-licensed and multilingual, built to
push DeepSeek-OCR one step further. Its signature trick is
LONG-HORIZON MULTI-PAGE parsing -- ``infer_multi`` reads an entire
document (many pages / a whole PDF) in a single 32k-token pass,
keeping cross-page context, instead of page-by-page calls.
GGUF/Ollama quantizations exist for smaller machines; vLLM and SGLang
serving recipes are on the model card.
Install (GPU recommended; 3B BF16)::
    pip install transformers torch pillow einops addict easydict
    pip install pypdfium2        # only for PDF input
The plugin follows the card's official transformers recipe exactly:
AutoModel/AutoTokenizer with trust_remote_code, the official prompts
('<image>document parsing.' single / '<image>Multi page parsing.'
multi), and the card's generation settings (max_length=32768,
no_repeat_ngram_size=35, ngram_window 128 single / 1024 multi).
Single images support the card's two configs via ``mode=``:
    mode='gundam'   (default) base_size=1024, image_size=640,
                    crop_mode=True -- tiles the page, best quality.
    mode='base'     image_size=1024, no cropping -- faster.
Multi-page input always uses the base config, per the card. PDFs are
rasterized in memory (pypdfium2) at the card-recommended 300 DPI.
Everything stays in memory: the model's ``infer``/``infer_multi`` are
written around file paths (they read images through a module-level
``load_pil_images()`` and create ``output_path`` via the module's
``os``), so for the duration of each call those two names in the
model's module are pointed at the decoded PIL pages and at an ``os``
stand-in whose ``makedirs`` is a no-op. Text is taken from the return
value (``eval_mode=True`` for ``infer``; ``infer_multi`` returns
``(text, token_count)``). Nothing is written to disk.
Geometry: Unlimited-OCR's raw output tags each block with
``<|det|>category [bbox]<|/det|>`` markers. For SINGLE images the
plugin parses them (grouping continuation lines into their block,
exactly like the card's own post-processing), scales the 0-1000
normalized coordinates to REAL image pixels, and emits block-level
boxes -- the dots.ocr geometry tier, locally. 'image' blocks are
skipped. For multipage runs, page-relative coordinates cannot be
anchored, so det markers are stripped and reading-order rows are
returned. Everything lands in the exact unified structure shared by
every plugin (like OCR.Space).
"""
from re import compile, split, sub, DOTALL
from sys import modules, path
from os.path import dirname

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_DEFAULT_MODEL = 'baidu/Unlimited-OCR'
_PROMPT_SINGLE = '<image>document parsing.'
_PROMPT_MULTI = '<image>Multi page parsing.'
#: The card's single-image configs.
_MODES = {
    'gundam': {'base_size': 1024, 'image_size': 640, 'crop_mode': True},
    'base': {'base_size': 1024, 'image_size': 1024, 'crop_mode': False},
}
#: The card's det-marker line format (its own OmniDocBench regex).
_DET_RE = compile(r'<\|det\|>([^<\s]+)(?:\s*\[([^\]]*)\])?\s*<\|/det\|>(.*)', DOTALL)
#: det bbox coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
#: Rasterization scale for PDF pages (card-recommended 300 DPI).
_PDF_SCALE = 300.0 / 72.0
#: Placeholder passed as image_file(s): only has to be truthy, the
#: patched loader never opens it.
_IN_MEMORY = '<in-memory>'


class _NoMakedirs(object):
    """
    Stand-in for the ``os`` module inside the model's remote code:
    everything is forwarded to the real module except ``makedirs``,
    which becomes a no-op so infer creates no output directory.
    """

    def __init__(self, real):
        self.__m_real = real

    def makedirs(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self.__m_real, name)


class InMemoryInfer(object):
    """
    Context manager under which the model's ``infer``/``infer_multi``
    work on PIL images and touch no files. The remote code looks up
    ``load_pil_images`` and ``os`` as globals of its own module at call
    time; both are swapped on entry and restored on exit (also on
    error). Not thread-safe -- neither is the model's infer.
    """

    def __init__(self, model, images):
        self.__m_module = modules[type(model).__module__]
        self.__m_images = [image.convert('RGB') for image in images]
        self.__m_saved = None

    def __enter__(self):
        module = self.__m_module
        if not callable(module.__dict__.get('load_pil_images')) or 'os' not in module.__dict__:
            raise OCRError(
                "The remote code of this checkpoint has no module-level "
                "'load_pil_images'/'os' (not the official Unlimited-OCR "
                "layout), so it cannot be run in memory by this plugin.")
        self.__m_saved = (module.__dict__['load_pil_images'], module.__dict__['os'])
        images = self.__m_images
        module.load_pil_images = lambda conversations: list(images)
        module.os = _NoMakedirs(self.__m_saved[1])
        return self

    def __exit__(self, *exc_info):
        module = self.__m_module
        module.load_pil_images, module.os = self.__m_saved
        return False


class UnlimitedOcr(OCRPlugin):
    """
    UnlimitedOcr class.
    """
    #: model name -> (tokenizer, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'baidu/Unlimited-OCR').
        :param mode: single-image config, 'gundam' (default) or 'base'; multi-page always uses base per the card.
        :param ocrPrompt: override the official prompt ('<image>' is prepended if missing; was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``.
        :param maxLength: generation budget (default 32768, per the card).
        :param ngramWindow: anti-repetition window (default 128 for single images, 1024 for multi-page, per the card).
        :param noRepeatNgram: no_repeat_ngram_size (default 35).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: optional list of 0-based PDF page indices.
        :param image: image/PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        mode = str(kwargs.pop('mode', 'gundam')).lower()
        if mode not in _MODES:
            raise OCRError("mode must be 'gundam' or 'base', not {!r}".format(mode))
        self.__m_mode = mode
        self.__m_prompt = kwargs.pop('ocrPrompt', None)  # 'prompt' stays for the base class
        self.__m_max_length = int(kwargs.pop('maxLength', 32768))
        self.__m_ngram_window = kwargs.pop('ngramWindow', None)
        self.__m_no_repeat = int(kwargs.pop('noRepeatNgram', 35))
        self.__m_device = kwargs.pop('device', None)
        self.__m_pages = kwargs.pop('pages', None)
        super(UnlimitedOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        name = self.getModel()
        if name in UnlimitedOcr._engines:
            return UnlimitedOcr._engines[name]
        try:
            from transformers import AutoModel, AutoTokenizer
            from torch import cuda, bfloat16, float32
        except ImportError:
            raise OCRError(
                'Unlimited-OCR needs transformers and torch (plus its '
                'custom-code deps). Run: pip install transformers '
                'torch pillow einops addict easydict')
        device = self.__m_device or ('cuda' if cuda.is_available() else 'cpu')
        dtype = bfloat16 if device == 'cuda' else float32
        tokenizer = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
        model = AutoModel.from_pretrained(name, trust_remote_code=True, use_safetensors=True, torch_dtype=dtype)
        model = model.eval()
        model = model.cuda() if device == 'cuda' else model.to(device)
        UnlimitedOcr._engines[name] = (tokenizer, model, device)
        return UnlimitedOcr._engines[name]

    # ------------------------------------------------------------------ #
    # Input handling (PIL pages, all in memory)                          #
    # ------------------------------------------------------------------ #
    def _pageImages(self):
        """
        RGB PIL page images (+ sizes) from any source.
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind in ('pil', 'array'):
            from numpy import asarray
            image = self.getImage() if kind == 'pil' else Image.fromarray(asarray(self.getImage()))
            image = image.convert('RGB')
            return [image], [image.size]
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
            return self._pdfPageImages(data)
        image = Image.open(BytesIO(data)).convert('RGB')
        return [image], [image.size]

    def _pdfPageImages(self, data):
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
            images, sizes = [], []
            for index in wanted:
                image = document[index].render(scale=_PDF_SCALE).to_pil().convert('RGB')
                images.append(image)
                sizes.append(image.size)
            return images, sizes
        finally:
            document.close()

    # ------------------------------------------------------------------ #
    # det-marker parsing (the card's own block grouping)                 #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _blocksFromRaw(raw):
        """
        [(category, bbox-or-None, [lines])] in reading order.
        """
        blocks = []
        current = None
        for line in (raw or '').splitlines():
            line = line.rstrip()
            if not line:
                continue
            match = _DET_RE.match(line)
            if match:
                category = match.group(1).strip()
                bbox = None
                if match.group(2):
                    try:
                        values = [float(v) for v in split(r'[,\s]+', match.group(2).strip()) if v]
                        if len(values) >= 4:
                            bbox = values[:4]
                    except ValueError:
                        bbox = None
                content = match.group(3).strip()
                if current is not None:
                    blocks.append(current)
                current = [category, bbox, [content] if content else []]
                continue
            if current is None:
                current = ['text', None, []]
            current[2].append(line)
        if current is not None:
            blocks.append(current)
        return blocks

    @staticmethod
    def _cleanLine(line):
        line = sub(r'<\|[^|]*\|>|<PAGE>', '', line)
        line = sub(r'^[#>\s]+', '', line)
        line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)
        line = sub(r'\|', ' ', line)
        return sub(r'\s+', ' ', line).strip()

    def _wordsFromDet(self, raw, size):
        """
        Single image: det blocks -> block boxes in REAL pixels.
        """
        width, height = size
        words = []
        row = 0
        for category, bbox, lines in self._blocksFromRaw(raw):
            if category.lower() == 'image':
                continue
            cleaned = [self._cleanLine(l) for l in lines]
            cleaned = [l for l in cleaned if l and not set(l) <= set('-: ')]
            if not cleaned:
                continue
            rect = None
            if bbox:
                x1, y1, x2, y2 = bbox
                if x2 > x1 and y2 > y1:
                    rect = (x1 / _COORD_SPACE * width, y1 / _COORD_SPACE * height,
                            (x2 - x1) / _COORD_SPACE * width, (y2 - y1) / _COORD_SPACE * height)
            for line_index, line in enumerate(cleaned):
                if rect:
                    left, top, box_w, box_h = rect
                    line_height = box_h / len(cleaned)
                    words.append(self.makeWord(
                        line, left, top + line_index * line_height, box_w, max(line_height, 1.0)))
                else:
                    words.append(self.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
                row += 1
        return words

    def _rowsFromRaw(self, raw, start_row=0):
        """
        Multi-page: det markers stripped, ordered rows.
        """
        words = []
        row = start_row
        for category, _, lines in self._blocksFromRaw(raw):
            if category.lower() == 'image':
                continue
            for line in lines:
                line = self._cleanLine(line)
                if not line or set(line) <= set('-: '):
                    continue
                words.append(self.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
                row += 1
        return words

    # ------------------------------------------------------------------ #
    # Inference                                                          #
    # ------------------------------------------------------------------ #
    def _prompt(self, multi):
        prompt = self.composePrompt(self.__m_prompt or (_PROMPT_MULTI if multi else _PROMPT_SINGLE))
        if '<image>' not in prompt:
            prompt = '<image>' + prompt
        return prompt

    def _infer(self, images):
        """
        Run the model on PIL pages; returns the raw text reply.
        """
        tokenizer, model, _ = self._engine()
        multi = len(images) > 1
        window = self.__m_ngram_window or (1024 if multi else 128)
        common = {
            'output_path': '',
            'max_length': self.__m_max_length,
            'no_repeat_ngram_size': self.__m_no_repeat,
            'ngram_window': window,
            'save_results': False,
        }
        with InMemoryInfer(model, images):
            if multi:  # long-horizon one-shot: base config, per the card
                result = model.infer_multi(
                    tokenizer, prompt=self._prompt(True), image_files=[_IN_MEMORY] * len(images),
                    image_size=1024, **common)
            else:
                result = model.infer(
                    tokenizer, prompt=self._prompt(False), image_file=_IN_MEMORY, eval_mode=True,
                    **dict(_MODES[self.__m_mode], **common))
        if isinstance(result, tuple):  # infer_multi -> (text, token_count)
            result = result[0]
        if result is None:
            result = ''
        return str(getattr(result, 'text', result) or '')

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (single image: block-level REAL-PIXEL boxes from the
                 det markers; multi-page: ordered rows).
        """
        images, sizes = self._pageImages()
        try:
            raw = self._infer(images)
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'Unlimited-OCR ran out of GPU memory: try '
                    "mode='base' (no crop tiling), fewer pages "
                    'per call, or a GGUF/Ollama quantization of '
                    "'baidu/Unlimited-OCR'. Original: " + message[:200])
            raise OCRError('Unlimited-OCR inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        if len(images) == 1:
            words = self._wordsFromDet(raw, sizes[0])
        else:
            words = self._rowsFromRaw(raw)
        if not words:
            raise OCRError(
                'Unlimited-OCR produced no text for this document (for scans, ensure ~300 DPI; gundam '
                'mode reads small text best).')
        return words
