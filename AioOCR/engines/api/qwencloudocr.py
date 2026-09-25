# coding=utf-8
"""
QwenCloud OCR plugin — Alibaba's Qwen-OCR models as an ``OCRPlugin``.
    https://www.qwencloud.com/        (docs: https://docs.qwencloud.com/)
QwenCloud exposes the Qwen models through DashScope. This plugin drives
the **Qwen-OCR** family (``qwen-vl-ocr``, ``qwen3.5-ocr``), which are
purpose-built text-extraction models rather than general chat VLMs, and
it uses the **DashScope multimodal endpoint** by default because that is
the only interface where the built-in OCR tasks — and their coordinates —
are exposed.
Why that matters for this suite: the ``advanced_recognition`` task
returns an ``ocr_result.words_info`` array where every text line carries
``location`` (four absolute vertices) and ``rotate_rect``
(cx, cy, w, h, angle). So unlike the VLM plugins whose boxes are prompt
estimates, this one gets **model-reported geometry** back from the API.
Built-in tasks (``task=``):
* ``advanced_recognition`` — text + line coordinates (the default).
* ``text_recognition``     — plain text.
* ``multi_lan``            — non-CJK/English languages.
* ``table_parsing``        — HTML tables.
* ``document_parsing``     — LaTeX document transcription.
* ``formula_recognition``  — LaTeX formulas.
* ``key_information_extraction`` — JSON key/values, optionally driven by
  your own ``schema=``.
Setup::
    export DASHSCOPE_API_KEY=sk-...        # home.qwencloud.com/api-keys
Usage::
    from qwencloudocr import QwenCloudOcr
    result = QwenCloudOcr(image='demo.jpg').parse()          # text + boxes
    result = QwenCloudOcr(image='doc.png', task='document_parsing').parse()   # LaTeX
    result = QwenCloudOcr(image='table.png',
                          task='table_parsing').parse()      # HTML
    result = QwenCloudOcr(image='ticket.jpg', task='key_information_extraction',
                          schema={'Invoice Number': 'the invoice number', 'Fare': 'the ticket price'}).parse()
    result = QwenCloudOcr(image='scan.pdf').parse()          # pages
    result = QwenCloudOcr(image='https://.../x.png', region='cn').parse()               # Beijing
    plugin = QwenCloudOcr(image='demo.jpg')
    result = plugin.parse()
    plugin.getPolygons()      # the four-vertex boxes the model returned
    plugin.getKeyValues()     # kv_result for the extraction task
    plugin.getMarkdown()      # raw HTML/LaTeX for the parsing tasks
"""
from os.path import isfile, dirname
from requests import get, post
from json import dumps, loads
from os import environ
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


#: Built-in OCR tasks, exactly as the API names them.
TASKS = (
    'advanced_recognition',
    'text_recognition',
    'multi_lan',
    'table_parsing',
    'document_parsing',
    'formula_recognition',
    'key_information_extraction',
)
#: Tasks whose reply carries per-line coordinates.
BOX_TASKS = ('advanced_recognition',)
#: Qwen-OCR models, newest first. Used as the fallback ladder when the
#: configured model is rejected by the account or region.
MODELS = (
    'qwen-vl-ocr-latest',
    'qwen3.5-ocr',
    'qwen-vl-ocr',
    'qwen-vl-ocr-2025-11-20',
    'qwen-vl-ocr-2025-08-28',
)
#: Environment variables searched for the API key, in order.
API_KEY_VARIABLES = ('DASHSCOPE_API_KEY', 'QWENCLOUD_API_KEY', 'QWEN_API_KEY')
#: Prompts for the OpenAI-compatible interface, which does not expose
#: ``ocr_options``: there the task instruction has to travel in the
#: message itself. These mirror the task descriptions in QwenCloud's
#: documentation; pass ``ocrPrompt=`` to send your own wording instead
#: (``prompt=`` APPENDS extra instructions, see the base class).
TASK_PROMPTS = {
    'text_recognition':
        'Please output only the text content from the image without any '
        'additional descriptions or formatting.',
    'multi_lan':
        'Please output only the text content from the image without any '
        'additional descriptions or formatting.',
    'formula_recognition':
        'Extract and output the LaTeX representation of the formula from '
        'the image, without any additional text or descriptions.',
    'table_parsing':
        'Convert every table in the image to HTML, transcribing each one '
        'with <tr> and <td> tags in the order they appear from top left '
        'to bottom right, and reproducing merged cells accurately.',
    'document_parsing':
        'Transcribe the text, tables and equations in the image into '
        'LaTeX, without modifying their content.',
    'advanced_recognition':
        'Read every text line in the image and return one JSON object per '
        'line as [{"text": ..., "location": [x1, y1, x2, y2, x3, y3, x4, '
        'y4]}], where the vertices run top-left, top-right, bottom-right, '
        'bottom-left in pixels of the original image. Output only JSON.',
    'key_information_extraction':
        'You are an information extraction expert. Extract the key/value '
        'pairs from the image and output valid JSON only, with no '
        'explanation. Keep the output language the same as the image, '
        'replace any character that is blurry or hidden by glare with a '
        'question mark, and use null where a value is absent.',
}


