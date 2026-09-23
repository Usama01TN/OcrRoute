# coding=utf-8
"""Error taxonomy shared by the routing engine, API and UIs."""
from __future__ import absolute_import, division, print_function

BAD_INPUT = 'bad_input'
UNSUPPORTED_INPUT = 'unsupported_input'
AUTH = 'auth'
QUOTA = 'quota'
RATE_LIMIT = 'rate_limit'
TIMEOUT = 'timeout'
NETWORK = 'network'
SERVER_ERROR = 'server_error'
ENGINE_MISSING = 'engine_missing'
EMPTY_RESULT = 'empty_result'
NO_CANDIDATE = 'no_candidate'
DEADLINE = 'deadline'
INTERNAL = 'internal'
INTERRUPTED = 'interrupted'
TOOLS_RESERVED = 'tools_reserved'

TERMINAL = {BAD_INPUT, UNSUPPORTED_INPUT}  # never fan out to more engines

HTTP_STATUS = {
    BAD_INPUT: 400,
    UNSUPPORTED_INPUT: 415,
    AUTH: 502,  # upstream engine credential problem; client auth is a separate 401
    QUOTA: 502,
    RATE_LIMIT: 429,
    TIMEOUT: 504,
    NETWORK: 502,
    SERVER_ERROR: 502,
    ENGINE_MISSING: 503,
    EMPTY_RESULT: 502,
    NO_CANDIDATE: 503,
    DEADLINE: 504,
    INTERNAL: 500,
    TOOLS_RESERVED: 501,
}


class OcrRouteError(Exception):
    """Base error carrying a machine-readable code."""

    code = INTERNAL
    status = 500

    def __init__(self, message='', code=None, status=None):
        super(OcrRouteError, self).__init__(message or self.__class__.__name__)
        if code:
            self.code = code
        self.status = status or HTTP_STATUS.get(self.code, type(self).status)
        self.message = message or self.__class__.__name__


class BadInput(OcrRouteError):
    code = BAD_INPUT


class UnsupportedInput(OcrRouteError):
    code = UNSUPPORTED_INPUT


class NoCandidate(OcrRouteError):
    code = NO_CANDIDATE


class Unauthorized(OcrRouteError):
    code = 'unauthorized'
    status = 401


class Forbidden(OcrRouteError):
    code = 'forbidden'
    status = 403


class NotFound(OcrRouteError):
    code = 'not_found'
    status = 404


class Conflict(OcrRouteError):
    code = 'conflict'
    status = 409


class TooLarge(OcrRouteError):
    code = 'too_large'
    status = 413


class RateLimited(OcrRouteError):
    code = RATE_LIMIT
    status = 429

    def __init__(self, message='Rate limit exceeded', retryAfter=1):
        super(RateLimited, self).__init__(message)
        self.retryAfter = retryAfter


class QueueFull(OcrRouteError):
    code = 'queue_full'
    status = 503


class ToolsReserved(OcrRouteError):
    code = TOOLS_RESERVED
    status = 501


class Classified(object):
    """
    A raw engine error mapped onto the OcrRoute taxonomy.
    """

    def __init__(self, *args, **kwargs):
        """
        :param code: str
        :param message: str
        """
        args = list(args)
        self.__m_code = kwargs.pop('code', args.pop(0) if args else '')
        self.__m_message = kwargs.pop('message', args.pop(0) if args else '')

    def getCode(self):
        """
        :return: str
        """
        return self.__m_code

    def setCode(self, code):
        """
        :param code: str
        """
        self.__m_code = code

    def getMessage(self):
        """
        :return: str
        """
        return self.__m_message

    def setMessage(self, message):
        """
        :param message: str
        """
        self.__m_message = message

    def toDict(self):
        """
        :return: dict
        """
        return {
            'code': self.__m_code,
            'message': self.__m_message,
        }

    def __repr__(self):
        return 'Classified({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    code = property(fget=getCode, fset=setCode)
    message = property(fget=getMessage, fset=setMessage)


_PATTERNS = [
    (
        AUTH,
        (
            '401',
            'unauthorized',
            'invalid api key',
            'invalid_api_key',
            'authentication',
            'forbidden',
            '403',
            'api key',
            'apikey',
            'permission denied',
            'invalid key',
        ),
    ),
    (QUOTA, ('quota', 'insufficient', 'billing', 'credit', 'exceeded your', 'out of tokens', 'exhausted')),
    (RATE_LIMIT, ('429', 'rate limit', 'rate_limit', 'too many requests', 'ratelimit')),
    (TIMEOUT, ('timed out', 'timeout', 'read timeout', 'connecttimeout')),
    (
        NETWORK,
        (
            'connection',
            'name resolution',
            'network is unreachable',
            'max retries exceeded',
            'dns',
            'ssl',
            'remote end closed',
            'connectionerror',
        ),
    ),
    (
        SERVER_ERROR,
        ('500', '502', '503', '504', 'internal server error', 'bad gateway', 'service unavailable', 'overloaded'),
    ),
    (
        ENGINE_MISSING,
        (
            'no module named',
            'not installed',
            'tesseract is not installed',
            'not found in path',
            'cannot import',
            'importerror',
            'modulenotfounderror',
            'not available',
        ),
    ),
    (
        BAD_INPUT,
        (
            'is not an existing file',
            'no such file',
            'cannot convert image source',
            'cannot identify image',
            'unsupported image source',
            'not valid base64',
            'sourceerror',
        ),
    ),
    (EMPTY_RESULT, ('empty parsedresults', 'no text', 'empty result')),
]


def classify(message):
    """Map a raw engine error string to the OcrRoute taxonomy."""
    text = (message or '').lower()
    for code, needles in _PATTERNS:
        if any(n in text for n in needles):
            return Classified(code, message)
    return Classified(SERVER_ERROR if text else EMPTY_RESULT, message)
