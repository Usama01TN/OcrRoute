# coding=utf-8
"""Minimal .docx writer (WordprocessingML) with no extra dependency - one paragraph per OCR line."""
from __future__ import absolute_import, division, print_function

import io
import zipfile
from xml.sax.saxutils import escape

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)


def writeDocx(result, meta):
    lines = [ln.get('LineText', '') for ln in result.get('TextOverlay', {}).get('Lines', [])] or result.get(
        'ParsedText', ''
    ).replace('\r\n', '\n').split('\n')
    body = ''.join(
        '<w:p><w:pPr>{}</w:pPr><w:r><w:t xml:space="preserve">{}</w:t></w:r></w:p>'.format(
            '<w:bidi/>' if _rtl(t) else '', escape(t)
        )
        for t in lines
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '{}<w:sectPr/></w:body></w:document>'.format(body)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', _CT)
        z.writestr('_rels/.rels', _RELS)
        z.writestr('word/document.xml', doc)
    return buf.getvalue()


def _rtl(text):
    from ocrroute.pipeline.postprocess import isRtlText

    return isRtlText(text)