class QwenCloudOcr(OCRPlugin):
    """
    QwenCloud (Alibaba DashScope) Qwen-OCR engine.
    Online engine: every call is an HTTPS request to QwenCloud with the
    image as a data URI (or passed through as a URL). Nothing is written
    to disk, and PDFs are rasterized in memory.
    """
    #: Multimodal generation path on the DashScope-native interface.
    DASHSCOPE_PATH = '/api/v1/services/aigc/multimodal-generation/generation'
    #: Chat path on the OpenAI-compatible interface.
    OPENAI_PATH = '/compatible-mode/v1/chat/completions'
    #: Responses path (qwen3.5-ocr PDF parsing).
    RESPONSES_PATH = '/compatible-mode/v1/responses'
    #: Region hosts for the shared (non-workspace) endpoints.
    HOSTS = {'intl': 'https://dashscope-intl.aliyuncs.com', 'cn': 'https://dashscope.aliyuncs.com'}
    #: Default pixel thresholds, as used throughout QwenCloud's samples.
    MIN_PIXELS = 32 * 32 * 3
    MAX_PIXELS = 32 * 32 * 8192
    #: Largest image the API accepts before Base64 encoding.
    MAX_IMAGE_BYTES = 10 * 1024 * 1024
    #: Vertical gap inserted between stacked PDF pages, in pixels.
    PAGE_GAP = 20.0
    #: Rendering scale used for PDF pages (72 dpi * scale).
    PDF_SCALE = 2.0
    #: Fraction of a row's height left as spacing when text without
    #: coordinates is laid out as rows.
    ROW_PADDING = 0.15

    def __init__(self, *args, **kwargs):
        """
        :param image: path | URL | base64/bytes | BytesIO | PIL image | numpy array | PDF (path or bytes)
        :param api: QwenCloud/DashScope key (else the environment)
        :param model: 'qwen-vl-ocr-latest' (default), 'qwen3.5-ocr', ...
        :param task: one of TASKS, or None for a plain prompt call
        :param ocrPrompt: custom instruction, replacing the task prompt (was ``prompt=``)
        :param prompt: extra instructions appended to the task/custom instruction (base-class
                       feature, e.g. 'the receipt is in Arabic'); change at runtime with
                       ``setExtraPrompt()``. In DashScope mode the built-in tasks normally send no
                       text at all, so an extra prompt makes the task's wording travel in the
                       message together with your addition.
        :param schema: dict of {field: description} for the extraction task; omit it to extract every field found
        :param api: 'dashscope' (default, exposes the built-in tasks and
                    their coordinates) or 'openai' (compatible mode)
        :param region: 'intl' (default, Singapore) or 'cn' (Beijing), or
                       a MaaS region such as 'ap-southeast-1' when a workspace is given
        :param workspace: workspace id for the per-workspace MaaS host
        :param endpoint: full base URL, overriding region/workspace
        :param minPixels / maxPixels: image scaling thresholds
        :param enableRotate: let the model straighten a rotated image
        :param maxTokens: output-length cap (the API defaults to 4096)
        :param temperature / topP: sampling overrides, off by default
        :param wordBox: split each text line into word boxes
        :param boxSpace: 'auto' (default), 'image', 'resized' or
                         'norm1000' — how to read the returned coordinates back to real pixels
        :param modelFallback: try the other Qwen-OCR models when the configured one is rejected
        :param arrayOrder: 'rgb' (default for arrays) or 'bgr'
        :param pdfScale: PDF rasterization scale (2.0 = 144 dpi)
        :param pages: optional list of 0-based PDF page indexes
        :param parameters: extra keys for the request's parameters block
        :param headers: extra HTTP headers
        """
        api = kwargs.pop('api', environ.get('QWENCLOUD_API_KEY', ''))
        model = kwargs.pop('model', MODELS[2])
        self.__m_task = kwargs.pop('task', 'advanced_recognition')
        self.__m_prompt = kwargs.pop('ocrPrompt', '')  # 'prompt' stays for the base class
        self.__m_schema = kwargs.pop('schema', None)
        self.__m_interface = kwargs.pop('api', 'dashscope').lower()
        self.__m_region = kwargs.pop('region', 'intl').lower()
        self.__m_workspace = kwargs.pop('workspace', '')
        self.__m_minPixels = int(kwargs.pop('minPixels', self.MIN_PIXELS))
        self.__m_maxPixels = int(kwargs.pop('maxPixels', self.MAX_PIXELS))
        self.__m_enableRotate = bool(kwargs.pop('enableRotate', False))
        self.__m_maxTokens = kwargs.pop('maxTokens', None)
        self.__m_temperature = kwargs.pop('temperature', None)
        self.__m_topP = kwargs.pop('topP', None)
        self.__m_wordBox = bool(kwargs.pop('wordBox', True))
        self.__m_boxSpace = kwargs.pop('boxSpace', 'auto').lower()
        self.__m_modelFallback = bool(kwargs.pop('modelFallback', True))
        self.__m_arrayOrder = str(kwargs.pop('arrayOrder', 'rgb')).lower()
        self.__m_pdfScale = float(kwargs.pop('pdfScale', self.PDF_SCALE))
        self.__m_pages = kwargs.pop('pages', None)
        self.__m_parameters = dict(kwargs.pop('parameters', {}))
        self.__m_headers = dict(kwargs.pop('headers', {}))
        self.__m_activeModel = ''
        self.__m_activeKey = ''
        self.__m_responses = []
        self.__m_polygons = []
        self.__m_markdown = []
        self.__m_keyValues = {}
        self.__m_pageSizes = []
        self.__m_usage = []
        # 'endpoint' is a base-class field; accept it as the host override.
        super(QwenCloudOcr, self).__init__(*args, **kwargs)
        self.setApi(api)
        self.setModel(model)
        self.setOnline(True)

    # ------------------------------------------------------------------ #
    # Engine entry point                                                 #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        Send the image to QwenCloud and return flat word dicts.
        :param image: whatever ``getImage()`` holds.
        :return: list[dict]
        """
        task = self.getTask()
        pages = self._loadPages(image)
        self.__m_responses = []
        self.__m_polygons = []
        self.__m_markdown = []
        self.__m_keyValues = {}
        self.__m_usage = []
        self.__m_pageSizes = [(page['width'], page['height']) for page in pages]
        words = []
        offset = 0.0
        for page in pages:
            body = self._send(page, task)
            self.__m_responses.append(body)
            parsed = self._parseBody(body, task)
            words.extend(self._wordsFromParsed(parsed, page, offset))
            offset += float(page['height'] or 0.0) + self.PAGE_GAP
        return words

    def getTask(self):
        """
        :return: the validated task name, or '' for a plain prompt call.
        """
        task = self.__m_task
        if not task:
            return ''
        task = str(task).lower()
        if task not in TASKS:
            raise OCRError('Unknown task {!r}; expected one of {}.'.format(task, ', '.join(TASKS)))
        return task

    # ------------------------------------------------------------------ #
    # HTTP                                                               #
    # ------------------------------------------------------------------ #
    def _send(self, page, task):
        """
        Post one page, rotating API keys on quota errors and falling back
        through the model ladder when a model is unavailable.
        :return: the decoded JSON body.
        """
        keys = self._apiKeys()
        models = self._models()
        lastError = None
        for model in models:
            credentials = True
            for key in keys:
                status, body = self._post(page, task, model, key)
                if status == 200 and not self._errorCode(body):
                    # Remember what worked: later pages start there.
                    self.__m_activeKey = key
                    self.__m_activeModel = model
                    self.setApi(key)
                    self.setModel(model)
                    return body
                lastError = self._describeError(status, body, model)
                if self._isQuota(status, body):
                    continue  # a different key may still work
                credentials = False
                if self._isModelError(status, body):
                    break  # the model is the problem, not the key
                raise OCRError(lastError)
            if credentials:
                # Every key was rejected: trying other models would just
                # repeat the same rejection.
                raise OCRError(lastError)
        raise OCRError(lastError or 'QwenCloud request failed.')

    def _post(self, page, task, model, key):
        """
        Perform a single HTTP request. :return: (status, body)
        """
        url = self.baseUrl() + self._path()
        headers = {'Authorization': 'Bearer {}'.format(key), 'Content-Type': 'application/json'}
        if self.__m_workspace:
            headers['X-DashScope-WorkSpace'] = self.__m_workspace
        headers.update(self.__m_headers)
        payload = self._payload(page, task, model)
        response = post(url, headers=headers, json=payload, timeout=self.getTimeout(), proxies=self.getProxy() or None)
        try:
            body = response.json()
        except ValueError:
            body = {'message': (response.text or '')[:400]}
        return response.status_code, body

    def baseUrl(self):
        """
        :return: the host to call, honouring endpoint/workspace/region.
        """
        endpoint = self.getEndpoint()
        if endpoint:
            return endpoint.rstrip('/')
        if self.__m_workspace:
            region = self.__m_region
            if region in self.HOSTS:
                region = 'ap-southeast-1' if region == 'intl' else 'cn-beijing'
            return 'https://{}.{}.maas.aliyuncs.com'.format(self.__m_workspace, region)
        host = self.HOSTS.get(self.__m_region)
        if host is None:
            raise OCRError(
                "region must be 'intl' or 'cn' (or a MaaS region together "
                'with workspace=), got {!r}.'.format(self.__m_region))
        return host

    def _path(self):
        """
        :return: the path for the configured interface.
        """
        if self.__m_interface == 'dashscope':
            return self.DASHSCOPE_PATH
        if self.__m_interface == 'openai':
            return self.OPENAI_PATH
        raise OCRError("api must be 'dashscope' or 'openai', got {!r}.".format(self.__m_interface))

    def _apiKeys(self):
        """
        API keys to try, in order: explicit, list, environment.
        """
        keys = []
        for candidate in [self.__m_activeKey, self.getApi(), self.getApi()] + list(self.getApiList() or []):
            if candidate and candidate not in keys:
                keys.append(candidate)
        for name in API_KEY_VARIABLES:
            candidate = environ.get(name)
            if candidate and candidate not in keys:
                keys.append(candidate)
        if not keys:
            raise OCRError(
                'No API key. Pass api= or set {} (get one at '
                'https://home.qwencloud.com/api-keys).'.format(API_KEY_VARIABLES[0]))
        return keys

    def _models(self):
        """
        The configured model, then the fallback ladder.
        """
        models = [self.__m_activeModel] if self.__m_activeModel else []
        if self.getModel() not in models:
            models.append(self.getModel())
        if self.__m_modelFallback:
            models.extend(name for name in MODELS if name not in models)
        return models

    # ------------------------------------------------------------------ #
    # Request bodies                                                     #
    # ------------------------------------------------------------------ #
    def _payload(self, page, task, model):
        """
        Build the request body for the configured interface.
        """
        if self.__m_interface == 'dashscope':
            return self._dashscopePayload(page, task, model)
        return self._openaiPayload(page, task, model)

    def _dashscopePayload(self, page, task, model):
        """
        DashScope-native body.
        The built-in task travels in ``parameters.ocr_options``, which is
        what makes the model return ``ocr_result`` (and, for
        ``advanced_recognition``, real coordinates).
        """
        content = [{
            'image': page['source'],
            'min_pixels': self.__m_minPixels,
            'max_pixels': self.__m_maxPixels,
            'enable_rotate': self.__m_enableRotate,
        }]
        prompt = self.__m_prompt
        if (self.getExtraPrompt() or '').strip():
            # Extra instructions need a text part; give them the
            # task's own wording as the base when none was set.
            prompt = self.prompt()
        if prompt:
            content.append({'text': prompt})
        payload = {'model': model, 'input': {'messages': [{'role': 'user', 'content': content}]}}
        parameters = {}
        if task:
            options = {'task': task}
            config = self._taskConfig(task)
            if config:
                options['task_config'] = config
            parameters['ocr_options'] = options
        if self.__m_maxTokens is not None:
            parameters['max_tokens'] = int(self.__m_maxTokens)
        if self.__m_temperature is not None:
            parameters['temperature'] = float(self.__m_temperature)
        if self.__m_topP is not None:
            parameters['top_p'] = float(self.__m_topP)
        parameters.update(self.__m_parameters)
        if parameters:
            payload['parameters'] = parameters
        return payload

    def _openaiPayload(self, page, task, model):
        """
        OpenAI-compatible body.
        Compatible mode does not expose ``ocr_options``, so the task
        instruction is written into the message instead and the reply is
        plain text — no ``ocr_result``, hence no model-reported boxes.
        """
        content = [{
            'type': 'image_url',
            'image_url': {'url': page['source']},
            'min_pixels': self.__m_minPixels,
            'max_pixels': self.__m_maxPixels,
        }]
        prompt = self.prompt()
        if prompt:
            content.append({'type': 'text', 'text': prompt})
        payload = {'model': model, 'messages': [{'role': 'user', 'content': content}]}
        if self.__m_maxTokens is not None:
            payload['max_tokens'] = int(self.__m_maxTokens)
        if self.__m_temperature is not None:
            payload['temperature'] = float(self.__m_temperature)
        if self.__m_topP is not None:
            payload['top_p'] = float(self.__m_topP)
        payload.update(self.__m_parameters)
        return payload

    def _taskConfig(self, task):
        """
        ``task_config`` for the tasks that take one.
        """
        return {'result_schema': self.__m_schema} if task == 'key_information_extraction' and self.__m_schema else None

    def prompt(self):
        """
        :return: the instruction sent in OpenAI-compatible mode: the
                 caller's ``ocrPrompt=``, else the task's own wording, with the extraction schema appended
                 when given -- plus the base class's extra prompt (``prompt=`` / ``setExtraPrompt``).
        """
        if self.__m_prompt:
            return self.composePrompt(self.__m_prompt)
        task = self.getTask()
        prompt = TASK_PROMPTS.get(task, '')
        if task == 'key_information_extraction' and self.__m_schema:
            prompt += ' The JSON schema to fill is: {}'.format(dumps(self.__m_schema, ensure_ascii=False))
        return self.composePrompt(prompt)

    # ------------------------------------------------------------------ #
    # Reply parsing                                                      #
    # ------------------------------------------------------------------ #
    def _parseBody(self, body, task):
        """
        Normalize either interface's reply.
        :return: dict with 'boxes', 'texts', 'text' and 'kv'.
        """
        text, ocrResult = self._extractContent(body)
        usage = body.get('usage') if isinstance(body, dict) else None
        if usage:
            self.__m_usage.append(usage)
        parsed = {'boxes': [], 'texts': [], 'text': '', 'kv': {}}
        if isinstance(ocrResult, dict):
            for entry in ocrResult.get('words_info') or []:
                if not isinstance(entry, dict):
                    continue
                box = entry.get('location')
                if box is None and entry.get('rotate_rect'):
                    box = self._fromRotateRect(entry['rotate_rect'])
                if box is None:
                    continue
                parsed['boxes'].append(box)
                parsed['texts'].append(str(entry.get('text', '')))
            kv = ocrResult.get('kv_result')
            if isinstance(kv, dict):
                parsed['kv'] = kv
        text = self._stripFence(text)
        if not parsed['boxes'] and task in BOX_TASKS:
            # Compatible mode (or a model without ocr_result) answers with
            # the same information as JSON in the text body.
            for entry in self._jsonLines(text):
                box = entry.get('location') or entry.get('bbox') \
                    or entry.get('box')
                if box is None and entry.get('rotate_rect'):
                    box = self._fromRotateRect(entry['rotate_rect'])
                if box is None:
                    continue
                parsed['boxes'].append(box)
                parsed['texts'].append(str(entry.get('text', '')))
        if not parsed['kv'] and task == 'key_information_extraction':
            decoded = self._loadJson(text)
            if isinstance(decoded, dict):
                parsed['kv'] = decoded
        parsed['text'] = text
        return parsed

    @staticmethod
    def _extractContent(body):
        """
        Pull (text, ocr_result) out of a DashScope or OpenAI reply.
        """
        if not isinstance(body, dict):
            return '', None
        if 'output' in body:  # DashScope native
            choices = (body.get('output') or {}).get('choices') or []
            if not choices:
                return str((body.get('output') or {}).get('text', '')), None
            content = ((choices[0] or {}).get('message') or {}).get(
                'content')
            if isinstance(content, str):
                return content, None
            text, ocrResult = '', None
            for item in content or []:
                if not isinstance(item, dict):
                    continue
                if item.get('ocr_result') is not None:
                    ocrResult = item['ocr_result']
                if item.get('text'):
                    text = str(item['text'])
            return text, ocrResult
        choices = body.get('choices') or []  # OpenAI compatible
        if not choices:
            return '', None
        message = (choices[0] or {}).get('message') or {}
        content = message.get('content')
        if isinstance(content, list):
            content = ''.join(item.get('text', '') for item in content if isinstance(item, dict))
        return str(content or ''), None

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
        for opening, closing in (('{', '}'), ('[', ']')):
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
    def _jsonLines(cls, text):
        """
        Decode a JSON reply into a flat list of line dicts.
        """
        decoded = cls._loadJson(text)
        entries = []
        if isinstance(decoded, dict):
            for key in ('words_info', 'lines', 'results', 'result'):
                if isinstance(decoded.get(key), list):
                    decoded = decoded[key]
                    break
            else:
                decoded = [decoded]
        if isinstance(decoded, list):
            for item in decoded:
                if isinstance(item, dict):
                    entries.append(item)
        return entries

    @staticmethod
    def _fromRotateRect(rect):
        """
        Convert [cx, cy, width, height, angle] into a flat quad.
        The angle is dropped: the base class works with axis-aligned
        boxes, and ``getPolygons()`` keeps the untouched originals.
        """
        values = [float(value) for value in rect][:5]
        if len(values) < 4:
            return None
        cx, cy, width, height = values[:4]
        left, top = cx - width / 2.0, cy - height / 2.0
        return [left, top, left + width, top, left + width, top + height, left, top + height]

    # ------------------------------------------------------------------ #
    # Errors                                                             #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _errorCode(body):
        """
        The API's error code, if the body carries one.
        """
        if not isinstance(body, dict):
            return ''
        error = body.get('error')
        if isinstance(error, dict):
            return str(error.get('code') or error.get('type') or 'error')
        return str(body.get('code') or '')

    @classmethod
    def _message(cls, body):
        """
        The API's human-readable error message.
        """
        if not isinstance(body, dict):
            return ''
        error = body.get('error')
        if isinstance(error, dict):
            return str(error.get('message', ''))
        return str(body.get('message', ''))

    @classmethod
    def _isQuota(cls, status, body):
        """
        True for throttling/quota/credential problems worth a new key.
        """
        code = cls._errorCode(body).lower()
        if status in (401, 403, 429):
            return True
        return any(word in code for word in (
            'throttling', 'quota', 'limit', 'apikey', 'arrearage', 'unauthorized', 'accessdenied'))

    @classmethod
    def _isModelError(cls, status, body):
        """
        True when the model itself is the problem.
        """
        code = cls._errorCode(body).lower()
        message = cls._message(body).lower()
        if 'model' in code and ('notfound' in code or 'not_found' in code or 'unsupported' in code):
            return True
        if status == 404:
            return True
        return 'model' in message and ('not exist' in message or 'not found' in message or 'not support' in message)

    @classmethod
    def _describeError(cls, status, body, model):
        """
        Build a useful message out of an error reply.
        """
        code = cls._errorCode(body) or 'HTTP {}'.format(status)
        message = cls._message(body) or 'no message'
        hint = ''
        if status == 401 or 'apikey' in code.lower():
            hint = ' Check the key and that it belongs to this region (keys are region-specific).'
        elif cls._isModelError(status, body):
            hint = ' Model {!r} is unavailable here.'.format(model)
        return 'QwenCloud {} ({}): {}.{}'.format(code, status, message, hint)

    # ------------------------------------------------------------------ #
    # Parsed reply -> words                                              #
    # ------------------------------------------------------------------ #
    def _wordsFromParsed(self, parsed, page, offset=0.0):
        """
        Turn one page's parsed reply into word dicts.

        :param parsed: dict from ``_parseBody``.
        :param page: the page descriptor (source, width, height).
        :param offset: vertical offset of that page.
        :return: list[dict]
        """
        width = float(page['width'] or 0.0)
        height = float(page['height'] or 0.0)
        if parsed['kv']:
            self.__m_keyValues.update(parsed['kv'])
        if parsed['text']:
            self.__m_markdown.append(parsed['text'])
        if parsed['boxes']:
            scaleX, scaleY = self._boxScale(parsed['boxes'], width, height)
            words = []
            for index, box in enumerate(parsed['boxes']):
                points = self._flatten(box)
                points[0::2] = [value * scaleX for value in points[0::2]]
                points[1::2] = [value * scaleY for value in points[1::2]]
                bounds = self._polygonToBox(points)
                if bounds is None:
                    continue
                text = parsed['texts'][index].strip() \
                    if index < len(parsed['texts']) else ''
                if not text:
                    continue
                left, top, boxWidth, boxHeight = bounds
                if width:
                    left = max(0.0, min(left, width))
                    boxWidth = max(1.0, min(boxWidth, width - left))
                if height:
                    top = max(0.0, min(top, height))
                    boxHeight = max(1.0, min(boxHeight, height - top))
                points[1::2] = [value + offset for value in points[1::2]]
                self.__m_polygons.append(points)
                top += offset
                if self.__m_wordBox:
                    words.extend(self._splitWords(
                        text, left, top, boxWidth, boxHeight))
                else:
                    words.append(self.makeWord(
                        text, left, top, boxWidth, boxHeight))
            return words
        # No coordinates (text/table/document/formula/extraction): lay the
        # transcription out as rows down the page. Estimates, not boxes.
        text = parsed['text']
        if parsed['kv'] and not text:
            text = '\n'.join('{}: {}'.format(key, value) for key, value in parsed['kv'].items())
        return self._rowsFromText(text, 0.0, offset, width or 1000.0, height or 1000.0)

    def _boxScale(self, boxes, width, height):
        """
        Factors bringing the returned coordinates back to real pixels.
        QwenCloud documents ``words_info.location`` as absolute pixels of
        the original image, and ``boxSpace='image'`` takes that at face
        value. But the API also rescales images to sit between
        ``min_pixels`` and ``max_pixels`` before inference, and a model
        can report coordinates in that resized space instead;
        ``boxSpace='resized'`` maps them back using the documented
        rounding rule. ``'auto'`` (the default) only intervenes when the
        boxes visibly overflow the real image, since a downscale leaves
        no evidence to detect. ``'norm1000'`` handles the general Qwen-VL
        models, whose localization grid runs 0-1000.
        :return: (scaleX, scaleY)
        """
        if not width or not height:
            return 1.0, 1.0
        points = [value for box in boxes for value in self._flatten(box)]
        if not points:
            return 1.0, 1.0
        maxX, maxY = max(points[0::2]), max(points[1::2])
        if self.__m_boxSpace == 'image':
            return 1.0, 1.0
        if self.__m_boxSpace == 'norm1000':
            return width / 1000.0, height / 1000.0
        resizedWidth, resizedHeight = self.resizedSize(width, height)
        resized = (width / float(resizedWidth), height / float(resizedHeight))
        if self.__m_boxSpace == 'resized':
            return resized
        if maxX <= width * 1.02 and maxY <= height * 1.02:
            return 1.0, 1.0  # already in image pixels
        if maxX * resized[0] <= width * 1.02 \
                and maxY * resized[1] <= height * 1.02:
            return resized
        # Unknown space: fall back to fitting the boxes to the image.
        overflow = max(maxX / width, maxY / height)
        return 1.0 / overflow, 1.0 / overflow

    def resizedSize(self, width, height):
        """
        The dimensions QwenCloud resizes an image to before inference.
        This is the scaling rule published with the token calculator:
        each side is rounded to a multiple of 32, then the whole image is
        scaled so its pixel count lands between ``min_pixels`` and
        ``max_pixels``.
        :return: (width, height) after resizing.
        """
        from math import ceil, floor, sqrt
        width, height = float(width), float(height)
        factor = 32
        barWidth = max(factor, round(width / factor) * factor)
        barHeight = max(factor, round(height / factor) * factor)
        minPixels = max(4 * factor * factor, self.__m_minPixels)
        maxPixels = self.__m_maxPixels
        if barWidth * barHeight > maxPixels:
            beta = sqrt((width * height) / maxPixels)
            barWidth = floor(width / beta / factor) * factor
            barHeight = floor(height / beta / factor) * factor
        elif barWidth * barHeight < minPixels:
            beta = sqrt(minPixels / (width * height))
            barWidth = ceil(width * beta / factor) * factor
            barHeight = ceil(height * beta / factor) * factor
        return max(factor, int(barWidth)), max(factor, int(barHeight))

    def _splitWords(self, text, left, top, width, height):
        """
        Split a line's text into word boxes.

        Qwen-OCR localizes whole text lines, so per-word geometry is
        estimated by distributing the line's width over its characters.
        ``wordBox=False`` keeps the model's own single box per line.
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

    # ------------------------------------------------------------------ #
    # Image handling (in memory only)                                    #
    # ------------------------------------------------------------------ #
    def _loadPages(self, image=None):
        """
        Turn the image source into page descriptors the API can take.
        Each descriptor is {'source': url|data URI, 'width', 'height'}.
        URLs are passed through untouched (QwenCloud downloads them);
        everything else becomes a data URI. PDFs rasterize in memory.
        :return: list[dict]
        """
        image = self.getImage() if image is None else image
        if image is None or (isinstance(image, str) and not image.strip()):
            raise OCRError('No image was given.')
        if isinstance(image, str) and is_url(image):
            width, height = self._remoteSize(image)
            return [{'source': image, 'width': width, 'height': height}]
        data, mime = self._sourceBytes(image)
        if data[:5] == b'%PDF-':
            return [self._fromPil(page) for page in self._pdfPages(data)]
        return [self._page(data, mime)]

    def _page(self, data, mime=None):
        """
        Build a data-URI page descriptor out of image bytes.
        """
        if len(data) > self.MAX_IMAGE_BYTES:
            raise OCRError(
                'Image is {:.1f} MB; QwenCloud accepts up to {:.0f} MB '
                'when Base64-encoded. Downscale it, or pass a public URL.'.format(
                    len(data) / 1048576.0, self.MAX_IMAGE_BYTES / 1048576.0))
        from base64 import b64encode
        width, height = self._size(data)
        return {
            'source': 'data:{};base64,{}'.format(
                mime or self._mime(data),
                b64encode(data).decode('ascii')),
            'width': width,
            'height': height,
        }

    def _sourceBytes(self, image):
        """
        Return (bytes, mime) for path/bytes/BytesIO/PIL/array/base64.
        """
        if isinstance(image, (bytes, bytearray)):
            return bytes(image), None
        if isinstance(image, BytesIO):
            return image.getvalue(), None
        if hasattr(image, 'save'):
            buffer = BytesIO()
            image.save(buffer, format='PNG')
            return buffer.getvalue(), 'image/png'
        if isinstance(image, str) or hasattr(image, 'encode'):
            if isfile(image):
                with open(image, 'rb') as handle:
                    return handle.read(), None
            decoded = self._fromBase64(image)
            if decoded is not None:
                return decoded, None
            raise OCRError('Image string is neither an existing path, a URL nor base64 data.')
        return self.imageBytes(), None

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

    def _pdfPages(self, data):
        """
        Rasterize a PDF from memory into PIL pages.
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
            return [document[index].render(scale=self.__m_pdfScale).to_pil() for index in indexes]
        finally:
            document.close()

    def _fromPil(self, image):
        """
        Encode a PIL page as a JPEG/PNG data-URI descriptor.
        """
        buffer = BytesIO()
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(buffer, format='JPEG', quality=90)
        data = buffer.getvalue()
        if len(data) > self.MAX_IMAGE_BYTES:
            buffer = BytesIO()
            image.save(buffer, format='JPEG', quality=70, optimize=True)
            data = buffer.getvalue()
        page = self._page(data, 'image/jpeg')
        page['width'], page['height'] = image.size
        return page

    @staticmethod
    def _mime(data):
        """
        Guess the MIME type from the file's magic bytes.
        """
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return 'image/png'
        if data[:3] == b'\xff\xd8\xff':
            return 'image/jpeg'
        if data[:2] == b'BM':
            return 'image/bmp'
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return 'image/webp'
        if data[:4] in (b'II*\x00', b'MM\x00*'):
            return 'image/tiff'
        return 'image/png'

    @staticmethod
    def _size(data):
        """
        (width, height) of encoded image bytes, or (0, 0).
        """
        try:
            from PIL import Image
            with Image.open(BytesIO(data)) as image:
                return image.size
        except Exception:  # noqa: BLE001 - size is best-effort
            return 0, 0

    def _remoteSize(self, url):
        """
        Fetch just enough of a remote image to learn its size.
        Coordinates come back in pixels, so the dimensions are needed to
        clamp them; if the probe fails the boxes are used unclamped.
        """
        try:
            response = get(url, timeout=self.getTimeout(), proxies=self.getProxy() or None, stream=True)
            response.raise_for_status()
            data = response.raw.read(262144, decode_content=True)
            response.close()
            return self._size(data)
        except Exception:  # noqa: BLE001 - the URL still goes to the API
            return 0, 0

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
        points = cls._flatten(polygon)
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
    def getResponses(self):
        """
        :return: QwenCloud's raw JSON body for each page.
        """
        return self.__m_responses

    def getPolygons(self):
        """
        :return: the four-vertex boxes the model reported, in pixels.
        """
        return self.__m_polygons

    def getKeyValues(self):
        """
        :return: the ``kv_result`` of the extraction task.
        """
        return self.__m_keyValues

    def getMarkdown(self):
        """
        :return: each page's transcription as returned (HTML for table parsing, LaTeX for document and formula parsing),
        which the line-based ParsedText flattens.
        """
        return self.__m_markdown

    def getPageSizes(self):
        """
        :return: list of (width, height) for every processed page.
        """
        return self.__m_pageSizes

    def getUsage(self):
        """
        :return: the token-usage block of each reply, for cost tracking.
        """
        return self.__m_usage

    # ------------------------------------------------------------------ #
    # Getters / setters                                                  #
    # ------------------------------------------------------------------ #
    def setTask(self, task):
        """
        :param task: str | unicode
        :return:
        """
        self.__m_task = task

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
        :return: str | unicode
        """
        return self.__m_schema

    def setSchema(self, schema):
        """
        :param schema: str | unicode
        :return:
        """
        self.__m_schema = schema

    def getInterface(self):
        """
        :return: str | unicode
        :return:
        """
        return self.__m_interface

    def setInterface(self, interface):
        """
        :param interface: str | unicode
        :return:
        """
        self.__m_interface = str(interface).lower()

    def getRegion(self):
        """
        :return: str | unicode
        """
        return self.__m_region

    def setRegion(self, region):
        """
        :param region: str | unicode
        :return:
        """
        self.__m_region = str(region).lower()

    def getWorkspace(self):
        """
        :return: str | unicode
        """
        return self.__m_workspace

    def setWorkspace(self, workspace):
        """
        :param workspace: str | unicode
        :return:
        """
        self.__m_workspace = workspace

    def getMinPixels(self):
        """
        :return: int
        """
        return self.__m_minPixels

    def setMinPixels(self, minPixels):
        """
        :param minPixels: int
        :return:
        """
        self.__m_minPixels = int(minPixels)

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
        self.__m_maxPixels = int(maxPixels)

    def isEnableRotate(self):
        """
        :return: bool
        """
        return self.__m_enableRotate

    def setEnableRotate(self, enable):
        """
        :param enable: bool
        :return:
        """
        self.__m_enableRotate = bool(enable)

    def getMaxTokens(self):
        """
        :return: int | None
        :return:
        """
        return self.__m_maxTokens

    def setMaxTokens(self, maxTokens):
        """
        :param maxTokens: int | None
        :return:
        """
        self.__m_maxTokens = maxTokens

    def isWordBox(self):
        """
        :return: bool
        """
        return self.__m_wordBox

    def setWordBox(self, wordBox):
        """
        :param wordBox: bool
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
        self.__m_boxSpace = str(boxSpace).lower()

    def isModelFallback(self):
        """
        :return: bool
        """
        return self.__m_modelFallback

    def setModelFallback(self, fallback):
        """
        :param fallback: bool
        :return:
        """
        self.__m_modelFallback = bool(fallback)

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
        :return: int | float
        """
        return self.__m_pdfScale

    def setPdfScale(self, scale):
        """
        :param scale: int | float
        :return:
        """
        self.__m_pdfScale = float(scale)

    def getPages(self):
        """
        :return: int | None
        """
        return self.__m_pages

    def setPages(self, pages):
        """
        :param pages: int | None
        :return:
        """
        self.__m_pages = pages

    def getParameters(self):
        """
        :return: dict
        """
        return self.__m_parameters

    def setParameters(self, parameters):
        """
        :param parameters: dict
        """
        self.__m_parameters = dict(parameters or {})

    def getHeaders(self):
        """
        :return: dict
        """
        return self.__m_headers

    def setHeaders(self, headers):
        """
        :param headers: dict
        """
        self.__m_headers = dict(headers or {})

    task = property(fget=getTask, fset=setTask)
    schema = property(fget=getSchema, fset=setSchema)
    interface = property(fget=getInterface, fset=setInterface)
    region = property(fget=getRegion, fset=setRegion)
    workspace = property(fget=getWorkspace, fset=setWorkspace)
    minPixels = property(fget=getMinPixels, fset=setMinPixels)
    maxPixels = property(fget=getMaxPixels, fset=setMaxPixels)
    enableRotate = property(fget=isEnableRotate, fset=setEnableRotate)
    maxTokens = property(fget=getMaxTokens, fset=setMaxTokens)
    wordBox = property(fget=isWordBox, fset=setWordBox)
    boxSpace = property(fget=getBoxSpace, fset=setBoxSpace)
    modelFallback = property(fget=isModelFallback, fset=setModelFallback)
    arrayOrder = property(fget=getArrayOrder, fset=setArrayOrder)
    pdfScale = property(fget=getPdfScale, fset=setPdfScale)
    pages = property(fget=getPages, fset=setPages)
    parameters = property(fget=getParameters, fset=setParameters)
    headers = property(fget=getHeaders, fset=setHeaders)
    responses = property(fget=getResponses)
    polygons = property(fget=getPolygons)
    keyValues = property(fget=getKeyValues)
    markdown = property(fget=getMarkdown)
    pageSizes = property(fget=getPageSizes)
    usage = property(fget=getUsage)


#: Alias matching the platform's own capitalization.
QwenOcr = QwenCloudOcr
__all__ = ['QwenCloudOcr', 'QwenOcr', 'TASKS', 'MODELS', 'TASK_PROMPTS']
