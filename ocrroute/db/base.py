# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Base(DeclarativeBase):
    pass
