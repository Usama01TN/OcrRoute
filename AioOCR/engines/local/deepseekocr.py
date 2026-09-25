# coding=utf-8
"""
DeepSeek-OCR plugin (deepseek-ai/DeepSeek-OCR and DeepSeek-OCR-2).
DeepSeek-OCR is a vision-language model that runs locally through
HuggingFace transformers (``trust_remote_code=True``). Unlike classical
engines it *reads* the page: with the ``<|grounding|>`` prompt it
returns text regions as ``<|ref|>text<|/ref|><|det|>[[x1,y1,x2,y2]]
<|/det|>`` pairs whose coordinates are normalized to a 0-1000 grid.
This plugin parses those pairs, rescales the boxes to real image
pixels, and feeds them to the base class, so DeepSeek-OCR returns the
exact same result structure as every other plugin.
The model's ``infer()`` is written around file paths: it reads the
image through its module-level ``load_pil_images()`` and creates
``output_path`` with the module's ``os`` reference. This plugin runs
``infer()`` fully in memory instead -- for the duration of the call
those two names in the model's module are pointed at the already
decoded PIL image and at an ``os`` stand-in whose ``makedirs`` is a
no-op -- and asks for ``eval_mode=True`` so the text comes back as
the return value. Nothing is written to disk. (This relies on the
official remote code's structure, shared by DeepSeek-OCR and
DeepSeek-OCR-2; a fork without ``load_pil_images`` is rejected with a
clear error rather than silently falling back to files.)
Requirements (see the model card for tested versions)::
    pip install torch transformers tokenizers einops addict easydict
    # optional, NVIDIA only: pip install flash-attn --no-build-isolation
Hardware: a CUDA GPU is strongly recommended (the model is ~3B
parameters). Apple Silicon (mps) and CPU work but are slow; community
forks may be needed for newest transformers versions.
Resolution presets (``preset=``):
    tiny    base_size=512  image_size=512  crop_mode=False
    small   base_size=640  image_size=640  crop_mode=False
    base    base_size=1024 image_size=1024 crop_mode=False
    large   base_size=1280 image_size=1280 crop_mode=False
    gundam  base_size=1024 image_size=640  crop_mode=True  (default)
"""
from re import compile, sub, DOTALL
from os.path import dirname
from sys import modules
from json import loads
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

#: Known resolution presets: (base_size, image_size, crop_mode).
_PRESETS = {
    'tiny': (512, 512, False),
    'small': (640, 640, False),
    'base': (1024, 1024, False),
    'large': (1280, 1280, False),
    'gundam': (1024, 640, True),
}
#: Grounding pairs: <|ref|>text<|/ref|><|det|>[[x1,y1,x2,y2],...]<|/det|>
_GROUNDING = compile(r'<\|ref\|>(.*?)<\|/ref\|>\s*<\|det\|>\s*(\[\[.*?\]\])\s*<\|/det\|>', DOTALL)
#: Grounding coordinates are normalized to this grid.
_COORD_SPACE = 1000.0
#: Structural labels the grounding pass emits that are not page text.
_SKIP_LABELS = frozenset(('image', 'figure', 'table', 'chart'))
#: Placeholder passed as ``image_file``: it only has to be truthy, the
#: patched loader never opens it.
_IN_MEMORY = '<in-memory>'


class _NoMakedirs(object):
    """
    Stand-in for the ``os`` module inside the model's remote code:
    everything is forwarded to the real module except ``makedirs``,
    which becomes a no-op so ``infer()`` creates no output directory.
    """

    def __init__(self, real):
        self.__m_real = real

    def makedirs(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self.__m_real, name)


