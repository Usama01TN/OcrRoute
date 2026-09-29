# coding=utf-8
"""
Lightweight internationalisation shared by the web panel and the desktop app.

Catalogues are plain JSON files (``ocrroute/i18n/<lang>.json``) mapping English source strings to translations.
Missing keys fall back to English, so partial catalogues are fine and contributors can add a language by adding
one file. Arabic (and any language listed in ``RTL``) switches both UIs to right-to-left layout.
"""
from __future__ import absolute_import, division, print_function

from json import load
from os import listdir
from os.path import dirname, exists, join, splitext

HERE = dirname(__file__)
DEFAULT = 'en'
RTL = ('ar', 'he', 'fa', 'ur')
NAMES = {'en': 'English', 'fr': 'Français', 'es': 'Español', 'de': 'Deutsch', 'ar': 'العربية', 'it': 'Italiano',
         'pt': 'Português', 'ru': 'Русский', 'zh': '中文'}

_catalogues = {}


def languages():
    """
    :return: list[tuple[str, str]]  (code, native name) for every catalogue on disk, English first
    """
    codes = sorted(splitext(f)[0] for f in listdir(HERE) if f.endswith('.json'))
    if DEFAULT in codes:
        codes.remove(DEFAULT)
        codes.insert(0, DEFAULT)
    return [(c, NAMES.get(c, c)) for c in codes]


def catalogue(lang):
    """
    :param lang: str  language code
    :return: dict[str, str]
    """
    lang = normalise(lang)
    if lang not in _catalogues:
        path = join(HERE, lang + '.json')
        if exists(path):
            with open(path, 'rb') as fh:
                _catalogues[lang] = load(fh)
        else:
            _catalogues[lang] = {}
    return _catalogues[lang]


def normalise(lang):
    """
    :param lang: str | None  'fr', 'fr-FR', 'fr_CA', ...
    :return: str  a known code, or DEFAULT
    """
    if not lang:
        return DEFAULT
    code = str(lang).replace('_', '-').split('-')[0].lower()
    return code if exists(join(HERE, code + '.json')) else DEFAULT


def isRtl(lang):
    """
    :param lang: str
    :return: bool
    """
    return normalise(lang) in RTL


def translate(text, lang=DEFAULT, **params):
    """
    :param text: str  English source string (may contain ``{name}`` placeholders)
    :param lang: str
    :param params: values for the placeholders
    :return: str
    """
    out = catalogue(lang).get(text, text) if normalise(lang) != DEFAULT else text
    if params:
        try:
            out = out.format(**params)
        except (KeyError, IndexError, ValueError):
            pass
    return out


def pickFromHeader(acceptLanguage):
    """
    :param acceptLanguage: str | None  an HTTP Accept-Language header value
    :return: str  best available code
    """
    for part in (acceptLanguage or '').split(','):
        code = normalise(part.split(';')[0].strip())
        if code != DEFAULT or part.strip().lower().startswith('en'):
            return code
    return DEFAULT


def relativeTime(isoTimestamp, lang=DEFAULT, now=None):
    """
    Humanised relative time ("3 minutes ago") for the UIs.

    :param isoTimestamp: str  ISO-8601 UTC
    :param lang: str
    :param now: datetime | None
    :return: str
    """
    from datetime import datetime, timezone

    if not isoTimestamp:
        return translate('never', lang)
    try:
        ts = datetime.fromisoformat(isoTimestamp.replace('Z', '+00:00'))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return isoTimestamp
    now = now or datetime.now(timezone.utc)
    seconds = int((now - ts).total_seconds())
    if seconds < 45:
        return translate('just now', lang)
    minutes = seconds // 60
    if minutes < 60:
        return translate('{n} min ago', lang, n=minutes)
    hours = minutes // 60
    if hours < 24:
        return translate('{n} h ago', lang, n=hours)
    days = hours // 24
    if days < 30:
        return translate('{n} d ago', lang, n=days)
    return ts.strftime('%Y-%m-%d')


__all__ = ['DEFAULT', 'NAMES', 'RTL', 'catalogue', 'isRtl', 'languages', 'normalise', 'pickFromHeader', 'relativeTime',
           'translate']
