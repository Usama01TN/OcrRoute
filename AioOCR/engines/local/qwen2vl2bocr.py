# coding=utf-8
"""
Qwen2-VL-2B-OCR plugin — JackChew's document-extraction fine-tune.
    https://huggingface.co/JackChew/Qwen2-VL-2B-OCR
A LoRA fine-tune of ``unsloth/Qwen2-VL-2B-Instruct`` trained for one job:
reading a document image out **completely**, with nothing dropped. Its
model card benchmarks it against the stock Qwen2-VL-2B on a payslip,
where the base model silently skipped the whole Deductions block and to
fine-tune kept it. Payslips, invoices and tables are what it was tuned
on, and it returns a markdown-style transcription — headings, key/value
lines and Markdown tables — rather than flat OCR lines.
It runs locally through ``transformers``: the model is imported and
called in-process, with no server, no HTTP API, no subprocess and no
temporary file. Pages are decoded in memory, and PDFs rasterize in RAM.
This builds on the ``QwenVlOcr`` engine, since the loading, generation
and page handling are the same. What differs is what the model can do:
* The repository is pinned, and the default prompt is the one the card
  recommends, which materially changes how much text comes back.
* Document presets (``payslip``, ``invoice``, ``table``, ``full``) send
  the wording this fine-tune responds best to.
* **It cannot ground text.** To fine-tune was never trained to emit
  coordinates, so ``task='boxes'`` is refused with a pointer to a model
  that can, rather than quietly returning invented geometry.
* ``maxSide`` downscales large pages before inference, which is the
  card's own remedy for running out of VRAM at 2B on 16 GB.
Install::
    pip install "transformers>=4.45" accelerate torch torchvision pillow
Usage::
    from qwen2vl2bocr import Qwen2Vl2bOcr
    result = Qwen2Vl2bOcr(image='payslip.png').parse()
    result = Qwen2Vl2bOcr(image='invoice.jpg', task='invoice').parse()
    result = Qwen2Vl2bOcr(image='report.pdf', maxSide=1600).parse()
    result = Qwen2Vl2bOcr(image='card.jpg', task='kv', schema={'name': '', 'net pay': ''}).parse()
    plugin = Qwen2Vl2bOcr(image='payslip.png', device='cuda:0')
    result = plugin.parse()
    plugin.getMarkdown()     # the transcription with its table markup
    plugin.getRaw()          # what the model actually generated
"""
from os.path import dirname
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .qwenvlocrlib import QwenVlOcr
    from .ocrplugin import OCRError
except:
    from qwenvlocrlib import QwenVlOcr
    from engines.ocrplugin import OCRError

#: The fine-tune this plugin exists for.
MODEL = 'JackChew/Qwen2-VL-2B-OCR'
#: Tasks this model is able to do. It transcribes; it does not ground,
#: so the box-producing tasks of the base engine are absent on purpose.
TASKS = ('full', 'payslip', 'invoice', 'table', 'text', 'markdown', 'kv')
#: Tasks the base engine offers that this fine-tune cannot serve.
UNSUPPORTED_TASKS = ('boxes', 'html')
#: Instructions per task. ``full`` uses the phrasing the model card
#: names as the one the fine-tune works best with; the rest keep that
#: shape — an explicit "miss nothing" instruction — since that is what
#: the fine-tuning data rewarded.
TASK_PROMPTS = {
    'full': 'Extract all text from image without miss anything',
    'payslip': 'Extract all text from payslip without miss anything, '
               'including every earnings and deductions row',
    'invoice': 'Extract all text from this invoice without miss anything, '
               'including every line item, total and tax row',
    'table': 'Extract every table from this image without miss anything, '
             'keeping the rows and columns as a markdown table',
    'text': 'Extract all text from image without miss anything, in '
            'reading order and with no commentary',
    'markdown': 'Extract all content from this document without miss '
                'anything, as markdown with headings and tables',
    'kv': 'Extract every field and its value from this image without '
          'miss anything, and output valid JSON only',
}


