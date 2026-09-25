# coding=utf-8
"""
MonkeyOCR-pro plugin (local, https://huggingface.co/echo840/MonkeyOCR-pro-1.2B).
MonkeyOCR-pro-1.2B is the sweet spot of the MonkeyOCR family: per its
own model card it SURPASSES the original MonkeyOCR-3B (by 7.4% on
Chinese documents), runs ~36% faster than pro-3B with only ~1.6%
lower accuracy, beats Nanonets-OCR-3B by 7.3% on olmOCR-Bench, and
fits on ~8 GB VRAM (4060 works; quantizable via AWQ). Fully local.
This plugin loads the pro checkpoint's Qwen2.5-VL-based RECOGNITION
model straight from Hugging Face with transformers -- no repo clone::
    pip install transformers torch pillow accelerate
    pip install pypdfium2        # only for PDF input
The pro weight repos have shipped the recognition model both under a
``Recognition/`` subfolder (like the base repo) and flat at the repo
root, so the plugin AUTO-DETECTS the layout: it tries
``subfolder='Recognition'`` first and falls back to a flat load,
caching whichever works. Pin a layout with ``subfolders=('',)`` or
``subfolders=('Recognition'),`` if you know yours.
Everything else -- the official task prompts (text/table/formula),
custom prompts, PDF rasterization with page selection, chat-template
generation, engine caching, and the CUDA-OOM guidance -- is inherited
from the MonkeyOcrHf plugin. Per the model card, MonkeyOCR targets
printed English/Chinese documents (handwriting, photographed text and
other languages are not fully supported) and the license is
academic/non-commercial.
The Recognition model alone returns NO coordinates -- ordered rows in
the unified structure (like OCR.Space's shape, rows tier). For
block-level boxes use the full-pipeline monkeyocrpro.
Usage::
    from monkeyocrpro import MonkeyOcrPro
    result = MonkeyOcrPro(image='doc.jpg').parse()
    result = MonkeyOcrPro(image='table.png', task='table').parse()
    result = MonkeyOcrPro(image='scan.pdf', pages=[0, 1]).parse()
    result = MonkeyOcrPro(image='x.png', model='echo840/MonkeyOCR-pro-3B').parse()
"""
from os.path import dirname
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .monkeyocrhf import MonkeyOcrHf
    from .ocrplugin import OCRError
except:
    from monkeyocrhf import MonkeyOcrHf
    from engines.ocrplugin import OCRError

_DEFAULT_MODEL = 'echo840/MonkeyOCR-pro-1.2B'
#: Repo layouts to try, in order: Recognition subfolder, then flat.
_DEFAULT_SUBFOLDERS = ('Recognition', '')


class MonkeyOcrPro(MonkeyOcrHf):
    """
    MonkeyOcrPro class.
    """
    #: (model, layout) -> (processor, model, device), loaded once.
    _engines = {}

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'echo840/MonkeyOCR-pro-1.2B'; also 'echo840/MonkeyOCR-pro-3B').
        :param subfolders: repo layouts to try, in order (default ('Recognition', '') -- subfolder first, then flat). 
                            A plain ``subfolder='...'`` kwarg pins exactly one.
        :param task: 'text' (default), 'table', or 'formula'.
        :param ocrPrompt: custom prompt overriding the task prompt (was ``prompt=``).
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature, e.g.
                       'the text is French'); change at runtime with ``setExtraPrompt()``. These small
                       task-tuned models follow their fixed prompts best, so keep additions short.
        :param maxTokens: generation budget per page (default 4096).
        :param device: 'cuda', 'cpu', or None for auto-detect.
        :param pages: optional list of 0-based PDF page indices.
        :param image: page image or PDF source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        kwargs.setdefault('model', _DEFAULT_MODEL)
        model = kwargs['model']
        if 'subfolder' in kwargs:
            self.__m_subfolders = (kwargs.pop('subfolder') or '',)
        else:
            self.__m_subfolders = tuple(kwargs.pop('subfolders', _DEFAULT_SUBFOLDERS))
        self.__m_device_wanted = kwargs.get('device', None)
        super(MonkeyOcrPro, self).__init__(*args, **kwargs)
        self.setModel(model)

    # ------------------------------------------------------------------ #
    # Engine with repo-layout auto-detection                             #
    # ------------------------------------------------------------------ #
    def _engine(self):
        for layout in self.__m_subfolders:
            key = (self.getModel(), layout)
            if key in MonkeyOcrPro._engines:
                return MonkeyOcrPro._engines[key]
        try:
            from transformers import AutoProcessor
            from torch import cuda, float16, float32
        except ImportError:
            raise OCRError(
                'MonkeyOCR needs transformers and torch. Run: pip install transformers torch pillow accelerate')
        device = self.__m_device_wanted or ('cuda' if cuda.is_available() else 'cpu')
        dtype = float16 if device == 'cuda' else float32
        attempts = []
        for layout in self.__m_subfolders:
            load_kwargs = {'trust_remote_code': True}
            if layout:
                load_kwargs['subfolder'] = layout
            try:
                processor = AutoProcessor.from_pretrained(self.getModel(), **load_kwargs)
                try:
                    from transformers import Qwen2_5_VLForConditionalGeneration
                    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                        self.getModel(), torch_dtype=dtype, **load_kwargs)
                except (ImportError, OSError, ValueError):
                    from transformers import AutoModelForVision2Seq
                    model = AutoModelForVision2Seq.from_pretrained(self.getModel(), torch_dtype=dtype, **load_kwargs)
            except Exception as exc:  # noqa: BLE001 - try next layout.
                attempts.append("{} -> {}: {}".format(layout or '<flat>', type(exc).__name__, str(exc)[:120]))
                continue
            model = model.to(device)
            model.eval()
            engine = (processor, model, device)
            MonkeyOcrPro._engines[(self.getModel(), layout)] = engine
            return engine
        raise OCRError(
            "Could not load '{}' under any repo layout (tried: {}). "
            'Check the checkpoint name and your network/HF access, or '
            "pin the layout with subfolder='Recognition' / "
            "subfolder='' .".format(self.getModel(), '; '.join(attempts) or 'none'))
