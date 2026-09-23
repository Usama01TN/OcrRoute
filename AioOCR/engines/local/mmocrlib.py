# coding=utf-8
"""
MMOCR plugin — OpenMMLab's MMOCR toolbox as an ``OCRPlugin`` engine.
    https://github.com/open-mmlab/mmocr
The library is imported directly (``from mmocr.apis import ...``); there is
no server, no HTTP call, no subprocess and no temporary file anywhere in
this module. Images are decoded in memory into BGR numpy arrays, which is
exactly what MMOCR's inferencers accept.
Both generations of the toolbox are supported transparently:
* **MMOCR 1.x** (``mmocr.apis.MMOCRInferencer`` / ``TextDetInferencer`` /
  ``TextRecInferencer`` / ``TextSpotInferencer`` / ``KIEInferencer``).
* **MMOCR 0.x** (``mmocr.utils.ocr.MMOCR().readtext(...)``).
MMOCR detects at *region* (usually text-line) level, so the plugin turns
each detection into either one entry per detection (``wordBox=False``) or
proportionally-split word boxes inside the detected region
(``wordBox=True``, the default). Either way the base class regroups the
entries into lines and returns the usual unified result.
Install (CPU example)::
    pip install -U openmim
    mim install mmengine "mmcv>=2.0.0" "mmdet>=3.0.0"
    mim install "mmocr>=1.0.0"
Usage::
    from mmocrlib import MmOcr
    result = MmOcr(image='demo.jpg').parse()                     # det + rec
    result = MmOcr(image='demo.jpg', det='DBNetpp', rec='ABINet').parse()                         # other models
    result = MmOcr(image='line.png', mode='rec').parse()         # cropped line
    result = MmOcr(image='demo.jpg', mode='det').parse()         # boxes only
    result = MmOcr(image='receipt.png', kie='SDMGR').parse()     # + KIE labels
    result = MmOcr(image='doc.pdf', device='cuda:0').parse()     # multipage
    plugin = MmOcr(image='demo.jpg', minConfidence=0.5)
    result = plugin.parse()
    boxes = plugin.getPolygons()          # real detector polygons
    raw = plugin.getPredictions()         # MMOCR's own prediction dicts
"""
from numpy import asarray, ascontiguousarray, stack, uint8, clip, ndarray
from os.path import isfile, dirname
from io import BytesIO
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError, is_url
except:
    from engines.ocrplugin import OCRPlugin, OCRError, is_url
#: Model names understood by MMOCR 1.x metafiles, for reference/validation
#: of the friendliest aliases. Any other metafile name, alias, config path
#: or ``.py`` config file works too; this list is documentation, not a
#: whitelist.
DET_MODELS = (
    'DB_r18', 'DB_r50', 'DBNet', 'DBPP_r50', 'DBNetpp', 'DRRG',
    'FCE_IC15', 'FCE_CTW_DCNv2', 'FCENet', 'MaskRCNN_CTW', 'MaskRCNN_IC15',
    'MaskRCNN', 'PANet_CTW', 'PANet_IC15', 'PS_CTW', 'PS_IC15', 'PSENet', 'TextSnake')
REC_MODELS = (
    'CRNN', 'SAR', 'SAR_CN', 'NRTR_1/16-1/8', 'RobustScanner', 'SATRN',
    'SATRN_sm', 'ABINet', 'ABINet_Vision', 'ASTER', 'MASTER', 'svtr-small', 'svtr-base')
KIE_MODELS = ('SDMGR',)


