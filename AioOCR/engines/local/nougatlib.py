# coding=utf-8
"""
Nougat plugin using Meta's nougat-ocr library directly
(https://github.com/facebookresearch/nougat).
Nougat -- "Neural Optical Understanding for Academic Documents" --
transcribes scientific PDFs into Mathpix Markdown (.mmd), turning
equations into LaTeX and tables into markup that plain OCR mangles.
This plugin calls the LIBRARY, in-process: NougatModel,
get_checkpoint, LazyDataset, model.inference and
markdown_compatible -- the same calls the repo's own predict.py
makes. There is no subprocess, no CLI, and NO TEMPORARY FILES: a PDF
path is handed to the dataset as-is, in-memory PDFs go in as a
BytesIO stream, and images are wrapped into a one-page PDF in RAM.
Install::
    pip install nougat-ocr
The package pins older dependency versions; if pip fights your
environment, install with ``--no-deps`` and supply torch,
transformers, timm, orjson, opencv-python-headless and pypdf
yourself. (The sibling ``nougatocr.py`` plugin runs the same weights
through plain transformers if you would rather avoid the package
entirely -- but it cannot do the repo's markdown post-processing.)
Checkpoints (downloaded automatically on first use, ``model=``):
    '0.1.0-small'   default, ~250M params, fine on CPU
    '0.1.0-base'    ~350M, better quality, GPU recommended
IMAGE SUPPORT: yes. PDFs go through ``LazyDataset`` exactly as
predict.py does them, and ``pages=`` selects pages (1-based, like the
CLI; accepts '1-4,7' as well as [1, 2, 3]). IMAGES (png, jpeg, PIL
objects, numpy arrays, bytes, URLs) skip PDF conversion altogether:
they are fed straight to ``model.encoder.prepare_input``, the same
call the library's own datasets make on each rasterized page, then
batched with ``torch.stack`` into ``model.inference``. That keeps the
image at its native resolution -- converting to PDF and re-rasterizing
at Nougat's 96 dpi would quietly downscale it -- and still writes
nothing to disk.
Bear in mind WHAT the image should be: Nougat expects a full
document page in arXiv style. A photo of a receipt or a UI screenshot
is outside its training domain and typically returns the
"no output for this page" signal (see repetition handling below).
REPETITION HANDLING is Nougat's own failure mode and the library
reports it: ``model.inference`` returns a ``repeats`` entry per page.
Following predict.py, a page with ``repeats > 0`` was truncated mid-repetition
loop (its text is kept, and flagged in ``getWarnings()``),
while ``repeats == 0`` means the page was too far from the training
domain to transcribe (skipped, also flagged). ``noSkipping=True``
disables the early-stopping that detects this.
Output is Mathpix Markdown mapped to ordered rows in the unified
structure (like OCR.Space's shape, rows tier) -- Nougat is
autoregressive and returns NO coordinates.
SCOPE, per the repo's own limitations: English scientific papers
(arXiv-style). On receipts, forms or screenshots it underperforms
every general engine -- use RapidOcr, the Nemotron plugins, or a
document VLM for those.
"""
from re import compile, sub
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

_DEFAULT_MODEL = '0.1.0-small'
#: Nougat's markers for pages it could not transcribe.
_MISSING_PAGE = compile(r'\[MISSING_PAGE[^\]]*\]')


