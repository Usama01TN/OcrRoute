# coding=utf-8
"""
OpenOCR plugin — Fudan FVL Lab's OpenOCR toolkit as an ``OCRPlugin`` engine.
    https://github.com/Topdu/OpenOCR
The library is imported directly (``from openocr import OpenOCR``); there
is no server, no HTTP call, no subprocess and no temporary file anywhere
in this module. Images are decoded in memory into BGR numpy arrays and
handed to the engine through its ``img_numpy`` argument, which is the
path OpenOCR itself uses for in-memory inference.
Five tasks are exposed, mirroring the toolkit's own names:
* ``'ocr'``  — detection + recognition (SVTRv2-based general OCR system).
* ``'det'``  — text detection only, real quad/polygon boxes.
* ``'rec'``  — recognition of an already-cropped word/line image.
* ``'unirec'`` — UniRec-0.1B, unified text / formula / table recognition
  (returns Markdown + LaTeX, no coordinates).
* ``'doc'``  — OpenDoc-0.1B document parsing with layout analysis.
Install::
    pip install openocr-python                 # ONNX backend, CPU
    pip install torch torchvision              # only for mode='server'
Usage::
    from openocr import OpenOcr
    result = OpenOcr(image='demo.jpg').parse()                    # det + rec
    result = OpenOcr(image='demo.jpg', mode='server', backend='torch').parse()                     # accurate
    result = OpenOcr(image='demo.jpg', detBoxType='poly').parse()  # curved
    result = OpenOcr(image='line.png', task='rec').parse()         # one crop
    result = OpenOcr(image='formula.png', task='unirec').parse()   # LaTeX
    result = OpenOcr(image='page.pdf').parse()                     # all pages
    plugin = OpenOcr(image='demo.jpg')
    result = plugin.parse()
    boxes = plugin.getPolygons()      # detector quads, real pixels
    raw = plugin.getPredictions()     # OpenOCR's own dicts, untouched
Note the module name: a file called ``openocr.py`` would shadow the
installed ``openocr`` package and break ``from openocr import OpenOCR``,
so this one is ``openocr.py``.
"""
from numpy import ndarray, frombuffer, uint8, ascontiguousarray, stack, clip, asarray
from os.path import isfile, splitext, dirname
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

#: Tasks this plugin drives.
TASKS = ('ocr', 'det', 'rec', 'unirec', 'doc')
#: Model sizes: 'mobile' is the default ONNX system, 'server' is the
#: larger SVTRv2 recognizer and requires the torch backend.
MODES = ('mobile', 'server')
#: Inference backends.
BACKENDS = ('onnx', 'torch')


