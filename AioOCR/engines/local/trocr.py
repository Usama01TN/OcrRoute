# coding=utf-8
"""
TrOCR plugin (local, https://huggingface.co/microsoft/trocr-base-printed).
TrOCR is Microsoft's transformer OCR (ViT encoder + text decoder) --
the classic that introduced end-to-end transformer text recognition.
The crucial fact, per the model card: TrOCR reads a SINGLE TEXT LINE
at a time (it was trained on printed line images such as SROIE
receipts). Its official usage is: one cropped line in, one string out.
This plugin therefore does the correct thing for whole pages: it runs
a local OpenCV text-line DETECTOR first (adaptive contrast handling
for both dark-on-light and light-on-dark text), crops every detected
line, and batches the crops through TrOCR for recognition. That
combo yields REAL PIXEL LINE BOXES from the detector plus TrOCR's
transcription -- line-level geometry (the RapidOCR/Tesseract tier),
fully local and free.
Install::
    pip install transformers torch pillow
    pip install opencv-python-headless numpy   # page-mode detection
Checkpoints (auto-downloaded on first use; pass ``model=``):
    microsoft/trocr-base-printed        default (printed text)
    microsoft/trocr-large-printed       better, slower
    microsoft/trocr-small-printed       fastest
    microsoft/trocr-base-handwritten    HANDWRITING (IAM-trained)
    microsoft/trocr-large-handwritten   best handwriting
Modes (``mode=``):
    'auto'   (default) images up to 128 px tall are treated as one
             text line (TrOCR's native use); taller images get line
             detection + per-line recognition.
    'line'   the image IS one text line -- single TrOCR pass, per the official snippet.
    'page'   force detection + recognition.
You can also pass your own line boxes and skip detection:
``boxes=[[x, y, w, h], ...]`` in image pixels.
Honest limits: TrOCR is English-centric (printed/handwritten English;
no CJK/Arabic checkpoints from Microsoft), reads one language at a
time, and the CV detector is heuristic -- for hard layouts prefer
RapidOcr (built-in detector) or the document VLMs. PDFs are not
supported here (line recognition on rasterized scans is slow);
use the document engines for PDFs. Everything lands in the exact
unified structure shared by every plugin (like OCR.Space).
"""
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

_DEFAULT_MODEL = 'microsoft/trocr-base-printed'
#: 'auto' treats images at most this tall as a single text line.
_LINE_HEIGHT_LIMIT = 128
#: Minimum detected box size (pixels) worth recognizing.
_MIN_BOX_W, _MIN_BOX_H = 10, 8
#: Padding added around detected line crops.
_CROP_PAD = 2


