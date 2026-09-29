# coding=utf-8
"""Artifact writers. Every writer: (result, doc_meta) -> bytes."""
from __future__ import absolute_import, division, print_function

from ocrroute.pipeline.export import alto, csvxlsx, docx, hocr, markdown, plain, searchable_pdf

WRITERS = {
    'json': (plain.writeJson, 'application/json', '.json'),
    'text': (plain.write_text, 'text/plain; charset=utf-8', '.txt'),
    'md': (markdown.writeMd, 'text/markdown; charset=utf-8', '.md'),
    'hocr': (hocr.writeHocr, 'application/xml', '.hocr.html'),
    'alto': (alto.writeAlto, 'application/xml', '.alto.xml'),
    'csv': (csvxlsx.writeCsv, 'text/csv; charset=utf-8', '.csv'),
    'xlsx': (csvxlsx.writeXlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
    'docx': (docx.writeDocx, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
    'pdf': (searchable_pdf.writePdf, 'application/pdf', '.pdf'),
}

KINDS = sorted(WRITERS) + ['overlay_png']


def write(kind, result, meta):
    if kind == 'overlay_png':
        from ocrroute.pipeline.overlay import renderOverlay

        return renderOverlay(meta['image_bytes'], result), 'image/png', '.png'
    if kind not in WRITERS:
        raise KeyError("Unknown export kind '{}'".format(kind))
    fn, mime, ext = WRITERS[kind]
    return fn(result, meta), mime, ext
