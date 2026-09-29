# coding=utf-8
"""
None
"""

from ocrroute.routing import strategies as S
from ocrroute.routing.candidates import Candidate, RequestContext, filterCandidates


def cands():
    return [
        Candidate(
            'p1',
            'a',
            'Tesseract',
            'local',
            0,
            est_cost_cents=0,
            quality_score=55,
            weight=1,
            recent_uses=5,
            recent_latency_ms=900,
        ),
        Candidate(
            'p2',
            'b',
            'GeminiOcr',
            'api',
            1,
            est_cost_cents=2.0,
            quality_score=88,
            weight=5,
            recent_uses=1,
            recent_latency_ms=300,
            supports_handwriting=True,
            supports_pdf=True,
        ),
        Candidate(
            'p3',
            'c',
            'OcrSpace',
            'api',
            2,
            est_cost_cents=0.5,
            quality_score=60,
            weight=2,
            recent_uses=3,
            recent_latency_ms=0,
            supports_pdf=True,
        ),
    ]


def names(lst):
    return [c.engine_id for c in lst]


def test_all_strategies_registered():
    assert set(S.names()) >= {
        'priority',
        'round_robin',
        'weighted',
        'fill_first',
        'least_used',
        'least_latency',
        'p2c',
        'random',
        'cost_optimised',
        'local_first',
        'quality_first',
        'language_aware',
        'ensemble_vote',
        'auto',
    }


def test_priority_and_cost_and_quality():
    ctx = RequestContext()
    assert names(S.get('priority')(cands(), ctx, {})) == ['Tesseract', 'GeminiOcr', 'OcrSpace']
    assert names(S.get('cost_optimised')(cands(), ctx, {})) == ['Tesseract', 'OcrSpace', 'GeminiOcr']
    assert names(S.get('quality_first')(cands(), ctx, {})) == ['GeminiOcr', 'OcrSpace', 'Tesseract']
    assert names(S.get('local_first')(cands(), ctx, {}))[0] == 'Tesseract'
    assert names(S.get('least_used')(cands(), ctx, {})) == ['GeminiOcr', 'OcrSpace', 'Tesseract']
    assert names(S.get('least_latency')(cands(), ctx, {})) == ['GeminiOcr', 'Tesseract', 'OcrSpace']


def test_round_robin_rotates():
    ctx, state = RequestContext(), {}
    first = names(S.get('round_robin')(cands(), ctx, state))
    second = names(S.get('round_robin')(cands(), ctx, state))
    assert first != second and set(first) == set(second)


def test_weighted_and_random_are_deterministic_with_seed():
    ctx = RequestContext()
    a = names(S.get('weighted')(cands(), ctx, {'seed': 7}))
    b = names(S.get('weighted')(cands(), ctx, {'seed': 7}))
    assert a == b and len(a) == 3
    assert sorted(names(S.get('random')(cands(), ctx, {'seed': 1}))) == sorted(names(cands()))


def test_auto_explains_and_prefers_capabilities():
    state = {}
    order = names(S.get('auto')(cands(), RequestContext(handwriting=True), state))
    assert order[0] == 'GeminiOcr' and any('handwriting' in x for x in state['explain'])
    state = {}
    order = names(S.get('auto')(cands(), RequestContext(sensitive=True), state))
    assert order[0] == 'Tesseract'
    state = {}
    order = names(S.get('auto')(cands(), RequestContext(mime='application/pdf', page_count=5), state))
    assert order[0] in ('OcrSpace', 'GeminiOcr')  # pdf-capable, cheaper first


def test_filterCandidates_reasons():
    c = cands()
    c[0].circuit_open = True
    c[1].over_limit = 'rpm'
    c[2].condition = {'language_in': ['ar']}
    kept, explain = filterCandidates(c, RequestContext(language=['en']))
    assert kept == [] and len(explain) == 3
    assert (
        any('circuit open' in e for e in explain)
        and any('rpm' in e for e in explain)
        and any('language' in e for e in explain)
    )


def test_ensemble_sets_parallel():
    state = {'k': 2}
    S.get('ensemble_vote')(cands(), RequestContext(), state)
    assert state['parallel'] == 2