class TrOcr(OCRPlugin):
    """
    TrOcr class.
    """
    #: model name -> (processor, model, device), loaded once.
    _engines = {}
    #: shared RapidOCR detection engine (detection-only calls).
    _rapid_detector = None

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'microsoft/trocr-base-printed'; handwritten and small/large variants listed
                        in the module doc).
        :param mode: 'auto' (default), 'line', or 'page'.
        :param boxes: optional [[x, y, w, h], ...] line boxes in image pixels -- skips detection.
        :param detector: 'auto' (default: RapidOCR's PP-OCR text detector when installed, else the OpenCV heuristic),
                            'rapidocr', or 'cv'.
        :param batchSize: line crops per TrOCR batch (default 8).
        :param maxTokens: generation budget per line (default 64).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        mode = str(kwargs.pop('mode', 'auto')).lower()
        if mode not in ('auto', 'line', 'page'):
            raise OCRError("mode must be 'auto', 'line' or 'page', not {!r}".format(mode))
        self.__m_mode = mode
        self.__m_boxes = kwargs.pop('boxes', None)
        detector = str(kwargs.pop('detector', 'auto')).lower()
        if detector not in ('auto', 'rapidocr', 'cv'):
            raise OCRError("detector must be 'auto', 'rapidocr' or 'cv', not {!r}".format(detector))
        self._detector_choice = detector
        self.__m_batchSize = max(1, int(kwargs.pop('batchSize', 8)))
        self.__m_maxTokens = int(kwargs.pop('maxTokens', 64))
        self.__m_device = kwargs.pop('device', None)
        super(TrOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine                                                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        name = self.getModel()
        if name in TrOcr._engines:
            return TrOcr._engines[name]
        try:
            from torch.cuda import is_available
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except ImportError:
            raise OCRError('TrOCR needs transformers and torch. Run: pip install transformers torch pillow')
        device = self.__m_device or ('cuda' if is_available() else 'cpu')
        processor = TrOCRProcessor.from_pretrained(name)
        model = VisionEncoderDecoderModel.from_pretrained(name)
        model = model.to(device)
        model.eval()
        TrOcr._engines[name] = (processor, model, device)
        return TrOcr._engines[name]

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _pageImage(self):
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'pil':
            return self.getImage().convert('RGB')
        if kind == 'array':
            from numpy import asarray
            return Image.fromarray(asarray(self.getImage())).convert('RGB')
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
            raise OCRError(
                'TrOCR reads text lines, not PDFs. Rasterize pages '
                'first, or use a document engine (UnlimitedOcr, '
                'QianfanOcr, ChandraOcr, MistralOcr...).')
        return Image.open(BytesIO(data)).convert('RGB')

    # ------------------------------------------------------------------ #
    # Line detection                                                     #
    # ------------------------------------------------------------------ #
    def _detectLines(self, image):
        """
        Text-line boxes [(x, y, w, h)] in reading order.
        """
        detector = self._detector_choice
        if detector == 'auto':
            try:
                import rapidocr  # noqa: F401 - presence check
                detector = 'rapidocr'
            except ImportError:
                try:
                    import rapidocr_onnxruntime  # noqa: F401
                    detector = 'rapidocr'
                except ImportError:
                    detector = 'cv'
        return self._detectLinesRapid(image) if detector == 'rapidocr' else self._detectLinesCv(image)

    @staticmethod
    def _detectLinesRapid(image):
        """
        PP-OCR DB text detector via RapidOCR (detection only).
        """
        from numpy import asarray
        if TrOcr._rapid_detector is None:
            try:
                from rapidocr import RapidOCR
            except ImportError:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError:
                    raise OCRError(
                        "detector='rapidocr' needs RapidOCR. Run: pip "
                        'install rapidocr onnxruntime (or use '
                        "detector='cv' / boxes=).")
            TrOcr._rapid_detector = RapidOCR()
        result = TrOcr._rapid_detector(asarray(image), use_det=True, use_cls=False, use_rec=False)
        quads = getattr(result, 'boxes', None)
        if quads is None and isinstance(result, tuple):
            entries = result[0] or []
            quads = [entry[0] if isinstance(entry, (list, tuple)) else entry for entry in entries]
        boxes = []
        for quad in list(quads) if quads is not None else []:
            points = quad.tolist() if hasattr(quad, 'tolist') else quad
            try:
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
            except (TypeError, ValueError, IndexError):
                continue
            x, y = min(xs), min(ys)
            w, h = max(xs) - x, max(ys) - y
            if w >= _MIN_BOX_W and h >= _MIN_BOX_H:
                boxes.append((x, y, w, h))
        return sorted(boxes, key=lambda b: (b[1] + b[3] / 2.0, b[0]))

    @staticmethod
    def _mergeBoxes(boxes):
        """
        Merge overlapping/duplicate rects across polarities.
        """
        merged = []
        for box in sorted(boxes, key=lambda b: (b[1], b[0])):
            x, y, w, h = box
            absorbed = False
            for index, (mx, my, mw, mh) in enumerate(merged):
                ix = max(0, min(x + w, mx + mw) - max(x, mx))
                iy = max(0, min(y + h, my + mh) - max(y, my))
                overlap = ix * iy
                if overlap > 0.5 * min(w * h, mw * mh):
                    nx, ny = min(x, mx), min(y, my)
                    merged[index] = (nx, ny, max(x + w, mx + mw) - nx, max(y + h, my + mh) - ny)
                    absorbed = True
                    break
            if not absorbed:
                merged.append((x, y, w, h))
        return merged

    def _detectLinesCv(self, image):
        """
        Heuristic OpenCV fallback (both text polarities).
        """
        try:
            from cv2 import cvtColor, boundingRect, getStructuringElement, dilate, threshold, findContours, \
                COLOR_RGB2GRAY, MORPH_RECT, THRESH_BINARY_INV, THRESH_BINARY, THRESH_OTSU, RETR_EXTERNAL, \
                CHAIN_APPROX_SIMPLE
            from numpy import asarray
        except ImportError:
            raise OCRError(
                'CV line detection needs OpenCV. Run: pip install opencv-python-headless numpy -- or better, '
                'pip install rapidocr onnxruntime for the real text '
                "detector (or pass boxes=, or use mode='line').")
        gray = cvtColor(asarray(image), COLOR_RGB2GRAY)
        height, width = gray.shape[:2]
        kernel = getStructuringElement(MORPH_RECT, (15, 3))
        boxes = []
        for invert in (False, True):
            _, binary = threshold(gray, 0, 255, (THRESH_BINARY_INV if not invert else THRESH_BINARY) + THRESH_OTSU)
            dilated = dilate(binary, kernel, iterations=1)
            contours, _ = findContours(dilated, RETR_EXTERNAL, CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = boundingRect(contour)
                if w < _MIN_BOX_W or h < _MIN_BOX_H:
                    continue
                if w > width * 0.98 and h > height * 0.98:
                    continue
                boxes.extend(self._refineBox(binary, x, y, w, h))
        merged = self._mergeBoxes(
            [b for b in boxes if b[2] >= _MIN_BOX_W and b[3] >= _MIN_BOX_H and \
             b[2] >= b[3] * 0.8 and b[3] <= height * 0.3])
        return sorted(merged, key=lambda b: (b[1] + b[3] / 2.0, b[0]))

    @staticmethod
    def _refineBox(binary, x, y, w, h):
        """
        Split a blob into ink-row bands, then split each band at
        wide horizontal gaps and trim each segment to its ink rows.
        """
        region = binary[y:y + h, x:x + w]
        row_ink = (region > 0).sum(axis=1)
        rows = []
        start = None
        for index in range(h + 1):
            filled = index < h and row_ink[index] > 0
            if filled and start is None:
                start = index
            elif not filled and start is not None:
                if index - start >= _MIN_BOX_H:
                    rows.append((start, index))
                start = None
        if start is not None and h - start >= _MIN_BOX_H:
            rows.append((start, h))
        refined = []
        for top, bottom in rows or [(0, h)]:
            band = region[top:bottom]
            colInk = (band > 0).sum(axis=0)
            gapLimit = max(10, (bottom - top) * 2 // 3)
            segStart, gap = None, 0
            segments = []
            for col in range(w + 1):
                filled = col < w and colInk[col] > 0
                if filled:
                    if segStart is None:
                        segStart = col
                    gap = 0
                elif segStart is not None:
                    gap += 1
                    if gap >= gapLimit or col == w:
                        segments.append((segStart, col - gap + 1))
                        segStart, gap = None, 0
            if segStart is not None:
                segments.append((segStart, w))
            for left, right in segments:
                sub = band[:, left:right]
                subRows = (sub > 0).sum(axis=1)
                filledRows = [i for i, v in enumerate(subRows) if v > 0]
                if not filledRows:
                    continue
                sub_top, sub_bottom = (filledRows[0], filledRows[-1] + 1)
                refined.append((x + left, y + top + sub_top, right - left, sub_bottom - sub_top))
        return refined

    # ------------------------------------------------------------------ #
    # Recognition                                                        #
    # ------------------------------------------------------------------ #
    def _recognize(self, crops):
        """
        Batch line crops -> list of strings (official recipe).
        """
        from torch import inference_mode
        processor, model, device = self._engine()
        texts = []
        for start in range(0, len(crops), self.__m_batchSize):
            chunk = crops[start:start + self.__m_batchSize]
            pixel_values = processor(images=chunk, return_tensors='pt').pixel_values
            with inference_mode():
                generated = model.generate(pixel_values.to(device), max_new_tokens=self.__m_maxTokens)
            texts.extend(processor.batch_decode(generated, skip_special_tokens=True))
        return texts

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble (REAL detector pixel line boxes in page mode;
                    the image rectangle in line mode).
        """
        page = self._pageImage()
        width, height = page.size
        mode = self.__m_mode
        if mode == 'auto':
            mode = 'line' if height <= _LINE_HEIGHT_LIMIT else 'page'
        if self.__m_boxes:
            boxes = [tuple(float(v) for v in box) for box in self.__m_boxes]
        elif mode == 'page':
            boxes = self._detectLines(page)
            if not boxes:
                boxes = [(0, 0, width, height)]  # last resort: one line
        else:
            boxes = [(0, 0, width, height)]
        crops = []
        for x, y, w, h in boxes:
            left = max(0, int(x) - _CROP_PAD)
            top = max(0, int(y) - _CROP_PAD)
            right = min(width, int(x + w) + _CROP_PAD)
            bottom = min(height, int(y + h) + _CROP_PAD)
            crops.append(page.crop((left, top, right, bottom)))
        try:
            texts = self._recognize(crops)
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA failures
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'TrOCR ran out of GPU memory: lower batchSize, or '
                    "use model='microsoft/trocr-small-printed', or "
                    "device='cpu'. Original: " + message[:200])
            raise OCRError('TrOCR inference failed: {}: {}'.format(type(exc).__name__, message[:250]))
        words = []
        for (x, y, w, h), text in zip(boxes, texts):
            text = ' '.join((text or '').split())
            if not text:
                continue
            words.append(self.makeWord(text, float(x), float(y), float(w), float(h)))
        if not words:
            raise OCRError(
                'TrOCR read no text. For full pages make sure OpenCV '
                "detection found lines (or pass boxes=); for a single "
                "text line use mode='line'; for handwriting use "
                "model='microsoft/trocr-base-handwritten'.")
        return words
