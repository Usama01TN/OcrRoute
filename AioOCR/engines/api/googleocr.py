# coding=utf-8
"""
Google Cloud Vision OCR plugin (images:annotate REST API).
Google's OCR returns WORD-level bounding boxes -- the same granularity
as OCR.Space -- via the stable v1 endpoint::
    POST https://vision.googleapis.com/v1/images:annotate?key=API_KEY
Setup:
1. In Google Cloud Console (https://console.cloud.google.com) create a
   project, enable the **Cloud Vision API**, and enable billing (the
   free tier -- 1,000 OCR requests per month -- still requires a
   billing account on the project; beyond that it is $1.50 per 1,000).
2. Create an API key (APIs & Services -> Credentials) and restrict it
   to the Cloud Vision API.
3. Pass it as ``api=`` or set GOOGLE_VISION_API_KEY.
Two recognition modes (``mode=``):
    text        TEXT_DETECTION           -- sparse text, screenshots, photos, signs (default)
    document    DOCUMENT_TEXT_DETECTION  -- dense documents, scans, handwriting
The reply's fullTextAnnotation hierarchy (pages > blocks > paragraphs
> words > symbols) is mapped word-by-word with real pixel boxes into
the exact unified structure shared by every plugin.
"""
from base64 import b64encode
from os.path import dirname
from requests import post
from os import environ
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_ENDPOINT = 'https://vision.googleapis.com/v1/images:annotate'
_MODES = {'text': 'TEXT_DETECTION', 'document': 'DOCUMENT_TEXT_DETECTION'}
#: Symbol detectedBreak types that end the word with whitespace; only
#: used to keep intra-word symbol joining faithful.
_BREAKS_WITH_SPACE = frozenset(('SPACE', 'SURE_SPACE', 'EOL_SURE_SPACE', 'LINE_BREAK'))