class MmOcr(OCRPlugin):
    """
    MMOCR engine (OpenMMLab).

    Everything is local: models are downloaded once by MMOCR itself into
    its cache directory, then loaded into memory. Inferencers are cached
    process-wide, so building several ``MmOcr`` instances with the same
    models re-uses the loaded weights.
    """
    #: Default text detector (ResNet-18 DBNet, light and reliable).
    #: ``'DBNet'``/``'DBNetpp'`` alias the oCLIP variants, which pull an
    #: extra backbone checkpoint on first use.
    DEFAULT_DET = 'DB_r18'
    #: Default text recognizer.
    DEFAULT_REC = 'SAR'
    #: Vertical gap inserted between stacked PDF pages, in pixels.
    PAGE_GAP = 20.0
    #: Rendering scale used for PDF pages (72 dpi * scale).
    PDF_SCALE = 2.0
    #: Process-wide inferencer cache: {key: inferencer}.
    _INFERENCERS = {}

    def __init__(self, *args, **kwargs):
        """
        :param image: path | URL | base64/bytes | BytesIO | PIL image |
                      numpy array | PDF (path or bytes)
        :param mode: 'auto' | 'ocr' | 'det' | 'rec' | 'spot' | 'kie'
        :param det: detector name, config path or None to disable
        :param detWeights: checkpoint overriding the metafile weights
        :param rec: recognizer name, config path or None to disable
        :param recWeights: checkpoint overriding the metafile weights
        :param kie: key-information-extraction model (e.g. 'SDMGR')
        :param kieWeights: checkpoint overriding the metafile weights
        :param spot: end-to-end text-spotting config (e.g. an ABCNet
                     config from MMOCR's ``projects/``)
        :param spotWeights: checkpoint for the spotting model
        :param device: 'cpu', 'cuda:0', ... (None = auto)
        :param boxes: optional [[x1,y1,x2,y2], ...] or polygons to
                      recognize instead of running the detector
        :param wordBox: split each detected region into word boxes
        :param minConfidence: drop words whose recognition score is lower
        :param detConfidence: drop detections whose score is lower
        :param batchSize: inference batch size
        :param detBatchSize: overrides batchSize for detection
        :param recBatchSize: overrides batchSize for recognition
        :param arrayOrder: 'bgr' (MMOCR's convention) or 'rgb' for the
                           channel order of numpy arrays you pass in
        :param pdfScale: PDF rasterization scale (2.0 = 144 dpi)
        :param pages: optional list of 0-based PDF page indexes
        :param inferencerParams: extra kwargs for the inferencer ctor
        :param callParams: extra kwargs for the inferencer call
        :param cache: keep loaded inferencers in the process cache
        """
        self.__m_mode = str(kwargs.pop('mode', 'auto')).lower()
        self.__m_det = kwargs.pop('det', self.DEFAULT_DET)
        self.__m_detWeights = kwargs.pop('detWeights', None)
        self.__m_rec = kwargs.pop('rec', self.DEFAULT_REC)
        self.__m_recWeights = kwargs.pop('recWeights', None)
        self.__m_kie = kwargs.pop('kie', None)
        self.__m_kieWeights = kwargs.pop('kieWeights', None)
        self.__m_spot = kwargs.pop('spot', None)
        self.__m_spotWeights = kwargs.pop('spotWeights', None)
        self.__m_device = kwargs.pop('device', None)
        self.__m_boxes = kwargs.pop('boxes', None)
        self.__m_wordBox = bool(kwargs.pop('wordBox', True))
        self.__m_minConfidence = float(kwargs.pop('minConfidence', 0.0))
        self.__m_detConfidence = float(kwargs.pop('detConfidence', 0.0))
        self.__m_batchSize = int(kwargs.pop('batchSize', 1))
        self.__m_detBatchSize = kwargs.pop('detBatchSize', None)
        self.__m_recBatchSize = kwargs.pop('recBatchSize', None)
        self.__m_arrayOrder = str(kwargs.pop('arrayOrder', 'bgr')).lower()
        self.__m_pdfScale = float(kwargs.pop('pdfScale', self.PDF_SCALE))
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_inferencerParams = dict(kwargs.pop('inferencerParams', {}))
        self.__m_callParams = dict(kwargs.pop('callParams', {}))
        self.__m_cache = bool(kwargs.pop('cache', True))
        self.__m_predictions = []
        self.__m_polygons = []
        self.__m_kieLabels = []
        self.__m_pageSizes = []
        super(MmOcr, self).__init__(*args, **kwargs)
        self.setModel(self._describeModel())

    # ------------------------------------------------------------------ #
    # Engine entry point                                                 #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        Run MMOCR on the current image and return flat word dicts.
        :param image: whatever ``getImage()`` holds.
        :return: list[dict]
        """
        pages = self._loadPages(image)
        if not pages:
            raise OCRError('No page could be decoded from the given image.')
        self.__m_predictions = []
        self.__m_polygons = []
        self.__m_kieLabels = []
        self.__m_pageSizes = [(int(p.shape[1]), int(p.shape[0])) for p in pages]
        words = []
        offset = 0.0
        for page in pages:
            prediction = self._inferPage(page)
            self.__m_predictions.append(prediction)
            words.extend(self._wordsFromPrediction(prediction, page, offset))
            offset += float(page.shape[0]) + self.PAGE_GAP
        return words

    # ------------------------------------------------------------------ #
    # Inference                                                          #
    # ------------------------------------------------------------------ #
    def resolveMode(self):
        """
        Resolve ``'auto'`` into a concrete mode.
        :return: 'ocr' | 'det' | 'rec' | 'spot' | 'kie'
        """
        mode = self.__m_mode
        if mode != 'auto':
            if mode not in ('ocr', 'det', 'rec', 'spot', 'kie'):
                raise OCRError('Unknown mode: {!r}.'.format(mode))
            return mode
        if self.__m_spot:
            return 'spot'
        if self.__m_kie:
            return 'kie'
        if self.__m_det and self.__m_rec:
            return 'ocr'
        if self.__m_rec:
            return 'rec'
        if self.__m_det:
            return 'det'
        raise OCRError('At least one of det, rec, kie or spot is required.')

    def _inferPage(self, page):
        """
        Run the right inferencer on a single BGR page array.
        :param page: numpy array (H, W, 3) in BGR order.
        :return: dict, MMOCR's prediction for that page (normalized to
                 the 1.x ``det_polygons``/``rec_texts`` shape).
        """
        if self.isLegacy():
            return self._inferLegacy(page)
        mode = self.resolveMode()
        boxes = self._normalizedBoxes()
        if boxes is not None and mode in ('ocr', 'kie', 'spot'):
            # Explicit regions were given: skip detection entirely.
            return self._inferBoxes(page, boxes)
        if mode == 'det':
            return self._inferDet(page)
        if mode == 'rec':
            return self._inferRec(page)
        if mode == 'spot':
            return self._inferSpot(page)
        return self._inferOcr(page, kie=(mode == 'kie'))

    def _inferOcr(self, page, kie=False):
        """
        Detection + recognition (+ KIE) through ``MMOCRInferencer``.
        """
        from mmocr.apis import MMOCRInferencer
        params = {
            'det': self.__m_det,
            'det_weights': self.__m_detWeights,
            'rec': self.__m_rec,
            'rec_weights': self.__m_recWeights,
            'device': self.__m_device,
        }
        if kie:
            if not self.__m_kie:
                raise OCRError("mode='kie' requires a kie model, e.g. 'SDMGR'.")
            params['kie'] = self.__m_kie
            params['kie_weights'] = self.__m_kieWeights
        params.update(self.__m_inferencerParams)
        inferencer = self._inferencer('mmocr', MMOCRInferencer, params)
        call = {
            'batch_size': self.__m_batchSize,
            'det_batch_size': self.__m_detBatchSize,
            'rec_batch_size': self.__m_recBatchSize,
            # Empty out_dir + save_* False: nothing is ever written to disk.
            'out_dir': '',
            'return_vis': False,
            'save_vis': False,
            'save_pred': False,
            'print_result': False,
        }
        call.update(self.__m_callParams)
        output = inferencer(page, **call)
        return self._firstPrediction(output)

    def _inferDet(self, page):
        """
        Detection only, through ``TextDetInferencer``.
        """
        from mmocr.apis import TextDetInferencer
        if not self.__m_det:
            raise OCRError("mode='det' requires a detection model.")
        params = {'model': self.__m_det, 'weights': self.__m_detWeights, 'device': self.__m_device}
        params.update(self.__m_inferencerParams)
        inferencer = self._inferencer('det', TextDetInferencer, params)
        output = inferencer(page, **self._standardCall(self.detBatchSize()))
        prediction = self._firstPrediction(output)
        return {
            'det_polygons': prediction.get('polygons', []),
            'det_bboxes': prediction.get('bboxes', []),
            'det_scores': prediction.get('scores', []),
        }

    def _inferRec(self, page):
        """
        Recognition only: the page *is* one cropped text line.
        """
        prediction = self._recognizeCrops([page])
        height, width = float(page.shape[0]), float(page.shape[1])
        return {
            'det_polygons': [[0.0, 0.0, width, 0.0, width, height, 0.0, height]],
            'det_scores': [1.0],
            'rec_texts': prediction['rec_texts'],
            'rec_scores': prediction['rec_scores'],
        }

    def _inferSpot(self, page):
        """
        End-to-end spotting through ``TextSpotInferencer``.
        """
        from mmocr.apis import TextSpotInferencer
        if not self.__m_spot:
            raise OCRError(
                "mode='spot' requires spot= (a text-spotting config, e.g. "
                "an ABCNet config from MMOCR's projects/ directory).")
        params = {'model': self.__m_spot, 'weights': self.__m_spotWeights, 'device': self.__m_device}
        params.update(self.__m_inferencerParams)
        inferencer = self._inferencer('spot', TextSpotInferencer, params)
        output = inferencer(page, **self._standardCall(self.__m_batchSize))
        prediction = self._firstPrediction(output)
        return {
            'det_polygons': prediction.get('polygons', []),
            'det_bboxes': prediction.get('bboxes', []),
            'det_scores': prediction.get('scores', []),
            'rec_texts': list(prediction.get('texts', [])),
            'rec_scores': list(prediction.get('scores', [])),
        }

    def _inferBoxes(self, page, boxes):
        """
        Recognize caller-supplied regions, no detector involved.
        """
        crops = [self._crop(page, box) for box in boxes]
        prediction = self._recognizeCrops(crops)
        polygons = [self._boxToPolygon(box) for box in boxes]
        return {
            'det_polygons': polygons,
            'det_scores': [1.0] * len(polygons),
            'rec_texts': prediction['rec_texts'],
            'rec_scores': prediction['rec_scores'],
        }

    def _recognizeCrops(self, crops):
        """
        Run ``TextRecInferencer`` on a list of cropped BGR arrays.

        :return: dict with 'rec_texts' and 'rec_scores'.
        """
        from mmocr.apis import TextRecInferencer
        if not self.__m_rec:
            raise OCRError('A recognition model is required (rec=...).')
        params = {
            'model': self.__m_rec,
            'weights': self.__m_recWeights,
            'device': self.__m_device,
        }
        params.update(self.__m_inferencerParams)
        inferencer = self._inferencer('rec', TextRecInferencer, params)
        output = inferencer(crops, **self._standardCall(self.recBatchSize()))
        texts, scores = [], []
        for prediction in output.get('predictions', []) or []:
            texts.append(str(prediction.get('text', '')))
            scores.append(self._score(prediction.get('scores', 1.0)))
        return {'rec_texts': texts, 'rec_scores': scores}

    def _standardCall(self, batchSize):
        """
        Shared call kwargs for the standard (non-MMOCR) inferencers.
        """
        call = {
            'batch_size': int(batchSize or 1),
            'progress_bar': False,
            'return_vis': False,
            'save_vis': False,
            'save_pred': False,
            'print_result': False,
            # Nothing is written: out_dir is emptied on purpose.
            'out_dir': '',
        }
        call.update(self.__m_callParams)
        return call

    # ------------------------------------------------------------------ #
    # MMOCR 0.x                                                          #
    # ------------------------------------------------------------------ #
    def isLegacy(self):
        """
        :return: True when the installed MMOCR is a 0.x release, whose API
                 is ``MMOCR().readtext()`` instead of the inferencers.
        """
        try:
            from mmocr.apis import MMOCRInferencer  # noqa: F401
            return False
        except ImportError:
            pass
        try:
            from mmocr.utils.ocr import MMOCR  # noqa: F401
            return True
        except ImportError:
            raise OCRError(
                'MMOCR is not installed. Install it with: pip install -U '
                'openmim && mim install mmengine "mmcv>=2.0.0" "mmdet>=3.0.0" "mmocr>=1.0.0"')

    def _inferLegacy(self, page):
        """
        Run MMOCR 0.x and normalize its output to the 1.x shape.
        """
        from mmocr.utils.ocr import MMOCR
        mode = self.resolveMode()
        if mode == 'spot':
            raise OCRError('Text spotting requires MMOCR >= 1.0.')
        params = {
            'det': self.__m_det if mode != 'rec' else None,
            'det_ckpt': self.__m_detWeights or '',
            'recog': self.__m_rec if mode != 'det' else None,
            'recog_ckpt': self.__m_recWeights or '',
            'kie': self.__m_kie or '',
            'kie_ckpt': self.__m_kieWeights or '',
            'device': self.__m_device,
        }
        params.update(self.__m_inferencerParams)
        inferencer = self._inferencer('legacy', MMOCR, params)
        call = {'details': True, 'print_result': False, 'imshow': False}
        call.update(self.__m_callParams)
        output = inferencer.readtext(page, **call)
        raw = output[0] if isinstance(output, (list, tuple)) and output else output
        return self._normalizeLegacy(raw, page)

    def _normalizeLegacy(self, raw, page):
        """
        Map a 0.x result dict onto the 1.x prediction keys.
        """
        prediction = {'det_polygons': [], 'det_scores': [], 'rec_texts': [], 'rec_scores': []}
        if not isinstance(raw, dict):
            return prediction
        if 'result' in raw:  # det + recog (+ kie)
            for entry in raw.get('result') or []:
                box = list(entry.get('box') or [])
                if not box:
                    continue
                prediction['det_polygons'].append([float(v) for v in box])
                prediction['det_scores'].append(self._score(entry.get('box_score', 1.0)))
                prediction['rec_texts'].append(str(entry.get('text', '')))
                prediction['rec_scores'].append(self._score(entry.get('text_score', 1.0)))
                if 'label' in entry:
                    self.__m_kieLabels.append((str(entry.get('text', '')), entry['label']))
            return prediction
        if 'boundary_result' in raw:  # Detection only.
            for boundary in raw.get('boundary_result') or []:
                boundary = [float(v) for v in boundary]
                prediction['det_polygons'].append(boundary[:-1])
                prediction['det_scores'].append(boundary[-1])
            return prediction
        if 'text' in raw:  # recognition only
            height, width = float(page.shape[0]), float(page.shape[1])
            prediction['det_polygons'].append(
                [0.0, 0.0, width, 0.0, width, height, 0.0, height])
            prediction['det_scores'].append(1.0)
            prediction['rec_texts'].append(str(raw.get('text', '')))
            prediction['rec_scores'].append(self._score(raw.get('score', 1.0)))
        return prediction

    # ------------------------------------------------------------------ #
    # Predictions -> words                                               #
    # ------------------------------------------------------------------ #
    def _wordsFromPrediction(self, prediction, page, offset=0.0):
        """
        Convert one page's prediction into flat word dicts.
        :param prediction: MMOCR prediction dict.
        :param page: the page array the prediction came from.
        :param offset: vertical offset of that page (multipage inputs).
        :return: list[dict]
        """
        polygons = prediction.get('det_polygons') or []
        if not polygons and prediction.get('det_bboxes'):
            polygons = [self._boxToPolygon(box) for box in prediction['det_bboxes']]
        texts = list(prediction.get('rec_texts') or [])
        recScores = list(prediction.get('rec_scores') or [])
        detScores = list(prediction.get('det_scores') or [])
        labels = list(prediction.get('kie_labels') or [])
        maxHeight = float(page.shape[0])
        maxWidth = float(page.shape[1])
        words = []
        for index, polygon in enumerate(polygons):
            detScore = self._score(detScores[index]) \
                if index < len(detScores) else 1.0
            if detScore < self.__m_detConfidence:
                continue
            recScore = self._score(recScores[index]) \
                if index < len(recScores) else 1.0
            if texts and recScore < self.__m_minConfidence:
                continue
            box = self._polygonToBox(polygon)
            if box is None:
                continue
            left, top, width, height = box
            left = max(0.0, min(left, maxWidth))
            top = max(0.0, min(top, maxHeight))
            width = max(1.0, min(width, maxWidth - left))
            height = max(1.0, min(height, maxHeight - top))
            top += offset
            points = self._flatten(polygon)
            points[1::2] = [value + offset for value in points[1::2]]
            self.__m_polygons.append(points)
            if index < len(labels):
                text = texts[index] if index < len(texts) else ''
                self.__m_kieLabels.append((text, labels[index]))
            text = texts[index].strip() if index < len(texts) else ''
            if not text:
                if texts:
                    continue  # recognized nothing: not a word
                # Detection-only mode: keep the geometry, no text.
                words.append(self.makeWord('', left, top, width, height))
                continue
            if self.__m_wordBox:
                words.extend(self._splitWords(text, left, top, width, height))
            else:
                words.append(self.makeWord(text, left, top, width, height))
        return words

    def _splitWords(self, text, left, top, width, height):
        """
        Split a region's text into word boxes.
        MMOCR's detectors are line/region level, so per-word geometry is
        estimated by distributing the region's width over the characters
        (spaces included). Use ``wordBox=False`` to keep the detector's
        own single box per region instead.
        :return: list[dict]
        """
        parts = text.split()
        if len(parts) <= 1:
            return [self.makeWord(text, left, top, width, height)]
        total = sum(len(part) for part in parts) + (len(parts) - 1)
        if total <= 0:
            return [self.makeWord(text, left, top, width, height)]
        charWidth = float(width) / total
        words = []
        cursor = float(left)
        for part in parts:
            partWidth = max(1.0, charWidth * len(part))
            words.append(self.makeWord(part, cursor, top, partWidth, height))
            cursor += partWidth + charWidth
        return words

    # ------------------------------------------------------------------ #
    # Image loading (in memory only)                                     #
    # ------------------------------------------------------------------ #
    def _loadPages(self, image=None):
        """
        Decode the current image source into a list of BGR numpy arrays.
        Accepts paths, URLs, base64/raw bytes, ``BytesIO``, PIL images,
        numpy arrays and PDFs (rasterized in RAM).
        :return: list[numpy.ndarray]
        """
        image = self.getImage() if image is None else image
        if image is None or (isinstance(image, str) and not image.strip()):
            raise OCRError('No image was given.')
        if isinstance(image, ndarray):
            return [self._asBgr(image)]
        if hasattr(image, 'save') and not isinstance(image, (bytes, bytearray)):
            return [self._fromPil(image)]
        data = self._sourceBytes(image)
        if data[:5] == b'%PDF-':
            return self._fromPdf(data)
        return [self._decode(data)]

    def _sourceBytes(self, image):
        """
        Return the raw bytes behind path/URL/bytes/BytesIO/base64.
        """
        if isinstance(image, (bytes, bytearray)):
            return bytes(image)
        if isinstance(image, BytesIO):
            return image.getvalue()
        if isinstance(image, str):
            if is_url(image):
                return self._download(image)
            if isfile(image):
                with open(image, 'rb') as handle:
                    return handle.read()
            decoded = self._fromBase64(image)
            if decoded is not None:
                return decoded
            raise OCRError('Image string is neither an existing path, a URL nor base64 data.')
        return self.imageBytes()

    def _download(self, url):
        """
        Fetch an image URL into memory (no file is written).
        """
        try:
            from requests import get
        except ImportError:
            get = None
        if get is not None:
            response = get(url, timeout=self.getTimeout(), proxies=self.getProxy() or None)
            response.raise_for_status()
            return response.content
        try:
            from urllib import urlopen
        except:
            from urllib.request import urlopen
        handle = urlopen(url, timeout=self.getTimeout())
        try:
            return handle.read()
        finally:
            handle.close()

    @staticmethod
    def _fromBase64(text):
        """
        Decode a (possibly data-URI) base64 string, else return None.
        """
        from base64 import b64decode
        payload = text.split(',', 1)[-1] if text.startswith('data:') else text
        payload = ''.join(payload.split())
        try:
            data = b64decode(payload, validate=True)
        except Exception:  # noqa: BLE001 - any malformed input means "no"
            return None
        return data or None

    def _decode(self, data):
        """
        Decode image bytes into a BGR array, preferring mmcv.
        """
        try:
            from mmcv import imfrombytes
            array = imfrombytes(data, channel_order='bgr')
            if array is not None:
                return array
        except Exception:  # noqa: BLE001 - fall back to PIL
            pass
        from PIL.Image import open
        return self._fromPil(open(BytesIO(data)))

    @staticmethod
    def _fromPil(image):
        """
        Convert a PIL image into a BGR numpy array.
        """
        if image.mode != 'RGB':
            image = image.convert('RGB')
        return asarray(image)[:, :, ::-1].copy()

    def _asBgr(self, array):
        """
        Normalize a caller-supplied numpy array to 3-channel BGR.
        """
        array = ascontiguousarray(array)
        if array.ndim == 2:
            array = stack([array] * 3, axis=-1)
        elif array.ndim == 3 and array.shape[2] == 4:
            array = array[:, :, :3]
        elif array.ndim != 3 or array.shape[2] != 3:
            raise OCRError('Unsupported array shape {}.'.format(array.shape))
        if self.__m_arrayOrder == 'rgb':
            array = array[:, :, ::-1].copy()
        if array.dtype != uint8:
            array = clip(array, 0, 255).astype(uint8)
        return array

    def _fromPdf(self, data):
        """
        Rasterize a PDF from memory into BGR page arrays.
        """
        try:
            from pypdfium2 import PdfDocument
        except ImportError:
            raise OCRError('Reading PDFs needs pypdfium2: pip install pypdfium2')
        document = PdfDocument(data)
        try:
            indexes = self.__m_pages
            if indexes is None:
                indexes = range(len(document))
            pages = []
            for index in indexes:
                page = document[index]
                pil = page.render(scale=self.__m_pdfScale).to_pil()
                pages.append(self._fromPil(pil))
            return pages
        finally:
            document.close()

    # ------------------------------------------------------------------ #
    # Geometry helpers                                                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _flatten(polygon):
        """
        Flatten [[x, y], ...] or [x, y, ...] into a flat float list.
        """
        points = []
        for value in polygon:
            if isinstance(value, (list, tuple)) or (isinstance(value, ndarray)):
                points.extend(float(item) for item in value)
            else:
                points.append(float(value))
        return points

    @classmethod
    def _polygonToBox(cls, polygon):
        """
        Axis-aligned (left, top, width, height) of a polygon/quad/bbox.
        :return: tuple | None when the polygon is unusable.
        """
        points = cls._flatten(polygon)
        if len(points) == 4:  # already a [x1, y1, x2, y2] bbox
            left, top, right, bottom = points
            return min(left, right), min(top, bottom), abs(right - left), abs(bottom - top)
        if len(points) < 6 or len(points) % 2:
            return None
        xs = points[0::2]
        ys = points[1::2]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    @staticmethod
    def _boxToPolygon(box):
        """
        Turn [x1, y1, x2, y2] (or a polygon) into a flat polygon.
        """
        values = [float(v) for v in MmOcr._flatten(box)]
        if len(values) != 4:
            return values
        left, top, right, bottom = values
        return [left, top, right, top, right, bottom, left, bottom]

    def _normalizedBoxes(self):
        """
        Caller-supplied regions, or None.
        """
        boxes = self.__m_boxes
        if boxes is None:
            return None
        if isinstance(boxes, ndarray):
            boxes = boxes.tolist()
        if not len(boxes):
            return None
        first = boxes[0]
        if not isinstance(first, (list, tuple)) and not (isinstance(first, ndarray)):
            boxes = [boxes]  # a single flat box
        return list(boxes)

    def _crop(self, page, box):
        """
        Crop the axis-aligned bounds of *box* out of *page*.
        """
        bounds = self._polygonToBox(box)
        if bounds is None:
            raise OCRError('Invalid box: {!r}.'.format(box))
        left, top, width, height = bounds
        x1 = max(0, int(round(left)))
        y1 = max(0, int(round(top)))
        x2 = min(int(page.shape[1]), int(round(left + width)))
        y2 = min(int(page.shape[0]), int(round(top + height)))
        if x2 <= x1 or y2 <= y1:
            raise OCRError('Box {!r} lies outside the image.'.format(box))
        return page[y1:y2, x1:x2].copy()

    @staticmethod
    def _score(value):
        """
        Coerce a score (float, list of per-character floats) to float.
        """
        if value is None:
            return 1.0
        if isinstance(value, (list, tuple)):
            values = [float(item) for item in value]
            return sum(values) / len(values) if values else 1.0
        if isinstance(value, ndarray):
            return float(value.mean()) if value.size else 1.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _firstPrediction(output):
        """
        Pull the first (single-image) prediction out of an output dict.
        """
        if isinstance(output, dict):
            predictions = output.get('predictions') or []
        else:
            predictions = output or []
        if not predictions:
            return {}
        prediction = predictions[0]
        return prediction if isinstance(prediction, dict) else {}

    # ------------------------------------------------------------------ #
    # Inferencer cache                                                   #
    # ------------------------------------------------------------------ #
    def _inferencer(self, kind, factory, params):
        """
        Build (or fetch from the cache) an inferencer.
        :param kind: cache namespace ('mmocr', 'det', 'rec', ...).
        :param factory: the inferencer class.
        :param params: constructor keyword arguments.
        :return: the inferencer instance.
        """
        if not self.__m_cache:
            return factory(**params)
        key = (kind, factory.__name__, tuple(sorted((str(name), repr(value)) for name, value in params.items())))
        inferencer = MmOcr._INFERENCERS.get(key)
        if inferencer is None:
            inferencer = factory(**params)
            MmOcr._INFERENCERS[key] = inferencer
        return inferencer

    @classmethod
    def clearCache(cls):
        """
        Drop every cached inferencer (frees the loaded weights).
        """
        cls._INFERENCERS.clear()

    def _describeModel(self):
        """
        Human-readable description of the configured models.
        """
        parts = []
        if self.__m_spot:
            parts.append('spot={}'.format(self.__m_spot))
        if self.__m_det:
            parts.append('det={}'.format(self.__m_det))
        if self.__m_rec:
            parts.append('rec={}'.format(self.__m_rec))
        if self.__m_kie:
            parts.append('kie={}'.format(self.__m_kie))
        return 'mmocr({})'.format(', '.join(parts))

    # ------------------------------------------------------------------ #
    # Extra public helpers                                               #
    # ------------------------------------------------------------------ #
    def detectBoxes(self):
        """
        Run detection only and return the polygons, without recognition.
        :return: list of flat polygons [x1, y1, x2, y2, ...]
        """
        pages = self._loadPages()
        legacy = self.isLegacy()
        previous = self.getMode()
        polygons = []
        offset = 0.0
        try:
            self.setMode('det')
            for page in pages:
                prediction = self._inferLegacy(page) if legacy else self._inferDet(page)
                for polygon in prediction.get('det_polygons') or []:
                    points = self._flatten(polygon)
                    points[1::2] = [value + offset for value in points[1::2]]
                    polygons.append(points)
                offset += float(page.shape[0]) + self.PAGE_GAP
        finally:
            self.setMode(previous)
        self.__m_polygons = polygons
        return polygons

    def getPredictions(self):
        """
        :return: list of MMOCR's own prediction dicts, one per page, as produced by the last ``parse()``.
        """
        return self.__m_predictions

    def getPolygons(self):
        """
        :return: list of detector polygons kept by the last ``parse()``.
        """
        return self.__m_polygons

    def getKieLabels(self):
        """
        :return: list of (text, label) pairs when a KIE model was used.
        """
        return self.__m_kieLabels

    def getPageSizes(self):
        """
        :return: list of (width, height) for every processed page.
        """
        return self.__m_pageSizes

    # ------------------------------------------------------------------ #
    # Getters / setters                                                  #
    # ------------------------------------------------------------------ #
    def getMode(self):
        """
        :return: str | unicode
        """
        return self.__m_mode

    def setMode(self, mode):
        """
        :param mode: str | unicode
        :return:
        """
        self.__m_mode = mode.lower()

    def getDet(self):
        """
        :return: str | unicode
        """
        return self.__m_det

    def setDet(self, det):
        """
        :param det: str | unicode
        :return:
        """
        self.__m_det = det
        self.setModel(self._describeModel())

    def getDetWeights(self):
        """
        :return: str | unicode
        """
        return self.__m_detWeights

    def setDetWeights(self, weights):
        """
        :param weights: str | unicode
        :return:
        """
        self.__m_detWeights = weights

    def getRec(self):
        """
        :return: str | unicode
        """
        return self.__m_rec

    def setRec(self, rec):
        """
        :param rec: str | unicode
        :return:
        """
        self.__m_rec = rec
        self.setModel(self._describeModel())

    def getRecWeights(self):
        """
        :return: str | unicode | None
        :return:
        """
        return self.__m_recWeights

    def setRecWeights(self, weights):
        """
        :param weights: str | unicode | None
        :return:
        """
        self.__m_recWeights = weights

    def getKie(self):
        """
        :return: str | unicode | None
        """
        return self.__m_kie

    def setKie(self, kie):
        """
        :param kie: str | unicode | None
        :return:
        """
        self.__m_kie = kie
        self.setModel(self._describeModel())

    def getKieWeights(self):
        """
        :return: str | unicode | None
        """
        return self.__m_kieWeights

    def setKieWeights(self, weights):
        """
        :param weights: str | unicode | None
        :return:
        """
        self.__m_kieWeights = weights

    def getSpot(self):
        """
        :return: str | unicode | None
        :return:
        """
        return self.__m_spot

    def setSpot(self, spot):
        """
        :param spot: str | unicode | None
        :return:
        """
        self.__m_spot = spot
        self.setModel(self._describeModel())

    def getSpotWeights(self):
        """
        :return: str | unicode
        """
        return self.__m_spotWeights

    def setSpotWeights(self, weights):
        """
        :param weights: str | unicode
        :return:
        """
        self.__m_spotWeights = weights

    def getDevice(self):
        """
        :return: str | unicode
        """
        return self.__m_device

    def setDevice(self, device):
        """
        :param device: str | unicode
        :return:
        """
        self.__m_device = device

    def getBoxes(self):
        """
        :return: iterable object
        """
        return self.__m_boxes

    def setBoxes(self, boxes):
        """
        :param boxes: iterable object
        :return:
        """
        self.__m_boxes = boxes

    def isWordBox(self):
        """
        :return: bool
        """
        return self.__m_wordBox

    def setWordBox(self, wordBox):
        """
        :param wordBox: bool
        :return:
        """
        self.__m_wordBox = bool(wordBox)

    def getMinConfidence(self):
        """
        :return: float | int
        """
        return self.__m_minConfidence

    def setMinConfidence(self, minConfidence):
        """
        :param minConfidence: float | int
        :return:
        """
        self.__m_minConfidence = float(minConfidence)

    def getDetConfidence(self):
        """
        :return: float | int
        """
        return self.__m_detConfidence

    def setDetConfidence(self, detConfidence):
        """
        :param detConfidence: float | int
        :return:
        """
        self.__m_detConfidence = float(detConfidence)

    def getBatchSize(self):
        """
        :return: int
        """
        return self.__m_batchSize

    def setBatchSize(self, batchSize):
        """
        :param batchSize: int
        :return:
        """
        self.__m_batchSize = int(batchSize)

    def detBatchSize(self):
        """
        :return: int
        """
        return self.__m_detBatchSize or self.__m_batchSize

    def setDetBatchSize(self, batchSize):
        """
        :param batchSize: int
        :return:
        """
        self.__m_detBatchSize = int(batchSize)

    def recBatchSize(self):
        """
        :return: int
        """
        return self.__m_recBatchSize or self.__m_batchSize

    def setRecBatchSize(self, batchSize):
        """
        :param batchSize: int
        :return:
        """
        self.__m_recBatchSize = int(batchSize)

    def getArrayOrder(self):
        """
        :return: str | unicode
        """
        return self.__m_arrayOrder

    def setArrayOrder(self, order):
        """
        :param order: str | unicode
        :return:
        """
        self.__m_arrayOrder = str(order).lower()

    def getPdfScale(self):
        """
        :return: float | int
        """
        return self.__m_pdfScale

    def setPdfScale(self, scale):
        """
        :param scale: float | int
        :return:
        """
        self.__m_pdfScale = float(scale)

    def getPages(self):
        """
        :return: list[int] | None
        """
        return self.__m_pages

    def setPages(self, pages):
        """
        :param pages: list[int] | None
        :return:
        """
        self.__m_pages = pages

    def getInferencerParams(self):
        """
        :return: dict
        """
        return self.__m_inferencerParams

    def setInferencerParams(self, params):
        """
        :param params: dict
        :return:
        """
        self.__m_inferencerParams = dict(params or {})

    def getCallParams(self):
        """
        :return: dict
        """
        return self.__m_callParams

    def setCallParams(self, params):
        """
        :param params: dict
        :return:
        """
        self.__m_callParams = dict(params or {})

    def isCache(self):
        """
        :return: bool
        """
        return self.__m_cache

    def setCache(self, cache):
        """
        :param cache: bool
        :return:
        """
        self.__m_cache = bool(cache)

    mode = property(fget=getMode, fset=setMode)
    det = property(fget=getDet, fset=setDet)
    detWeights = property(fget=getDetWeights, fset=setDetWeights)
    rec = property(fget=getRec, fset=setRec)
    recWeights = property(fget=getRecWeights, fset=setRecWeights)
    kie = property(fget=getKie, fset=setKie)
    kieWeights = property(fget=getKieWeights, fset=setKieWeights)
    spot = property(fget=getSpot, fset=setSpot)
    spotWeights = property(fget=getSpotWeights, fset=setSpotWeights)
    device = property(fget=getDevice, fset=setDevice)
    boxes = property(fget=getBoxes, fset=setBoxes)
    wordBox = property(fget=isWordBox, fset=setWordBox)
    minConfidence = property(fget=getMinConfidence, fset=setMinConfidence)
    detConfidence = property(fget=getDetConfidence, fset=setDetConfidence)
    batchSize = property(fget=getBatchSize, fset=setBatchSize)
    arrayOrder = property(fget=getArrayOrder, fset=setArrayOrder)
    pdfScale = property(fget=getPdfScale, fset=setPdfScale)
    pages = property(fget=getPages, fset=setPages)
    inferencerParams = property(fget=getInferencerParams, fset=setInferencerParams)
    callParams = property(fget=getCallParams, fset=setCallParams)
    predictions = property(fget=getPredictions)
    polygons = property(fget=getPolygons)
    kieLabels = property(fget=getKieLabels)
    pageSizes = property(fget=getPageSizes)


#: Alias, for callers that prefer the toolbox's own capitalization.
MMOcr = MmOcr
__all__ = ['MmOcr', 'MMOcr', 'DET_MODELS', 'REC_MODELS', 'KIE_MODELS']
