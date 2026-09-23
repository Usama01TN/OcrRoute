# coding=utf-8
"""
GLM-OCR plugin, HuggingFace-model edition
(https://huggingface.co/zai-org/GLM-OCR).
Runs the model itself through transformers -- no glmocr library, no
service, no key. Install::
    pip install -U torch transformers pillow accelerate
GLM-OCR is natively supported by transformers (no trust_remote_code);
if loading fails with an unknown-architecture error, upgrade
transformers. The model is 0.9B parameters (~2.2 GB VRAM in BF16): it
fits a T4-class GPU, and CPU inference works too (slowly). The first
run downloads ~2 GB of weights into the local HF cache; afterwards it
is fully offline.
This follows the model card's recipe verbatim: AutoProcessor +
apply_chat_template with one of the model's documented prompts
("Text Recognition:" / "Formula Recognition:" / "Table Recognition:",
or a strict JSON schema for information extraction), generate, decode
the new tokens. The raw model outputs text without coordinates (boxes
come from the separate PP-DocLayout stage of the full pipeline), so
reading order is kept with synthesized row boxes in the same unified
structure as every other plugin.
"""
from transformers import AutoModelForImageTextToText, AutoProcessor
from torch import bfloat16, cuda, float16, float32, inference_mode
from os.path import dirname
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

#: The model's documented prompt modes.
_TASKS = {'text': 'Text Recognition:', 'formula': 'Formula Recognition:', 'table': 'Table Recognition:'}


class GlmOcrHF(OCRPlugin):
    """
    GlmOcrHF class.
    """
    #: (model_name, device) -> (model, processor); loading is slow and
    #: heavy, so share across instances.
    _models = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: HF model id or local path (default 'zai-org/GLM-OCR').
        :param task: 'text' (default), 'formula', or 'table'.
        :param ocrPrompt: custom prompt; overrides ``task`` (was ``prompt=``). Use a strict JSON schema string
                          for information extraction.
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
        :param device: 'cuda', 'mps', 'cpu', or None to let device_map='auto' place the model.
        :param maxNewTokens: generation budget (default 8192).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings.
        """
        model = kwargs.pop('model', 'zai-org/GLM-OCR')
        task = str(kwargs.pop('task', 'text')).lower()
        if task not in _TASKS:
            raise OCRError('Unknown task {!r}; choose from {}'.format(task, ', '.join(sorted(_TASKS))))
        self.__m_prompt = kwargs.pop('ocrPrompt', None) or _TASKS[task]  # 'prompt' stays for the base class
        self.__m_device = kwargs.pop('device', None)
        self.__m_max_new_tokens = int(kwargs.pop('maxNewTokens', 8192))
        super(GlmOcrHF, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Model management                                                   #
    # ------------------------------------------------------------------ #
    def _pickDtype(self):
        """
        bfloat16 where supported, float16 on older GPUs, else fp32.
        """
        device = self.__m_device
        if device == 'cuda' or (device is None and cuda.is_available()):
            supported = getattr(cuda, 'is_bf16_supported', None)
            return bfloat16 if supported and supported() else float16
        if device == 'mps':
            return float16
        if device is None:
            return 'auto'
        return float32

    def _loadModel(self):
        key = (self.getModel(), self.__m_device)
        if key in GlmOcrHF._models:
            return GlmOcrHF._models[key]
        dtype = self._pickDtype()
        load_kwargs = {'low_cpu_mem_usage': True}
        if self.__m_device is None:
            load_kwargs['device_map'] = 'auto'
        try:
            processor = AutoProcessor.from_pretrained(self.getModel())
            # transformers renamed torch_dtype -> dtype in v5.
            for dtype_kwarg in ('torch_dtype', 'dtype'):
                try:
                    model = AutoModelForImageTextToText.from_pretrained(
                        self.getModel(), **dict(load_kwargs, **{dtype_kwarg: dtype}))
                    break
                except TypeError:
                    continue
            else:
                model = AutoModelForImageTextToText.from_pretrained(self.getModel(), **load_kwargs)
        except (KeyError, ValueError, OSError) as exc:
            message = 'Could not load {}: {}'.format(self.getModel(), exc)
            text = str(exc).lower()
            if 'glm_ocr' in text or 'model type' in text or 'architecture' in text:
                import transformers
                message += (
                    '\nYour transformers ({}) predates native GLM-OCR '
                    'support (added early 2026). Fix: '
                    'pip install -U transformers'.format(transformers.__version__))
            raise OCRError(message)
        model = model.eval()
        if self.__m_device is not None:
            model = model.to(self.__m_device)
        GlmOcrHF._models[key] = (model, processor)
        return GlmOcrHF._models[key]

    # ------------------------------------------------------------------ #
    # Input                                                              #
    # ------------------------------------------------------------------ #
    def _imageContent(self):
        """
        Build the image part of the chat message.
        """
        kind = self.imageKind()
        if kind in ('path', 'url'):
            return {'type': 'image', 'url': self.getImage()}
        if kind in ('pil', 'bytes', 'buffer', 'array'):
            from io import BytesIO
            from PIL import Image
            if kind == 'pil':
                image = self.getImage()
            elif kind == 'array':
                image = Image.fromarray(self.getImage())
            else:
                image = Image.open(BytesIO(self.imageBytes()))
            return {'type': 'image', 'image': image.convert('RGB')}
        raise OCRError(
            "Image source '{}' is not an existing file, URL, or "
            "supported type. Check the path (the current working "
            "directory matters for relative paths).".format(self.getImage()))

    # ------------------------------------------------------------------ #
    # Output                                                             #
    # ------------------------------------------------------------------ #
    @classmethod
    def _textToRows(cls, text):
        """
        Model output lines -> ordered rows (light markup cleanup).
        """
        words = []
        row = 0
        for line in (text or '').splitlines():
            line = sub(r'^[#>\s]+', '', line)
            line = sub(r'\|', ' ', line).strip()
            if not line or set(line) <= set('-: '):
                continue
            words.append(cls.makeWord(line, 0.0, float(row * 10), 1.0, 8.0))
            row += 1
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        model, processor = self._loadModel()
        messages = [{'role': 'user', 'content': [self._imageContent(), {'type': 'text', 'text': self.composePrompt(self.__m_prompt)}]}]
        inputs = processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt').to(model.device)
        inputs.pop('token_type_ids', None)  # per the model card.
        with inference_mode():
            generated = model.generate(**inputs, max_new_tokens=self.__m_max_new_tokens)
        prompt_length = inputs['input_ids'].shape[1]
        text = processor.decode(generated[0][prompt_length:], skip_special_tokens=True)
        words = self._textToRows(text)
        if not words:
            raise OCRError('GLM-OCR produced no text for this image.')
        return words
