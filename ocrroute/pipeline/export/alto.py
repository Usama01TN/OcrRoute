# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from xml.sax.saxutils import quoteattr


def writeAlto(result, meta):
    w, h = int(meta.get('width', 0)), int(meta.get('height', 0))
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">',
        '<Description><MeasurementUnit>pixel</MeasurementUnit>'
        '<OCRProcessing ID="OCR_0"><ocrProcessingStep><processingSoftware>'
        '<softwareName>OcrRoute</softwareName></processingSoftware></ocrProcessingStep></OCRProcessing>'
        '</Description>',
        '<Layout><Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="{}" HEIGHT="{}"><PrintSpace HPOS="0" VPOS="0" WIDTH="{}" HEIGHT="{}">'
        '<TextBlock ID="B1">'.format(w, h, w, h),
    ]
    for i, ln in enumerate(result.get('TextOverlay', {}).get('Lines', []), 1):
        words = ln.get('Words', [])
        if not words:
            continue
        x1 = int(min(wd['Left'] for wd in words))
        y1 = int(min(wd['Top'] for wd in words))
        x2 = int(max(wd['Left'] + wd['Width'] for wd in words))
        y2 = int(max(wd['Top'] + wd['Height'] for wd in words))
        out.append('<TextLine ID="L{}" HPOS="{}" VPOS="{}" WIDTH="{}" HEIGHT="{}">'.format(i, x1, y1, x2 - x1, y2 - y1))
        for j, wd in enumerate(words, 1):
            out.append(
                '<String ID="S{}_{}" HPOS="{}" VPOS="{}" WIDTH="{}" HEIGHT="{}" CONTENT={}/>'.format(
                    i,
                    j,
                    int(wd['Left']),
                    int(wd['Top']),
                    int(wd['Width']),
                    int(wd['Height']),
                    quoteattr(str(wd['WordText'])),
                )
            )
            if j < len(words):
                out.append('<SP/>')
        out.append('</TextLine>')
    out.append('</TextBlock></PrintSpace></Page></Layout></alto>')
    return ''.join(out).encode('utf-8')