class OpenOcr(OCRPlugin):
    """
    OpenOCR engine (Topdu / FVL Lab, Fudan University).
    Everything runs locally: the ONNX/torch weights are downloaded once by
    OpenOCR itself into its own cache, then loaded into memory. Engines
    are cached process-wide, so several ``OpenOcr`` instances sharing the
    same settings re-use the loaded models.
    """
    #: Vertical gap inserted between stacked PDF pages, in pixels.
    PAGE_GAP = 20.0
    #: Rendering scale used for PDF pages (72 dpi * scale).
    PDF_SCALE = 2.0
    #: Fraction of a row's height left as spacing when text without
    #: coordinates (UniRec) is laid out as rows.
    ROW_PADDING = 0.15
    #: Process-wide engine cache: {key: engine}.
    _ENGINES = {}

    def __init__(self, *args, **kwargs):
        """
        :param image: path | URL | base64/bytes | BytesIO | PIL image | numpy array | PDF (path or bytes)
        :param task: 'ocr' | 'det' | 'rec' | 'unirec' | 'doc'
        :param mode: 'mobile' (default) | 'server'
        :param backend: 'onnx' (default) | 'torch'
        :param useGpu: 'auto' | 'true' | 'false'
        :param dropScore: OpenOCR's own recognition-score threshold
        :param detBoxType: 'quad' (default) | 'poly' for curved text
        :param minConfidence: extra post-filter on the returned scores
        :param wordBox: split each detected region into word boxes
        :param recBatchNum: recognition batch size
        :param cropInfer: OpenOCR's crop-inference mode for big images
        :param maxLength: UniRec/OpenDoc maximum generation length
        :param detModelPath: custom detection ONNX model
        :param recModelPath: custom recognition ONNX model
        :param unirecEncoderPath: custom UniRec encoder ONNX model
        :param unirecDecoderPath: custom UniRec decoder ONNX model
        :param tokenizerMappingPath: custom UniRec tokenizer mapping JSON
        :param layoutModelPath: custom OpenDoc layout model
        :param layoutThreshold: OpenDoc layout-detection threshold
        :param useLayoutDetection: run layout analysis in the doc task
        :param useChartRecognition: recognize charts in the doc task
        :param docLabels: keep only these OpenDoc block labels
        :param arrayOrder: 'bgr' (OpenOCR's convention) or 'rgb'
        :param pdfScale: PDF rasterization scale (2.0 = 144 dpi)
        :param pages: optional list of 0-based PDF page indexes
        :param allowLibraryTempFiles: allow the 'doc' task to run on
               in-memory input, which makes OpenOCR write its own temporary JPEG (off by default)
        :param engineParams: extra kwargs for the engine constructor
        :param callParams: extra kwargs for the engine call
        :param cache: keep loaded engines in the process cache
        """
        self.__m_task = kwargs.pop('task', 'ocr').lower()
        self.__m_mode = kwargs.pop('mode', 'mobile').lower()
        self.__m_backend = kwargs.pop('backend', 'onnx').lower()
        self.__m_useGpu = kwargs.pop('useGpu', 'auto').lower()
        self.__m_dropScore = float(kwargs.pop('dropScore', 0.5))
        self.__m_detBoxType = kwargs.pop('detBoxType', 'quad').lower()
        self.__m_minConfidence = float(kwargs.pop('minConfidence', 0.0))
        self.__m_wordBox = bool(kwargs.pop('wordBox', True))
        self.__m_recBatchNum = int(kwargs.pop('recBatchNum', 6))
        self.__m_cropInfer = bool(kwargs.pop('cropInfer', False))
        self.__m_maxLength = int(kwargs.pop('maxLength', 2048))
        self.__m_detModelPath = kwargs.pop('detModelPath', None)
        self.__m_recModelPath = kwargs.pop('recModelPath', None)
        self.__m_unirecEncoderPath = kwargs.pop('unirecEncoderPath', None)
        self.__m_unirecDecoderPath = kwargs.pop('unirecDecoderPath', None)
        self.__m_tokenizerMappingPath = kwargs.pop('tokenizerMappingPath', None)
        self.__m_layoutModelPath = kwargs.pop('layoutModelPath', None)
        self.__m_layoutThreshold = float(kwargs.pop('layoutThreshold', 0.5))
        self.__m_useLayoutDetection = bool(kwargs.pop('useLayoutDetection', True))
        self.__m_useChartRecognition = bool(kwargs.pop('useChartRecognition', True))
        self.__m_docLabels = kwargs.pop('docLabels', None)
        self.__m_arrayOrder = kwargs.pop('arrayOrder', 'bgr').lower()
        self.__m_pdfScale = float(kwargs.pop('pdfScale', self.PDF_SCALE))
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_allowTempFiles = bool(kwargs.pop('allowLibraryTempFiles', False))
        self.__m_engineParams = dict(kwargs.pop('engineParams', {}))
        self.__m_callParams = dict(kwargs.pop('callParams', {}))
        self.__m_cache = bool(kwargs.pop('cache', True))
        self.__m_predictions = []
        self.__m_polygons = []
        self.__m_pageSizes = []
        self.__m_markdown = []
        super(OpenOcr, self).__init__(*args, **kwargs)
        self.setModel(self._describeModel())

    # ------------------------------------------------------------------ #
    # Engine entry point                                                 #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        Run OpenOCR on the current image and return flat word dicts.
        :param image: whatever ``getImage()`` holds.
        :return: list[dict]
        """
        task = self.getTask()
        if task == 'doc':
            return self._runDoc()
        pages = self._loadPages(image)
        if not pages:
            raise OCRError('No page could be decoded from the given image.')
        self.__m_predictions = []
        self.__m_polygons = []
        self.__m_markdown = []
        self.__m_pageSizes = [(int(page.shape[1]), int(page.shape[0])) for page in pages]
        words = []
        offset = 0.0
        for page in pages:
            prediction = self._inferPage(page, task)
            self.__m_predictions.append(prediction)
            words.extend(self._wordsFromPrediction(prediction, page, offset))
            offset += float(page.shape[0]) + self.PAGE_GAP
        return words

    def getTask(self):
        """
        :return: the validated task name.
        """
        task = self.__m_task
        if task not in TASKS:
            raise OCRError('Unknown task {!r}; expected one of {}.'.format(task, ', '.join(TASKS)))
        return task

    # ------------------------------------------------------------------ #
    # Inference                                                          #
    # ------------------------------------------------------------------ #
    def _inferPage(self, page, task):
        """
        Run one page through the engine and normalize the reply.
        :param page: BGR numpy array.
        :return: dict with 'boxes', 'texts', 'scores' (and 'text' for the coordinate-free tasks).
        """
        engine = self._engine(task)
        call = self._callParamsFor(task)
        output = self._invoke(engine, page, call)
        if task == 'ocr':
            return self._normalizeOcr(output)
        if task == 'det':
            return self._normalizeDet(output)
        if task == 'rec':
            return self._normalizeRec(output, page)
        return self._normalizeUnirec(output)

    @staticmethod
    def _invoke(engine, page, call):
        """
        Call the engine with an in-memory array.
        The unified ``OpenOCR`` wrapper (openocr-python >= 0.1.3) forwards
        ``image_path`` plus any extra keyword to the underlying model, so
        ``img_numpy`` reaches it untouched. Older releases exposed the
        end-to-end engine directly and only take ``img_numpy``.
        """
        if getattr(engine, 'task', None) is not None:
            return engine(image_path=None, img_numpy=page, **call)
        return engine(img_numpy=page, **call)

    def _callParamsFor(self, task):
        """
        Per-task call keywords, before the caller's overrides.
        """
        call = {}
        if task == 'ocr':
            call['rec_batch_num'] = self.__m_recBatchNum
            call['crop_infer'] = self.__m_cropInfer
            # No visualization: the numpy path writes nothing at all.
            call['is_visualize'] = False
        elif task == 'rec':
            call['batch_num'] = self.__m_recBatchNum
        elif task == 'unirec':
            call['max_length'] = self.__m_maxLength
        call.update(self.__m_callParams)
        return call

    def _engineParamsFor(self, task):
        """
        Constructor keywords for a given task.
        """
        params = {'use_gpu': self.__m_useGpu}
        if task in ('ocr', 'rec'):
            params['mode'] = self.__m_mode
        if task in ('ocr', 'det', 'rec'):
            params['backend'] = self.__m_backend
        if task in ('ocr', 'det'):
            params['onnx_det_model_path'] = self.__m_detModelPath
        if task in ('ocr', 'rec'):
            params['onnx_rec_model_path'] = self.__m_recModelPath
        if task == 'ocr':
            params['drop_score'] = self.__m_dropScore
            params['det_box_type'] = self.__m_detBoxType
        if task == 'det':
            params['det_box_type'] = self.__m_detBoxType
        if task in ('unirec', 'doc'):
            params['unirec_encoder_path'] = self.__m_unirecEncoderPath
            params['unirec_decoder_path'] = self.__m_unirecDecoderPath
            params['tokenizer_mapping_path'] = self.__m_tokenizerMappingPath
            params['max_length'] = self.__m_maxLength
        if task == 'doc':
            params['layout_model_path'] = self.__m_layoutModelPath
            params['layout_threshold'] = self.__m_layoutThreshold
            params['use_layout_detection'] = self.__m_useLayoutDetection
            params['use_chart_recognition'] = self.__m_useChartRecognition
        params.update(self.__m_engineParams)
        return params

    def _engine(self, task):
        """
        Build (or fetch from the cache) the OpenOCR engine for *task*.
        :return: the engine object.
        """
        factory = self._openocr()
        params = self._engineParamsFor(task)
        if self._acceptsTask(factory):
            params = dict(params)
            params['task'] = task
        else:
            # openocr-python 0.0.x: OpenOCR *is* the end-to-end engine.
            if task != 'ocr':
                raise OCRError(
                    "task={!r} needs openocr-python >= 0.1.3 (the unified "
                    'interface); upgrade with: pip install -U openocr-python'.format(task))
            params = {name: value for name, value in params.items()
                      if name in ('mode', 'backend', 'drop_score', 'det_box_type', 'use_gpu')}
        if not self.__m_cache:
            return factory(**params)
        key = (task, tuple(sorted((str(name), repr(value)) for name, value in params.items())))
        engine = OpenOcr._ENGINES.get(key)
        if engine is None:
            engine = factory(**params)
            OpenOcr._ENGINES[key] = engine
        return engine

    @staticmethod
    def _openocr():
        """
        Import and return the ``OpenOCR`` class.
        """
        try:
            from openocr import OpenOCR
        except ImportError:
            raise OCRError('OpenOCR is not installed. Install it with: pip install openocr-python')
        return OpenOCR

    @staticmethod
    def _acceptsTask(factory):
        """
        True when this is the unified interface (0.1.3+).
        """
        from inspect import signature
        try:
            return 'task' in signature(factory.__init__).parameters
        except (TypeError, ValueError):  # Pragma: no cover - exotic builds.
            return True

    @classmethod
    def clearCache(cls):
        """
        Drop every cached engine (frees the loaded models).
        """
        cls._ENGINES.clear()

    # ------------------------------------------------------------------ #
    # Reply normalization                                                #
    # ------------------------------------------------------------------ #
    def _normalizeOcr(self, output):
        """
        Map the end-to-end reply onto boxes + texts + scores.
        ``OpenOCRE2E`` returns ``(results, time_dicts)``; ``results[0]``
        holds one dict per detected region with ``transcription``,
        ``points`` and ``score``. Older builds hand back the
        ``"name\\tjson"`` line the CLI writes, which is parsed too.
        """
        entries = self._entries(output)
        boxes, texts, scores = [], [], []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            points = entry.get('points')
            if points is None:
                points = entry.get('box') or entry.get('boxes')
            if points is None:
                continue
            boxes.append(points)
            texts.append(str(entry.get('transcription', entry.get('text', ''))))
            scores.append(self._score(entry.get('score', 1.0)))
        return {'boxes': boxes, 'texts': texts, 'scores': scores}

    @staticmethod
    def _normalizeDet(output):
        """
        ``OpenDetector`` returns [{'boxes': ndarray, 'elapse': ...}].
        """
        entries = output[0] if isinstance(output, (list, tuple)) and output else output
        boxes = []
        if isinstance(entries, dict):
            raw = entries.get('boxes')
            if raw is not None:
                boxes = list(raw)
        return {'boxes': boxes, 'texts': [], 'scores': []}

    def _normalizeRec(self, output, page):
        """
        ``OpenRecognizer`` returns [{'text': ..., 'score': ...}].
        """
        entry = output[0] if isinstance(output, (list, tuple)) and output else output
        if not isinstance(entry, dict):
            return {'boxes': [], 'texts': [], 'scores': []}
        height, width = float(page.shape[0]), float(page.shape[1])
        return {
            'boxes': [[[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]]],
            'texts': [str(entry.get('text', ''))],
            'scores': [self._score(entry.get('score', 1.0))]}

    @staticmethod
    def _normalizeUnirec(output):
        """
        UniRec returns ``(generated_text, generated_ids)`` — Markdown and
        LaTeX with no coordinates, so the text is kept for row layout.
        """
        text = output
        if isinstance(output, (list, tuple)) and output:
            text = output[0]
            if isinstance(text, (list, tuple)) and text:
                # PDF input handed back one tuple per page.
                text = '\n'.join(str(item[0]) for item in output if isinstance(item, (list, tuple)) and item)
        return {'boxes': [], 'texts': [], 'scores': [], 'text': str(text)}

    @staticmethod
    def _entries(output):
        """
        Unwrap the end-to-end reply down to the per-region list.
        Handles ``(results, time_dicts)``, ``(results, time_dicts, mask)``,
        a bare list of images, and the ``"name\\tjson"`` string form.
        """
        from json import loads
        results = output
        if isinstance(output, tuple):
            results = output[0] if output else []
        if not isinstance(results, (list, tuple)):
            return []
        entries = list(results)
        if entries and isinstance(entries[0], (list, tuple)):
            entries = list(entries[0])  # one image was submitted
        parsed = []
        for entry in entries:
            if isinstance(entry, str):
                payload = entry.split('\t', 1)[-1]
                try:
                    decoded = loads(payload)
                except ValueError:
                    continue
                if isinstance(decoded, list):
                    parsed.extend(decoded)
                elif isinstance(decoded, dict):
                    parsed.append(decoded)
            else:
                parsed.append(entry)
        return parsed

    # ------------------------------------------------------------------ #
    # OpenDoc                                                            #
    # ------------------------------------------------------------------ #
    def _runDoc(self):
        """
        Run the OpenDoc-0.1B document parser.
        OpenDoc is the one task whose in-memory path is not in-memory:
        for a numpy array (and for every PDF page) the library writes a
        temporary JPEG of its own before parsing it. This plugin never
        creates a temp file, so the doc task is restricted to a plain
        image *path*, which OpenDoc reads directly. Pass
        ``allowLibraryTempFiles=True`` to opt into the library's own
        temporary files for arrays and PDFs.
        """
        image = self.getImage()
        path = image if isinstance(image, str) and isfile(image) else None
        isPdf = bool(path) and splitext(path)[1].lower() == '.pdf'
        if path is None or (isPdf and not self.__m_allowTempFiles):
            if not self.__m_allowTempFiles:
                raise OCRError(
                    "task='doc' only accepts a path to an image file: for "
                    'arrays, bytes and PDF pages OpenDoc writes its own '
                    'temporary JPEG, which this plugin will not trigger. '
                    'Pass a file path, use task=\'ocr\'/\'unirec\', or set '
                    'allowLibraryTempFiles=True to accept them.')
        engine = self._engine('doc')
        call = {'layout_threshold': self.__m_layoutThreshold, 'max_length': self.__m_maxLength}
        call.update(self.__m_callParams)
        if path is not None:
            output = engine(image_path=path, **call) if getattr(engine, 'task', None) is not None else engine(
                img_path=path, **call)
        else:
            page = self._loadPages()[0]
            output = engine(image_path=None, img_numpy=page, **call)
        results = output if isinstance(output, list) else [output]
        self.__m_predictions = list(results)
        self.__m_polygons = []
        self.__m_markdown = []
        self.__m_pageSizes = []
        words = []
        offset = 0.0
        for result in results:
            if not isinstance(result, dict):
                continue
            width = float(result.get('width') or 0.0)
            height = float(result.get('height') or 0.0)
            self.__m_pageSizes.append((int(width), int(height)))
            words.extend(self._wordsFromDoc(result, offset))
            offset += height + self.PAGE_GAP
        return words

    def _wordsFromDoc(self, result, offset=0.0):
        """
        Turn OpenDoc's recognition blocks into word dicts.
        """
        words = []
        markdown = []
        for block in result.get('recognition_results') or []:
            if not isinstance(block, dict):
                continue
            label = block.get('label', '')
            if self.__m_docLabels and label not in self.__m_docLabels:
                continue
            score = self._score(block.get('score', 1.0))
            if score < self.__m_minConfidence:
                continue
            text = (block.get('text') or '').strip()
            if not text:
                continue
            markdown.append(text)
            box = self._polygonToBox(block.get('bbox'))
            if box is None:
                continue
            left, top, width, height = box
            self.__m_polygons.append(self._boxToPolygon([left, top + offset, left + width, top + height + offset]))
            words.extend(self._rowsFromText(text, left, top + offset, width, height))
        self.__m_markdown.append('\n\n'.join(markdown))
        return words

    # ------------------------------------------------------------------ #
    # Predictions -> words                                               #
    # ------------------------------------------------------------------ #
    def _wordsFromPrediction(self, prediction, page, offset=0.0):
        """
        Convert one page's normalized prediction into word dicts.
        :param prediction: dict from ``_normalize*``.
        :param page: the page the prediction came from.
        :param offset: vertical offset of that page (multi-page inputs).
        :return: list[dict]
        """
        maxWidth = float(page.shape[1])
        maxHeight = float(page.shape[0])
        if not prediction.get('boxes') and prediction.get('text'):
            # UniRec: text without geometry, laid out as evenly spaced rows.
            text = prediction['text']
            self.__m_markdown.append(text)
            return self._rowsFromText(text, 0.0, offset, maxWidth, maxHeight)
        texts = prediction.get('texts') or []
        scores = prediction.get('scores') or []
        words = []
        for index, box in enumerate(prediction.get('boxes') or []):
            score = self._score(scores[index]) if index < len(scores) else 1.0
            if texts and score < self.__m_minConfidence:
                continue
            bounds = self._polygonToBox(box)
            if bounds is None:
                continue
            left, top, width, height = bounds
            left = max(0.0, min(left, maxWidth))
            top = max(0.0, min(top, maxHeight))
            width = max(1.0, min(width, maxWidth - left))
            height = max(1.0, min(height, maxHeight - top))
            points = self._flatten(box)
            points[1::2] = [value + offset for value in points[1::2]]
            self.__m_polygons.append(points)
            text = texts[index].strip() if index < len(texts) else ''
            top += offset
            if not text:
                if texts:
                    continue  # recognized nothing: not a word
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
        OpenOCR's detector works at line/region level, so per-word
        geometry is estimated by distributing the region's width over the
        characters (spaces included). Use ``wordBox=False`` to keep the
        detector's own single box per region instead.
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

    def _rowsFromText(self, text, left, top, width, height):
        """
        Lay coordinate-free text out as evenly spaced rows inside a box.
        UniRec and OpenDoc return transcriptions (Markdown, LaTeX) rather
        than per-line geometry, so the lines are distributed down the
        region they came from. Those boxes are layout estimates, not
        detections — ``getPredictions()`` keeps the untouched reply.
        :return: list[dict]
        """
        lines = [line.strip() for line in str(text).splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            return []
        rowHeight = float(height) / len(lines)
        padding = rowHeight * self.ROW_PADDING
        words = []
        for index, line in enumerate(lines):
            rowTop = float(top) + index * rowHeight + padding / 2.0
            rowBox = max(1.0, rowHeight - padding)
            if self.__m_wordBox:
                words.extend(self._splitWords(line, left, rowTop, width, rowBox))
            else:
                words.append(self.makeWord(line, left, rowTop, width, rowBox))
        return words

    # ------------------------------------------------------------------ #
    # Image loading (in memory only)                                     #
    # ------------------------------------------------------------------ #
    def _loadPages(self, image=None):
        """
        Decode the current image source into a list of BGR numpy arrays.
        Accepts paths, URLs, base64/raw bytes, ``BytesIO``, PIL images,
        numpy arrays and PDFs (rasterized in RAM).
        :return: list[ndarray]
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
            raise OCRError(
                'Image string is neither an existing path, a URL nor '
                'base64 data.')
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
            from urllib.request import urlopen
        except:
            from urllib import urlopen
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
        Decode image bytes into a BGR array, preferring OpenCV.
        """
        try:
            from cv2 import imdecode, IMREAD_COLOR
            array = imdecode(frombuffer(data, uint8), IMREAD_COLOR)
            if array is not None:
                return array
        except Exception:  # noqa: BLE001 - fall back to PIL.
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
            return [self._fromPil(document[index].render(scale=self.__m_pdfScale).to_pil()) for index in indexes]
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
        if isinstance(polygon, ndarray):
            polygon = polygon.tolist()
        points = []
        for value in polygon:
            if isinstance(value, (list, tuple)):
                points.extend(float(item) for item in value)
            elif isinstance(value, ndarray):
                points.extend(float(item) for item in value.tolist())
            else:
                points.append(float(value))
        return points

    @classmethod
    def _polygonToBox(cls, polygon):
        """
        Axis-aligned (left, top, width, height) of a quad/polygon/bbox.
        :return: tuple | None when the polygon is unusable.
        """
        if polygon is None:
            return None
        points = cls._flatten(polygon)
        if len(points) == 4:  # [x1, y1, x2, y2]
            left, top, right, bottom = points
            return min(left, right), min(top, bottom), abs(right - left), abs(bottom - top)
        if len(points) < 6 or len(points) % 2:
            return None
        xs = points[0::2]
        ys = points[1::2]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    @classmethod
    def _boxToPolygon(cls, box):
        """
        Turn [x1, y1, x2, y2] (or a polygon) into a flat polygon.
        """
        values = cls._flatten(box)
        if len(values) != 4:
            return values
        left, top, right, bottom = values
        return [left, top, right, top, right, bottom, left, bottom]

    @staticmethod
    def _score(value):
        """
        Coerce a score (float, list, ndarray) into a float.
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

    def _describeModel(self):
        """
        Human-readable description of the configured engine.
        """
        parts = ['task={}'.format(self.__m_task)]
        if self.__m_task in ('ocr', 'rec'):
            parts.append('mode={}'.format(self.__m_mode))
        if self.__m_task in ('ocr', 'det', 'rec'):
            parts.append('backend={}'.format(self.__m_backend))
        return 'openocr({})'.format(', '.join(parts))

    # ------------------------------------------------------------------ #
    # Extra public helpers                                               #
    # ------------------------------------------------------------------ #
    def detectBoxes(self):
        """
        Run detection only and return the boxes, without recognition.
        :return: list of flat polygons [x1, y1, x2, y2, ...]
        """
        pages = self._loadPages()
        polygons = []
        offset = 0.0
        for page in pages:
            prediction = self._inferPage(page, 'det')
            for box in prediction.get('boxes') or []:
                points = self._flatten(box)
                points[1::2] = [value + offset for value in points[1::2]]
                polygons.append(points)
            offset += float(page.shape[0]) + self.PAGE_GAP
        self.__m_polygons = polygons
        return polygons

    def getMarkdown(self):
        """
        :return: the raw transcription of each page for the 'unirec' and
                 'doc' tasks (Markdown with LaTeX formulas and HTML
                 tables), which the line-based ParsedText flattens.
        """
        return self.__m_markdown

    def getPredictions(self):
        """
        :return: OpenOCR's own reply for each page, exactly as returned.
        """
        return self.__m_predictions

    def getPolygons(self):
        """
        :return: detector polygons kept by the last ``parse()``.
        """
        return self.__m_polygons

    def getPageSizes(self):
        """
        :return: list of (width, height) for every processed page.
        """
        return self.__m_pageSizes

    # ------------------------------------------------------------------ #
    # Getters / setters                                                  #
    # ------------------------------------------------------------------ #
    def setTask(self, task):
        self.__m_task = str(task).lower()
        self.setModel(self._describeModel())

    def getMode(self):
        """
        :return: str | unicode
        """
        return self.__m_mode

    def setMode(self, mode):
        self.__m_mode = str(mode).lower()
        self.setModel(self._describeModel())

    def getBackend(self):
        """
        :return: str | unicode
        """
        return self.__m_backend

    def setBackend(self, backend):
        """
        :param backend: str | unicode
        :return:
        """
        self.__m_backend = backend.lower()
        self.setModel(self._describeModel())

    def getUseGpu(self):
        """
        :return: str | unicode
        """
        return self.__m_useGpu

    def setUseGpu(self, useGpu):
        """
        :param useGpu: str | unicode
        """
        self.__m_useGpu = useGpu.lower()

    def getDropScore(self):
        """
        :return: float | int
        """
        return self.__m_dropScore

    def setDropScore(self, dropScore):
        """
        :param dropScore: float | int
        :return:
        """
        self.__m_dropScore = dropScore

    def getDetBoxType(self):
        """
        :return: str | unicode
        """
        return self.__m_detBoxType

    def setDetBoxType(self, detBoxType):
        """
        :param detBoxType: str | unicode
        :return:
        """
        self.__m_detBoxType = detBoxType.lower()

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
        self.__m_minConfidence = minConfidence

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

    def getRecBatchNum(self):
        """
        :return: int
        """
        return self.__m_recBatchNum

    def setRecBatchNum(self, batchNum):
        """
        :param batchNum: int
        :return:
        """
        self.__m_recBatchNum = int(batchNum)

    def isCropInfer(self):
        """
        :return: bool
        """
        return self.__m_cropInfer

    def setCropInfer(self, cropInfer):
        """
        :param cropInfer: bool
        :return:
        """
        self.__m_cropInfer = bool(cropInfer)

    def getMaxLength(self):
        """
        :return: int
        """
        return self.__m_maxLength

    def setMaxLength(self, maxLength):
        """
        :param maxLength: int
        :return:
        """
        self.__m_maxLength = int(maxLength)

    def getDetModelPath(self):
        """
        :return: str | unicode | None
        """
        return self.__m_detModelPath

    def setDetModelPath(self, p):
        """
        :param p: str | unicode | None
        :return:
        """
        self.__m_detModelPath = p

    def getRecModelPath(self):
        """
        :return: str | unicode | None
        """
        return self.__m_recModelPath

    def setRecModelPath(self, p):
        """
        :param p: str | unicode | None
        :return:
        """
        self.__m_recModelPath = p

    def getLayoutThreshold(self):
        """
        :return: float | int
        """
        return self.__m_layoutThreshold

    def setLayoutThreshold(self, threshold):
        """
        :param threshold: float | int
        :return:
        """
        self.__m_layoutThreshold = threshold

    def isUseLayoutDetection(self):
        """
        :return: bool
        """
        return self.__m_useLayoutDetection

    def setUseLayoutDetection(self, use):
        """
        :param use: bool
        :return:
        """
        self.__m_useLayoutDetection = bool(use)

    def getDocLabels(self):
        """
        :return: iterable object
        """
        return self.__m_docLabels

    def setDocLabels(self, labels):
        """
        :param labels: iterable object
        :return:
        """
        self.__m_docLabels = labels

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
        self.__m_arrayOrder = order.lower()

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
        self.__m_pdfScale = scale

    def getPages(self):
        """
        :return: list[int]
        """
        return self.__m_pages

    def setPages(self, pages):
        self.__m_pages = pages

    def getEngineParams(self):
        """
        :return: dict
        """
        return self.__m_engineParams

    def setEngineParams(self, params):
        """
        :param params: dict
        :return:
        """
        self.__m_engineParams = dict(params or {})

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

    task = property(fget=getTask, fset=setTask)
    mode = property(fget=getMode, fset=setMode)
    backend = property(fget=getBackend, fset=setBackend)
    useGpu = property(fget=getUseGpu, fset=setUseGpu)
    dropScore = property(fget=getDropScore, fset=setDropScore)
    detBoxType = property(fget=getDetBoxType, fset=setDetBoxType)
    minConfidence = property(fget=getMinConfidence, fset=setMinConfidence)
    wordBox = property(fget=isWordBox, fset=setWordBox)
    recBatchNum = property(fget=getRecBatchNum, fset=setRecBatchNum)
    cropInfer = property(fget=isCropInfer, fset=setCropInfer)
    maxLength = property(fget=getMaxLength, fset=setMaxLength)
    detModelPath = property(fget=getDetModelPath, fset=setDetModelPath)
    recModelPath = property(fget=getRecModelPath, fset=setRecModelPath)
    layoutThreshold = property(fget=getLayoutThreshold, fset=setLayoutThreshold)
    docLabels = property(fget=getDocLabels, fset=setDocLabels)
    arrayOrder = property(fget=getArrayOrder, fset=setArrayOrder)
    pdfScale = property(fget=getPdfScale, fset=setPdfScale)
    pages = property(fget=getPages, fset=setPages)
    engineParams = property(fget=getEngineParams, fset=setEngineParams)
    callParams = property(fget=getCallParams, fset=setCallParams)
    markdown = property(fget=getMarkdown)
    predictions = property(fget=getPredictions)
    polygons = property(fget=getPolygons)
    pageSizes = property(fget=getPageSizes)


__all__ = ['OpenOcr', 'TASKS', 'MODES', 'BACKENDS']
