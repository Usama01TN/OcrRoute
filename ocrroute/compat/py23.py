# coding=utf-8
"""
Python 2 / 3 compatibility shims used across the gateway (six-style, no dependency).

Note: the runtime frameworks (FastAPI, SQLAlchemy 2, PyQt5 wheels) only ship for Python 3; these shims keep the
*syntax and idioms* of the gateway portable, so modules parse and their logic reads the same on both lines.
"""
from __future__ import absolute_import, division, print_function

from sys import version_info

PY2 = version_info[0] == 2
PY3 = not PY2

if PY2:  # pragma: no cover
    text_type = unicode  # noqa: F821
    binary_type = str
    string_types = (str, unicode)  # noqa: F821
    integer_types = (int, long)  # noqa: F821
    from Queue import Empty, Queue  # noqa: F401
    from StringIO import StringIO  # noqa: F401
    from urlparse import urljoin, urlparse  # noqa: F401
else:
    text_type = str
    binary_type = bytes
    string_types = (str,)
    integer_types = (int,)
    from io import StringIO  # noqa: F401
    from queue import Empty, Queue  # noqa: F401
    from urllib.parse import urljoin, urlparse  # noqa: F401


def iteritems(d):
    """
    :param d: dict
    :return: iterator over (key, value)
    """
    return iter(d.items())


def ensureText(value, encoding='utf-8'):
    """
    :param value: str | bytes | unicode
    :param encoding: str
    :return: text
    """
    if isinstance(value, binary_type):
        return value.decode(encoding, 'replace')
    return text_type(value)


def ensureBytes(value, encoding='utf-8'):
    """
    :param value: str | bytes | unicode
    :param encoding: str
    :return: bytes
    """
    if isinstance(value, binary_type):
        return value
    return text_type(value).encode(encoding)


def raiseFrom(value, fromValue):
    """
    Raise ``value`` chained to ``fromValue`` (``raise value from fromValue`` on Python 3).

    :param value: BaseException
    :param fromValue: BaseException | None
    """
    if PY3:
        value.__cause__ = fromValue
        value.__suppress_context__ = True
    raise value


def mergeDicts(*dicts):
    """
    :param dicts: dict  merged left to right (later keys win) - replaces ``{**a, **b}``
    :return: dict
    """
    out = {}
    for d in dicts:
        if d:
            out.update(d)
    return out


__all__ = ['PY2', 'PY3', 'Empty', 'Queue', 'StringIO', 'binary_type', 'ensureBytes', 'ensureText', 'integer_types',
           'iteritems', 'mergeDicts', 'raiseFrom', 'string_types', 'text_type', 'urljoin', 'urlparse']
