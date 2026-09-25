# coding=utf-8
"""
GOT-OCR 2.0 plugin (local,
https://huggingface.co/stepfun-ai/GOT-OCR-2.0-hf).
GOT -- "General OCR Theory: Towards OCR-2.0 via a Unified End-to-end
Model" -- is a 580M unified model built on the premise that every
artificial optical signal is a "character": plain text, math and
molecular formulas, tables, charts, geometric shapes and even SHEET
MUSIC. A high-compression encoder feeds a long-context decoder, so
one small model covers scene text, whole-page documents and slices,
and emits either plain text or FORMATTED output (markdown/LaTeX/tikz/
smiles/kern) on request.
This plugin drives the native transformers integration, following the
documented recipe exactly: ``AutoModelForImageTextToText`` +
``AutoProcessor(use_fast=True)``, the processor called with the image
alone (GOT's OCR prompt is built in -- there is no prompt to write),
then ``generate(do_sample=False, tokenizer=processor.tokenizer,
stop_strings='<|im_end|>')`` and decoding with the prompt tokens
trimmed off.
Install::
    pip install transformers torch accelerate
    pip install pypdfium2      # only for PDF input
Features, each mapped to a documented processor argument:
    formatted=True     ask for formatted output (markdown/LaTeX...)
                       instead of plain text -- the ``format`` kwarg.
    cropToPatches=True dynamically crop the page into patches and
                       merge the results. GOT natively takes
                       1024x1024, which covers A4 pages and scene
                       text, but horizontally stitched two-page PDFs
                       or odd aspect ratios read better in patches
                       (``max_patches``, default 12).
    box=[x1,y1,x2,y2]  INTERACTIVE region OCR: read only that region.
    color='red'        read only the text inside a box of that colour
                       ('red', 'green', 'blue').
    multiPage=True     several pages in ONE pass, producing a single
                       continuous text -- for documents whose
                       formatting runs across page boundaries. PDFs
                       use this automatically.
Output is plain text (or the requested markup) mapped to ordered rows
in the unified structure (like OCR.Space's shape, rows tier): GOT is
autoregressive and returns no coordinates. For pixel boxes use
RapidOcr, the Nemotron plugins or HunyuanOCR's spotting task. Sheet
music, tikz and SMILES come back as source you can render with
verovio, pdftex or rdkit.
No temporary files are written: images stay in memory, PDFs are
rasterized in RAM.
"""
from os.path import dirname
from requests import get
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

_DEFAULT_MODEL = 'stepfun-ai/GOT-OCR-2.0-hf'
#: The stop string the model card generates with.
_STOP_STRING = '<|im_end|>'
#: Default PDF rasterization DPI.
_PDF_DPI = 200.0
#: Colours the model was taught to key on.
_COLORS = ('red', 'green', 'blue')
_BOX_SPACES = ('pixel', 'norm1000')


