# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from ocrroute.db.session import getSession
from ocrroute.runtime.context import getContext


def getDb():
    s = getSession()
    try:
        yield s
    finally:
        s.close()


def getCtx():
    return getContext()
