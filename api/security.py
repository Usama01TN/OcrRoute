# coding=utf-8
"""Client API-key authentication and scope checks."""
from __future__ import absolute_import, division, print_function

from datetime import datetime, timezone

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from ocrroute.api.deps import getDb
from ocrroute.crypto import hashApiKey
from ocrroute.db.base import utcnow
from ocrroute.db.models import ApiKey
from ocrroute.db.repo.usage import monthCostForKey
from ocrroute.errors import Forbidden, RateLimited, Unauthorized
from ocrroute.runtime.limits import WINDOW

SCOPES = ('ocr:read', 'ocr:write', 'manage', 'admin')
ROLE_SCOPES = {'admin': ['ocr:read', 'ocr:write', 'manage', 'admin'], 'operator': ['ocr:read', 'ocr:write', 'manage'],
               'viewer': ['ocr:read']}


def _extract(authorization, x_api_key):
    if authorization and authorization.lower().startswith('bearer '):
        return authorization[7:].strip()
    return (x_api_key or '').strip()


def requireKey(*scopes):
    def dep(
        request: Request,
        db: Session = Depends(getDb),
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiKey:
        # a logged-in panel session may call the API as admin (same-origin, cookie)
        user = getattr(request.state, 'panel_user', None)
        if user is not None:
            role = getattr(request.state, 'panel_role', 'viewer') or 'viewer'
            granted = ROLE_SCOPES.get(role, ROLE_SCOPES['viewer'])
            if 'admin' not in granted and any(sc not in granted for sc in scopes):
                raise Forbidden('Your role ({}) cannot perform this action'.format(role))
            return ApiKey(id=None, name='panel:{}'.format(user), key_hash='', key_prefix='', scopes=list(granted))
        raw = _extract(authorization, x_api_key)
        if not raw:
            raise Unauthorized("Missing API key. Send 'Authorization: Bearer ocrr_…'")
        key = db.query(ApiKey).filter(ApiKey.key_hash == hashApiKey(raw)).one_or_none()
        if key is None or not key.enabled:
            raise Unauthorized('Invalid or disabled API key')
        if key.expires_at and key.expires_at <= utcnow():
            raise Unauthorized('API key expired')
        if 'admin' not in key.scopes and any(s not in key.scopes for s in scopes):
            raise Forbidden('API key lacks scope(s): {}'.format(', '.join(scopes)))
        over = WINDOW.check('key:{}'.format(key.id), key.rpm_limit, key.rpd_limit)
        if over:
            raise RateLimited(
                'API key over its {} limit'.format(over),
                retryAfter=WINDOW.retryAfter('key:{}'.format(key.id), key.rpm_limit),
            )
        if key.monthly_budget_cents:
            month = datetime.now(timezone.utc).strftime('%Y-%m')
            if monthCostForKey(db, key.id, month) >= key.monthly_budget_cents:
                raise RateLimited('API key monthly budget exhausted', retryAfter=3600)
        WINDOW.hit('key:{}'.format(key.id))
        key.last_used_at = utcnow()
        db.commit()
        return key

    return dep
