# coding=utf-8
"""
PytorchOCR plugin (https://github.com/WenmuZhou/PytorchOCR).
PytorchOCR is a pure-PyTorch OCR engine -- a PyTorch answer to
PaddleOCR from the author of DBNet.pytorch -- pairing a DB text
DETECTOR with a CRNN RECOGNIZER, both trainable and runnable from the
same repo. Backbones range from MobileNet (2.3 MB, mobile) through
ResNet18/50 to Swin Transformer and ConvNeXt (server side), so the
same plugin covers a tiny footprint or a heavy accurate model
depending on which checkpoint you point it at.
Because detection returns polygons in the original image, results
carry REAL PIXEL LINE BOXES (the OCR.Space/RapidOcr geometry tier),
not approximate VLM coordinates.
THE REPOSITORY'S OWN CLASSES ARE USED DIRECTLY -- no subprocess, no
server, no API calls, no temporary files. The plugin imports
``tools/det_infer.py::DetInfer`` and ``tools/rec_infer.py::RecInfer``
straight from your clone (they are scripts rather than an installed
package, so they are loaded by file path with importlib, with the
repo root put on sys.path first so their ``torchocr`` imports resolve).
Setup::
    git clone https://github.com/WenmuZhou/PytorchOCR.git
    cd PytorchOCR && pip3 install -r requirements.txt
Then download a detection and a recognition checkpoint from the
README's tables (DB detector: MobileNet 2.3 MB / ResNet18 47.2 MB /
ResNet50 97.3 MB / Swin 240 MB / ConvNeXt 113 MB; CRNN recognizer:
mobile or server) and point the plugin at them::
    from pytorchocr import PytorchOcr
    result = PytorchOcr(image='page.png',
                        repoPath='/path/to/PytorchOCR',
                        detModel='/models/db_mobilenet.pth',
                        recModel='/models/crnn_lite.pth').parse()
``repoPath`` may also come from the PYTORCH_OCR_PATH environment
variable, and the model paths from PYTORCH_OCR_DET / PYTORCH_OCR_REC.
The checkpoints carry their own config (``ckpt['cfg']``), so nothing
else needs configuring: image size, normalization, post-processing
and the alphabet all come from the file.
MODES (``mode=``):
    'auto' (default)  detect lines, then recognize each crop.
    'det'             detection only -- see also detectBoxes().
    'rec'             the image IS one text line: recognize it whole
                      (skips detection, so no detModel is needed).

Detected quadrilaterals are cropped with a perspective warp when
OpenCV is available (correct for rotated or skewed lines) and with an
axis-aligned crop otherwise. ``dropScore`` filters weak detections.
Everything lands in the exact unified structure shared by every
plugin (like OCR.Space).
"""
from os.path import isdir, isfile, join
from sys import path as syspath
from os.path import dirname
from os import environ
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_MODES = ('auto', 'det', 'rec')
#: Padding added around axis-aligned crops.
_CROP_PAD = 1


