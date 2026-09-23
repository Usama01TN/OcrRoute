# coding=utf-8
"""Input acquisition: path / URL / base64 / bytes / PIL, with SSRF guard, limits, MIME sniffing and PDF split."""
from __future__ import absolute_import, division, print_function

import base64
from ocrroute.compat.py23 import raiseFrom
import hashlib
import io
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from ocrroute.errors import BadInput, TooLarge, UnsupportedInput

Image.MAX_IMAGE_PIXELS = None  # we enforce our own limit

IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/webp', 'image/tiff', 'image/bmp', 'image/gif'}
PDF_MIME = 'application/pdf'
SUPPORTED = IMAGE_MIMES | {PDF_MIME}


class InputDocument(object):
    """
    A validated input (image or PDF) ready for OCR.
    """

    def __init__(self, *args, **kwargs):
        """
        :param data: bytes - raw bytes
        :param mime: str
        :param kind: str - path | url | base64 | bytes | upload
        :param sha256: str
        :param filename: str
        :param page_count: int
        :param width: int
        :param height: int
        :param pages: list[bytes] - rasterised PNG pages when a PDF was split
        """
        args = list(args)
        self.__m_data = kwargs.pop('data', args.pop(0) if args else b'')
        self.__m_mime = kwargs.pop('mime', args.pop(0) if args else '')
        self.__m_kind = kwargs.pop('kind', args.pop(0) if args else '')
        self.__m_sha256 = kwargs.pop('sha256', args.pop(0) if args else '')
        self.__m_filename = kwargs.pop('filename', args.pop(0) if args else '')
        self.__m_pageCount = kwargs.pop('page_count', args.pop(0) if args else 1)
        self.__m_width = kwargs.pop('width', args.pop(0) if args else 0)
        self.__m_height = kwargs.pop('height', args.pop(0) if args else 0)
        self.__m_pages = kwargs.pop('pages', args.pop(0) if args else list())

    def getData(self):
        """
        :return: bytes
        """
        return self.__m_data

    def setData(self, data):
        """
        :param data: bytes
        """
        self.__m_data = data

    def getMime(self):
        """
        :return: str
        """
        return self.__m_mime

    def setMime(self, mime):
        """
        :param mime: str
        """
        self.__m_mime = mime

    def getKind(self):
        """
        :return: str
        """
        return self.__m_kind

    def setKind(self, kind):
        """
        :param kind: str
        """
        self.__m_kind = kind

    def getSha256(self):
        """
        :return: str
        """
        return self.__m_sha256

    def setSha256(self, sha256):
        """
        :param sha256: str
        """
        self.__m_sha256 = sha256

    def getFilename(self):
        """
        :return: str
        """
        return self.__m_filename

    def setFilename(self, filename):
        """
        :param filename: str
        """
        self.__m_filename = filename

    def getPageCount(self):
        """
        :return: int
        """
        return self.__m_pageCount

    def setPageCount(self, pageCount):
        """
        :param pageCount: int
        """
        self.__m_pageCount = pageCount

    def getWidth(self):
        """
        :return: int
        """
        return self.__m_width

    def setWidth(self, width):
        """
        :param width: int
        """
        self.__m_width = width

    def getHeight(self):
        """
        :return: int
        """
        return self.__m_height

    def setHeight(self, height):
        """
        :param height: int
        """
        self.__m_height = height

    def getPages(self):
        """
        :return: list[bytes]
        """
        return self.__m_pages

    def setPages(self, pages):
        """
        :param pages: list[bytes]
        """
        self.__m_pages = pages

    def __repr__(self):
        return 'InputDocument({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    def isPdf(self):
        """
        :return: bool
        """
        return self.__m_mime == PDF_MIME

    is_pdf = property(fget=isPdf)

    data = property(fget=getData, fset=setData)
    mime = property(fget=getMime, fset=setMime)
    kind = property(fget=getKind, fset=setKind)
    sha256 = property(fget=getSha256, fset=setSha256)
    filename = property(fget=getFilename, fset=setFilename)
    page_count = property(fget=getPageCount, fset=setPageCount)
    width = property(fget=getWidth, fset=setWidth)
    height = property(fget=getHeight, fset=setHeight)
    pages = property(fget=getPages, fset=setPages)


def sniffMime(data):
    if data[:4] == b'%PDF':
        return PDF_MIME
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:4] in (b'II*\x00', b'MM\x00*'):
        return 'image/tiff'
    if data[:2] == b'BM':
        return 'image/bmp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    return 'application/octet-stream'


# ------------------------------------------------------------------ SSRF guard
_BLOCKED_NETS = [
    ipaddress.ip_network(n)
    for n in (
        '0.0.0.0/8',
        '10.0.0.0/8',
        '100.64.0.0/10',
        '127.0.0.0/8',
        '169.254.0.0/16',
        '172.16.0.0/12',
        '192.0.0.0/24',
        '192.168.0.0/16',
        '198.18.0.0/15',
        '224.0.0.0/4',
        '240.0.0.0/4',
        '::/128',
        '::1/128',
        'fc00::/7',
        'fe80::/10',
        'ff00::/8',
        '::ffff:0:0/96',
    )
]


def _hostMatches(host, patterns):
    host = host.lower()
    for p in patterns:
        p = p.lower().strip()
        if not p:
            continue
        if p.startswith('*.') and host.endswith(p[1:]):
            return True
        if host == p:
            return True
    return False