class NougatLib(OCRPlugin):
    """
    NougatLib class.
    """
    #: (checkpoint, full_precision) -> NougatModel, once per process.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint tag (default '0.1.0-small'; also '0.1.0-base').
        :param checkpoint: explicit checkpoint directory, bypassing the tag download.
        :param pages: 1-based PDF pages to read: [1, 2, 3] or '1-4,7' (default: all).
        :param batchSize: pages per batch (default: the library's own default_batch_size()).
        :param noSkipping: disable Nougat's repetition detection and early stopping (default False).
        :param fullPrecision: use fp32 instead of bfloat16 (default False).
        :param markdown: apply markdown_compatible post-processing (default True, as predict.py does).
        :param cuda: run on GPU when available (default True).
        :param image: PDF or image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        self.__m_checkpoint = kwargs.pop('checkpoint', None)
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_batch_size = kwargs.pop('batchSize', None)
        self._NougatLib__no_skipping = bool(kwargs.pop('noSkipping', False))
        self.__m_full_precision = bool(kwargs.pop('fullPrecision', False))
        self.__m_markdown = bool(kwargs.pop('markdown', True))
        self.__m_cuda = bool(kwargs.pop('cuda', True))
        self.__m_warnings = []
        super(NougatLib, self).__init__(*args, **kwargs)
        self.setModel(model)
        self.setOnline(False)

    def getWarnings(self):
        """
        :return: per-page notes from the last run (truncated pages, pages Nougat could not transcribe).
        """
        return list(self.__m_warnings)

    # ------------------------------------------------------------------ #
    # Library imports (one friendly error for the whole package)         #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _library():
        """
        Import the nougat-ocr pieces predict.py uses.
        """
        try:
            from nougat.postprocessing import markdown_compatible
            from nougat.utils.checkpoint import get_checkpoint
            from nougat.utils.device import move_to_device
            from nougat.utils.dataset import LazyDataset
            from nougat import NougatModel
        except ImportError as exc:
            raise OCRError(
                'The nougat-ocr library is not installed (or is '
                'incomplete): pip install nougat-ocr. If dependency '
                'pins clash with your environment, use pip install '
                '--no-deps nougat-ocr and provide torch, '
                'transformers, timm, orjson, opencv-python-headless '
                'and pypdf yourself. Original: ' + str(exc)[:160])
        return NougatModel, get_checkpoint, move_to_device, LazyDataset, markdown_compatible

    # ------------------------------------------------------------------ #
    # Model (mirrors predict.py's setup)                                 #
    # ------------------------------------------------------------------ #
    def _model(self):
        key = (self.__m_checkpoint or self.getModel(), self.__m_full_precision, self.__m_cuda)
        if key in NougatLib._engines:
            return NougatLib._engines[key]
        NougatModel, get_checkpoint, move_to_device, _, _ = self._library()
        checkpoint = self.__m_checkpoint
        if not checkpoint:
            checkpoint = get_checkpoint(model_tag=self.getModel())
        if not checkpoint:
            raise OCRError(
                "Could not obtain the Nougat checkpoint '{}': check "
                'the tag and the network, or pass checkpoint=/path/to/weights.'.format(self.getModel()))
        model = NougatModel.from_pretrained(checkpoint)
        try:
            model = move_to_device(model, bf16=not self.__m_full_precision, cuda=self.__m_cuda)
        except Exception:  # noqa: BLE001 - CPU-only environments
            pass
        model.eval()
        NougatLib._engines[key] = model
        return model

    def _batchSize(self):
        from nougat.utils import device
        if self.__m_batch_size is not None:
            return max(int(self.__m_batch_size), 1)
        try:
            from device import default_batch_size
            return max(int(default_batch_size()), 1)
        except Exception:  # noqa: BLE001 - helper is optional
            return 1

    # ------------------------------------------------------------------ #
    # Input handling (no temporary files)                                #
    # ------------------------------------------------------------------ #
    def _source(self):
        """
        ('pdf', path-or-BytesIO) or ('images', [PIL images]).
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'pil':
            image = self.getImage()
            image.load()
            return 'images', [image.convert('RGB')]
        if kind == 'array':
            from numpy import asarray
            return 'images', [Image.fromarray(asarray(self.getImage())).convert('RGB')]
        if kind == 'path':
            data = self.imageBytes()
            if data[:5] == b'%PDF-':
                return 'pdf', self.getImage()  # real path: no copy
        elif kind == 'url':
            from requests import get
            reply = get(self.getImage(), timeout=self.getTimeout())
            reply.raise_for_status()
            data = reply.content
        elif kind in ('bytes', 'buffer'):
            data = self.imageBytes()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(self.getImage()))
        if data[:5] == b'%PDF-':
            return 'pdf', BytesIO(data)  # pypdf/pypdfium2 read it
        image = Image.open(BytesIO(data))
        image.load()
        return 'images', [image.convert('RGB')]

    def _pageList(self):
        """
        Pages as a list of 1-based ints, or None for all.
        """
        pages = self.__m_pages
        if pages is None:
            return None
        if isinstance(pages, str):
            numbers = []
            for chunk in pages.split(','):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if '-' in chunk:
                    start, end = chunk.split('-', 1)
                    numbers.extend(range(int(start), int(end) + 1))
                else:
                    numbers.append(int(chunk))
            return numbers
        return [int(page) for page in pages]

    # ------------------------------------------------------------------ #
    # Inference (the predict.py loop)                                    #
    # ------------------------------------------------------------------ #
    def _collect(self, result, outputs, page_number, markdown_compatible):
        """
        Apply predict.py's repeats handling to one inference call.
        """
        predictions = result.get('predictions') or []
        repeats = result.get('repeats') or [None] * len(predictions)
        for index, prediction in enumerate(predictions):
            page_number += 1
            repeat = repeats[index] if index < len(repeats) else None
            if repeat is not None and not repeat:
                # predict.py: no output for this page at all.
                self._NougatLib__warnings.append(
                    "Page {}: no output (the page is too far from Nougat's training domain).".format(page_number))
                continue
            if repeat:
                self._NougatLib__warnings.append('Page {}: truncated because of repetitions.'.format(page_number))
            text = prediction or ''
            if self._NougatLib__markdown:
                text = markdown_compatible(text)
            outputs.append(text)
        return page_number

    def _transcribePdf(self, pdf_source):
        """
        PDF pages through LazyDataset, exactly as predict.py does.
        """
        from functools import partial
        _, _, _, LazyDataset, markdown_compatible = self._library()
        from torch.utils.data import DataLoader
        model = self._model()
        prepare = partial(model.encoder.prepare_input, random_padding=False)
        try:
            dataset = LazyDataset(pdf_source, prepare, self._pageList())
        except Exception as exc:  # noqa: BLE001 - unreadable PDF
            raise OCRError('Nougat could not open this PDF: {}: {}'.format(type(exc).__name__, str(exc)[:200]))
        loader = DataLoader(dataset, batch_size=self._batchSize(), pin_memory=True, shuffle=False,
                            collate_fn=LazyDataset.ignore_none_collate)
        outputs, page_number = [], 0
        for batch in loader:
            sample = batch[0] if isinstance(batch, (list, tuple)) else batch
            if sample is None:
                continue
            result = model.inference(image_tensors=sample, early_stopping=not self._NougatLib__no_skipping)
            page_number = self._collect(result, outputs, page_number, markdown_compatible)
        return '\n\n'.join(outputs)

    def _transcribeImages(self, images):
        """
        Images straight through the encoder: no PDF, no resampling.
        """
        _, _, _, _, markdown_compatible = self._library()
        from torch import stack
        model = self._model()
        tensors = []
        for image in images:
            try:
                tensors.append(model.encoder.prepare_input(image, random_padding=False))
            except Exception as exc:  # noqa: BLE001 - odd input
                raise OCRError('Nougat could not prepare this image: {}: {}'.format(type(exc).__name__, str(exc)[:200]))
        outputs, page_number = [], 0
        size = self._batchSize()
        for start in range(0, len(tensors), size):
            batch = stack(tensors[start:start + size])
            result = model.inference(image_tensors=batch, early_stopping=not self._NougatLib__no_skipping)
            page_number = self._collect(result, outputs, page_number, markdown_compatible)
        return '\n\n'.join(outputs)

    # ------------------------------------------------------------------ #
    # Mathpix Markdown -> rows                                           #
    # ------------------------------------------------------------------ #
    @classmethod
    def _markdownToRows(cls, markdown):
        words = []
        row = 0
        for line in (markdown or '').splitlines():
            line = _MISSING_PAGE.sub('', line)
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'!\[[^\]]*\]\([^)]*\)', '', line)
            line = sub(r'\|', ' ', line)
            line = sub(r'\s+', ' ', line).strip()
            if not line or set(line) <= set('-:*_+= '):
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
                 (ordered rows; Nougat emits Markdown without coordinates).
        """
        self.__m_warnings = []
        kind, source = self._source()
        try:
            markdown = (self._transcribePdf(source) if kind == 'pdf' else self._transcribeImages(source))
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'Nougat ran out of GPU memory: lower batchSize, '
                    "use model='0.1.0-small', or pass cuda=False. Original: " + message[:200])
            raise OCRError('Nougat inference failed: {}: {}'.format(type(exc).__name__, message[:220]))
        words = self._markdownToRows(markdown)
        if not words:
            detail = (' ' + ' '.join(self.__m_warnings) if self.__m_warnings else '')
            raise OCRError(
                'Nougat produced no text for this input.{} Nougat is '
                'built for English scientific paper PAGES -- '
                'screenshots, receipts, forms and cropped text are '
                'outside its training domain, so use a general engine '
                '(RapidOcr, the Nemotron plugins) or a document VLM for those.'.format(detail))
        return words
