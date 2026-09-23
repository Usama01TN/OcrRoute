# coding=utf-8
"""Result cache keyed by sha256(input | engine-or-route | normalised options)."""
from __future__ import absolute_import, division, print_function

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ocrroute.db.base import utcnow
from ocrroute.db.models import CacheEntry


def cache_key(sha256, target, options):
    norm = json.dumps(options, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256('{}|{}|{}'.format(sha256, target, norm).encode()).hexdigest()


def get(session, key):
    row = session.get(CacheEntry, key)
    if row is None:
        return None
    if row.expires_at <= utcnow():
        session.delete(row)
        return None
    row.hits += 1
    return row


def put(session, key, run_id, result, routing, ttl):
    if ttl <= 0:
        return
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).replace(microsecond=0).isoformat()
    body = json.dumps(result, ensure_ascii=False)
    row = session.get(CacheEntry, key)
    if row is None:
        row = CacheEntry(cache_key=key)
        session.add(row)
    row.run_id = run_id
    row.result_json = result
    row.routing_json = routing
    row.bytes = len(body)
    row.expires_at = expires


def purgeExpired(session):
    res = session.execute(delete(CacheEntry).where(CacheEntry.expires_at <= utcnow()))
    return int(res.rowcount or 0)


def clear(session):
    return int(session.execute(delete(CacheEntry)).rowcount or 0)


def count(session):
    return len(session.execute(select(CacheEntry.cache_key)).all())
