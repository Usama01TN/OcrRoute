# coding=utf-8
"""Draw word and line boxes over the input image (PNG)."""
from __future__ import absolute_import, division, print_function

import io

from PIL import Image, ImageDraw


def renderOverlay(image_bytes, result, page_index=0):
    im = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
    layer = Image.new('RGBA', im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for ln in result.get('TextOverlay', {}).get('Lines', []):
        if int(ln.get('Page', 1)) != page_index + 1:
            continue
        words = ln.get('Words', [])
        if words:
            x1 = min(w['Left'] for w in words)
            y1 = min(w['Top'] for w in words)
            x2 = max(w['Left'] + w['Width'] for w in words)
            y2 = max(w['Top'] + w['Height'] for w in words)
            draw.rectangle([x1 - 2, y1 - 2, x2 + 2, y2 + 2], outline=(27, 34, 48, 160), width=1)
        for w in words:
            draw.rectangle(
                [w['Left'], w['Top'], w['Left'] + w['Width'], w['Top'] + w['Height']],
                fill=(245, 180, 0, 70),
                outline=(245, 180, 0, 220),
                width=1,
            )
    out = Image.alpha_composite(im, layer).convert('RGB')
    buf = io.BytesIO()
    out.save(buf, format='PNG')
    return buf.getvalue()
