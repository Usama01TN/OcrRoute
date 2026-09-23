# coding=utf-8
"""
HunyuanOCR plugin -- Hugging Face edition
(https://huggingface.co/tencent/HunyuanOCR).
HunyuanOCR is Tencent's end-to-end OCR-specialist VLM: a remarkably
small 1B-parameter model unifying DOCUMENT PARSING, TEXT SPOTTING,
INFORMATION EXTRACTION and TEXT-IMAGE TRANSLATION. It reaches 70.92%
overall across scenarios -- beating traditional OCR stacks and much
larger general VLMs -- leads multilingual document parsing on edit
distance, and matches Qwen3-VL-235B on photo translation at a
fraction of the size. Tencent Hunyuan Community License.
THIS IS THE SELF-CONTAINED HUGGING FACE EDITION: it needs nothing but
``transformers`` and the weights from the Hub. No GitHub clone, no
vLLM, no server, no API calls, no subprocess, and no temporary files.
(The sibling ``hunyuanocr.py`` plugin adds the repository's own
modules -- its loader, streaming early-stop and doc_parse markdown
normalization -- plus an OpenAI-compatible server path; use that one
when you have the clone.)
Install::
    pip install "transformers>=5.13.0" torch accelerate
    # optional, faster attention:
    #   pip install flash-attn --no-build-isolation
    #   then pass attnImplementation='flash_attention_2'
Usage::
    from hunyuanocrhf import HunyuanOcrHf
    result = HunyuanOcrHf(image='document.png').parse()
    result = HunyuanOcrHf(image='photo.jpg', task='spotting_json').parse()
    result = HunyuanOcrHf(image='table.png', task='table').parse()
    result = HunyuanOcrHf(image='card.jpg', prompt='Extract the ID number.').parse()
The model card's transformers recipe is followed exactly:
``AutoProcessor.from_pretrained(..., trust_remote_code=True,
use_fast=False)`` and ``HunYuanVLForConditionalGeneration`` in
bfloat16 with ``device_map='auto'``, the chat template called with
``add_generation_prompt=True, tokenize=True, return_dict=True,
return_tensors='pt'``, greedy decoding, and the prompt tokens trimmed
off before decoding. Sampling matches the model's published settings
(greedy, repetition_penalty 1.08).
``method='pipeline'`` uses the card's other snippet -- the
``image-text-to-text`` pipeline -- instead of the manual path.
MODEL VERSION: ``version='1.5'`` (default, repo root) or
``version='1.0'`` (the archived checkpoint, loaded with
``subfolder='v1.0'``). ``model='...'`` accepts a local weights
directory so nothing is fetched at run time.
TASKS (``task=``) carry the official prompt wording for each job:
    doc_parse (default), structured_parse, spotting_json,
    spotting_hunyuan, layout, layout_parse, chart_parse, formula,
    table, doc_trans_en2zh, trans_other2en, trans_other2zh
with the aliases 'spotting', 'text', 'chart', 'trans_zh', 'trans_en'.
``prompt=`` overrides them, though the model's authors deliberately
ship fixed per-task prompts because hand-edited instructions were
observed to degrade quality.
GEOMETRY: ``task='spotting_json'`` asks for a JSON array of
``{"box": [xmin, ymin, xmax, ymax], "text": ...}`` with coordinates
normalized to [0, 1000], which the plugin scales to REAL PIXELS
(word/line tier). Every other task returns text, mapped to ordered
rows. Both land in the exact unified structure shared by every plugin
(like OCR.Space).
"""
from os.path import dirname
from requests import get
from json import loads
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

