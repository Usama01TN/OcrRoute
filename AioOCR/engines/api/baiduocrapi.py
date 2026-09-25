# coding=utf-8
"""
Baidu Cloud OCR plugin (aip.baidubce.com REST API).
Baidu's OCR service (docs: https://ai.baidu.com/ai-doc/OCR/Ck3h7y2ia)
offers several general text-recognition endpoints with free monthly
quotas. Setup:
1. Register at https://console.bce.baidu.com/ai/#/ai/ocr/overview/index
2. Create an application to get an **API Key** and **Secret Key**.
3. Claim the free quota for the interfaces you want in the console
   (each endpoint has its own free monthly allowance).
Authentication is OAuth2 client-credentials: the plugin exchanges the
API Key + Secret Key for an access_token (valid ~30 days) and caches it
process-wide, refreshing automatically on expiry or on the API's
110/111 invalid-token errors.
Recognition modes (``mode=``):
    general         standard, WITH word boxes            (default)
    accurate        high precision, WITH word boxes
    general_basic   standard, text only (larger free quota)
    accurate_basic  high precision, text only
    webimage        web images, text only
    webimage_loc    web images, WITH word boxes
Modes with boxes map ``location`` to real pixel coordinates; text-only
modes keep reading order with synthesized row boxes. Either way the
result has the exact same unified structure as every other plugin.
"""
from base64 import b64encode
from os.path import dirname
from requests import post
from os import environ
from time import time
from sys import path

if dirname(__file__) not in path:
    path.append(dirname(__file__))
if dirname(dirname(__file__)) not in path:
    path.append(dirname(dirname(__file__)))

try:
    from .ocrplugin import OCRPlugin, OCRError
except:
    from engines.ocrplugin import OCRPlugin, OCRError

_TOKEN_URL = 'https://aip.baidubce.com/oauth/2.0/token'
_OCR_BASE = 'https://aip.baidubce.com/rest/2.0/ocr/v1/'
_MODES = ('general', 'accurate', 'general_basic', 'accurate_basic', 'webimage', 'webimage_loc')
#: Baidu error codes worth a specific explanation.
_ERROR_HINTS = {
    4: 'request limit reached for this interface',
    14: 'IAM authentication failed',
    17: 'DAILY free quota exhausted for this endpoint. Claim/raise the '
        'free quota in the Baidu console, switch mode= to an endpoint '
        'with remaining quota (general_basic usually has the largest '
        'free allowance), or wait until tomorrow',
    18: 'QPS limit reached (free tier allows ~2 requests/second); the plugin retries with backoff automatically',
    19: 'total request limit reached',
    110: 'access token invalid',
    111: 'access token expired',
    216201: 'unsupported image format (use jpg/png/bmp)',
    216202: 'image size error (base64 must be under 4 MB, sides 15px-8192px)',
    216630: 'recognition error, try again',
}
#: Map short ISO codes to Baidu language_type values.
_LANG_MAP = {'en': 'ENG', 'fr': 'FRE', 'de': 'GER', 'es': 'SPA',
             'it': 'ITA', 'pt': 'POR', 'ru': 'RUS', 'ja': 'JAP',
             'ko': 'KOR', 'da': 'DAN', 'nl': 'DUT', 'zh': 'CHN_ENG',
             'auto': 'auto_detect'}
#: Constructor kwargs that must never leak into the API payload.
_RESERVED = frozenset((
    'endpoint', 'image', 'language', 'engine', 'online', 'payload',
    'apiList', 'api', 'apiKey', 'apikey', 'secret', 'secretKey',
    'proxyList', 'proxy', 'timeout', 'retries', 'lastError', 'mode',
    'model', 'prompt',  # base-class settings; Baidu has no prompt
))


