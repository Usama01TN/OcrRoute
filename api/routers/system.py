# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ocrroute.api.deps import getCtx, getDb
from ocrroute.api.security import requireKey
from ocrroute.db.models import ApiKey, Provider
from ocrroute.runtime.context import AppContext
from ocrroute.runtime.doctor import report, toMarkdown
from ocrroute.version import __version__

router = APIRouter(tags=['system'])


@router.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@router.get('/ready')
def ready(db: Session = Depends(getDb), ctx: AppContext = Depends(getCtx)) -> Any:
    db.execute(text('select 1'))
    healthy = db.query(Provider).filter(Provider.enabled.is_(True), Provider.health != 'down').count()
    local_ok = any(e.kind == 'local' for e in ctx.registry.available())
    ok = healthy > 0 or local_ok
    body = {'status': 'ready' if ok else 'degraded', 'providers_usable': healthy, 'local_engines': local_ok}
    if ok:
        return body
    import json

    return Response(json.dumps(body), status_code=503, media_type='application/json')


@router.get('/version')
def version() -> dict[str, str]:
    return {'name': 'OcrRoute', 'version': __version__}


@router.get('/doctor')
def doctor(key: ApiKey = Depends(requireKey('admin')), ctx: AppContext = Depends(getCtx), format: str = 'json') -> Any:
    rep = report(ctx.settings, ctx.registry)
    if format == 'markdown':
        return Response(toMarkdown(rep), media_type='text/markdown')
    return rep
