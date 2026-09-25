# coding=utf-8
"""
Nougat OCR plugin (local, Meta's Neural Optical Understanding for
Academic Documents -- https://github.com/facebookresearch/nougat).
Nougat is a vision-encoder-decoder model that transcribes ACADEMIC
document pages into markdown, and it is the specialist nothing else
in this suite matches: mathematical formulas come out as LaTeX
(``\\frac{a}{b}``, ``\\sum``...), and complex tables as markdown --
which plain OCR engines mangle. Fully local and free.
This plugin drives Nougat through Hugging Face transformers (the
standalone ``nougat-ocr`` package carries stale dependency pins)::
    pip install transformers torch pillow
    pip install pypdfium2          # only needed for PDF input
Models (downloaded automatically on first use):
    facebook/nougat-small   default, ~247M params, ok on CPU
    facebook/nougat-base    ~350M, better quality, GPU recommended
Nougat reads one-PAGE IMAGE at a time; PDFs are rasterized locally
with pypdfium2 page by page. It is trained on scientific papers
(arXiv-style layouts) -- on screenshots or receipts it underperforms
the general engines, so keep it for papers, theses and technical
reports. Being autoregressive markdown generation, it returns NO
coordinates: reading order is preserved with synthesized row boxes in
the exact unified structure shared by every plugin. Pages that Nougat
cannot read produce '[MISSING_PAGE...' markers, which are skipped.
"""
from re import sub, compile
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

_DEFAULT_MODEL = 'facebook/nougat-small'
#: Rasterization scale for PDF pages (2.0 ~ 144 dpi, Nougat's sweet
#: spot after its own resize).
_PDF_SCALE = 2.0
#: Nougat's failure markers for unreadable pages.
_MISSING_PAGE = compile(r'\[MISSING_PAGE[^\]]*\]')


class NougatOcr(OCRPlugin):
    """
    NougatOcr class.
    """
    #: model name -> (processor, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'facebook/nougat-small'; 'facebook/nougat-base' for better quality).
        :param maxTokens: generation budget per page (default 3584, Nougat's practical page maximum).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param fixMarkdown: let the processor normalize markdown (default False -- raw output is closer to the page).
        :param pages: optional list of 0-based PDF page indices (default: all pages).
        :param image: page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 3584))
        self.__m_device = kwargs.pop('device', None)
        self.__m_fix_markdown = bool(kwargs.pop('fixMarkdown', False))
        self.__m_pages = kwargs.pop('pages', None)
        super(NougatOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        name = self.getModel()
        if name in NougatOcr._engines:
            return NougatOcr._engines[name]
        try:
            from transformers import NougatProcessor, VisionEncoderDecoderModel
            from torch.cuda import is_available
        except ImportError:
            raise OCRError('Nougat needs transformers and torch. Run: pip install transformers torch pillow')
        device = self.__m_device or ('cuda' if is_available() else 'cpu')
        processor = NougatProcessor.from_pretrained(name)
        model = VisionEncoderDecoderModel.from_pretrained(name)
        model = model.to(device)
        model.eval()
        NougatOcr._engines[name] = (processor, model, device)
        return NougatOcr._engines[name]

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _pageImages(self):
        """
        Yield PIL page images from any source (PDFs rasterized).
        """
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
        """
        Rasterize PDF pages with pypdfium2.
        """
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
            images = []
            for index in wanted:
                page = document[index]
                bitmap = page.render(scale=_PDF_SCALE)
                images.append(bitmap.to_pil().convert('RGB'))
            return images
        finally:
            document.close()

    # ------------------------------------------------------------------ #
    # Markdown -> rows                                                   #
    # ------------------------------------------------------------------ #
    @classmethod
    def _markdownToRows(cls, markdown, start_row=0):
        words = []
        row = start_row
        for line in (markdown or '').splitlines():
            line = _MISSING_PAGE.sub('', line)
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
        """
        One-page image -> Nougat Markdown string.
        """
        from torch import inference_mode
        processor, model, device = self._engine()
        pixelValues = processor(images=image, return_tensors='pt').pixel_values
        generateKwargs = {'min_length': 1, 'max_new_tokens': self.__m_max_tokens}
        unk = getattr(processor.tokenizer, 'unk_token_id', None)
        if unk is not None:
            generateKwargs['bad_words_ids'] = [[unk]]
        with inference_mode():
            outputs = model.generate(pixelValues.to(device), **generateKwargs)
        sequence = processor.batch_decode(outputs, skip_special_tokens=True)[0]
        try:
            sequence = processor.post_process_generation(sequence, fix_markdown=self.__m_fix_markdown)
        except Exception:  # noqa: BLE001 - post-processing is optional
            pass
        return sequence or ''

    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                    (ordered rows; Nougat produces Markdown without coordinates).
        """
        words = []
        for pageImage in self._pageImages():
            markdown = self._transcribePage(pageImage)
            words.extend(self._markdownToRows(markdown, start_row=len(words)))
        if not words:
            raise OCRError(
                'Nougat produced no text for this document (screenshots '
                'and non-academic layouts often yield [MISSING_PAGE]; '
                'Nougat is built for scientific papers -- use a general '
                'engine like RapidOcr or Tesseract for other content).')
        return words
