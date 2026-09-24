# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function


def writeMd(result, meta):
    lines = result.get('TextOverlay', {}).get('Lines', [])
    out = []
    page = None
    for ln in lines:
        p = ln.get('Page')
        if p != page and p is not None:
            page = p
            out.append('\n## Page {}\n'.format(p))
        out.append(ln.get('LineText', ''))
    if not lines:
        out.append(result.get('ParsedText', '').replace('\r\n', '\n'))
    return ('\n'.join(out).strip() + '\n').encode('utf-8')