_DEFAULT_MODEL = 'tencent/HunyuanOCR'
#: The official per-task prompts, verbatim.
_TASK_PROMPTS = {
    'doc_parse': (
        '\u63d0\u53d6\u6587\u6863\u56fe\u7247\u4e2d\u6b63\u6587\u7684'
        '\u6240\u6709\u4fe1\u606f\u7528markdown\u683c\u5f0f\u8868\u793a'
        '\uff0c\u5176\u4e2d\u9875\u7709\u3001\u9875\u811a\u90e8\u5206'
        '\u5ffd\u7565\uff0c\u8868\u683c\u7528html\u683c\u5f0f\u8868'
        '\u8fbe\uff0c\u6587\u6863\u4e2d\u516c\u5f0f\u7528latex\u683c'
        '\u5f0f\u8868\u793a\uff0c\u6309\u7167\u9605\u8bfb\u987a\u5e8f'
        '\u7ec4\u7ec7\u8fdb\u884c\u89e3\u6790\u3002'),
    'structured_parse': (
        '\u63d0\u53d6\u56fe\u4e2d\u7684\u6587\u5b57\u3002'),
    'spotting_json': (
        '\u68c0\u6d4b\u5e76\u8bc6\u522b\u56fe\u4e2d\u6240\u6709\u7684'
        '\u6587\u5b57\u884c\uff0c\u8bf7\u6309\u4ece\u4e0a\u5230\u4e0b'
        '\u3001\u4ece\u5de6\u5230\u53f3\u7684\u9605\u8bfb\u987a\u5e8f'
        '\u8fdb\u884c\u8bc6\u522b\u3002 \u8f93\u51fa\u683c\u5f0f\u4e3a '
        'JSON \u6570\u7ec4\uff0c\u6bcf\u4e2a\u5143\u7d20\u5fc5\u987b'
        '\u5305\u542b\uff1a"box": [xmin, ymin, xmax, ymax]\uff08\u5750'
        '\u6807\u9700\u5f52\u4e00\u5316\u5230 [0, 1000] \u8303\u56f4'
        '\u5185\uff09\uff1b"text": "\u8bc6\u522b\u51fa\u7684\u6587\u5b57'
        '\u5185\u5bb9"\u3002 \u6ce8\u610f\uff1a\u8bf7\u76f4\u63a5\u8f93'
        '\u51fa JSON \u6570\u7ec4\uff0c\u4e0d\u8981\u5305\u542b\u4efb'
        '\u4f55\u591a\u4f59\u7684\u63cf\u8ff0\u6027\u6587\u5b57\u3002'),
    'spotting_hunyuan': (
        '\u68c0\u6d4b\u5e76\u8bc6\u522b\u56fe\u7247\u4e2d\u7684\u6587'
        '\u5b57\uff0c\u5c06\u6587\u672c\u5750\u6807\u683c\u5f0f\u5316'
        '\u8f93\u51fa\u3002'),
    'layout': (
        '\u6309\u7167\u9605\u8bfb\u987a\u5e8f\u89e3\u6790\u56fe\u4e2d'
        '\u7684\u7248\u5f0f\u4fe1\u606f\u3002'),
    'layout_parse': (
        '\u63d0\u53d6\u6587\u6863\u56fe\u7247\u4e2d\u6240\u6709\u5185'
        '\u5bb9\u7528markdown\u683c\u5f0f\u8868\u793a\uff0c\u8868\u683c'
        '\u7528html\u683c\u5f0f\u8868\u8fbe\uff0c\u6587\u6863\u4e2d'
        '\u516c\u5f0f\u7528latex\u683c\u5f0f\u8868\u793a\uff0c\u8bf7'
        '\u6309\u7167\u9605\u8bfb\u987a\u5e8f\u7ec4\u7ec7\u8fdb\u884c'
        '\u5168\u6587\u89e3\u6790\uff0c\u5e76\u8f93\u51fa\u7248\u5f0f'
        '\u5206\u6790\u4fe1\u606f\u3002'),
    'chart_parse': (
        '\u89e3\u6790\u56fe\u4e2d\u7684\u56fe\u8868\uff0c\u5bf9\u4e8e'
        '\u6d41\u7a0b\u56fe\u4f7f\u7528Mermaid\u683c\u5f0f\u8868\u793a'
        '\uff0c\u5176\u4ed6\u56fe\u8868\u4f7f\u7528Markdown\u683c\u5f0f'
        '\u8868\u793a\u3002'),
    'formula': (
        '\u8bc6\u522b\u56fe\u7247\u4e2d\u7684\u516c\u5f0f\uff0c\u7528'
        'LaTeX\u683c\u5f0f\u8868\u793a\u3002'),
    'table': (
        '\u628a\u56fe\u4e2d\u7684\u8868\u683c\u89e3\u6790\u4e3aHTML\u3002'),
    'doc_trans_en2zh': (
        '\u5148\u89e3\u6790\u6587\u6863\uff0c\u518d\u5c06\u6587\u6863'
        '\u5185\u5bb9\u7ffb\u8bd1\u4e3a\u4e2d\u6587\uff0c\u5176\u4e2d'
        '\u9875\u7709\u3001\u9875\u811a\u5ffd\u7565\uff0c\u516c\u5f0f'
        '\u7528latex\u683c\u5f0f\u8868\u793a\uff0c\u8868\u683c\u7528'
        'html\u683c\u5f0f\u8868\u793a\u3002'),
    'trans_other2en': (
        '\u6309\u7167\u9605\u8bfb\u987a\u5e8f\uff0c\u63d0\u53d6\u56fe'
        '\u4e2d\u6587\u5b57\uff0c\u516c\u5f0f\u7528latex\u683c\u5f0f'
        '\u8868\u793a\uff0c\u8868\u683c\u7528markdown\u683c\u5f0f\u8868'
        '\u793a\uff0c\u518d\u5c06\u6587\u5b57\u5185\u5bb9\u7ffb\u8bd1'
        '\u4e3a\u82f1\u6587\u3002'),
    'trans_other2zh': (
        '\u6309\u7167\u9605\u8bfb\u987a\u5e8f\uff0c\u63d0\u53d6\u56fe'
        '\u4e2d\u6587\u5b57\uff0c\u516c\u5f0f\u7528latex\u683c\u5f0f'
        '\u8868\u793a\uff0c\u8868\u683c\u7528markdown\u683c\u5f0f\u8868'
        '\u793a\uff0c\u518d\u5c06\u6587\u5b57\u5185\u5bb9\u7ffb\u8bd1'
        '\u4e3a\u4e2d\u6587\u3002'),
}
#: Convenience spellings.
_TASK_ALIASES = {
    'spotting': 'spotting_json',
    'text': 'structured_parse',
    'parse': 'doc_parse',
    'chart': 'chart_parse',
    'trans_zh': 'trans_other2zh',
    'trans_en': 'trans_other2en',
    'doc_trans': 'doc_trans_en2zh',
}
#: The model's published sampling settings.
_REPETITION_PENALTY = 1.08
#: spotting_json coordinates are normalized to this grid.
_SPOTTING_GRID = 1000.0
_METHODS = ('generate', 'pipeline')
_COORD_SPACES = ('auto', 'pixel', 'norm1', 'norm1000')


