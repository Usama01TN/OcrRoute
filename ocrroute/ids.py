# coding=utf-8
"""ULID-style identifiers (time-sortable, 26 chars, Crockford base32)."""
from __future__ import absolute_import, division, print_function

import os
import time

_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'


def _encode(value, length):
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return ''.join(reversed(out))


def newId():
    ts = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), 'big')
    return _encode(ts, 10) + _encode(rand, 16)
