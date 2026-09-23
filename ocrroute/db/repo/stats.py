# coding=utf-8
"""Aggregations for dashboards - index-backed, no N+1."""
from __future__ import absolute_import, division, print_function

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from ocrroute.db.models import Attempt, CacheEntry, Job, Provider, Run


def _since(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def summary(session, hours=24):
    since = _since(hours)
    total, ok, cached, dur_sum, pages, cost = session.execute(
        select(
            func.count(Run.id),
            func.sum(case((Run.status == 'succeeded', 1), else_=0)),
            func.sum(case((Run.status == 'cached', 1), else_=0)),
            func.sum(Run.duration_ms),
            func.sum(Run.page_count),
            func.sum(Run.cost_cents),
        ).where(Run.created_at >= since, Run.origin != 'panel_test')
    ).one()
    total = total or 0
    durations = [
        r[0]
        for r in session.execute(
            select(Run.duration_ms).where(Run.created_at >= since, Run.status == 'succeeded').order_by(Run.duration_ms)
        ).all()
    ]

    def pct(p):
        if not durations:
            return 0
        return int(durations[min(len(durations) - 1, int(len(durations) * p))])

    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_cost = session.execute(select(func.sum(Run.cost_cents)).where(Run.created_at >= month_start)).scalar() or 0
    providers = session.execute(
        select(Provider.health, func.count(Provider.id)).where(Provider.enabled.is_(True)).group_by(Provider.health)
    ).all()
    health = {h: c for h, c in providers}
    queue = session.execute(select(func.count(Job.id)).where(Job.status.in_(('queued', 'running')))).scalar() or 0
    cache_rows = session.execute(select(func.count(CacheEntry.cache_key))).scalar() or 0
    return {
        'window_hours': hours,
        'runs': total,
        'succeeded': int(ok or 0) + int(cached or 0),
        'success_rate': round(((int(ok or 0) + int(cached or 0)) / total) * 100, 1) if total else 0.0,
        'cache_hit_rate': round((int(cached or 0) / total) * 100, 1) if total else 0.0,
        'p50_ms': pct(0.5),
        'p95_ms': pct(0.95),
        'pages': int(pages or 0),
        'cost_cents': round(float(cost or 0), 3),
        'month_cost_cents': round(float(month_cost), 3),
        'healthy_providers': int(health.get('healthy', 0)),
        'total_providers': sum(health.values()),
        'queue_depth': int(queue),
        'cache_entries': int(cache_rows),
    }


def dailySeries(session, days=7):
    since = _since(days * 24)
    day = func.substr(Run.created_at, 1, 10)
    rows = session.execute(
        select(day, Run.status, func.count(Run.id), func.sum(Run.cost_cents))
        .where(Run.created_at >= since)
        .group_by(day, Run.status)
        .order_by(day)
    ).all()
    out = {}
    for d, status, n, cost in rows:
        bucket = out.setdefault(d, {'day': d, 'succeeded': 0, 'failed': 0, 'cached': 0, 'other': 0, 'cost_cents': 0.0})
        bucket[status if status in bucket else 'other'] += int(n)
        bucket['cost_cents'] += float(cost or 0)
    return list(out.values())


def engineStats(session, hours=24):
    since = _since(hours)
    rows = session.execute(
        select(
            Attempt.engine_id,
            Attempt.status,
            func.count(Attempt.id),
            func.avg(Attempt.duration_ms),
            func.sum(Attempt.cost_cents),
        )
        .where(Attempt.started_at >= since)
        .group_by(Attempt.engine_id, Attempt.status)
    ).all()
    out = {}
    for eng, status, n, avg_ms, cost in rows:
        b = out.setdefault(
            eng, {'engine': eng, 'attempts': 0, 'succeeded': 0, 'failed': 0, 'avg_ms': 0, 'cost_cents': 0.0}
        )
        b['attempts'] += int(n)
        if status == 'succeeded':
            b['succeeded'] += int(n)
            b['avg_ms'] = int(avg_ms or 0)
        else:
            b['failed'] += int(n)
        b['cost_cents'] += float(cost or 0)
    for b in out.values():
        b['success_rate'] = round(b['succeeded'] / b['attempts'] * 100, 1) if b['attempts'] else 0.0
    return sorted(out.values(), key=lambda b: -b['attempts'])


def providerRecentLatency(session, limit_per_provider=20):
    """EWMA-ish recent latency per provider id (used by least_latency)."""
    rows = session.execute(
        select(Attempt.provider_id, Attempt.duration_ms)
        .where(Attempt.status == 'succeeded')
        .order_by(Attempt.started_at.desc())
        .limit(limit_per_provider * 50)
    ).all()
    acc = {}
    for pid, ms in rows:
        lst = acc.setdefault(pid, [])
        if len(lst) < limit_per_provider:
            lst.append(int(ms))
    out = {}
    for pid, lst in acc.items():
        ewma = float(lst[-1])
        for v in reversed(lst[:-1]):
            ewma = 0.7 * ewma + 0.3 * v
        out[pid] = ewma
    return out


def providerRecentConfidence(session, limit=300):
    """
    :param session: Session
    :param limit: int  recent successful runs to consider
    :return: dict[str, float]  provider label -> mean of mean_confidence
    """
    rows = session.execute(
        select(Run.winning_provider, Run.mean_confidence).where(Run.status == 'succeeded', Run.mean_confidence > 0)
        .order_by(Run.created_at.desc()).limit(limit)
    ).all()
    acc = {}
    for label, conf in rows:
        acc.setdefault(label, []).append(float(conf))
    return {k: sum(v) / len(v) for k, v in acc.items() if v}


provider_recent_confidence = providerRecentConfidence