class InMemoryInfer(object):
    """
    Context manager under which the model's ``infer()`` works on PIL
    images and touches no files::
        with InMemoryInfer(model, [pil_image]):
            text = model.infer(tokenizer, prompt=..., image_file=_IN_MEMORY,
                               output_path='', save_results=False, eval_mode=True)
    The remote code looks up ``load_pil_images`` and ``os`` as globals
    of its own module at call time; both are swapped on entry and
    restored on exit (also on error). Not thread-safe -- neither is
    ``infer()`` itself.
    """

    def __init__(self, model, images):
        self.__m_module = modules[type(model).__module__]
        self.__m_images = [image.convert('RGB') for image in images]
        self.__m_saved = None

    def __enter__(self):
        module = self.__m_module
        if not callable(module.__dict__.get('load_pil_images')) or 'os' not in module.__dict__:
            raise OCRError(
                "The remote code of this checkpoint has no module-level "
                "'load_pil_images'/'os' (not the official DeepSeek-OCR "
                "layout), so it cannot be run in memory by this plugin.")
        self.__m_saved = (module.__dict__['load_pil_images'], module.__dict__['os'])
        images = self.__m_images
        module.load_pil_images = lambda conversations: list(images)
        module.os = _NoMakedirs(self.__m_saved[1])
        return self

    def __exit__(self, *exc_info):
        module = self.__m_module
        module.load_pil_images, module.os = self.__m_saved
        return False