class HunyuanOcrHf(OCRPlugin):
    """
    HunyuanOcrHf class.
    """
    #: engine key -> (processor, model) or pipeline, loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: Hub id or local weights directory (default 'tencent/HunyuanOCR').
        :param version: '1.5' (default) or '1.0' (v1.0 subfolder).
        :param task: official task type (default 'doc_parse').
        :param ocrPrompt: custom prompt overriding the task (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
        :param method: 'generate' (default, the card's manual recipe) or 'pipeline' (the card's pipeline snippet).
        :param maxTokens: generation budget (default 8000, as in the card's example).
        :param attnImplementation: e.g. 'flash_attention_2' or 'eager' (default: the model's own).
        :param coordSpace: 'auto' (default), 'pixel', 'norm1' or 'norm1000' for spotting boxes.
        :param device: device map override ('cuda', 'cpu', 'auto').
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (timeout, retries...).
        """
        model = kwargs.pop('model', _DEFAULT_MODEL)
        version = kwargs.pop('version', '1.5')
        if version not in ('1.5', '1.0'):
            raise OCRError("version must be '1.5' or '1.0', not {!r}".format(version))
        self.__m_version = version
        task = str(kwargs.pop('task', 'doc_parse')).lower()
        task = _TASK_ALIASES.get(task, task)
        if task not in _TASK_PROMPTS:
            raise OCRError('task must be one of {}, not {!r}'.format(', '.join(sorted(_TASK_PROMPTS)), task))
        self.__m_task = task
        self.__m_prompt = kwargs.pop('ocrPrompt', None)  # 'prompt' stays for the base class
        method = kwargs.pop('method', 'generate').lower()
        if method not in _METHODS:
            raise OCRError("method must be 'generate' or 'pipeline', not {!r}".format(method))
        self.__m_method = method
        self.__m_max_tokens = int(kwargs.pop('maxTokens', 8000))
        self.__m_attn = kwargs.pop('attnImplementation', None)
        space = kwargs.pop('coordSpace', 'auto').lower()
        if space not in _COORD_SPACES:
            raise OCRError('coordSpace must be one of {}, not {!r}'.format(', '.join(_COORD_SPACES), space))
        self.__m_coord_space = space
        self.__m_device = kwargs.pop('device', None)
        super(HunyuanOcrHf, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Prompts                                                            #
    # ------------------------------------------------------------------ #
    def getPrompt(self):
        """
        :return: the prompt that will be sent (custom or task prompt, plus the base class's extra prompt).
        """
        return self.composePrompt(self.__m_prompt or _TASK_PROMPTS[self.__m_task])

    @staticmethod
    def listTasks():
        """
        :return: the official task names.
        """
        return sorted(_TASK_PROMPTS)

    # ------------------------------------------------------------------ #
    # Model loading (the card's recipe)                                  #
    # ------------------------------------------------------------------ #
    def _options(self):
        options = {'trust_remote_code': True}
        if self.__m_version == '1.0':
            options['subfolder'] = 'v1.0'
        return options

    def _engine(self):
        key = (self.getModel(), self.__m_version, self.__m_device,
               self.__m_attn, self.__m_method)
        if key in HunyuanOcrHf._engines:
            return HunyuanOcrHf._engines[key]
        try:
            from torch import bfloat16
        except ImportError:
            raise OCRError('HunyuanOCR needs PyTorch: pip install torch')
        if self.__m_method == 'pipeline':
            try:
                from transformers import pipeline
            except ImportError:
                raise OCRError('HunyuanOCR needs transformers: pip install "transformers>=5.13.0" torch accelerate')
            options = self._options()
            if self.__m_attn:
                options['attn_implementation'] = self.__m_attn
            try:
                engine = pipeline('image-text-to-text', model=self.getModel(), torch_dtype=bfloat16,
                                  device_map=self.__m_device or 'auto', model_kwargs=options)
            except Exception as exc:  # noqa: BLE001 - load failure
                raise self._loadError(exc)
            HunyuanOcrHf._engines[key] = engine
            return engine
        try:
            from transformers import AutoProcessor
        except ImportError:
            raise OCRError('HunyuanOCR needs transformers: pip install "transformers>=5.13.0" torch accelerate')
        try:
            from transformers import HunYuanVLForConditionalGeneration as ModelClass
        except ImportError:
            raise OCRError(
                'This transformers build has no '
                'HunYuanVLForConditionalGeneration -- the HunyuanOCR '
                'integration needs transformers 5.13.0 or newer: pip install -U "transformers>=5.13.0"')
        options = self._options()
        try:
            processor = AutoProcessor.from_pretrained(self.getModel(), use_fast=False, **options)
            model_options = dict(options)
            if self.__m_attn:
                model_options['attn_implementation'] = self.__m_attn
            model = ModelClass.from_pretrained(self.getModel(), torch_dtype=bfloat16,
                                               device_map=self.__m_device or 'auto', **model_options).eval()
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - load failure
            raise self._loadError(exc)
        HunyuanOcrHf._engines[key] = (processor, model)
        return HunyuanOcrHf._engines[key]

    def _loadError(self, exc):
        message = str(exc)
        if 'CUDA' in message or 'out of memory' in message.lower():
            return OCRError(
                'HunyuanOCR ran out of GPU memory loading the model '
                "(unusual for 1B): try device='cpu'. Original: " + message[:180])
        return OCRError(
            "Could not load '{}' ({}): {}. Check the model id or "
            'local path, and that the weights finished downloading.'.format(
                self.getModel(), type(exc).__name__, message[:180]))

    # ------------------------------------------------------------------ #
    # Input handling (in memory: no temporary files)                     #
    # ------------------------------------------------------------------ #
    def _imageAndSize(self):
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'pil':
            image = self.getImage()
            image.load()
            image = image.convert('RGB')
            return image, image.size
        if kind == 'array':
            from numpy import asarray
            image = Image.fromarray(asarray(self.getImage())).convert('RGB')
            return image, image.size
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
            raise OCRError(
                'HunyuanOCR takes images, not PDFs. Rasterize the '
                'pages first, or use a PDF-capable plugin (QianfanOcr, MistralOcr, NougatLib).')
        image = Image.open(BytesIO(data))
        image.load()
        image = image.convert('RGB')
        return image, image.size

    # ------------------------------------------------------------------ #
    # Inference                                                          #
    # ------------------------------------------------------------------ #
    def _messages(self, image):
        return [{'role': 'user', 'content': [{'type': 'image', 'image': image},
                                             {'type': 'text', 'text': self.getPrompt()}]}]

    def _generate(self, image):
        """
        The card's manual transformers' recipe.
        """
        from torch import inference_mode
        processor, model = self._engine()
        inputs = processor.apply_chat_template(
            self._messages(image), add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors='pt')
        inputs = inputs.to(model.device)
        with inference_mode():
            outputs = model.generate(
                **inputs, max_new_tokens=self.__m_max_tokens, do_sample=False, repetition_penalty=_REPETITION_PENALTY)
        generated = outputs[:, inputs['input_ids'].shape[1]:]
        decoded = processor.batch_decode(generated, skip_special_tokens=True)
        return (decoded[0] if decoded else '') or ''

    @staticmethod
    def _fromPipeline(result):
        """
        Pull the assistant text out of a pipeline result.
        """
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            if not result:
                return ''
            first = result[0]
            if isinstance(first, dict):
                text = first.get('generated_text')
                if isinstance(text, str):
                    return text
                if isinstance(text, list) and text:
                    last = text[-1]
                    if isinstance(last, dict):
                        content = last.get('content')
                        if isinstance(content, str):
                            return content
                        if isinstance(content, list):
                            return ''.join(
                                part.get('text', '')
                                for part in content
                                if isinstance(part, dict))
                    return str(last)
                return str(first)
            return str(first)
        return str(result or '')

    def _pipeline(self, image):
        """
        The card's image-text-to-text pipeline snippet.
        """
        engine = self._engine()
        result = engine(text=self._messages(image), max_new_tokens=self.__m_max_tokens, return_full_text=False)
        return self._fromPipeline(result)

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
        for line in cls._htmlToText(text or '').splitlines():
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

    @staticmethod
    def _boxPoints(entry):
        for key in ('box', 'bbox', 'points', 'quad', 'polygon', 'coordinates', 'coords'):
            value = entry.get(key)
            if not value:
                continue
            flat = []
            for item in value:
                if isinstance(item, (list, tuple)):
                    flat.extend(item)
                else:
                    flat.append(item)
            try:
                numbers = [float(number) for number in flat]
            except (TypeError, ValueError):
                continue
            if len(numbers) == 4:
                x1, y1, x2, y2 = numbers
                return (min(x1, x2), min(y1, y2),
                        max(x1, x2), max(y1, y2))
            if len(numbers) >= 8 and len(numbers) % 2 == 0:
                xs, ys = numbers[0::2], numbers[1::2]
                return min(xs), min(ys), max(xs), max(ys)
        return None

    def _scale(self, boxes, size):
        width, height = size
        space = self.__m_coord_space
        if space == 'auto':
            largest = max((max(box) for box in boxes), default=0.0)
            if largest <= 1.5:
                space = 'norm1'
            elif self.__m_task in ('spotting_json', 'spotting_hunyuan'):
                space = 'norm1000' if largest <= 1000.0 else 'pixel'
            elif largest <= 1000.0 and max(width, height) > 1200:
                space = 'norm1000'
            else:
                space = 'pixel'
        if space == 'norm1':
            return width, height
        if space == 'norm1000':
            return width / _SPOTTING_GRID, height / _SPOTTING_GRID
        return 1.0, 1.0

    def _spottingWords(self, text, size):
        cleaned = sub(r'```(?:json)?|```', '', text or '').strip()
        start, end = cleaned.find('['), cleaned.rfind(']')
        if start < 0 or end <= start:
            return []
        try:
            items = loads(cleaned[start:end + 1])
        except ValueError:
            return []
        entries = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = ''
            for key in ('text', 'content', 'label', 'transcription'):
                if item.get(key):
                    label = str(item[key]).strip()
                    break
            if not label:
                continue
            entries.append((label, self._boxPoints(item)))
        boxes = [box for _, box in entries if box]
        if not boxes:
            return self._toRows('\n'.join(label for label, _ in entries))
        scaleX, scaleY = self._scale(boxes, size)
        words, row = [], 0
        for label, box in entries:
            if not box:
                words.append(self.makeWord(label, 0.0, float(row * 10), 1.0, 8.0))
                row += 1
                continue
            x1, y1, x2, y2 = box
            words.append(self.makeWord(
                label, x1 * scaleX, y1 * scaleY, max((x2 - x1) * scaleX, 1.0), max((y2 - y1) * scaleY, 1.0)))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                (REAL pixel boxes for the spotting tasks; ordered rows otherwise).
        """
        page, size = self._imageAndSize()
        try:
            if self.__m_method == 'pipeline':
                output = self._pipeline(page)
            else:
                output = self._generate(page)
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/CUDA
            message = str(exc)
            if 'CUDA' in message or 'out of memory' in message.lower():
                raise OCRError(
                    'HunyuanOCR ran out of GPU memory (unusual for a '
                    "1B model): lower maxTokens or use device='cpu'. Original: " + message[:200])
            raise OCRError('HunyuanOCR inference failed: {}: {}'.format(type(exc).__name__, message[:220]))
        if self.__m_task in ('spotting_json', 'spotting_hunyuan'):
            words = self._spottingWords(output, size) or self._toRows(output)
        else:
            words = self._toRows(output)
        if not words:
            raise OCRError("HunyuanOCR returned no text for this image (task '{}').".format(self.__m_task))
        return words