class BaiduOcr(OCRPlugin):
    """
    BaiduOcr class.
    """
    #: (api, secretKey) -> (access_token, expiry_epoch).
    _tokens = {}

    def __init__(self, *args, **kwargs):
        """
        :param api: Baidu application API Key (or ``api=``, or the BAIDU_API_KEY environment variable).
        :param secretKey: Baidu application Secret Key (or ``secret=``, or the BAIDU_SECRET_KEY environment variable).
        :param mode: Recognition endpoint, see module docstring (default 'general': standard with word boxes).
        :param language: ISO code ('en', 'fr'...) or Baidu code
                            ('ENG', 'CHN_ENG'...); default Chinese+English (the API's own default).
        :param kwargs: Extra API options forwarded as-is, e.g. detect_direction=True, paragraph=True, probability=True.
        """
        self.__m_api = kwargs.pop('api', environ.get('BAIDU_API_KEY', ''))
        self.__m_secretKey = kwargs.pop('secretKey', kwargs.pop('secret', '')) or environ.get('BAIDU_SECRET_KEY', '')
        mode = str(kwargs.pop('mode', 'general'))
        if mode not in _MODES:
            raise OCRError('Unknown mode {!r}; choose from {}'.format(mode, ', '.join(_MODES)))
        self.__m_mode = mode
        language = kwargs.pop('language', None)
        extras = {k: v for k, v in kwargs.items() if k not in _RESERVED}
        for key in extras:
            kwargs.pop(key)
        super(BaiduOcr, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setApi(self.__m_api)
        self.setEndpoint(_OCR_BASE + mode)
        payload = {}
        if language:
            if isinstance(language, (list, tuple)):
                language = language[0] if language else 'en'
            payload['language_type'] = _LANG_MAP.get(str(language).lower(), str(language))
        payload.update(extras)
        self.setPayload(payload)

    # ------------------------------------------------------------------ #
    # Authentication                                                     #
    # ------------------------------------------------------------------ #
    def _token(self, force=False):
        """
        Return a valid access_token (cached ~30 days, auto-refresh).
        """
        key = (self.__m_api, self.__m_secretKey)
        if not force:
            cached = BaiduOcr._tokens.get(key)
            if cached and cached[1] > time():
                return cached[0]
        kwargs = {
            'params': {
                'grant_type': 'client_credentials',
                'client_id': self.__m_api,
                'client_secret': self.__m_secretKey,
            },
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            kwargs['proxies'] = self.getProxy()
        reply = post(_TOKEN_URL, **kwargs)
        if not reply.ok:
            raise OCRError('Baidu token request failed (HTTP {}): {}'.format(
                reply.status_code, (reply.text or '')[:200]))
        data = reply.json()
        token = data.get('access_token')
        if not token:
            raise OCRError(
                'Baidu rejected the credentials: {}. Check the API Key '
                'and Secret Key from your application at '
                'https://console.bce.baidu.com/ai/#/ai/ocr/overview/index'.format(str(data)[:200]))
        scope = data.get('scope', '')
        if scope and 'brain_all_scope' not in scope.split(' '):
            raise OCRError(
                'This Baidu application lacks the OCR permission '
                '(brain_all_scope). Enable the OCR ability for the '
                'application in the Baidu console.')
        # Refresh one hour before the reported expiry (~30 days).
        expiry = time() + max(int(data.get('expires_in', 2592000)) - 3600, 60)
        BaiduOcr._tokens[key] = (token, expiry)
        return token

    # ------------------------------------------------------------------ #
    # Input handling                                                     #
    # ------------------------------------------------------------------ #
    def _imageParam(self):
        """
        Return ({'image': b64} or {'url': ...}, approximate check).
        """
        kind = self.imageKind()
        if kind == 'url':
            return {'url': self.getImage()}
        if kind in ('path', 'pil', 'bytes', 'buffer', 'array'):
            if kind == 'array':
                from io import BytesIO
                from PIL import Image
                buffer = BytesIO()
                Image.fromarray(self.getImage()).save(buffer, format='PNG')
                data = buffer.getvalue()
            else:
                data = self.imageBytes()
            encoded = b64encode(data).decode('ascii')
            if len(encoded) > 4 * 1024 * 1024:
                raise OCRError(
                    'Image too large for Baidu OCR: base64 is {:.1f} MB '
                    'but the limit is 4 MB. Downscale or recompress the '
                    'image first.'.format(len(encoded) / 1048576.0))
            return {'image': encoded}
        raise OCRError(
            "Image source '{}' is not an existing file, URL, or "
            "supported type. Check the path (the current working "
            "directory matters for relative paths).".format(self.getImage()))

    # ------------------------------------------------------------------ #
    # Response parsing                                                   #
    # ------------------------------------------------------------------ #
    @classmethod
    def _wordsFromResult(cls, words_result):
        """
        Map Baidu words_result entries to word dicts.
        """
        boxed, texts = [], []
        for entry in words_result or []:
            text = str(entry.get('words', '')).strip()
            if not text:
                continue
            texts.append(text)
            location = entry.get('location') or {}
            width = float(location.get('width', 0))
            height = float(location.get('height', 0))
            if width > 0 and height > 0:
                boxed.append(cls.makeWord(
                    text,
                    float(location.get('left', 0)),
                    float(location.get('top', 0)),
                    width, height,
                ))
        if boxed:
            return boxed
        # Text-only modes: synthetic row boxes keep reading order.
        return [cls.makeWord(text, 0.0, float(i * 10), 1.0, 8.0) for i, text in enumerate(texts)]

    # ------------------------------------------------------------------ #
    # OCR                                                                #
    # ------------------------------------------------------------------ #
    def _run(self, image, *args, **kwargs):
        """
        :return: list of word dicts for the base class to assemble
                 (Baidu returns text LINES; the base class groups them
                 geometrically like the other engines).
        """
        if not self.__m_api or not self.__m_secretKey:
            raise OCRError(
                'Missing Baidu credentials. Create an application at '
                'https://console.bce.baidu.com/ai/#/ai/ocr/overview/'
                'index and pass api=... and secretKey=... (or set BAIDU_API_KEY / BAIDU_SECRET_KEY).')
        data = dict(self.getPayload())
        data.update(self._imageParam())
        result = self._call(data, self._token())
        code = result.get('error_code')
        if code in (110, 111):
            # Stale token: refresh once and retry.
            result = self._call(data, self._token(force=True))
            code = result.get('error_code')
        if code:
            hint = _ERROR_HINTS.get(code, '')
            raise OCRError('Baidu error {}: {}{}'.format(
                code, result.get('error_msg', ''), ' ({})'.format(hint) if hint else ''))
        return self._wordsFromResult(result.get('words_result'))

    def _call(self, data, token):
        """
        One POST to the recognition endpoint; returns parsed JSON.
        """
        kwargs = {
            'params': {'access_token': token},
            'data': data, 'headers': {'Content-Type': 'application/x-www-form-urlencoded'},
            'timeout': self.getTimeout(),
        }
        if self.getProxy():
            kwargs['proxies'] = self.getProxy()
        reply = post(self.getEndpoint(), **kwargs)
        if not reply.ok:
            raise OCRError('HTTP {} from Baidu OCR: {}'.format(reply.status_code, (reply.text or '')[:300].strip()))
        return reply.json()
