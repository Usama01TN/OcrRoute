# coding=utf-8
"""
Fake engines used only by the test-suite. They follow the OCRPlugin contract exactly.
"""

import time

from ocrroute.enginelib import OCRError, OCRPlugin

CALLS = {}
LAST_PROMPT = {}


class FakeOcr(OCRPlugin):
    """Always succeeds; the text encodes which API key it saw so rotation can be asserted."""

    def _run(self, image, *args, **kwargs):  # type: ignore[no-untyped-def]
        CALLS['FakeOcr'] = CALLS.get('FakeOcr', 0) + 1
        LAST_PROMPT['prompt'] = self.getExtraPrompt() if hasattr(self, 'getExtraPrompt') else None
        key = self.getApi() or 'nokey'
        return [
            self.makeWord('hello', 10, 10, 40, 12),
            self.makeWord('world', 60, 10, 40, 12),
            self.makeWord('key:{}'.format(key), 10, 40, 80, 12),
        ]


class FakeAuthFail(OCRPlugin):
    """Fails with 401 unless the key is 'good'; simulates a provider with a bad first credential."""

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super(FakeAuthFail, self).__init__(*args, **kwargs)
        self.setOnline(True)
        self.setRetries(1)

    def _run(self, image, *args, **kwargs):  # type: ignore[no-untyped-def]
        CALLS['FakeAuthFail'] = CALLS.get('FakeAuthFail', 0) + 1
        if self.getApi() != 'good':
            raise OCRError('HTTP 401 Unauthorized: invalid api key')
        return [self.makeWord('authed', 10, 10, 60, 12)]


class FakeEmpty(OCRPlugin):
    def _run(self, image, *args, **kwargs):  # type: ignore[no-untyped-def]
        CALLS['FakeEmpty'] = CALLS.get('FakeEmpty', 0) + 1
        return []


class FakeSlow(OCRPlugin):
    def _run(self, image, *args, **kwargs):  # type: ignore[no-untyped-def]
        time.sleep(3)
        return [self.makeWord('slow', 0, 0, 10, 10)]


class FakeUnavailable(OCRPlugin):
    def _run(self, image, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise ImportError("No module named 'nothing'")
