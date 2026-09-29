# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from xml.sax.saxutils import escape


def writeHocr(result, meta):
    w, h = int(meta.get('width', 0)), int(meta.get('height', 0))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8"/>',
        '<meta name="ocr-system" content="OcrRoute"/>',
        '<meta name="ocr-capabilities" content="ocr_page ocr_line ocrx_word"/></head><body>',
        '<div class="ocr_page" id="page_1" title="bbox 0 0 {} {}">'.format(w, h),
    ]
    for i, ln in enumerate(result.get('TextOverlay', {}).get('Lines', []), 1):
        words = ln.get('Words', [])
        if words:
            x1 = int(min(wd['Left'] for wd in words))
            y1 = int(min(wd['Top'] for wd in words))
            x2 = int(max(wd['Left'] + wd['Width'] for wd in words))
            y2 = int(max(wd['Top'] + wd['Height'] for wd in words))
        else:
            x1 = y1 = x2 = y2 = 0
        parts.append('<span class="ocr_line" id="line_{}" title="bbox {} {} {} {}">'.format(i, x1, y1, x2, y2))
        for j, wd in enumerate(words, 1):
            bx = 'bbox {} {} {} {}'.format(
                int(wd['Left']), int(wd['Top']), int(wd['Left'] + wd['Width']), int(wd['Top'] + wd['Height'])
            )
            conf = '; x_wconf {}'.format(int(float(wd['Confidence']))) if 'Confidence' in wd else ''
            parts.append(
                '<span class="ocrx_word" id="word_{}_{}" title="{}{}">{}</span> '.format(
                    i, j, bx, conf, escape(str(wd['WordText']))
                )
            )
        parts.append('</span>\n')
    parts.append('</div></body></html>')
    return ''.join(parts).encode('utf-8')
