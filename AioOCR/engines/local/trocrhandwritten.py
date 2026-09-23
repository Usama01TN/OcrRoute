# coding=utf-8
"""
TrOCR handwritten plugin (local,
https://huggingface.co/microsoft/trocr-base-handwritten).
The handwriting specialist of the suite: Microsoft's TrOCR fine-tuned
on the IAM handwriting database -- the classic transformer recognizer
for handwritten ENGLISH text lines. Ships as a preset of the TrOcr
plugin (keep trocr.py alongside), inheriting its
detector+recognizer architecture: text lines are located first
(RapidOCR's PP-OCR detector when installed, OpenCV heuristic
otherwise, or your own ``boxes=``), then each cropped line is read by
TrOCR -- so results carry REAL detector pixel line boxes.
Install::
    pip install transformers torch pillow
    pip install rapidocr onnxruntime     # recommended page-mode detector
Checkpoints (``model=``):
    microsoft/trocr-base-handwritten     default
    microsoft/trocr-large-handwritten the best quality, slower
    microsoft/trocr-small-handwritten    fastest
Handwriting-specific notes:
- BEAM SEARCH helps handwriting: pass ``numBeams=4`` (default stays
  greedy like the official snippet). Beams cost linear extra compute
  per line.
- The line DETECTORS are tuned for printed text; they usually work on
  neat handwriting, but for cursive or messy pages results improve
  with your own ``boxes=[[x, y, w, h], ...]`` (add a few px margin)
  or ``mode='line'`` on pre-cropped strips.
- English only (IAM training data); printed documents read better
  with the printed checkpoints (TrOcr plugin) or RapidOcr.
Everything lands in the exact unified structure shared by every plugin (like OCR.Space).
Usage::
    from trocrhandwritten import TrOcrHandwritten
    result = TrOcrHandwritten(image='note.jpg').parse()
    result = TrOcrHandwritten(image='note.jpg', numBeams=4).parse()
    result = TrOcrHandwritten(image='line.png', mode='line').parse()
    result = TrOcrHandwritten(image='form.png', boxes=[[40, 120, 500, 60]]).parse()
"""
from os.path import dirname
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .trocr import TrOcr
except:
    from trocr import TrOcr

_DEFAULT_MODEL = 'microsoft/trocr-base-handwritten'


class TrOcrHandwritten(TrOcr):
    """
    TrOcrHandwritten class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param model: checkpoint (default 'microsoft/trocr-base-handwritten';
                        small/large variants listed in the module doc).
        :param numBeams: beam-search width (default 1 = greedy, per
                         the official snippet; 4 is a good handwriting setting).
        :param mode: 'auto' (default), 'line', or 'page' (inherited).
        :param boxes: optional [[x, y, w, h], ...] line boxes (inherited; skips detection).
        :param detector: 'auto', 'rapidocr', or 'cv' (inherited).
        :param batchSize: line crops per batch (inherited, default 8).
        :param maxTokens: generation budget per line (inherited, default 64).
        :param device: 'cuda', 'cpu', or None (inherited).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (retries...).
        """
        kwargs.setdefault('model', _DEFAULT_MODEL)
        self.__m_numBeams = max(1, int(kwargs.pop('numBeams', 1)))
        self.__m_batchSize = max(1, int(kwargs.get('batchSize', 8)))
        self.__m_maxTokens = int(kwargs.get('maxTokens', 64))
        super(TrOcrHandwritten, self).__init__(*args, **kwargs)

    # ------------------------------------------------------------------ #
    # Recognition (adds beam search to the parent recipe)                #
    # ------------------------------------------------------------------ #
    def _recognize(self, crops):
        """
        Batch line crops -> strings; beam search when numBeams>1.
        """
        from torch import inference_mode
        processor, model, device = self._engine()
        generate_kwargs = {'max_new_tokens': self.__m_maxTokens}
        if self.__m_numBeams > 1:
            generate_kwargs['num_beams'] = self.__m_numBeams
            generate_kwargs['early_stopping'] = True
        texts = []
        for start in range(0, len(crops), self.__m_batchSize):
            chunk = crops[start:start + self.__m_batchSize]
            pixelValues = processor(images=chunk, return_tensors='pt').pixel_values
            with inference_mode():
                generated = model.generate(pixelValues.to(device), **generate_kwargs)
            texts.extend(processor.batch_decode(generated, skip_special_tokens=True))
        return texts