class PytorchOcr(OCRPlugin):
    """
    PytorchOcr class.
    """
    #: repo path -> (DetInfer, RecInfer) classes.
    _classes = {}
    #: model path -> loaded engine, built once per process.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param repoPath: local clone of WenmuZhou/PytorchOCR (or the PYTORCH_OCR_PATH environment variable).
        :param detModel: DB detection checkpoint (.pth), or PYTORCH_OCR_DET.
        :param recModel: CRNN recognition checkpoint (.pth), or PYTORCH_OCR_REC.
        :param mode: 'auto' (default), 'det' or 'rec'.
        :param batchSize: crops per recognition batch (default 16, the repo's own default).
        :param dropScore: ignore detections below this score (default 0.0).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        self.__m_repo_path = kwargs.pop('repoPath', None) or environ.get('PYTORCH_OCR_PATH', '') or None
        self.__m_det_model = kwargs.pop('detModel', None) or environ.get('PYTORCH_OCR_DET', '') or None
        self.__m_rec_model = kwargs.pop('recModel', None) or environ.get('PYTORCH_OCR_REC', '') or None
        mode = str(kwargs.pop('mode', 'auto')).lower()
        if mode not in _MODES:
            raise OCRError("mode must be 'auto', 'det' or 'rec', not {!r}".format(mode))
        self.__m_mode = mode
        self.__m_batch_size = int(kwargs.pop('batchSize', 16))
        self.__m_drop_score = float(kwargs.pop('dropScore', 0.0))
        super(PytorchOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)

    # ------------------------------------------------------------------ #
    # The repository's own inference classes                             #
    # ------------------------------------------------------------------ #
    def _repoClasses(self):
        """
        (DetInfer, RecInfer) loaded from the clone.
        """
        key = self.__m_repo_path or ''
        if key in PytorchOcr._classes:
            return PytorchOcr._classes[key]
        if not self.__m_repo_path:
            raise OCRError(
                'PytorchOCR needs its repository: git clone '
                'https://github.com/WenmuZhou/PytorchOCR.git, then '
                'pass repoPath=/path/to/PytorchOCR (or set PYTORCH_OCR_PATH).')
        root = self.__m_repo_path
        if not isdir(join(root, 'torchocr')):
            raise OCRError(
                "No 'torchocr' package under '{}'. repoPath should be "
                'the root of the PytorchOCR clone.'.format(root))
        if root not in syspath:  # so the scripts' torchocr imports work
            syspath.insert(0, root)
        classes = []
        for name, attribute in (('det_infer', 'DetInfer'), ('rec_infer', 'RecInfer')):
            path = join(root, 'tools', name + '.py')
            if not isfile(path):
                raise OCRError("Missing '{}' in the PytorchOCR clone (expected {}).".format(name + '.py', path))
            try:
                from importlib.util import module_from_spec, spec_from_file_location
                spec = spec_from_file_location('pytorchocr_' + name, path)
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                classes.append(getattr(module, attribute))
            except OCRError:
                raise
            except ImportError as exc:
                raise OCRError(
                    'PytorchOCR could not be imported ({}). Install its requirements: cd {} && pip3 install -r '
                    'requirements.txt'.format(str(exc)[:150], root))
            except Exception as exc:  # noqa: BLE001 - script errors
                raise OCRError('Loading {} failed: {}: {}'.format(attribute, type(exc).__name__, str(exc)[:180]))
        PytorchOcr._classes[key] = tuple(classes)
        return PytorchOcr._classes[key]

    def _detector(self):
        if not self.__m_det_model:
            raise OCRError(
                'No detection checkpoint: pass detModel=/path/to/'
                'db_model.pth (or set PYTORCH_OCR_DET). The '
                "README's table links DB checkpoints from MobileNet "
                "(2.3 MB) to ResNet50 -- or use mode='rec' if the "
                'image is already a single text line.')
        if not isfile(self.__m_det_model):
            raise OCRError("Detection checkpoint not found: '{}'.".format(self.__m_det_model))
        key = ('det', self.__m_det_model)
        if key not in PytorchOcr._engines:
            DetInfer, _ = self._repoClasses()
            PytorchOcr._engines[key] = self._build(DetInfer, self.__m_det_model)
        return PytorchOcr._engines[key]

    def _recognizer(self):
        if not self.__m_rec_model:
            raise OCRError(
                'No recognition checkpoint: pass recModel=/path/to/'
                'crnn_model.pth (or set PYTORCH_OCR_REC), or use '
                "mode='det' for boxes only.")
        if not isfile(self.__m_rec_model):
            raise OCRError("Recognition checkpoint not found: '{}'.".format(self.__m_rec_model))
        key = ('rec', self.__m_rec_model, self.__m_batch_size)
        if key not in PytorchOcr._engines:
            _, RecInfer = self._repoClasses()
            PytorchOcr._engines[key] = self._build(RecInfer, self.__m_rec_model, batch_size=self.__m_batch_size)
        return PytorchOcr._engines[key]

    @staticmethod
    def _build(factory, model_path, **options):
        try:
            return factory(model_path, **options)
        except TypeError:  # older signature without the extra options
            return factory(model_path)
        except Exception as exc:  # noqa: BLE001 - checkpoint problems
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'PytorchOCR ran out of GPU memory loading {}: try '
                    'a lighter checkpoint (the MobileNet models are '
                    '2.3 MB) or run on CPU. Original: {}'.format(model_path, message[:160]))
            raise OCRError(
                "Could not load the PytorchOCR checkpoint '{}' ({}): "
                '{}. The .pth must be a PytorchOCR training '
                "checkpoint carrying its own ckpt['cfg'].".format(model_path, type(exc).__name__, message[:160]))

    # ------------------------------------------------------------------ #
    # Input handling (in memory, no temporary files)                     #
    # ------------------------------------------------------------------ #
    def _bgrImage(self):
        """
        BGR numpy array, as the repo's scripts feed from cv2.
        """
        from numpy import asarray, ascontiguousarray
        from PIL.Image import fromarray, open
        from io import BytesIO
        kind = self.imageKind()
        if kind == 'array':
            array = asarray(self.getImage())
            if array.ndim == 3 and array.shape[2] >= 3:
                return ascontiguousarray(array[:, :, ::-1])
            image = fromarray(array).convert('RGB')
            return ascontiguousarray(asarray(image)[:, :, ::-1])
        if kind == 'pil':
            image = self.getImage()
            image.load()
        else:
            if kind == 'url':
                from requests import get
                reply = get(self.getImage(), timeout=self.getTimeout())
                reply.raise_for_status()
                data = reply.content
            elif kind in ('path', 'bytes', 'buffer'):
                data = self.imageBytes()
            else:
                raise OCRError(
                    "Image source '{}' is not an existing file, URL, "
                    'or supported type. Check the path (the current '
                    'working directory matters for relative paths).'.format(self.getImage()))
            if data[:5] == b'%PDF-':
                raise OCRError(
                    'PytorchOCR reads images, not PDFs. Rasterize the '
                    'pages first, or use a PDF-capable plugin (QianfanOcr, MistralOcr, NougatLib).')
            image = open(BytesIO(data))
            image.load()
        image = image.convert('RGB')
        return ascontiguousarray(asarray(image)[:, :, ::-1])

    # ------------------------------------------------------------------ #
    # Detection helpers                                                  #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rect(box):
        """
        (left, top, right, bottom) around a polygon.
        """
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        return min(xs), min(ys), max(xs), max(ys)

    def _detect(self, image):
        """
        [(quad, score)] sorted in reading order.
        """
        detector = self._detector()
        try:
            boxes, scores = detector.predict(image)
        except Exception as exc:  # noqa: BLE001 - torch/CUDA
            raise self._runtimeError('detection', exc)
        pairs = []
        for index, box in enumerate(boxes if boxes is not None else []):
            score = 1.0
            if scores is not None and index < len(scores):
                try:
                    score = float(scores[index])
                except (TypeError, ValueError):
                    score = 1.0
            if score < self.__m_drop_score:
                continue
            points = box.tolist() if hasattr(box, 'tolist') else box
            if len(points) < 3:
                continue
            pairs.append((points, score))
        return sorted(pairs, key=lambda pair: (
            self._rect(pair[0])[1], self._rect(pair[0])[0]))

    def _crop(self, image, quad):
        """
        Perspective-warped crop (cv2) or an axis-aligned one.
        """
        from numpy import array, linalg
        height, width = image.shape[:2]
        left, top, right, bottom = self._rect(quad)
        try:
            from cv2 import getPerspectiveTransform, warpPerspective, BORDER_REPLICATE
            cv2 = 1
        except ImportError:
            cv2 = 0
        if cv2 and len(quad) == 4:
            source = array(quad, dtype='float32')
            boxW = int(max(linalg.norm(source[0] - source[1]), linalg.norm(source[2] - source[3])))
            boxH = int(max(linalg.norm(source[0] - source[3]), linalg.norm(source[1] - source[2])))
            if boxW > 1 and boxH > 1:
                target = array([[0, 0], [boxW, 0], [boxW, boxH], [0, boxH]], dtype='float32')
                matrix = getPerspectiveTransform(source, target)
                return warpPerspective(image, matrix, (boxW, boxH), borderMode=BORDER_REPLICATE)
        x1 = max(0, int(left) - _CROP_PAD)
        y1 = max(0, int(top) - _CROP_PAD)
        x2 = min(width, int(right) + _CROP_PAD)
        y2 = min(height, int(bottom) + _CROP_PAD)
        if x2 <= x1 or y2 <= y1:
            return None
        return image[y1:y2, x1:x2]

    @staticmethod
    def _text(entry):
        """
        RecInfer returns nested (text, score) structures.
        """
        while isinstance(entry, (list, tuple)) and entry:
            if len(entry) == 2 and isinstance(entry[0], str) and not isinstance(entry[1], (list, tuple, str)):
                return str(entry[0])
            entry = entry[0]
        return str(entry) if isinstance(entry, str) else ''

    def _recognize(self, crops):
        recognizer = self._recognizer()
        try:
            results = recognizer.predict(crops)
        except Exception as exc:  # noqa: BLE001 - torch/CUDA
            raise self._runtimeError('recognition', exc)
        return [self._text(entry) for entry in (results or [])]

    @staticmethod
    def _runtimeError(stage, exc):
        message = str(exc)
        if 'CUDA' in message or 'out of memory' in message.lower():
            return OCRError(
                'PytorchOCR ran out of GPU memory during {}: lower '
                'batchSize or use a lighter checkpoint. Original: {}'.format(stage, message[:180]))
        return OCRError('PytorchOCR {} failed: {}: {}'.format(stage, type(exc).__name__, message[:200]))

    # ------------------------------------------------------------------ #
    # Detection-only helper                                              #
    # ------------------------------------------------------------------ #
    def detectBoxes(self):
        """
        Run the DB detector only (no recognition).
        :return: list of dicts with Left/Top/Width/Height/Score/Points in image pixels.
        """
        image = self._bgrImage()
        boxes = []
        for quad, score in self._detect(image):
            left, top, right, bottom = self._rect(quad)
            boxes.append({'Left': left, 'Top': top, 'Width': right - left, 'Height': bottom - top, 'Score': score,
                          'Points': quad})
        return boxes

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble (REAL pixel line boxes from the DB detector,
                    with CRNN transcriptions).
        """
        page = self._bgrImage()
        height, width = page.shape[:2]
        if self.__m_mode == 'rec':
            texts = self._recognize([page])
            text = ' '.join((texts[0] if texts else '').split())
            if not text:
                raise OCRError('PytorchOCR read no text from this line image.')
            return [self.makeWord(text, 0.0, 0.0, float(width), float(height))]
        detected = self._detect(page)
        if not detected:
            raise OCRError(
                'The PytorchOCR detector found no text in this image '
                "(try another DB checkpoint, or mode='rec' if the image is a single cropped line).")
        if self.__m_mode == 'det':
            words = []
            for quad, _ in detected:
                left, top, right, bottom = self._rect(quad)
                words.append(self.makeWord('', left, top, max(right - left, 1.0), max(bottom - top, 1.0)))
            raise OCRError(
                "mode='det' returns geometry only: call detectBoxes() "
                'for the {} boxes found, or use the default '
                "mode='auto' to recognize them.".format(len(words)))
        crops, quads = [], []
        for quad, _ in detected:
            crop = self._crop(page, quad)
            if crop is None or not getattr(crop, 'size', 1):
                continue
            crops.append(crop)
            quads.append(quad)
        if not crops:
            raise OCRError('PytorchOCR could not crop any detected line from this image.')
        texts = self._recognize(crops)
        words = []
        for index, quad in enumerate(quads):
            text = texts[index] if index < len(texts) else ''
            text = ' '.join((text or '').split())
            if not text:
                continue
            left, top, right, bottom = self._rect(quad)
            words.append(self.makeWord(text, left, top, max(right - left, 1.0), max(bottom - top, 1.0)))
        if not words:
            raise OCRError(
                'PytorchOCR detected {} line(s) but recognized no '
                'text: check that the recognition checkpoint matches '
                "the image's language (its alphabet comes from the checkpoint).".format(len(quads)))
        return words