class GotOcr(OCRPlugin):
    """
    GotOcr class.
    """
    #: (model, device) -> (processor, model), loaded once per process.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'stepfun-ai/GOT-OCR-2.0-hf'; a local directory works too).
        :param formatted: request formatted output -- Markdown, LaTeX, tikz, smiles, kern (default False).
        :param cropToPatches: crop large or oddly shaped images into patches (default False).
        :param minPatches: minimum patches when cropping (default 1).
        :param maxPatches: maximum patches when cropping (default 12).
        :param box: [x1, y1, x2, y2] region to read (interactive OCR).
        :param boxSpace: 'pixel' (default) or 'norm1000' -- how your
                         box coordinates are expressed; 'pixel' values
                         are converted to the 0-1000 grid the processor documents.
        :param color: 'red', 'green' or 'blue' -- read the text inside a box of that colour.
        :param multiPage: process several pages in one pass,
                            yielding one continuous text (default: True for multi-page PDFs).
        :param numImageTokens: image tokens per image (default 256).
        :param maxTokens: generation budget (default 4096).
        :param pdfDpi: PDF rasterization DPI (default 200).
        :param pages: optional list of 0-based PDF page indices.
        :param device: 'cuda', 'cpu', or None for 'auto'.
        :param image: image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (timeout, retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        self.__m_formatted = bool(kwargs.pop('formatted', False))
        self.__m_crop = bool(kwargs.pop('cropToPatches', False))
        self.__m_min_patches = int(kwargs.pop('minPatches', 1))
        self.__m_max_patches = int(kwargs.pop('maxPatches', 12))
        self.__m_box = kwargs.pop('box', None)
        space = str(kwargs.pop('boxSpace', 'pixel')).lower()
        if space not in _BOX_SPACES:
            raise OCRError("boxSpace must be 'pixel' or 'norm1000', not {!r}".format(space))
        self.__m_box_space = space
        color = kwargs.pop('color', None)
        if color is not None and str(color).lower() not in _COLORS:
            raise OCRError('color must be one of {}, not {!r}'.format(', '.join(_COLORS), color))
        self.__m_color = str(color).lower() if color else None
        self.__m_multi_page = kwargs.pop('multiPage', None)
        self.__m_num_image_tokens = int(kwargs.pop('numImageTokens', 256))
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 4096))
        self.__m_pdf_dpi = float(kwargs.pop('pdfDpi', _PDF_DPI))
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_device = kwargs.pop('device', None)
        super(GotOcr, self).__init__(*args, **kwargs)
        self.setModel(model)
        self.setOnline(False)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        key = (self.getModel(), self.__m_device)
        if key in GotOcr._engines:
            return GotOcr._engines[key]
        try:
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError:
            raise OCRError('GOT-OCR 2.0 needs transformers and torch. Run: pip install transformers torch accelerate')
        try:
            processor = AutoProcessor.from_pretrained(self.getModel(), use_fast=True)
            model = AutoModelForImageTextToText.from_pretrained(self.getModel(), device_map=self.__m_device or 'auto')
        except Exception as exc:  # noqa: BLE001 - load failure
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'GOT-OCR ran out of GPU memory loading the model '
                    "(unusual for 580M): try device='cpu'. Original: " + message[:180])
            raise OCRError(
                "Could not load '{}' ({}): {}. GOT-OCR needs a recent "
                'transformers (the model was integrated in 4.48); '
                'upgrade with pip install -U transformers.'.format(self.getModel(), type(exc).__name__, message[:180]))
        GotOcr._engines[key] = (processor, model)
        return GotOcr._engines[key]

    # ------------------------------------------------------------------ #
    # Input handling (in memory, no temporary files)                     #
    # ------------------------------------------------------------------ #
    def _pageImages(self):
        """
        [PIL images]: one entry per page.
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'pil':
            image = self.getImage()
            image.load()
            return [image.convert('RGB')]
        if kind == 'array':
            from numpy import asarray
            return [Image.fromarray(asarray(self.getImage())).convert('RGB')]
        if kind == 'url':
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
        image = Image.open(BytesIO(data))
        image.load()
        return [image.convert('RGB')]

    def _pdfPages(self, data):
        try:
            from pypdfium2 import PdfDocument
        except ImportError:
            raise OCRError(
                'PDF input needs pypdfium2 for rasterization. Run: pip install pypdfium2 (or pass page images instead).'
            )
        scale = self.__m_pdf_dpi / 72.0
        document = PdfDocument(data)  # bytes: no temp file
        try:
            count = len(document)
            wanted = range(count) if self.__m_pages is None else [index for index in self.__m_pages if 0 <= index < count]
            return [document[index].render(scale=scale).to_pil().convert('RGB') for index in wanted]
        finally:
            document.close()

    # ------------------------------------------------------------------ #
    # Processor arguments                                                #
    # ------------------------------------------------------------------ #
    def _boxArgument(self, size):
        """
        The box in the 0-1000 grid the processor documents.
        """
        if not self.__m_box:
            return None
        try:
            values = [float(value) for value in self.__m_box]
        except (TypeError, ValueError):
            raise OCRError('box must be [x1, y1, x2, y2] numbers, not {!r}'.format(self.__m_box))
        if len(values) != 4:
            raise OCRError('box must have 4 values [x1, y1, x2, y2], got {}'.format(len(values)))
        if self.__m_box_space == 'pixel':
            width, height = size
            values = [values[0] / width * 1000.0, values[1] / height * 1000.0, values[2] / width * 1000.0,
                      values[3] / height * 1000.0]
        return [int(round(value)) for value in values]

    def _processorKwargs(self, size):
        options = {'return_tensors': 'pt'}
        if self.__m_formatted:
            options['format'] = True
        if self.__m_crop:
            options['crop_to_patches'] = True
            options['min_patches'] = self.__m_min_patches
            options['max_patches'] = self.__m_max_patches
        box = self._boxArgument(size)
        if box:
            options['box'] = box
        if self.__m_color:
            options['color'] = self.__m_color
        if self.__m_num_image_tokens != 256:
            options['num_image_tokens'] = self.__m_num_image_tokens
        return options

    # ------------------------------------------------------------------ #
    # Inference (the documented recipe)                                  #
    # ------------------------------------------------------------------ #
    def _transcribe(self, images, multi_page):
        from torch import inference_mode
        processor, model = self._engine()
        size = images[0].size
        options = self._processorKwargs(size)
        if multi_page:
            options['multi_page'] = True
            payload = images
        else:
            payload = images[0] if len(images) == 1 else images
        inputs = processor(payload, **options)
        inputs = inputs.to(model.device)
        with inference_mode():
            generated = model.generate(**inputs, do_sample=False, tokenizer=processor.tokenizer,
                                       stop_strings=_STOP_STRING, max_new_tokens=self.__m_max_tokens)
        prompt_length = inputs['input_ids'].shape[1]
        if multi_page or len(images) == 1:
            return [processor.decode(generated[0, prompt_length:], skip_special_tokens=True)]
        return list(processor.batch_decode(generated[:, prompt_length:], skip_special_tokens=True))

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
        text = sub(r'(?i)<\s*/?\s*(p|div|table|tbody|thead)[^>]*>', ' ', text)
        text = sub(r'<[^>]+>', '', text)
        return text

    @classmethod
    def _toRows(cls, text, start_row=0):
        words = []
        row = start_row
        text = (text or '').replace(_STOP_STRING, '')
        for line in cls._htmlToText(text).splitlines():
            line = sub(r'^\s*```[a-zA-Z]*\s*$', '', line)
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)
            line = sub(r'\|', ' ', line)
            line = sub(r'\s+', ' ', line).strip()
            if not line or set(line) <= set('-:*_ '):
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                (ordered rows; GOT-OCR returns text without coordinates).
        """
        images = self._pageImages()
        if not images:
            raise OCRError('No page to read (check pages=).')
        multiPage = self.__m_multi_page
        if multiPage is None:
            multiPage = len(images) > 1
        if multiPage and len(images) == 1:
            multiPage = False
        try:
            outputs = self._transcribe(images, multiPage)
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'GOT-OCR ran out of GPU memory: lower maxPatches, '
                    'process fewer pages at once (multiPage=False), '
                    "or use device='cpu'. Original: " + message[:180])
            raise OCRError('GOT-OCR inference failed: {}: {}'.format(type(exc).__name__, message[:220]))
        words = []
        for output in outputs:
            words.extend(self._toRows(output, start_row=len(words)))
        if not words:
            detail = ''
            if self.__m_box or self.__m_color:
                detail = (' The region filter may not match any text: check the box coordinates (boxSpace=) or '
                          'the colour of the drawn box.')
            raise OCRError('GOT-OCR returned no text for this image.{} For large or oddly shaped pages try '
                           'cropToPatches=True.'.format(detail))
        return words