class Qwen2Vl2bOcr(QwenVlOcr):
    """
    JackChew/Qwen2-VL-2B-OCR: a 2B document-transcription model.
    Everything the base ``QwenVlOcr`` engine provides — model caching,
    PDF pages, every input type, the row layout, ``getMarkdown()`` and
    ``getRaw()`` — applies here too. This class pins the checkpoint,
    supplies the prompts it was tuned for, and is honest about the one
    thing it cannot do.
    """
    #: Recommended VRAM for the 2B checkpoint, per the model card.
    RECOMMENDED_VRAM_GB = 16

    def __init__(self, *args, **kwargs):
        """
        :param image: path | URL | base64/bytes | BytesIO | PIL image |
                      numpy array | PDF (path or bytes)
        :param task: 'full' (default), 'payslip', 'invoice', 'table',
                     'text', 'markdown' or 'kv'
        :param ocrPrompt: custom instruction, replacing the task prompt (was ``prompt=``)
        :param prompt: extra instructions appended to the prompt actually sent (base-class feature,
                       e.g. 'amounts are in MYR'); change at runtime with ``setExtraPrompt()``
        :param schema: dict of {field: hint} for the 'kv' task
        :param maxSide: downscale pages whose longest side exceeds this,
                        the card's remedy for CUDA out-of-memory
        :param model: override the pinned repository (a sibling
                      fine-tune, or a local copy of this one)
        Every other keyword — device, dtype, deviceMap,
        attnImplementation, maxNewTokens, wordBox, pdfScale, pages,
        modelParams, generateParams, cache — behaves as in QwenVlOcr.
        """
        self.__m_ocrTask = str(kwargs.pop('task', 'full')).lower()
        self.__m_ocrPrompt = kwargs.pop('ocrPrompt', '')  # 'prompt' stays for the base class
        self.__m_ocrSchema = kwargs.pop('schema', None)
        self.__m_maxSide = kwargs.pop('maxSide', None)
        kwargs.setdefault('model', MODEL)
        # The fine-tune answers in long Markdown; the base cap is enough
        # for a payslip but not for a dense multi-column page.
        kwargs.setdefault('maxNewTokens', 4096)
        super(Qwen2Vl2bOcr, self).__init__(*args, **kwargs)

    # ------------------------------------------------------------------ #
    # Tasks and prompting                                                #
    # ------------------------------------------------------------------ #
    def getTask(self):
        """
        :return: the validated task name.
        """
        task = self.__m_ocrTask
        if task in UNSUPPORTED_TASKS:
            raise OCRError(
                '{} was fine-tuned to transcribe documents, not to locate '
                "text, so task={!r} cannot be served: it would return "
                'invented coordinates. Use QwenVlOcr with a grounding '
                'model (Qwen/Qwen3-VL-2B-Instruct) for boxes, or one of: {}.'.format(MODEL, task, ', '.join(TASKS)))
        if task not in TASKS:
            raise OCRError('Unknown task {!r}; expected one of {}.'.format(task, ', '.join(TASKS)))
        return task

    def setTask(self, task):
        """
        :param task: str | unicode
        :return:
        """
        self.__m_ocrTask = task.lower()

    def prompt(self):
        """
        :return: the instruction sent to the model (custom or task prompt, plus the base class's extra prompt).
        """
        if self.__m_ocrPrompt:
            return self.composePrompt(self.__m_ocrPrompt)
        prompt = TASK_PROMPTS[self.getTask()]
        if self.getTask() == 'kv' and self.__m_ocrSchema:
            from json import dumps
            prompt += '. Fill in this JSON schema: {}'.format(dumps(self.__m_ocrSchema, ensure_ascii=False))
        return self.composePrompt(prompt)

    def getPrompt(self):
        """
        :return: str | unicode
        """
        return self.__m_ocrPrompt

    def setPrompt(self, prompt):
        """
        :param prompt: str | unicode
        :return:
        """
        self.__m_ocrPrompt = prompt

    def getSchema(self):
        """
        :return: dict | None
        """
        return self.__m_ocrSchema

    def setSchema(self, schema):
        """
        :param schema: dict | None
        :return:
        """
        self.__m_ocrSchema = schema

    # ------------------------------------------------------------------ #
    # Pages                                                              #
    # ------------------------------------------------------------------ #
    def _loadPages(self, image=None):
        """
        Decode the pages, then downscale them if ``maxSide`` asks for it.
        A 2B vision model on a 16 GB card runs out of memory on large
        scans; the model card's own advice is to shrink the image before
        inference, and this is that, done in memory.
        :return: list[PIL.Image.Image]
        """
        pages = super(Qwen2Vl2bOcr, self)._loadPages(image)
        limit = self.__m_maxSide
        if not limit:
            return pages
        limit = int(limit)
        scaled = []
        for page in pages:
            width, height = page.size
            longest = max(width, height)
            if longest <= limit:
                scaled.append(page)
                continue
            ratio = float(limit) / float(longest)
            scaled.append(page.resize((max(1, int(round(width * ratio))), max(1, int(round(height * ratio))))))
        return scaled

    def getMaxSide(self):
        """
        :return: int | None
        """
        return self.__m_maxSide

    def setMaxSide(self, maxSide):
        """
        :param maxSide: int | None
        :return:
        """
        self.__m_maxSide = maxSide

    task = property(fget=getTask, fset=setTask)
    prompt_ = property(fget=getPrompt, fset=setPrompt)
    schema = property(fget=getSchema, fset=setSchema)
    maxSide = property(fget=getMaxSide, fset=setMaxSide)


#: Alias matching the repository name.
QwenTwoVlOcr = Qwen2Vl2bOcr

__all__ = ['Qwen2Vl2bOcr', 'QwenTwoVlOcr', 'MODEL', 'TASKS',
           'UNSUPPORTED_TASKS', 'TASK_PROMPTS']
