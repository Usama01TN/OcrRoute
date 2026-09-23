# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

import json

import httpx

from ocrroute.logsetup import getLogger

log = getLogger(__name__)


def deliver(url, payload, timeout=10.0):
    try:
        from ocrroute.config import getSettings
        from ocrroute.pipeline.input import checkUrlAllowed

        checkUrlAllowed(url, getSettings())
        r = httpx.post(
            url,
            content=json.dumps(payload),
            headers={'Content-Type': 'application/json', 'User-Agent': 'OcrRoute-Webhook/0.1'},
            timeout=timeout,
        )
        return r.status_code < 300
    except Exception as exc:  # noqa: BLE001
        log.warning('webhook failed', url=url, error=str(exc))
        return False
