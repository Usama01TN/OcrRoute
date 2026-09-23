# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

import json


def writeJson(result, meta):
    return json.dumps(
        {'result': result, **{k: v for k, v in meta.items() if not isinstance(v, (bytes, bytearray, list))}},
        ensure_ascii=False,
        indent=2,
    ).encode('utf-8')


def write_text(result, meta):
    return result.get('ParsedText', '').replace('\r\n', '\n').encode('utf-8')