class DeepSeekOcr(OCRPlugin):
    """
    DeepSeekOcr class.
    """
    #: (model_name, device) -> (model, tokenizer). Loading takes tens of
    #: seconds and several GB, so cache across instances.
    _models = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: HF model id or local path (default 'deepseek-ai/DeepSeek-OCR'; the newer
                      'deepseek-ai/DeepSeek-OCR-2' also works).
        :param preset: resolution preset name (default 'gundam'), or
                       pass baseSize/imageSize/cropMode explicitly.
        :param device: 'cuda', 'mps', 'cpu' or None for auto-detect.
        :param ocrPrompt: override the OCR prompt (was ``prompt=``). The default grounding
                       prompt returns word/line boxes; a prompt WITHOUT
                       '<|grounding|>' returns plain text and this
                       plugin then synthesizes row boxes to keep the
                       unified structure (HasOverlay stays meaningful).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
        :param attention: attention implementation ('flash_attention_2', 'sdpa', 'eager');
                          default tries flash_attention_2, falls back.
        :param revision: pin the HF revision (weights + remote code) to
                         a specific commit hash for reproducible loads
                         and to silence the "new version of ... was downloaded" warnings.
        :param kwargs: other settings.
        """
        model = kwargs.pop('model', 'deepseek-ai/DeepSeek-OCR')
        self.__m_revision = kwargs.pop('revision', None)
        preset = kwargs.pop('preset', 'gundam').lower()
        if preset not in _PRESETS:
            raise OCRError('Unknown preset {!r}; choose from {}'.format(preset, sorted(_PRESETS)))
        base, size, crop = _PRESETS[preset]
        self.__m_base_size = int(kwargs.pop('baseSize', base))
        self.__m_image_size = int(kwargs.pop('imageSize', size))
        self.__m_crop_mode = bool(kwargs.pop('cropMode', crop))
        self.__m_device = kwargs.pop('device', None)
        self.__m_prompt = kwargs.pop('ocrPrompt', '<image>\n<|grounding|>OCR this image. ')  # 'prompt': base
        self.__m_attention = kwargs.pop('attention', None)
        super(DeepSeekOcr, self).__init__(*args, **kwargs)
        self.setOnline(False)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Model management                                                   #
    # ------------------------------------------------------------------ #
    def _pickDevice(self):
        """
        Auto-detect the best available torch device.
        """
        if self.__m_device:
            return self.__m_device
        from torch import cuda, backends
        if cuda.is_available():
            return 'cuda'
        if getattr(backends, 'mps', None) is not None and backends.mps.is_available():
            return 'mps'
        return 'cpu'

    @staticmethod
    def _shimTransformers():
        """
        Compatibility shim for transformers >= 4.47.
        The model's remote code does::
            from transformers.models.llama.modeling_llama import (
                LlamaAttention, LlamaFlashAttention2)
        but ``LlamaFlashAttention2`` / ``LlamaSdpaAttention`` were
        REMOVED in transformers 4.47 (attention refactor), producing
        ``ImportError: cannot import name 'LlamaFlashAttention2'``.
        DeepSeek-OCR runs MLA attention by default (config.use_mla is
        True), so those Llama classes are imported but never actually
        instantiated -- they are MHA-mode fallbacks only. Aliasing the
        missing names to the unified ``LlamaAttention`` therefore lets
        the remote code import cleanly without changing behaviour.
        """
        try:
            from transformers.models.llama import modeling_llama
        except ImportError:
            return
        base = getattr(modeling_llama, 'LlamaAttention', None)
        if base is None:
            return
        for name in ('LlamaFlashAttention2', 'LlamaSdpaAttention'):
            if not hasattr(modeling_llama, name):
                setattr(modeling_llama, name, base)
        # Some revisions also look the classes up via this mapping.
        if not hasattr(modeling_llama, 'LLAMA_ATTENTION_CLASSES'):
            modeling_llama.LLAMA_ATTENTION_CLASSES = {
                'eager': base,
                'flash_attention_2': getattr(modeling_llama, 'LlamaFlashAttention2', base),
                'sdpa': getattr(modeling_llama, 'LlamaSdpaAttention', base),
            }

    def _loadModel(self, device):
        """
        Load (or fetch from cache) the model and tokenizer.
        """
        key = (self.getModel(), device)
        if key in DeepSeekOcr._models:
            return DeepSeekOcr._models[key]
        from transformers import AutoModel, AutoTokenizer
        from torch import cuda, bfloat16, float16, float32
        self._shimTransformers()
        extra = {}
        if self.__m_revision:
            # Pins BOTH the weights and the trust_remote_code files
            # (silences the "new version downloaded" warning and makes
            # loads reproducible).
            extra['revision'] = self.__m_revision
            extra['code_revision'] = self.__m_revision
        tokenizer = AutoTokenizer.from_pretrained(self.getModel(), trust_remote_code=True, **extra)
        # Decide the dtype BEFORE loading: without it, from_pretrained
        # materializes ~12+ GB of float32 weights in CPU RAM first,
        # which crashes small machines (e.g. Colab free tier ~12.7 GB
        # RAM: "session crashed after using all available RAM").
        # torch_dtype + low_cpu_mem_usage memory-maps the safetensors
        # and loads directly in half precision instead.
        if device == 'cuda':
            # Older GPUs (e.g. Colab's T4, pre-Ampere) lack bfloat16
            # hardware support: use float16 there.
            supported = getattr(cuda, 'is_bf16_supported', None)
            dtype = (bfloat16 if supported and supported() else float16)
        elif device == 'mps':
            dtype = float16
        else:
            dtype = float32
        attentions = ([self.__m_attention] if self.__m_attention else ['flash_attention_2', 'sdpa', 'eager'])
        model = None
        last_exc = None
        for attn in attentions:
            for dtype_kwargs in (
                    {'torch_dtype': dtype, 'low_cpu_mem_usage': True},
                    {'dtype': dtype},  # transformers v5 renamed it
                    {}):  # last resort: defaults
                try:
                    model = AutoModel.from_pretrained(self.getModel(), _attn_implementation=attn,
                                                      trust_remote_code=True, use_safetensors=True,
                                                      **dtype_kwargs, **extra)
                    break
                except TypeError as exc:
                    # Unknown kwarg on this transformers version: retry
                    # with the next kwarg style.
                    last_exc = exc
                except Exception as exc:  # noqa: BLE001 - flash-attn etc.
                    last_exc = exc
                    break  # not a kwarg problem: try next attention impl
            if model is not None:
                break
        if model is None:
            try:
                import transformers as _tf
                installed = _tf.__version__
            except Exception:  # noqa: BLE001
                installed = 'unknown'
            message = 'Could not load {} (transformers {}): {}'.format(self.getModel(), installed, last_exc)
            text = str(last_exc)
            if "DeepseekV2" in text and 'cannot import name' in text:
                message += (
                    "\nYour transformers version predates the built-in "
                    "DeepseekV2 architecture (added in transformers "
                    "4.54.0), which this model's remote code requires. "
                    "Fix: pip install -U \"transformers>=4.54\" "
                    "(transformers==4.57.2 is the version the "
                    "community-fixed weights were validated on).")
            elif 'cannot import name' in text:
                message += (
                    "\nThis is the known incompatibility between the "
                    "model's remote code (written for transformers=="
                    "4.46.3) and your installed transformers version. "
                    "Options: (1) for DeepSeek-OCR v1, use the "
                    "community-fixed weights: model="
                    "'strangervisionhf/deepseek-ocr-latest-transformers'"
                    " with transformers>=4.54; (2) on Python <=3.12, "
                    "pin: pip install transformers==4.46.3 "
                    "tokenizers==0.20.3; (3) check the model's HF "
                    "discussions for a fixed revision to pass as "
                    "revision=...")
            raise OCRError(message)
        model = model.eval().to(device).to(dtype)
        DeepSeekOcr._models[key] = (model, tokenizer)
        return DeepSeekOcr._models[key]

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _pilImage(self):
        """
        The page as an RGB PIL image, whatever the source. DeepSeek-OCR
        applies EXIF orientation itself when it opens files; we do the
        same here so results match.
        """
        from io import BytesIO
        from PIL import Image, ImageOps
        kind = self.imageKind()
        source = self.getImage()
        if kind == 'pil':
            image = source
        elif kind == 'array':
            image = Image.fromarray(source)
        elif kind == 'url':
            from requests import get
            reply = get(source, timeout=self.getTimeout())
            reply.raise_for_status()
            image = Image.open(BytesIO(reply.content))
        elif kind in ('path', 'bytes', 'buffer'):
            image = Image.open(BytesIO(self.imageBytes()))
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(source))
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:  # noqa: BLE001 - orientation is best-effort
            pass
        return image.convert('RGB')

    # ------------------------------------------------------------------ #
    # Output parsing                                                     #
    # ------------------------------------------------------------------ #
    @classmethod
    def _parseGrounding(cls, text, width, height):
        """
        Parse <|ref|>...<|/ref|><|det|>[[...]]<|/det|> pairs into word
        dicts with pixel coordinates (grounding grid is 0-1000).
        """
        words = []
        for match in _GROUNDING.finditer(text or ''):
            label = match.group(1).strip()
            if not label or label.lower() in _SKIP_LABELS:
                continue
            try:
                boxes = loads(match.group(2))
            except ValueError:
                continue
            for box in boxes:
                if len(box) != 4:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box)
                left = x1 / _COORD_SPACE * width
                top = y1 / _COORD_SPACE * height
                words.append(cls.makeWord(
                    label, left, top,
                    max((x2 - x1) / _COORD_SPACE * width, 1.0),
                    max((y2 - y1) / _COORD_SPACE * height, 1.0),
                ))
        return words

    @classmethod
    def _plainToRows(cls, text):
        """
        No grounding markers in the reply (plain-text prompt): keep the
        unified structure by giving each text line a synthetic stacked
        box so reading order is preserved.
        """
        words = []
        row = 0
        for line in (text or '').splitlines():
            line = line.strip()
            if not line:
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
                 Grounded regions are typically LINES; the base class
                 groups them geometrically like the other engines.
        """
        device = self._pickDevice()
        model, tokenizer = self._loadModel(device)
        image = self._pilImage()
        width, height = image.size
        with InMemoryInfer(model, [image]):
            reply = model.infer(tokenizer, prompt=self.composePrompt(self.__m_prompt), image_file=_IN_MEMORY,
                                output_path='',
                                base_size=self.__m_base_size, image_size=self.__m_image_size,
                                crop_mode=self.__m_crop_mode, save_results=False, eval_mode=True)
        if not (isinstance(reply, str) or hasattr(reply, 'encode')):
            # Some revisions return dict-like results; be permissive.
            reply = str(getattr(reply, 'text', '') or reply or '')
        words = self._parseGrounding(reply, width, height)
        if not words:
            # Plain-text reply (no grounding markers): strip any stray
            # special tokens and keep reading order with row boxes.
            cleaned = sub(r'<\|[^|>]*\|>', '', reply)
            words = self._plainToRows(cleaned)
        return words
