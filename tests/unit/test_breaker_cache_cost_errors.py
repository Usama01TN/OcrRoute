# coding=utf-8
"""
None
"""

from ocrroute.errors import AUTH, BAD_INPUT, ENGINE_MISSING, NETWORK, QUOTA, RATE_LIMIT, TIMEOUT, classify
from ocrroute.routing.breaker import CircuitBreaker
from ocrroute.routing.cost import estimateCents
from ocrroute.runtime.cache import cache_key
from ocrroute.runtime.limits import SlidingWindow


def test_breaker_opens_after_threshold_and_escalates():
    b = CircuitBreaker(threshold=3, cooldown=10)
    assert not b.recordFailure('x', now=0) and not b.recordFailure('x', now=1)
    assert b.recordFailure('x', now=2) is True
    assert b.isOpen('x', now=5) and not b.allow('x', now=5)
    assert b.allow('x', now=13)  # half-open probe
    assert b.recordFailure('x', now=13) is True
    assert b.openUntil('x') >= 13 + 20  # escalated cooldown
    b.recordSuccess('x')
    assert not b.isOpen('x', now=14)


def test_classify_taxonomy():
    assert classify('HTTP 401 Unauthorized').code == AUTH
    assert classify('quota exceeded for this month').code == QUOTA
    assert classify('429 Too Many Requests').code == RATE_LIMIT
    assert classify('Read timed out').code == TIMEOUT
    assert classify('ConnectionError: Max retries exceeded').code == NETWORK
    assert classify("No module named 'torch'").code == ENGINE_MISSING
    assert classify("Image source 'x' is not an existing file").code == BAD_INPUT


def test_cost_models():
    assert estimateCents('local', 1.0, pages=10) == 0
    assert estimateCents('per_page', 0.15, pages=3) == 0.45
    assert estimateCents('per_request', 0.2) == 0.2
    assert estimateCents('per_token', 0.0001, pages=1, chars=400) > 0


def test_cache_key_is_order_independent():
    a = cache_key('abc', 'route', {'language': ['en'], 'options': {'x': 1, 'y': 2}})
    b = cache_key('abc', 'route', {'options': {'y': 2, 'x': 1}, 'language': ['en']})
    assert a == b and a != cache_key('abc', 'other', {})


def test_sliding_window():
    w = SlidingWindow()
    for _ in range(3):
        w.hit('k', now=100)
    assert w.check('k', rpm=3, now=100) == 'rpm'
    assert w.check('k', rpm=3, now=161) == ''
    assert w.check('k', rpd=2, now=161) == 'rpd'
