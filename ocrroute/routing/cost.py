# coding=utf-8
"""Cost estimation and accounting in USD cents."""
from __future__ import absolute_import, division, print_function


def estimateCents(cost_model, unit_price, pages=1, chars=0, requests=1):
    if cost_model in ('free', 'local'):
        return 0.0
    if cost_model == 'per_page':
        return round(unit_price * max(1, pages), 4)
    if cost_model == 'per_request':
        return round(unit_price * max(1, requests), 4)
    if cost_model == 'per_token':
        # ~4 chars per output token plus ~1100 image tokens per page as a rough input estimate
        tokens = max(0, chars) / 4.0 + 1100 * max(1, pages)
        return round(unit_price * tokens, 4)
    return 0.0
