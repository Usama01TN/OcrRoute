# coding=utf-8
"""
Auto-routing catalog: built-in ``auto/*`` routes resolved dynamically from the providers you have, usable as the
``route`` field of any request with no setup (the same idea as a gateway's built-in combos, applied to OCR).
"""
from __future__ import absolute_import, division, print_function

from collections import OrderedDict

AUTO_TEMPLATES = OrderedDict([
    ('auto/balanced', {'title': 'Balanced', 'strategy': 'auto', 'description': 'Explainable heuristic over every healthy provider: fast engines for small images, PDF-capable for documents, handwriting/table engines on hints.', 'filter': {}, 'hints': {}}),
    ('auto/fast', {'title': 'Fastest first', 'strategy': 'least_latency', 'description': 'Lowest recent latency first; local classic engines usually win.', 'filter': {}, 'hints': {}}),
    ('auto/accurate', {'title': 'Most accurate', 'strategy': 'confidence_first', 'description': 'Providers with the best observed mean confidence first, then curated quality.', 'filter': {}, 'hints': {}}),
    ('auto/cheapest', {'title': 'Cheapest', 'strategy': 'cost_optimised', 'description': 'Local engines (free) first, then cloud engines by estimated cost.', 'filter': {}, 'hints': {}}),
    ('auto/free', {'title': 'Free only', 'strategy': 'quality_first', 'description': 'Only engines with no per-use cost (local and free tiers).', 'filter': {'cost_models': ['free', 'local']}, 'hints': {}}),
    ('auto/private', {'title': 'Private', 'strategy': 'local_first', 'description': 'Never leaves the machine: local engines only.', 'filter': {'kinds': ['local']}, 'hints': {'offline': True}}),
    ('auto/handwriting', {'title': 'Handwriting', 'strategy': 'quality_first', 'description': 'Handwriting-capable engines (VLMs, TrOCR) first.', 'filter': {'supports': 'handwriting'}, 'hints': {'handwriting': True}}),
    ('auto/tables', {'title': 'Tables & forms', 'strategy': 'quality_first', 'description': 'Engines that keep table structure first.', 'filter': {'supports': 'tables'}, 'hints': {'tables': True}}),
    ('auto/pdf', {'title': 'Documents (PDF)', 'strategy': 'cost_optimised', 'description': 'Native PDF-capable engines first, cheapest wins.', 'filter': {'supports': 'pdf'}, 'hints': {}}),
    ('auto/multilingual', {'title': 'Multilingual', 'strategy': 'script_aware', 'description': 'Engines declaring the requested script (Latin, Arabic, CJK, Cyrillic, Devanagari) first.', 'filter': {}, 'hints': {}}),
    ('auto/consensus', {'title': 'Consensus', 'strategy': 'ensemble_vote', 'description': 'Run the top 3 engines in parallel and vote word by word.', 'filter': {}, 'hints': {}}),
    ('auto/best-of-two', {'title': 'Best of two', 'strategy': 'best_of_two', 'description': 'Run two engines in parallel and keep the result with the higher confidence.', 'filter': {}, 'hints': {}}),
])

CATEGORIES = {
    'intelligent': ['auto', 'least_latency', 'cost_optimised', 'confidence_first', 'quality_first', 'language_aware',
                    'script_aware', 'ensemble_vote', 'best_of_two', 'p2c', 'least_used'],
    'deterministic': ['priority', 'round_robin', 'weighted', 'fill_first', 'random', 'local_first', 'sticky'],
}


def categoryOf(strategy):
    """
    :param strategy: str
    :return: str  intelligent | deterministic
    """
    return 'deterministic' if strategy in CATEGORIES['deterministic'] else 'intelligent'


def isAutoRoute(name):
    """
    :param name: str | None
    :return: bool
    """
    return bool(name) and name in AUTO_TEMPLATES


def applyFilter(cands, spec):
    """
    :param cands: list[Candidate]
    :param spec: dict  template 'filter'
    :return: list[Candidate]
    """
    out = cands
    if spec.get('kinds'):
        out = [c for c in out if c.kind in spec['kinds']]
    if spec.get('cost_models'):
        out = [c for c in out if c.est_cost_cents == 0]
    sup = spec.get('supports')
    if sup:
        attr = {'handwriting': 'supports_handwriting', 'tables': 'supports_tables', 'pdf': 'supports_pdf'}[sup]
        preferred = [c for c in out if getattr(c, attr)]
        out = preferred or out  # fall back to everything rather than nothing
    return out


def catalog():
    """
    :return: list[dict]  JSON view of the templates
    """
    return [{'id': k, **{kk: vv for kk, vv in v.items() if kk != 'filter'}, 'category': categoryOf(v['strategy'])}
            for k, v in AUTO_TEMPLATES.items()]


__all__ = ['AUTO_TEMPLATES', 'CATEGORIES', 'applyFilter', 'catalog', 'categoryOf', 'isAutoRoute']
