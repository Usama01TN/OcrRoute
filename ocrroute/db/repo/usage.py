# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from sqlalchemy import func, select

from ocrroute.db.models import Run, UsageDaily


def monthCostForProvider(session, provider_id, month):
    from ocrroute.db.models import Attempt

    v = session.execute(
        select(func.sum(Attempt.cost_cents)).where(
            Attempt.provider_id == provider_id, Attempt.started_at.like('{}%'.format(month))
        )
    ).scalar()
    return float(v or 0.0)


def monthCostForKey(session, api_key_id, month):
    v = session.execute(
        select(func.sum(Run.cost_cents)).where(Run.api_key_id == api_key_id, Run.created_at.like('{}%'.format(month)))
    ).scalar()
    return float(v or 0.0)


def groupedUsage(session, group_by='engine', since=''):
    col = {
        'engine': Run.winning_engine,
        'route': Run.route_name,
        'key': Run.api_key_id,
        'day': func.substr(Run.created_at, 1, 10),
    }.get(group_by, Run.winning_engine)
    q = select(
        col,
        func.count(Run.id),
        func.sum(caseOk()),
        func.sum(Run.page_count),
        func.sum(Run.chars),
        func.sum(Run.cost_cents),
        func.avg(Run.duration_ms),
    ).where(Run.origin != 'panel_test')
    if since:
        q = q.where(Run.created_at >= since)
    rows = session.execute(q.group_by(col).order_by(func.count(Run.id).desc())).all()
    return [
        {
            'group': g or '(none)',
            'runs': int(n),
            'successes': int(ok or 0),
            'pages': int(p or 0),
            'chars': int(c or 0),
            'cost_cents': round(float(cost or 0), 4),
            'avg_ms': int(avg or 0),
        }
        for g, n, ok, p, c, cost, avg in rows
    ]


def caseOk():
    from sqlalchemy import case

    return case((Run.status.in_(('succeeded', 'cached')), 1), else_=0)


def rollupDay(session, day):
    """Aggregate one day of runs into ``usage_daily`` (idempotent)."""
    rows = session.execute(
        select(
            Run.winning_engine,
            Run.api_key_id,
            func.count(Run.id),
            func.sum(caseOk()),
            func.sum(Run.page_count),
            func.sum(Run.chars),
            func.sum(Run.cost_cents),
        )
        .where(Run.created_at.like('{}%'.format(day)))
        .group_by(Run.winning_engine, Run.api_key_id)
    ).all()
    n = 0
    for eng, key, runs, ok, pages, chars, cost in rows:
        pk = {'day': day, 'engine_id': eng or '', 'provider_id': '', 'api_key_id': key or ''}
        row = session.get(UsageDaily, pk)
        if row is None:
            row = UsageDaily(**pk)
            session.add(row)
        row.runs = int(runs)
        row.successes = int(ok or 0)
        row.failures = int(runs) - int(ok or 0)
        row.pages = int(pages or 0)
        row.chars = int(chars or 0)
        row.cost_cents = float(cost or 0)
        n += 1
    return n
