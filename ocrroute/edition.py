# coding=utf-8
"""
Which build of OcrRoute is running: ``source`` (pip / git), or a stand-alone executable edition ``lean`` / ``full``.
"""
from __future__ import absolute_import, division, print_function

import json
import sys
from os.path import dirname, exists, join

_cache = {}


def info():
    """
    :return: dict  {edition, bundled_extra_engines, frozen}
    """
    if not _cache:
        data = {'edition': 'source', 'bundled_extra_engines': []}
        if getattr(sys, 'frozen', False):
            data['edition'] = 'lean'
            path = join(dirname(__file__), 'edition.json')
            if exists(path):
                try:
                    with open(path) as fh:
                        data.update(json.load(fh))
                except (OSError, ValueError):
                    pass
        data['frozen'] = bool(getattr(sys, 'frozen', False))
        _cache.update(data)
    return dict(_cache)


def name():
    """
    :return: str  source | lean | full
    """
    return info()['edition']
