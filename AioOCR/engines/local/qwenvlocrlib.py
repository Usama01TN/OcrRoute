# coding=utf-8
"""
Qwen-VL plugin — Qwen's OCR-capable vision models from Hugging Face.
    https://huggingface.co/Qwen        (Qwen3-VL, Qwen2.5-VL, Qwen2-VL)
The weights run locally through ``transformers``: the model is imported
and called in-process, with no server, no HTTP API, no subprocess and no
temporary file. Pages are decoded to PIL images in memory, and PDFs are
rasterized in RAM.
This is the local sibling of the QwenCloud plugin. The same family of
models, but here you own the weights — useful offline, for private
documents, or for fine-tunes of the Qwen-VL bases (``JackChew/Qwen2-VL-
2B-OCR``, ``syntheticbot/ocr-qwen`` and friends all load the same way).
Tasks (``task=``):
* ``boxes``   — grounded line-by-line OCR: text *and* coordinates.
* ``text``    — plain text transcription.
* ``markdown``/``html`` — document parsing; the QwenVL HTML form carries
  ``data-bbox`` attributes, which this plugin parses back into geometry.
* ``latex``, ``table``, ``formula`` — structured transcription.
* ``kv``      — JSON key/values, optionally driven by your ``schema=``.
Install::
    pip install "transformers>=4.57" accelerate torch torchvision pillow
Usage::
    from qwenvlocr import QwenVlOcr
    result = QwenVlOcr(image='demo.jpg').parse()                 # + boxes
    result = QwenVlOcr(image='demo.jpg', task='text').parse()
    result = QwenVlOcr(image='page.png', task='html').parse()    # data-bbox
    result = QwenVlOcr(image='scan.pdf', model='Qwen/Qwen3-VL-8B-Instruct').parse()
    result = QwenVlOcr(image='card.jpg', task='kv', schema={'name': '', 'id number': ''}).parse()
    plugin = QwenVlOcr(image='demo.jpg', device='cuda:0')
    result = plugin.parse()
    plugin.getPolygons()     # boxes the model grounded, in real pixels
    plugin.getRaw()          # what the model actually generated
Coordinate spaces differ by generation and the plugin handles both:
Qwen3-VL grounds on a normalized 0-1000 grid, while Qwen2-VL and
Qwen2.5-VL report absolute pixels of the *resized* image. The resized
size is read back from the processor's own ``image_grid_thw``, so the
mapping uses what the model actually saw rather than a guess.
"""
from numpy import ascontiguousarray, stack, clip, uint8, ndarray
from os.path import isfile, dirname
from json import dumps, loads
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

#: Tasks this plugin can ask for.
TASKS = ('boxes', 'text', 'markdown', 'html', 'latex', 'table', 'formula', 'kv')
#: Tasks whose reply is expected to carry geometry.
BOX_TASKS = ('boxes', 'html')
#: A few Qwen-VL repositories that work well for OCR, smallest first.
MODELS = (
    'Qwen/Qwen3-VL-2B-Instruct',
    'Qwen/Qwen3-VL-4B-Instruct',
    'Qwen/Qwen3-VL-8B-Instruct',
    'Qwen/Qwen2.5-VL-7B-Instruct',
)
#: Instructions sent to the model. ``qwenvl html`` and ``qwenvl markdown``
#: are the trigger phrases Qwen documents for document parsing; the rest
#: are plain requests, and ``prompt=`` replaces any of them.
TASK_PROMPTS = {
    'boxes':
        'Detect every line of text in the image. Return only a JSON array, '
        'one object per line in reading order, shaped '
        '[{"bbox_2d": [x1, y1, x2, y2], "text": "..."}]. '
        'No explanation, no code fence.',
    'text':
        'Output only the text content of the image, preserving reading '
        'order, with no description and no extra formatting.',
    'markdown': 'qwenvl markdown',
    'html': 'qwenvl html',
    'latex':
        'Transcribe the text, tables and equations in the image into '
        'LaTeX, without changing their content.',
    'table':
        'Convert every table in the image to HTML using <tr> and <td>, '
        'following the layout from top left to bottom right and '
        'reproducing merged cells. Output only the HTML.',
    'formula':
        'Output the LaTeX representation of the formula in the image, '
        'with no other text.',
    'kv':
        'Extract the key/value pairs from the image and output valid JSON '
        'only, with no explanation. Keep the output language the same as '
        'the image and use null where a value is missing.',
}
#: Model families and the coordinate space they ground in. Qwen3-VL
#: returns relative coordinates on a 0-1000 grid; Qwen2-VL and
#: Qwen2.5-VL return absolute pixels of the resized image.
NORMALIZED_FAMILIES = ('qwen3_vl', 'qwen3_vl_moe', 'qwen3_5_vl')
RESIZED_FAMILIES = ('qwen2_vl', 'qwen2_5_vl')