def checkUrlAllowed(url, settings):
    """Validate a user URL against scheme, allow/deny lists and private-address ranges. Returns the host."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raiseFrom(BadInput('Malformed URL: {}'.format(exc)), exc)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise BadInput('Only http(s) URLs with a host are accepted')
    host = parsed.hostname
    if settings.url_denylist and _hostMatches(host, settings.url_denylist):
        raise BadInput("Host '{}' is denied by policy".format(host))
    if settings.url_allowlist and not _hostMatches(host, settings.url_allowlist):
        raise BadInput("Host '{}' is not in the URL allowlist".format(host))
    if settings.allow_private_urls:
        return host
    if host.lower() in ('localhost',) or host.lower().endswith('.localhost') or host.lower().endswith('.local'):
        raise BadInput('URLs pointing at local hosts are blocked')
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raiseFrom(BadInput("Cannot resolve host '{}'".format(host)), exc)
    for a in addrs:
        ip = ipaddress.ip_address(a.split('%')[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or any(ip in n for n in _BLOCKED_NETS)
        ):
            raise BadInput('URL resolves to a blocked address ({})'.format(a))
    return host


def fetchUrl(url, settings, timeout=20.0):
    checkUrlAllowed(url, settings)
    data = bytearray()
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        current = url
        for _ in range(4):
            with client.stream('GET', current, headers={'User-Agent': 'OcrRoute/0.1'}) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    current = str(resp.headers.get('location', ''))
                    if not current:
                        raise BadInput('Redirect without location')
                    current = str(httpx.URL(url).join(current)) if not current.startswith('http') else current
                    checkUrlAllowed(current, settings)
                    continue
                if resp.status_code >= 400:
                    raise BadInput('URL returned HTTP {}'.format(resp.status_code))
                for chunk in resp.iter_bytes():
                    data.extend(chunk)
                    if len(data) > settings.max_upload_bytes:
                        raise TooLarge('Remote file exceeds {} bytes'.format(settings.max_upload_bytes))
                return bytes(data), resp.headers.get('content-type', '').split(';')[0].strip()
    raise BadInput('Too many redirects')


# ------------------------------------------------------------------ loaders
def loadInput(settings, file_bytes=None, filename='', url='', b64='', path='', pages='', pdf_dpi=150):
    kind = ''
    pass  # data: bytes
    if file_bytes is not None:
        kind, data = 'upload', file_bytes
    elif url:
        kind = 'url'
        data, _ = fetchUrl(url, settings)
        filename = filename or Path(urlparse(url).path).name
    elif b64:
        kind = 'base64'
        payload = b64.split(',', 1)[-1]
        try:
            data = base64.b64decode(payload, validate=True)
        except Exception as exc:
            raiseFrom(BadInput('Invalid base64 payload'), exc)
    elif path:
        kind = 'path'
        p = Path(path).expanduser()
        if not p.is_file():
            raise BadInput("Image source '{}' is not an existing file".format(path))
        if p.stat().st_size > settings.max_upload_bytes:
            raise TooLarge('File exceeds {} bytes'.format(settings.max_upload_bytes))
        data = p.read_bytes()
        filename = filename or p.name
    else:
        raise BadInput('Provide one of: file, url, base64 or path')
    if not data:
        raise BadInput('Empty input')
    if len(data) > settings.max_upload_bytes:
        raise TooLarge('Input exceeds {} bytes'.format(settings.max_upload_bytes))
    mime = sniffMime(data)
    if mime not in SUPPORTED:
        raise UnsupportedInput('Unsupported content ({}); accepted: PNG, JPEG, WEBP, TIFF, BMP, GIF, PDF'.format(mime))
    doc = InputDocument(data=data, mime=mime, kind=kind, sha256=hashlib.sha256(data).hexdigest(), filename=filename)
    if doc.is_pdf:
        _splitPdf(doc, settings, pages, pdf_dpi)
    else:
        try:
            with Image.open(io.BytesIO(data)) as im:
                doc.width, doc.height = im.size
                if getattr(im, 'n_frames', 1) > 1 and mime == 'image/tiff':
                    doc.page_count = im.n_frames
        except Exception as exc:
            raiseFrom(BadInput('Cannot decode image: {}'.format(exc)), exc)
        if doc.width * doc.height > settings.max_pixels:
            raise TooLarge('Image has {} pixels; limit is {}'.format(doc.width * doc.height, settings.max_pixels))
    return doc


def parsePageSpec(spec, total):
    """'1-3,7' → [0,1,2,6] (zero-based, clipped)."""
    if not spec:
        return list(range(total))
    out = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else total
            out.extend(range(start - 1, min(end, total)))
        else:
            i = int(part) - 1
            if 0 <= i < total:
                out.append(i)
    return sorted(set(i for i in out if 0 <= i < total))


def _splitPdf(doc, settings, pages, dpi):
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raiseFrom(UnsupportedInput('PDF support requires pypdfium2 (pip install pypdfium2)'), exc)
    try:
        pdf = pdfium.PdfDocument(doc.data)
    except Exception as exc:
        raiseFrom(BadInput('Cannot open PDF: {}'.format(exc)), exc)
    total = len(pdf)
    wanted = parsePageSpec(pages, total)
    if len(wanted) > settings.max_pages:
        raise TooLarge('PDF selection has {} pages; limit is {}'.format(len(wanted), settings.max_pages))
    scale = dpi / 72.0
    rendered = []
    for i in wanted:
        page = pdf[i]
        bitmap = page.render(scale=scale)
        im = bitmap.toPil()
        if not doc.width:
            doc.width, doc.height = im.size
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        rendered.append(buf.getvalue())
    doc.pages = rendered
    doc.page_count = len(rendered)


def toPil(data):
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def imageDims(data):
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def asDict(doc):
    return {
        'kind': doc.kind,
        'mime': doc.mime,
        'bytes': len(doc.data),
        'sha256': doc.sha256,
        'filename': doc.filename,
        'page_count': doc.page_count,
        'width': doc.width,
        'height': doc.height,
    }
