# coding=utf-8
"""
Tesseract OCR plugin with automatic preprocessing (ensemble version).
Raw Tesseract performs poorly on screenshots and UI captures: light
text on dark backgrounds, small fonts, and icons hallucinated as words.
When ``preprocess='auto'`` (the default) this plugin:
1. converts to grayscale and INVERTS the image when the background is
   dark (Tesseract expects dark text on a light background);
2. adaptively stretches contrast and UPSCALES small images by integer
   factors (small UI fonts need ~20px+ glyph height);
3. runs FOUR variants: Otsu binary (crisp strong text), soft binary
   (keeps faint strokes), sharpened grayscale, and plain grayscale;
4. clusters detections across variants by bounding-box overlap, then
   ACCEPTS a cluster only if some variant is highly confident OR at
   least two variants AGREE on the exact same text -- icon garbage
   rarely repeats identically across variants, real text does. This
   voting makes results stable across Tesseract builds/versions whose
   confidence calibrations differ;
5. within a cluster, the variant with the best mean confidence provides
   the words (so 'Server 2012 R2' beats a merged 'Server2012R2').
Bounding boxes are scaled back to the ORIGINAL image coordinates.
Set ``preprocess=False`` to feed the image to Tesseract untouched.
"""
from pytesseract import pytesseract, Output
from os.path import exists, dirname
from shutil import which
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

# Locate the tesseract binary portably (common platform locations,
# then PATH) instead of hardcoding a Windows path.
_CANDIDATES = (
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    '/usr/bin/tesseract',
    '/usr/local/bin/tesseract',
    '/opt/homebrew/bin/tesseract',
    which('tesseract'),
)
for _cmd in _CANDIDATES:
    if _cmd and exists(_cmd):
        pytesseract.tesseract_cmd = _cmd
        break
# Map short ISO codes to tesseract's 3-letter codes.
_LANG_MAP = {'en': 'eng', 'fr': 'fra', 'de': 'deu', 'es': 'spa',
             'it': 'ita', 'pt': 'por', 'nl': 'nld', 'ar': 'ara',
             'ru': 'rus', 'zh': 'chi_sim', 'ja': 'jpn'}
#: Words below this are not even collected from a variant.
_COLLECT_FLOOR = 25.0
#: A cluster is accepted alone when some variant reaches this.
_SOLO_CONFIDENCE = 70.0
#: ...or when >= 2 variants agree on the same text at this level.
_VOTE_CONFIDENCE = 45.0
#: Words emitted from the winning variant must reach this.
_KEEP_FLOOR = 40.0
#: Upscale small images so their longest side reaches this many pixels
#: (capped at 3x integer factor).
_TARGET_SIZE = 2000