class GoogleOcr(OCRPlugin):
    """
    GoogleOcr class.
    """

    def __init__(self, *args, **kwargs):
        """
        :param api: Cloud Vision API key (or ``api=``,
                        or the GOOGLE_VISION_API_KEY / GOOGLE_API_KEY environment variables).
        :param mode: 'text' (default) or 'document', see module docstring.
        :param language: optional language hint(s),
            ISO codes ('en', 'fr', ...); usually unnecessary -- Vision auto-detects -- but helps on ambiguous scripts.
        :param minConfidence: drop words below this confidence, 0-1
                                (default 0; confidence comes from the fullTextAnnotation hierarchy).
        :param image: image source (path, URL, PIL, bytes...).
        :param kwargs: other settings (timeout, retries, proxy...).
        """
        api = kwargs.pop('api', environ.get('GOOGLE_VISION_API_KEY', '') or environ.get('GOOGLE_API_KEY', ''))
        mode = str(kwargs.pop('mode', 'text')).lower()
        if mode not in _MODES:
            raise OCRError('Unknown mode {!r}; choose from {}'.format(mode, ', '.join(sorted(_MODES))))
        self.__m_feature = _MODES[mode]
        self.__m_min_confidence = float(kwargs.pop('minConfidence', 0.0))
        kwargs.setdefault('timeout', 60)
        super(GoogleOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(api)
        self.setEndpoint(_ENDPOINT)

    # ------------------------------------------------------------------ #
    # Request building                                                   #
    # ------------------------------------------------------------------ #
    def _imagePart(self):
        """
        Build the request 'image' object (inline bytes or URI).
        """
        kind = self.imageKind()
        if kind == 'url':
            return {'source': {'imageUri': self.getImage()}}
        if kind == 'array':
            from io import BytesIO
            from PIL import Image
            buffer = BytesIO()
            Image.fromarray(self.getImage()).save(buffer, format='PNG')
            data = buffer.getvalue()
        elif kind in ('path', 'pil', 'bytes', 'buffer'):
            data = self.imageBytes()
        else:
            raise OCRError(
                "Image source '{}' is not an existing file, URL, or "
                "supported type. Check the path (the current working "
                "directory matters for relative paths).".format(self.getImage()))
        if data[:5] == b'%PDF-':
            raise OCRError(
                'images:annotate does not accept PDFs. Convert the '
                'page to an image first, or use a PDF-capable plugin '
                '(MistralOcr, GlmOcr, OlmOcr).')
        return {'content': b64encode(data).decode('ascii')}

    def _body(self):
        request = {'image': self._imagePart(), 'features': [{'type': self.__m_feature}]}
        languages = self.getLanguage()
        if languages and languages != ['en']:
            if isinstance(languages, str):
                languages = [languages]
            request['imageContext'] = {'languageHints': [str(l) for l in languages]}
        return {'requests': [request]}

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _boxFromVertices(bounding_box):
        """
        boundingBox/boundingPoly vertices -> (left, top, w, h). Google
        OMITS zero-valued x/y fields, so missing keys default to 0.
        """
        vertices = (bounding_box or {}).get('vertices') or []
        if not vertices:
            return None
        xs = [float(v.get('x', 0)) for v in vertices]
        ys = [float(v.get('y', 0)) for v in vertices]
        left, top = min(xs), min(ys)
        width, height = max(xs) - left, max(ys) - top
        if width <= 0 or height <= 0:
            return None
        return left, top, width, height

    def _wordsFromFullText(self, annotation, y_offset=0.0):
        """
        fullTextAnnotation pages>blocks>paragraphs>words -> words.
        """
        words = []
        for page in annotation.get('pages') or []:
            for block in page.get('blocks') or []:
                for paragraph in block.get('paragraphs') or []:
                    for word in paragraph.get('words') or []:
                        confidence = word.get('confidence')
                        if confidence is not None and float(confidence) < self.__m_min_confidence:
                            continue
                        text = ''.join(symbol.get('text', '') for symbol in word.get('symbols') or [])
                        text = text.strip()
                        if not text:
                            continue
                        rect = self._boxFromVertices(word.get('boundingBox'))
                        if rect is None:
                            continue
                        left, top, width, height = rect
                        words.append(self.makeWord(text, left, y_offset + top, width, height))
            y_offset += float(page.get('height', 0) or 0)
        return words

    def _wordsFromTextAnnotations(self, annotations):
        """
        Fallback: textAnnotations[1:] are word-ish entries.
        """
        words = []
        for entry in (annotations or [])[1:]:
            text = str(entry.get('description', '')).strip()
            if not text:
                continue
            rect = self._boxFromVertices(entry.get('boundingPoly'))
            if rect is None:
                continue
            left, top, width, height = rect
            words.append(self.makeWord(text, left, top, width, height))
        return words

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (WORD-level boxes; the base class groups into lines).
        """
        if not self.getApi():
            raise OCRError(
                'No Google Cloud Vision API key. Enable the Cloud '
                'Vision API on a Google Cloud project (billing '
                'required, 1,000 requests/month free), create an API '
                'key, and pass api=... or set GOOGLE_VISION_API_KEY.')
        request_kwargs = {'params': {'key': self.getApi()}, 'json': self._body(), 'timeout': self.getTimeout()}
        if self.getProxy():
            request_kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **request_kwargs)
        if reply.status_code in (401, 403):
            raise OCRError(
                'Google Vision rejected the request (HTTP {}): check '
                'that the API key is valid, the Cloud Vision API is '
                'ENABLED on the project, and billing is active. '
                'API said: {}'.format(reply.status_code, (reply.text or '')[:250]))
        if reply.status_code == 429:
            raise OCRError('Google Vision rate limit hit: ' + (reply.text or '')[:200])
        if not reply.ok:
            raise OCRError('HTTP {} from Google Vision: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        payload = reply.json()
        responses = payload.get('responses') or [{}]
        response = responses[0]
        error = response.get('error')
        if error:
            raise OCRError('Google Vision error {}: {}'.format(error.get('code', ''), error.get('message', '')[:250]))
        annotation = response.get('fullTextAnnotation') or {}
        words = self._wordsFromFullText(annotation)
        if not words:
            words = self._wordsFromTextAnnotations(response.get('textAnnotations'))
        if not words:
            raise OCRError('Google Vision found no text in this image.')
        return words