class QwenVlOcr(OCRPlugin):
    """
    Local Qwen-VL engine (Hugging Face ``transformers``).
    Weights are downloaded once by ``transformers`` into its own cache,
    then held in memory. Loaded models are cached process-wide, so
    several ``QwenVlOcr`` instances with the same settings share one
    copy of the weights.
    """
    #: Vertical gap inserted between stacked PDF pages, in pixels.
    PAGE_GAP = 20.0
    #: Rendering scale used for PDF pages (72 dpi * scale).
    PDF_SCALE = 2.0
    #: Fraction of a row's height left as spacing when text without
    #: coordinates is laid out as rows.
    ROW_PADDING = 0.15
    #: Process-wide cache: {key: (model, processor)}.
    _MODELS = {}

    def __init__(self, *args, **kwargs):
        """
        :param image: path | URL | base64/bytes | BytesIO | PIL image | numpy array | PDF (path or bytes).
        :param model: repo id or local directory of a Qwen-VL model.
        :param task: 'boxes' (default), 'text', 'markdown', 'html', 'latex', 'table', 'formula' or 'kv'.
        :param ocrPrompt: custom instruction, replacing the task prompt (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``.
        :param schema: dict of {field: hint} for the 'kv' task.
        :param system: optional system message.
        :param device: 'cuda:0', 'cpu', 'mps'; None uses deviceMap.
        :param deviceMap: 'auto' by default, passed to from_pretrained.
        :param dtype: 'auto' by default ('bfloat16', 'float16', ...).
        :param attnImplementation: e.g. 'flash_attention_2'.
        :param trustRemoteCode: allow custom model code from the repo.
        :param minPixels / maxPixels: processor resize thresholds.
        :param maxNewTokens: generation cap (2048).
        :param doSample: sampling off by default, for reproducible OCR.
        :param temperature / topP / topK / repetitionPenalty: sampling.
        :param wordBox: split each text line into word boxes.
        :param boxSpace: 'auto' (default), 'norm1000', 'resized' or 'image' — how to read the returned coordinates.
        :param arrayOrder: 'rgb' (default) or 'bgr' for numpy input.
        :param pdfScale: PDF rasterization scale (2.0 = 144 dpi).
        :param pages: optional list of 0-based PDF page indexes.
        :param modelParams: extra kwargs for from_pretrained.
        :param processorParams: extra kwargs for the processor.
        :param generateParams: extra kwargs for generate().
        :param cache: keep loaded models in the process cache.
        :param args: any
        :param kwargs: any
        """
        model = kwargs.pop('model', MODELS[0])
        self.__m_task = str(kwargs.pop('task', 'boxes')).lower()
        self.__m_prompt = kwargs.pop('ocrPrompt', '')  # 'prompt' stays for the base class
        self.__m_schema = kwargs.pop('schema', None)
        self.__m_system = kwargs.pop('system', '')
        self.__m_device = kwargs.pop('device', None)
        self.__m_deviceMap = kwargs.pop('deviceMap', 'auto')
        self.__m_dtype = kwargs.pop('dtype', 'auto')
        self.__m_attn = kwargs.pop('attnImplementation', None)
        self.__m_trustRemoteCode = bool(kwargs.pop('trustRemoteCode', False))
        self.__m_minPixels = kwargs.pop('minPixels', None)
        self.__m_maxPixels = kwargs.pop('maxPixels', None)
        self.__m_maxNewTokens = int(kwargs.pop('maxNewTokens', 2048))
        self.__m_doSample = bool(kwargs.pop('doSample', False))
        self.__m_temperature = kwargs.pop('temperature', None)
        self.__m_topP = kwargs.pop('topP', None)
        self.__m_topK = kwargs.pop('topK', None)
        self.__m_repetitionPenalty = kwargs.pop('repetitionPenalty', None)
        self.__m_wordBox = bool(kwargs.pop('wordBox', True))
        self.__m_boxSpace = str(kwargs.pop('boxSpace', 'auto')).lower()
        self.__m_arrayOrder = str(kwargs.pop('arrayOrder', 'rgb')).lower()
        self.__m_pdfScale = float(kwargs.pop('pdfScale', self.PDF_SCALE))
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_modelParams = dict(kwargs.pop('modelParams', {}))
        self.__m_processorParams = dict(kwargs.pop('processorParams', {}))
        self.__m_generateParams = dict(kwargs.pop('generateParams', {}))
        self.__m_cache = bool(kwargs.pop('cache', True))
        self.__m_raw = []
        self.__m_polygons = []
        self.__m_markdown = []
        self.__m_keyValues = {}
        self.__m_pageSizes = []
        self.__m_family = ''
        super(QwenVlOcr, self).__init__(*args, **kwargs)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine entry point                                                 #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        Run the model over the image and return flat word dicts.
        :param image: whatever ``getImage()`` holds.
        :return: list[dict]
        """
        task = self.getTask()
        pages = self._loadPages(image)
        if not pages:
            raise OCRError('No page could be decoded from the given image.')
        model, processor = self._load()
        self.__m_raw = []
        self.__m_polygons = []
        self.__m_markdown = []
        self.__m_keyValues = {}
        self.__m_pageSizes = [page.size for page in pages]
        words = []
        offset = 0.0
        for page in pages:
            text, resized = self._generate(model, processor, page)
            self.__m_raw.append(text)
            parsed = self._parse(text, task)
            words.extend(self._words(parsed, page, resized, offset))
            offset += float(page.size[1]) + self.PAGE_GAP
        return words

    def getTask(self):
        """
        :return: the validated task name.
        """
        task = self.__m_task
        if task not in TASKS:
            raise OCRError('Unknown task {!r}; expected one of {}.'.format(task, ', '.join(TASKS)))
        return task

    def prompt(self):
        """
        :return: the instruction sent to the model (custom or task prompt, plus the base class's extra prompt).
        """
        if self.__m_prompt:
            return self.composePrompt(self.__m_prompt)
        prompt = TASK_PROMPTS[self.getTask()]
        if self.getTask() == 'kv' and self.__m_schema:
            prompt += ' Fill in this JSON schema: {}'.format(dumps(self.__m_schema, ensure_ascii=False))
        return self.composePrompt(prompt)

    # ------------------------------------------------------------------ #
    # Model loading                                                      #
    # ------------------------------------------------------------------ #
    def _load(self):
        """
        Load (or fetch from the cache) the model and its processor.
        :return: (model, processor)
        """
        key = (self.getModel(), str(self.__m_dtype), str(self.__m_device),
               str(self.__m_deviceMap), str(self.__m_attn),
               str(self.__m_minPixels), str(self.__m_maxPixels))
        if self.__m_cache and key in QwenVlOcr._MODELS:
            model, processor = QwenVlOcr._MODELS[key]
            self.__m_family = self._family(model)
            return model, processor
        transformers = self._transformers()
        processorParams = {'trust_remote_code': self.__m_trustRemoteCode}
        if self.__m_minPixels is not None:
            processorParams['min_pixels'] = int(self.__m_minPixels)
        if self.__m_maxPixels is not None:
            processorParams['max_pixels'] = int(self.__m_maxPixels)
        processorParams.update(self.__m_processorParams)
        processor = transformers.AutoProcessor.from_pretrained(
            self.__m_model, **processorParams)
        modelParams = {'trust_remote_code': self.__m_trustRemoteCode}
        if self.__m_dtype is not None:
            modelParams['dtype'] = self.__m_dtype
        if self.__m_device is None:
            modelParams['device_map'] = self.__m_deviceMap
        if self.__m_attn:
            modelParams['attn_implementation'] = self.__m_attn
        modelParams.update(self.__m_modelParams)
        model = self._fromPretrained(transformers, modelParams)
        if self.__m_device is not None:
            model = model.to(self.__m_device)
        if hasattr(model, 'eval'):
            model.eval()
        self.__m_family = self._family(model)
        if self.__m_cache:
            QwenVlOcr._MODELS[key] = (model, processor)
        return model, processor

    def _fromPretrained(self, transformers, params):
        """
        Instantiate the model, preferring the generic auto class.
        ``AutoModelForImageTextToText`` covers every Qwen-VL generation on
        current transformers; the named classes are tried afterwards so
        that older installations, MoE checkpoints and community fine-tunes
        still load.
        """
        candidates = ['AutoModelForImageTextToText',
                      'Qwen3VLForConditionalGeneration',
                      'Qwen3VLMoeForConditionalGeneration',
                      'Qwen2_5_VLForConditionalGeneration',
                      'Qwen2VLForConditionalGeneration',
                      'AutoModelForVision2Seq']
        errors = []
        for name in candidates:
            factory = getattr(transformers, name, None)
            if factory is None:
                continue
            try:
                return factory.from_pretrained(self.getModel(), **params)
            except Exception as error:  # noqa: BLE001 - try the next class
                errors.append('{}: {}'.format(name, error))
        raise OCRError(
            'Could not load {!r}. Tried {}. Last errors: {}'.format(
                self.getModel(), ', '.join(candidates), ' | '.join(errors[-2:]) or 'none'))

    @staticmethod
    def _transformers():
        """
        Import transformers, with an actionable message if it is absent.
        """
        try:
            import transformers
        except ImportError:
            raise OCRError(
                'transformers is not installed. '
                'Install it with: pip install "transformers>=4.57" accelerate torch torchvision')
        return transformers

    @staticmethod
    def _family(model):
        """
        The model_type of a loaded model, e.g. 'qwen3_vl'.
        """
        return str(getattr(getattr(model, 'config', None), 'model_type', '') or '').lower()

    @classmethod
    def clearCache(cls):
        """
        Drop every cached model (frees the loaded weights).
        """
        cls._MODELS.clear()

    # ------------------------------------------------------------------ #
    # Generation                                                         #
    # ------------------------------------------------------------------ #
    def _generate(self, model, processor, page):
        """
        Run one page through the model.
        :return: (generated text, (resizedWidth, resizedHeight))
        """
        messages = self._messages(page)
        inputs = self._inputs(processor, messages, page)
        resized = self._resizedSize(processor, inputs)
        inputs = self._toDevice(model, inputs)
        params = {'max_new_tokens': self.__m_maxNewTokens, 'do_sample': self.__m_doSample}
        if self.__m_temperature is not None:
            params['temperature'] = float(self.__m_temperature)
        if self.__m_topP is not None:
            params['top_p'] = float(self.__m_topP)
        if self.__m_topK is not None:
            params['top_k'] = int(self.__m_topK)
        if self.__m_repetitionPenalty is not None:
            params['repetition_penalty'] = float(self.__m_repetitionPenalty)
        params.update(self.__m_generateParams)
        return self._decode(processor, inputs, self._noGrad(lambda: model.generate(**inputs, **params))), resized

    def _messages(self, page):
        """
        The chat messages for one page.
        """
        messages = []
        if self.__m_system:
            messages.append({'role': 'system', 'content': [{'type': 'text', 'text': self.__m_system}]})
        messages.append({'role': 'user', 'content': [
            {'type': 'image', 'image': page}, {'type': 'text', 'text': self.prompt()}]})
        return messages

    def _inputs(self, processor, messages, page):
        """
        Build the model inputs.
        The classic two-step path — render the chat template to text, then
        call the processor with the PIL image — works across every
        transformers version that ships Qwen-VL. Newer releases can do
        both in ``apply_chat_template``, which is used as the fallback.
        """
        try:
            text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            return processor(text=[text], images=[page], padding=True, return_tensors='pt')
        except Exception:  # noqa: BLE001 - fall back to the newer path
            return processor.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt')

    @staticmethod
    def _resizedSize(processor, inputs):
        """
        The size the processor actually resized the page to.
        ``image_grid_thw`` counts vision patches, so multiplying by the
        processor's patch size gives the pixel dimensions the model saw —
        which is the space Qwen2/2.5-VL report their coordinates in.
        :return: (width, height), or (0, 0) when unavailable.
        """
        try:
            grid = inputs['image_grid_thw']
        except Exception:  # noqa: BLE001 - not every processor returns it
            return 0, 0
        try:
            values = grid.tolist() if hasattr(grid, 'tolist') else list(grid)
            while values and isinstance(values[0], list):
                values = values[0]
            if len(values) < 3:
                return 0, 0
            patch = getattr(getattr(processor, 'image_processor', None), 'patch_size', 14) or 14
            if isinstance(patch, (list, tuple)):
                patch = patch[-1]
            return int(values[2]) * int(patch), int(values[1]) * int(patch)
        except Exception:  # noqa: BLE001 - geometry stays best-effort
            return 0, 0

    @staticmethod
    def _toDevice(model, inputs):
        """
        Move the inputs next to the model's parameters.
        """
        device = getattr(model, 'device', None)
        if device is None or not hasattr(inputs, 'to'):
            return inputs
        try:
            return inputs.to(device)
        except Exception:  # noqa: BLE001 - already placed by device_map
            return inputs

    @staticmethod
    def _noGrad(call):
        """
        Run *call* with gradients disabled when torch is present.
        """
        try:
            import torch
        except ImportError:
            return call()
        with torch.no_grad():
            return call()

    @staticmethod
    def _decode(processor, inputs, generated):
        """
        Decode only the newly generated tokens.
        """
        try:
            prompt = inputs['input_ids']
            trimmed = [output[len(source):] for source, output in zip(prompt, generated)]
        except Exception:  # noqa: BLE001 - decode everything instead
            trimmed = generated
        decoded = processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        return decoded[0] if decoded else ''

    # ------------------------------------------------------------------ #
    # Output parsing                                                     #
    # ------------------------------------------------------------------ #
    def _parse(self, text, task):
        """
        Turn the generated text into boxes, text and key/values.
        The ladder is deliberate: JSON grounding first, then QwenVL HTML
        ``data-bbox`` attributes, then the text itself. A model that
        ignores the JSON instruction still produces a usable result
        instead of an error.
        :return: dict with 'boxes', 'texts', 'text' and 'kv'.
        """
        text = self._stripFence(text)
        parsed = {'boxes': [], 'texts': [], 'text': text, 'kv': {}}
        if task in BOX_TASKS:
            for entry in self._jsonEntries(text):
                box = entry.get('bbox_2d') or entry.get('bbox') or entry.get('box') or entry.get('location')
                content = entry.get('text', entry.get('content', ''))
                if box is None or not str(content).strip():
                    continue
                parsed['boxes'].append(box)
                parsed['texts'].append(str(content))
            if not parsed['boxes'] and '<' in text:
                boxes, texts = self._htmlBoxes(text)
                parsed['boxes'], parsed['texts'] = boxes, texts
            if parsed['boxes']:
                return parsed
        if task == 'kv':
            decoded = self._loadJson(text)
            if isinstance(decoded, dict):
                parsed['kv'] = decoded
        return parsed

    @staticmethod
    def _stripFence(text):
        """
        Remove a ```json / ```html / ```latex fence, if present.
        """
        text = str(text or '').strip()
        if not text.startswith('```'):
            return text
        body = text[3:]
        newline = body.find('\n')
        if newline != -1 and ' ' not in body[:newline]:
            body = body[newline + 1:]
        if body.rstrip().endswith('```'):
            body = body.rstrip()[:-3]
        return body.strip()

    @classmethod
    def _loadJson(cls, text):
        """
        Decode the first JSON value in *text*, else None.
        """
        text = cls._stripFence(text)
        for opening, closing in (('[', ']'), ('{', '}')):
            start = text.find(opening)
            end = text.rfind(closing)
            if start == -1 or end <= start:
                continue
            try:
                return loads(text[start:end + 1])
            except ValueError:
                continue
        return None

    @classmethod
    def _jsonEntries(cls, text):
        """
        Decode a grounded reply into a flat list of line dicts.
        """
        decoded = cls._loadJson(text)
        if isinstance(decoded, dict):
            for key in ('lines', 'results', 'words_info', 'items'):
                if isinstance(decoded.get(key), list):
                    decoded = decoded[key]
                    break
            else:
                decoded = [decoded]
        if not isinstance(decoded, list):
            return []
        return [item for item in decoded if isinstance(item, dict)]

    @staticmethod
    def _htmlBoxes(html):
        """
        Pull ``data-bbox`` elements out of a QwenVL HTML document.

        :return: (boxes, texts)
        """
        from html.parser import HTMLParser
        from html import unescape

        class Collector(HTMLParser):
            """
            Collector class.
            """

            def __init__(self):
                HTMLParser.__init__(self, convert_charrefs=True)
                self.boxes = []
                self.texts = []
                self.stack = []

            def handle_starttag(self, tag, attrs):
                """
                :param tag: str | unicode
                :param attrs: list[tuple[str | unicode, str | unicode | None]]
                :return:
                """
                bx = dict(attrs).get('data-bbox')
                self.stack.append([bx, []] if bx else None)

            def handle_data(self, data):
                """
                :param data: str | unicode
                :return:
                """
                for frame in self.stack:
                    if frame is not None:
                        frame[1].append(data)

            def handle_endtag(self, tag):
                """
                :param tag: str | unicode
                :return:
                """
                while self.stack:
                    frame = self.stack.pop()
                    if frame is None:
                        return
                    txt = ' '.join(''.join(frame[1]).split())
                    if txt:
                        self.boxes.append(frame[0])
                        self.texts.append(unescape(txt))
                    return

        collector = Collector()
        try:
            collector.feed(html)
            collector.close()
        except Exception:  # noqa: BLE001 - malformed markup: keep what we got
            pass
        boxes, texts = [], []
        for box, text in zip(collector.boxes, collector.texts):
            values = str(box).replace(',', ' ').split()
            if len(values) < 4:
                continue
            try:
                boxes.append([float(value) for value in values[:4]])
            except ValueError:
                continue
            texts.append(text)
        return boxes, texts

    # ------------------------------------------------------------------ #
    # Parsed reply -> words                                              #
    # ------------------------------------------------------------------ #
    def _words(self, parsed, page, resized, offset=0.0):
        """
        Turn one page's parsed reply into word dicts.
        :param parsed: dict from ``_parse``.
        :param page: the PIL page it came from.
        :param resized: the size the processor fed the model.
        :param offset: vertical offset of that page.
        :return: list[dict]
        """
        width, height = float(page.size[0]), float(page.size[1])
        if parsed['kv']:
            self.__m_keyValues.update(parsed['kv'])
        if parsed['text']:
            self.__m_markdown.append(parsed['text'])
        if parsed['boxes']:
            scaleX, scaleY = self.boxScale(parsed['boxes'], width, height, resized)
            words = []
            for index, box in enumerate(parsed['boxes']):
                points = self._flatten(box)
                points[0::2] = [value * scaleX for value in points[0::2]]
                points[1::2] = [value * scaleY for value in points[1::2]]
                bounds = self._polygonToBox(points)
                text = parsed['texts'][index].strip() if index < len(parsed['texts']) else ''
                if bounds is None or not text:
                    continue
                left, top, boxWidth, boxHeight = bounds
                left = max(0.0, min(left, width))
                top = max(0.0, min(top, height))
                boxWidth = max(1.0, min(boxWidth, width - left))
                boxHeight = max(1.0, min(boxHeight, height - top))
                points[1::2] = [value + offset for value in points[1::2]]
                self.__m_polygons.append(points)
                top += offset
                if self.__m_wordBox:
                    words.extend(self._splitWords(text, left, top, boxWidth, boxHeight))
                else:
                    words.append(self.makeWord(text, left, top, boxWidth, boxHeight))
            return words

        text = parsed['text']
        if parsed['kv'] and not text:
            text = '\n'.join('{}: {}'.format(key, value) for key, value in parsed['kv'].items())
        if '<' in text and '>' in text:
            text = self._stripTags(text)
        return self._rowsFromText(text, 0.0, offset, width, height)

    def boxScale(self, boxes, width, height, resized=(0, 0)):
        """
        Factors mapping the model's coordinates onto real pixels.
        Qwen3-VL grounds on a normalized 0-1000 grid, while Qwen2-VL and
        Qwen2.5-VL report absolute pixels of the resized image — so
        ``'auto'`` picks by the loaded model's ``model_type`` rather than
        guessing from the numbers, and only falls back to fitting the
        boxes to the image when the family is unknown.
        :return: (scaleX, scaleY)
        """
        if not width or not height:
            return 1.0, 1.0
        space = self.__m_boxSpace
        if space == 'auto':
            space = self.boxSpaceFor(self.__m_family)
        if space == 'image':
            return 1.0, 1.0
        if space == 'norm1000':
            return width / 1000.0, height / 1000.0
        if space == 'resized':
            if resized and resized[0] and resized[1]:
                return width / float(resized[0]), height / float(resized[1])
            return 1.0, 1.0
        points = [value for box in boxes for value in self._flatten(box)]
        if not points:
            return 1.0, 1.0
        maxX, maxY = max(points[0::2]), max(points[1::2])
        if maxX <= width * 1.02 and maxY <= height * 1.02:
            return 1.0, 1.0
        overflow = max(maxX / width, maxY / height)
        return 1.0 / overflow, 1.0 / overflow

    @staticmethod
    def boxSpaceFor(family):
        """
        :return: the coordinate space a Qwen-VL family grounds in.
        """
        family = (family or '').lower()
        if family in NORMALIZED_FAMILIES:
            return 'norm1000'
        if family in RESIZED_FAMILIES:
            return 'resized'
        return 'fit'

    def _splitWords(self, text, left, top, width, height):
        """
        Split a line's text into word boxes.
        The model grounds whole lines, so per-word geometry is estimated
        by distributing the line's width over its characters.
        ``wordBox=False`` keeps one box per grounded line.
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
        Lay coordinate-free text out as evenly spaced rows.
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

    @staticmethod
    def _stripTags(html):
        """
        Flatten HTML markup to one text line per block element.
        """
        from html.parser import HTMLParser

        class Stripper(HTMLParser):
            """
            Stripper class.
            """

            def __init__(self):
                HTMLParser.__init__(self, convert_charrefs=True)
                self.parts = []

            def handle_data(self, data):
                """
                :param data: str | unicode
                """
                self.parts.append(data)

            def handle_starttag(self, tag, attrs):
                """
                :param tag: str | unicode
                :param attrs:  list[tuple[str, str | None]]
                :return:
                """
                if tag in ('tr', 'p', 'div', 'br', 'li', 'h1', 'h2', 'h3'):
                    self.parts.append('\n')
                elif tag in ('td', 'th'):
                    self.parts.append(' ')

        stripper = Stripper()
        try:
            stripper.feed(html)
            stripper.close()
        except Exception:  # noqa: BLE001 - keep whatever parsed
            pass
        lines = [' '.join(line.split()) for line in ''.join(stripper.parts).splitlines()]
        return '\n'.join(line for line in lines if line)

    # ------------------------------------------------------------------ #
    # Image handling (in memory only)                                    #
    # ------------------------------------------------------------------ #
    def _loadPages(self, image=None):
        """
        Decode the image source into a list of RGB PIL pages.
        :return: list[PIL.Image.Image]
        """
        from PIL import Image
        image = self.getImage() if image is None else image
        if image is None or (isinstance(image, str) and not image.strip()):
            raise OCRError('No image was given.')
        if isinstance(image, ndarray):
            return [self._fromArray(image)]
        if hasattr(image, 'convert') and hasattr(image, 'size'):
            return [image.convert('RGB')]
        data = self._sourceBytes(image)
        if data[:5] == b'%PDF-':
            return self._fromPdf(data)
        return [Image.open(BytesIO(data)).convert('RGB')]

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
            response = get(
                url, timeout=self.getTimeout(),
                proxies=self.getProxy() or None)
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
        except Exception:  # noqa: BLE001 - malformed input means "no"
            return None
        return data or None

    def _fromArray(self, array):
        """
        Convert a numpy array into an RGB PIL image.
        """
        from PIL.Image import fromarray
        array = ascontiguousarray(array)
        if array.ndim == 2:
            array = stack([array] * 3, axis=-1)
        elif array.ndim == 3 and array.shape[2] == 4:
            array = array[:, :, :3]
        elif array.ndim != 3 or array.shape[2] != 3:
            raise OCRError('Unsupported array shape {}.'.format(array.shape))
        if self.__m_arrayOrder == 'bgr':
            array = array[:, :, ::-1]
        if array.dtype != uint8:
            array = clip(array, 0, 255).astype(uint8)
        return fromarray(array)

    def _fromPdf(self, data):
        """
        Rasterize a PDF from memory into RGB pages.
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
            return [document[index].render(scale=self.__m_pdfScale).to_pil().convert('RGB') for index in indexes]
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
            if isinstance(value, (list, tuple)):
                points.extend(float(item) for item in value)
            else:
                points.append(float(value))
        return points

    @classmethod
    def _polygonToBox(cls, polygon):
        """
        Axis-aligned (left, top, width, height), or None.
        """
        try:
            points = cls._flatten(polygon)
        except (TypeError, ValueError):
            return None
        if len(points) == 4:
            left, top, right, bottom = points
            return min(left, right), min(top, bottom), abs(right - left), abs(bottom - top)
        if len(points) < 6 or len(points) % 2:
            return None
        xs, ys = points[0::2], points[1::2]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    # ------------------------------------------------------------------ #
    # Extra public helpers                                               #
    # ------------------------------------------------------------------ #
    def getRaw(self):
        """
        :return: what the model generated for each page, untouched.
        """
        return self.__m_raw

    def getPolygons(self):
        """
        :return: the boxes the model grounded, mapped to real pixels.
        """
        return self.__m_polygons

    def getKeyValues(self):
        """
        :return: the JSON object returned by the 'kv' task.
        """
        return self.__m_keyValues

    def getMarkdown(self):
        """
        :return: each page's transcription as generated
                    (Markdown, HTML or LaTeX), which the line-based ParsedText flattens.
        """
        return self.__m_markdown

    def getPageSizes(self):
        """
        :return: list of (width, height) for every processed page.
        """
        return self.__m_pageSizes

    def getFamily(self):
        """
        :return: the loaded model's family ('qwen3_vl', 'qwen2_5_vl', ...), which decides the default coordinate space.
        """
        return self.__m_family

    # ------------------------------------------------------------------ #
    # Getters / setters                                                  #
    # ------------------------------------------------------------------ #
    def setTask(self, task):
        """
        :param task: str | unicode
        :return:
        """
        self.__m_task = task.lower()

    def getPrompt(self):
        """
        :return: str | unicode
        """
        return self.__m_prompt

    def setPrompt(self, prompt):
        """
        :param prompt: str | unicode
        :return:
        """
        self.__m_prompt = prompt

    def getSchema(self):
        """
        :return: dict | None
        """
        return self.__m_schema

    def setSchema(self, schema):
        """
        :param schema: dict | None
        :return:
        """
        self.__m_schema = schema

    def getSystem(self):
        """
        :return: str | unicode
        """
        return self.__m_system

    def setSystem(self, system):
        """
        :param system: str | unicode
        :return:
        """
        self.__m_system = system

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

    def getDeviceMap(self):
        """
        :return: str | unicode
        """
        return self.__m_deviceMap

    def setDeviceMap(self, deviceMap):
        self.__m_deviceMap = deviceMap

    def getDtype(self):
        """
        :return: str | unicode
        """
        return self.__m_dtype

    def setDtype(self, dtype):
        """
        :param dtype: str | unicode
        :return:
        """
        self.__m_dtype = dtype

    def getMinPixels(self):
        """
        :return: int
        """
        return self.__m_minPixels

    def setMinPixels(self, minPixels):
        self.__m_minPixels = minPixels

    def getMaxPixels(self):
        """
        :return: int
        """
        return self.__m_maxPixels

    def setMaxPixels(self, maxPixels):
        """
        :param maxPixels: int
        :return:
        """
        self.__m_maxPixels = maxPixels

    def getMaxNewTokens(self):
        """
        :return: int
        """
        return self.__m_maxNewTokens

    def setMaxNewTokens(self, maxNewTokens):
        """
        :param maxNewTokens: int
        :return:
        """
        self.__m_maxNewTokens = int(maxNewTokens)

    def isDoSample(self):
        """
        :return: bool
        """
        return self.__m_doSample

    def setDoSample(self, doSample):
        """
        :param doSample: bool
        :return:
        """
        self.__m_doSample = bool(doSample)

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

    def getBoxSpace(self):
        """
        :return: str | unicode
        """
        return self.__m_boxSpace

    def setBoxSpace(self, boxSpace):
        """
        :param boxSpace: str | unicode
        :return:
        """
        self.__m_boxSpace = boxSpace.lower()

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
        :return: list[int] | None
        """
        return self.__m_pages

    def setPages(self, pages):
        """
        :param pages: list[int] | None
        :return:
        """
        self.__m_pages = pages

    def getModelParams(self):
        """
        :return: dict
        """
        return self.__m_modelParams

    def setModelParams(self, params):
        """
        :param params: dict
        :return:
        """
        self.__m_modelParams = dict(params or {})

    def getProcessorParams(self):
        """
        :return: dict
        """
        return self.__m_processorParams

    def setProcessorParams(self, params):
        """
        :param params: dict
        :return:
        """
        self.__m_processorParams = dict(params or {})

    def getGenerateParams(self):
        """
        :return: dict
        """
        return self.__m_generateParams

    def setGenerateParams(self, params):
        """
        :param params: a dictionary that maps parameter names to their values.
        :return:
        """
        self.__m_generateParams = dict(params or {})

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
    schema = property(fget=getSchema, fset=setSchema)
    system = property(fget=getSystem, fset=setSystem)
    device = property(fget=getDevice, fset=setDevice)
    deviceMap = property(fget=getDeviceMap, fset=setDeviceMap)
    dtype = property(fget=getDtype, fset=setDtype)
    minPixels = property(fget=getMinPixels, fset=setMinPixels)
    maxPixels = property(fget=getMaxPixels, fset=setMaxPixels)
    maxNewTokens = property(fget=getMaxNewTokens, fset=setMaxNewTokens)
    doSample = property(fget=isDoSample, fset=setDoSample)
    wordBox = property(fget=isWordBox, fset=setWordBox)
    boxSpace = property(fget=getBoxSpace, fset=setBoxSpace)
    arrayOrder = property(fget=getArrayOrder, fset=setArrayOrder)
    pdfScale = property(fget=getPdfScale, fset=setPdfScale)
    pages = property(fget=getPages, fset=setPages)
    modelParams = property(fget=getModelParams, fset=setModelParams)
    processorParams = property(fget=getProcessorParams, fset=setProcessorParams)
    generateParams = property(fget=getGenerateParams, fset=setGenerateParams)
    raw = property(fget=getRaw)
    polygons = property(fget=getPolygons)
    keyValues = property(fget=getKeyValues)
    markdown = property(fget=getMarkdown)
    pageSizes = property(fget=getPageSizes)
    family = property(fget=getFamily)


#: Alias for callers who think of these as the Qwen OCR models.
QwenOcrHf = QwenVlOcr
__all__ = ['QwenVlOcr', 'QwenOcrHf', 'TASKS', 'MODELS', 'TASK_PROMPTS']
