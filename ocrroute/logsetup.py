# coding=utf-8
"""structlog configuration with secret redaction."""
from __future__ import absolute_import, division, print_function

import logging
import re

import structlog

_SECRET_RE = re.compile(
    r'(?i)(sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|ocrr_[A-Za-z0-9]{16,}|'
    r"(?:api[_-]?key|apikey|token|secret|authorization|password)[\"'=:\s]+[A-Za-z0-9._\-]{6,})"
)


def redact(text):
    if not text:
        return text

    def _mask(m):
        s = m.group(0)
        return s[:6] + '…' + s[-4:] if len(s) > 12 else '***'

    return _SECRET_RE.sub(_mask, text)


def _redactProcessor(_logger, _name, event):
    for k, v in list(event.items()):
        if isinstance(v, str):
            event[k] = redact(v)
    return event


def configureLogging(level='INFO', json_logs=False):
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format='%(message)s')
    for noisy in ('httpx', 'httpcore', 'uvicorn.access', 'PIL'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt='iso'),
            _redactProcessor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=False,
    )


def getLogger(name='ocrroute'):
    return structlog.getLogger(name)