class Tesseract(OCRPlugin):
    """
    Tesseract class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param language: document language(s).
        :param image: image source (path, PIL, bytes...).
        :param minConfidence: extra confidence filter on the final words. Accepts 0-100 or a 0-1 fraction (0.3 == 30).
                                Auto mode already filters noise by cross-variant voting, so this is optional there;
                                in raw mode it is the only filter (default 0).
        :param preprocess: 'auto' (default) or False for raw mode.
        :param psm: Tesseract page segmentation mode (default 3).
        :param kwargs: other settings.
        """
        conf = float(kwargs.pop('minConfidence', -1))
        if 0 < conf <= 1:
            conf *= 100  # Fraction given (e.g. 0.3 meaning 30%).
        self.__m_minConfidence = conf
        self.__m_preprocess = kwargs.pop('preprocess', 'auto')
        self.__m_psm = int(kwargs.pop('psm', 3))
        super(Tesseract, self).__init__(*args, **kwargs)
        self.setOnline(False)

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _tessLang(self):
        """
        Build a tesseract language string like 'eng+fra'.
        """
        langs = self.getLanguage()
        if isinstance(langs, str):
            langs = [langs]
        return '+'.join(_LANG_MAP.get(l, l) for l in langs) or 'eng'

    def _loadPil(self):
        """
        Load the current image source as a PIL image.
        """
        from io import BytesIO
        from PIL import Image
        kind = self.imageKind()
        if kind == 'path':
            return Image.open(self.getImage())
        if kind == 'pil':
            return self.getImage()
        if kind in ('bytes', 'buffer'):
            return Image.open(BytesIO(self.imageBytes()))
        if kind == 'array':
            return Image.fromarray(self.getImage())
        if kind == 'url':
            raise OCRError('Tesseract is offline; download the URL first.')
        raise OCRError(
            "Image source '{}' is not an existing file or a supported "
            "type. Check the path (the current working directory "
            "matters for relative paths).".format(self.getImage()))

    @staticmethod
    def _otsuThreshold(gray_array):
        """
        Compute Otsu's global threshold for a grayscale numpy array.
        """
        from numpy import histogram, dot, arange
        hist, _ = histogram(gray_array, bins=256, range=(0, 256))
        total = gray_array.size
        sumAll = float(dot(arange(256), hist))
        sumB = 0.0
        weightB = 0
        bestT, bestVar = 128, 0.0
        for t in range(256):
            weightB += int(hist[t])
            if not weightB:
                continue
            weightF = total - weightB
            if not weightF:
                break
            sumB += t * float(hist[t])
            mean_b = sumB / weightB
            mean_f = (sumAll - sumB) / weightF
            var = weightB * weightF * (mean_b - mean_f) ** 2
            if var > bestVar:
                bestVar, bestT = var, t
        return bestT

    def _ocrWords(self, pil_image, scale, conf_min, src=0):
        """
        Run one Tesseract pass; return word dicts in ORIGINAL coords.
        """
        data = pytesseract.image_to_data(
            pil_image, lang=self._tessLang(), config='--psm {}'.format(self.__m_psm), output_type=Output.DICT)
        words = []
        for i in range(len(data['text'])):
            text = data['text'][i].strip()
            if not text:
                continue
            try:
                conf = float(data['conf'][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < conf_min:
                continue
            word = self.makeWord(
                text,
                data['left'][i] / scale, data['top'][i] / scale,
                data['width'][i] / scale, data['height'][i] / scale,
            )
            word['_conf'] = conf
            word['_src'] = src
            words.append(word)
        return words

    @staticmethod
    def _overlap(a, b):
        """
        Intersection area over the SMALLER box (0..1).
        """
        x1 = max(a['Left'], b['Left'])
        y1 = max(a['Top'], b['Top'])
        x2 = min(a['Left'] + a['Width'], b['Left'] + b['Width'])
        y2 = min(a['Top'] + a['Height'], b['Top'] + b['Height'])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        smaller = min(a['Width'] * a['Height'], b['Width'] * b['Height'])
        return (x2 - x1) * (y2 - y1) / max(smaller, 1.0)

    @classmethod
    def _clusterWords(cls, words):
        """
        Group overlapping detections (across variants) into clusters.
        """
        clusters = []
        for word in words:
            hits = [i for i, cluster in enumerate(clusters)
                    if any(cls._overlap(word, member) >= 0.4 for member in cluster)]
            if not hits:
                clusters.append([word])
            else:
                base = clusters[hits[0]]
                base.append(word)
                # A wide detection can bridge several clusters: merge.
                for i in reversed(hits[1:]):
                    base.extend(clusters[i])
                    del clusters[i]
        return clusters

    def _selectFromClusters(self, clusters):
        """
        Vote-based acceptance + best-variant selection per cluster.
        """
        floor = self.__m_minConfidence if self.__m_minConfidence >= 0 else 0.0
        keep_floor = max(_KEEP_FLOOR, floor)
        final = []
        for cluster in clusters:
            max_conf = max(w['_conf'] for w in cluster)
            # Text agreement: same exact text from >= 2 distinct
            # variants at reasonable confidence.
            seen = {}
            for w in cluster:
                if w['_conf'] >= _VOTE_CONFIDENCE:
                    seen.setdefault(w['WordText'], set()).add(w['_src'])
            agreed = any(len(srcs) >= 2 for srcs in seen.values())
            if max_conf < _SOLO_CONFIDENCE and not agreed:
                continue  # icon/graphic noise
            # Winner: variant whose words in this cluster have the best
            # mean confidence (finer segmentations win when better).
            by_src = {}
            for w in cluster:
                by_src.setdefault(w['_src'], []).append(w)
            best = max(by_src.values(), key=lambda ws: sum(w['_conf'] for w in ws) / len(ws))
            final.extend(w for w in best if w['_conf'] >= keep_floor)
        return final

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble.
        """
        pil = self._loadPil()
        if not self.__m_preprocess:
            conf = self.__m_minConfidence if self.__m_minConfidence >= 0 else 0
            words = self._ocrWords(pil, 1.0, conf)
        else:
            words = self._autoPipeline(pil)
        for word in words:
            word.pop('_conf', None)
            word.pop('_src', None)
        return words

    def _autoPipeline(self, pil):
        """
        Auto preprocessing + four-variant ensemble OCR + voting.
        """
        from PIL import Image, ImageFilter, ImageOps
        from numpy import asarray, median
        gray = pil.convert('L')
        # 1. Invert when the background is dark (mean brightness).
        if asarray(gray).mean() < 128:
            gray = ImageOps.invert(gray)
        # 2. ADAPTIVE contrast stretch + integer upscale. Order matters:
        #    - Documents with an already-white background (median >=
        #      245): clipping the histogram before upscaling corrupts
        #      thin anti-aliased strokes, so stretch AFTER.
        #    - Screenshots/photos (dimmer background): faint text must
        #      be stretched BEFORE interpolation or it is lost.
        clean_document = float(median(asarray(gray))) >= 245
        if not clean_document:
            gray = ImageOps.autocontrast(gray, cutoff=1)
        scale = 1.0
        longest = max(gray.size)
        if longest < _TARGET_SIZE:
            # Integer factors only: fractional LANCZOS scaling creates
            # interpolation artifacts that measurably hurt Tesseract.
            scale = float(min(3, -(-_TARGET_SIZE // longest)))
            gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.LANCZOS)
        if clean_document:
            gray = ImageOps.autocontrast(gray, cutoff=1)
        # 3. Four variants: strong binary, soft binary (keeps faint
        #    strokes), sharpened grayscale, plain grayscale.
        threshold = self._otsuThreshold(asarray(gray))
        soft = (threshold + 255) // 2
        variants = (
            gray.point(lambda p, t=threshold: 0 if p < t else 255),
            gray.point(lambda p, t=soft: 0 if p < t else 255),
            gray.filter(ImageFilter.SHARPEN), gray)
        words = []
        for src, im in enumerate(variants):
            words.extend(self._ocrWords(im, scale, _COLLECT_FLOOR, src))
        # 4-5. Cluster across variants, vote, select best segmentation.
        return self._selectFromClusters(self._clusterWords(words))
