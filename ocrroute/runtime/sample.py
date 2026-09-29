# coding=utf-8
"""A tiny bundled test image rendered at import time (no binary asset in the repo)."""
from __future__ import absolute_import, division, print_function

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont


@lru_cache(maxsize=1)
def sampleImageBytes():
    im = Image.new('RGB', (640, 200), 'white')
    d = ImageDraw.Draw(im)
    font = None
    for path in (
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        'C:/Windows/Fonts/arial.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf',
    ):
        try:
            font = ImageFont.truetype(path, 34)
            break
        except OSError:
            continue
    d.text((28, 40), 'OcrRoute sample 1234', fill='black', font=font)
    d.text((28, 110), 'Quick brown fox', fill='black', font=font)
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()
