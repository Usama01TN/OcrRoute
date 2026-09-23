# coding=utf-8
"""Ordering strategies. Each is pure: (candidates, ctx, state) -> ordered candidates."""
from __future__ import absolute_import, division, print_function

import random

from ocrroute.routing.candidates import Candidate, RequestContext  # noqa: F401  (documented contract types)


_REGISTRY = {}
DESCRIPTIONS = {}


def register(name, description):
    def deco(fn):
        _REGISTRY[name] = fn
        DESCRIPTIONS[name] = description
        return fn

    return deco


def get(name):
    if name not in _REGISTRY:
        raise KeyError("Unknown strategy '{}'. Known: {}".format(name, ', '.join(sorted(_REGISTRY))))
    return _REGISTRY[name]


def names():
    return sorted(_REGISTRY)


def _rng(state):
    seed = state.get('seed')
    return random.Random(seed) if seed is not None else random.Random()


@register('priority', 'Strict member order - the classic fallback chain.')
def priority(c, ctx, state):
    return sorted(c, key=lambda x: (x.order_index, x.priority))


@register('round_robin', 'Rotate the starting member on every run.')
def roundRobin(c, ctx, state):
    ordered = priority(c, ctx, state)
    if not ordered:
        return ordered
    counter = int(state.get('counter', 0))
    state['counter'] = counter + 1
    k = counter % len(ordered)
    return ordered[k:] + ordered[:k]


@register('weighted', 'Random order, probability proportional to member weight.')
def weighted(c, ctx, state):
    rng = _rng(state)
    pool = list(c)
    out = []
    while pool:
        total = sum(max(1, x.weight) for x in pool)
        r = rng.uniform(0, total)
        acc = 0.0
        for x in pool:
            acc += max(1, x.weight)
            if r <= acc:
                out.append(x)
                pool.remove(x)
                break
    return out


@register('fill_first', "Exhaust a member's quota before moving to the next (maximises free tiers).")
def fillFirst(c, ctx, state):
    return priority(c, ctx, state)  # limits are enforced upstream by filterCandidates(over_limit)


@register('least_used', 'Members with the fewest recent runs first.')
def leastUsed(c, ctx, state):
    return sorted(c, key=lambda x: (x.recent_uses, x.order_index))


@register('least_latency', 'Lowest recent latency (EWMA) first; unknown latency goes last.')
def leastLatency(c, ctx, state):
    return sorted(c, key=lambda x: (x.recent_latency_ms or float('inf'), x.order_index))


@register('p2c', 'Power of two choices: sample two members, take the one with fewer in-flight attempts.')
def p2c(c, ctx, state):
    rng = _rng(state)
    pool = list(c)
    out = []
    while len(pool) > 1:
        a, b = rng.sample(pool, 2)
        pick = a if (a.in_flight, a.order_index) <= (b.in_flight, b.order_index) else b
        out.append(pick)
        pool.remove(pick)
    return out + pool


@register('random', 'Uniform shuffle.')
def randomOrder(c, ctx, state):
    pool = list(c)
    _rng(state).shuffle(pool)
    return pool


@register('cost_optimised', 'Cheapest estimated cost first (local engines cost 0).')
def costOptimised(c, ctx, state):
    return sorted(c, key=lambda x: (x.est_cost_cents, -x.quality_score, x.order_index))


@register('local_first', 'All local engines before any API engine.')
def localFirst(c, ctx, state):
    return sorted(c, key=lambda x: (0 if x.kind == 'local' else 1, x.order_index))


@register('quality_first', 'Highest curated quality score first, cost ignored.')
def qualityFirst(c, ctx, state):
    return sorted(c, key=lambda x: (-x.quality_score, x.order_index))


@register('language_aware', 'Members declaring the requested language first.')
def languageAware(c, ctx, state):
    wanted = set(ctx.language)

    def score(x):
        langs = set(x.languages) | ({x.language} if x.language else set())
        return 0 if (not langs or wanted & langs) else 1

    return sorted(c, key=lambda x: (score(x), x.order_index))


