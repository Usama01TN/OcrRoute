# coding=utf-8
"""Searchable PDF: the input raster as the page image plus an invisible text layer positioned from word boxes.
Written by hand (no reportlab) so the core install stays light."""
from __future__ import absolute_import, division, print_function

import io
import zlib

from PIL import Image


def _pdfEscape(text):
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _toWin1252(text):
    return text.encode('cp1252', errors='replace').decode('cp1252')


def writePdf(result, meta):
    image_bytes = meta.get('image_bytes')
    pages_bytes = meta.get('page_images') or ([image_bytes] if image_bytes else [])
    lines = result.get('TextOverlay', {}).get('Lines', [])
    by_page = {}
    for ln in lines:
        by_page.setdefault(int(ln.get('Page', 1)), []).append(ln)
    if not pages_bytes:
        raise ValueError('searchable PDF export needs the input image bytes')

    objects = []

    def add(obj):
        objects.append(obj)
        return len(objects)

    page_ids = []
    font_id = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    pages_placeholder = add(b'')  # fixed up later
    for pno, img_bytes in enumerate(pages_bytes, 1):
        im = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        w, h = im.size
        jpg = io.BytesIO()
        im.save(jpg, format='JPEG', quality=85)
        img_id = add(
            '<< /Type /XObject /Subtype /Image /Width {} /Height {} /ColorSpace /DeviceRGB '
            '/BitsPerComponent 8 /Filter /DCTDecode /Length {} >>'.format(w, h, jpg.getbuffer().nbytes).encode()
            + b'\nstream\n'
            + jpg.getvalue()
            + b'\nendstream'
        )
        content = ['q {} 0 0 {} 0 0 cm /Im1 Do Q'.format(w, h).encode(), b'BT 3 Tr']  # 3 Tr = invisible text
        for ln in by_page.get(pno, []):
            for wd in ln.get('Words', []):
                text = _toWin1252(str(wd['WordText']))
                if not text.strip():
                    continue
                size = max(2.0, float(wd['Height']) * 0.9)
                x = float(wd['Left'])
                y = h - float(wd['Top']) - float(wd['Height']) * 0.8
                # horizontal scaling so the glyph run spans the box width
                approx = max(1.0, 0.5 * size * len(text))
                tz = max(10.0, min(300.0, 100.0 * float(wd['Width']) / approx))
                content.append(
                    '/F1 {:.2f} Tf {:.1f} Tz 1 0 0 1 {:.2f} {:.2f} Tm ({}) Tj'.format(
                        size, tz, x, y, _pdfEscape(text)
                    ).encode()
                )
        content.append(b'ET')
        stream = zlib.compress(b'\n'.join(content))
        cont_id = add(
            '<< /Length {} /Filter /FlateDecode >>'.format(len(stream)).encode()
            + b'\nstream\n'
            + stream
            + b'\nendstream'
        )
        page_id = add(
            '<< /Type /Page /Parent {} 0 R /MediaBox [0 0 {} {}] '
            '/Resources << /Font << /F1 {} 0 R >> /XObject << /Im1 {} 0 R >> >> '
            '/Contents {} 0 R >>'.format(pages_placeholder, w, h, font_id, img_id, cont_id).encode()
        )
        page_ids.append(page_id)
    kids = ' '.join('{} 0 R'.format(p) for p in page_ids)
    objects[pages_placeholder - 1] = '<< /Type /Pages /Kids [{}] /Count {} >>'.format(kids, len(page_ids)).encode()
    catalog_id = add('<< /Type /Catalog /Pages {} 0 R >>'.format(pages_placeholder).encode())
    out = io.BytesIO()
    out.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write('{} 0 obj\n'.format(i).encode() + obj + b'\nendobj\n')
    xref = out.tell()
    out.write('xref\n0 {}\n0000000000 65535 f \n'.format(len(objects) + 1).encode())
    for off in offsets:
        out.write('{:010d} 00000 n \n'.format(off).encode())
    out.write(
        'trailer\n<< /Size {} /Root {} 0 R >>\nstartxref\n{}\n%%EOF\n'.format(
            len(objects) + 1, catalog_id, xref
        ).encode()
    )
    return out.getvalue()