@register('ensemble_vote', 'Run the first k healthy members in parallel and reconcile by word-box voting.')
def ensembleVote(c, ctx, state):
    state['parallel'] = int(state.get('k', 3))
    return qualityFirst(c, ctx, state)


@register('auto', 'Explainable heuristic: input shape, language, hints and health choose a sub-strategy.')
def auto(c, ctx, state):
    explain = state.setdefault('explain', [])
    if ctx.sensitive or ctx.offline:
        explain.append('auto: sensitive/offline → local_first')
        return localFirst(c, ctx, state)
    if ctx.handwriting:
        explain.append('auto: handwriting hint → handwriting-capable engines first')
        return sorted(c, key=lambda x: (0 if x.supports_handwriting else 1, -x.quality_score, x.order_index))
    if ctx.tables:
        explain.append('auto: tables requested → table-capable engines first')
        return sorted(c, key=lambda x: (0 if x.supports_tables else 1, -x.quality_score, x.order_index))
    if ctx.is_pdf:
        explain.append('auto: multi-page/PDF → PDF-capable engines by cost')
        return sorted(c, key=lambda x: (0 if x.supports_pdf else 1, x.est_cost_cents, x.order_index))
    if ctx.small:
        fast = {'Tesseract', 'RapidOcr', 'PaddleOcr', 'EasyOCR', 'OpenOcr'}
        explain.append('auto: small single image → fast classic engines first')
        return sorted(c, key=lambda x: (0 if x.engine_id in fast else 1, x.recent_latency_ms or 1e9, x.order_index))
    explain.append('auto: default → least_latency over healthy candidates')
    return sorted(c, key=lambda x: (0 if x.health == 'healthy' else 1, x.recent_latency_ms or 1e9, x.order_index))


SCRIPTS = {
    'latin': set('en fr de es it pt nl sv da no fi pl cs ro hu tr id vi eng fre ger spa ita por dut'.split()),
    'arabic': set('ar fa ur ara'.split()), 'cjk': set('zh ja ko chs cht jpn kor zh-cn zh-tw'.split()),
    'cyrillic': set('ru uk bg sr rus'.split()), 'devanagari': set('hi mr ne hin'.split()), 'hebrew': set(['he', 'heb']),
    'thai': set(['th', 'tha']), 'greek': set(['el', 'ell']),
}


def scriptOf(lang):
    """
    :param lang: str  language code
    :return: str  script family name ('latin' when unknown)
    """
    code = (lang or 'en').lower().split('-')[0]
    for family, codes in SCRIPTS.items():
        if code in codes or lang.lower() in codes:
            return family
    return 'latin'


@register('confidence_first', 'Providers with the best observed mean confidence first, then curated quality.')
def confidenceFirst(c, ctx, state):
    return sorted(c, key=lambda x: (-(getattr(x, 'recent_confidence', 0.0) or 0.0), -x.quality_score, x.order_index))


@register('script_aware', 'Providers declaring a language of the requested script (Latin, Arabic, CJK, Cyrillic, ...) first.')
def scriptAware(c, ctx, state):
    wanted = set(scriptOf(lang) for lang in ctx.language)
    handwriting_ok = ctx.handwriting

    def score(x):
        langs = set(x.languages) | (set([x.language]) if x.language else set())
        families = set(scriptOf(lang) for lang in langs)
        if not langs:  # VLM engines usually declare nothing and read any script
            return 1 if not handwriting_ok else 0
        return 0 if wanted & families else 2

    return sorted(c, key=lambda x: (score(x), -x.quality_score, x.order_index))


@register('sticky', 'Same input always goes to the same provider (hash of the image), with the others as fallback.')
def sticky(c, ctx, state):
    ordered = priority(c, ctx, state)
    if not ordered:
        return ordered
    digest = getattr(ctx, 'sha256', '') or ''
    k = int(digest[:8], 16) % len(ordered) if digest else 0
    return ordered[k:] + ordered[:k]


@register('best_of_two', 'Run two providers in parallel and keep the result with the higher confidence.')
def bestOfTwo(c, ctx, state):
    state['parallel'] = 2
    state['pick'] = 'best'
    return confidenceFirst(c, ctx, state)
