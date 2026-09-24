# coding=utf-8
"""Management endpoints: engines, providers, credentials, routes, keys, usage, settings, audit."""
from __future__ import absolute_import, division, print_function

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ocrroute.api.deps import getCtx, getDb
from ocrroute.api.schemas.admin import (
    ApiKeyIn,
    ApiKeyPatch,
    CredentialIn,
    ProviderIn,
    ProviderPatch,
    RouteIn,
    RouteMemberIn,
    RoutePatch,
    SettingsPatch,
    SimulateIn,
)
from ocrroute.api.security import requireKey
from ocrroute.crypto import hashApiKey, mask, newApiKey
from ocrroute.db.base import utcnow
from ocrroute.db.models import ApiKey, AuditLog, Credential, Engine, Provider, Route, RouteMember, Setting
from ocrroute.db.repo.engines import syncEngines
from ocrroute.db.repo.stats import dailySeries, engineStats, summary
from ocrroute.db.repo.usage import groupedUsage
from ocrroute.errors import BadInput, Conflict, NotFound
from ocrroute.routing import strategies
from ocrroute.routing.candidates import RequestContext
from ocrroute.runtime.context import AppContext

router = APIRouter(tags=['admin'])
admin = requireKey('admin')
manage = requireKey('manage')


def audit(
    db: Session,
    request: Request,
    key: ApiKey,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor=key.name,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
            ip=request.client.host if request.client else '',
        )
    )


# ---------------------------------------------------------------- engines
def engineToDict(e: Engine) -> dict[str, Any]:
    return {c.name: getattr(e, c.name) for c in Engine.__table__.columns}


@router.get('/engines')
def listEngines(
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:read')),
    kind: str = '',
    available: bool | None = None,
) -> dict[str, Any]:
    q = select(Engine).order_by(Engine.kind, Engine.id)
    if kind:
        q = q.where(Engine.kind == kind)
    if available is not None:
        q = q.where(Engine.available.is_(available))
    return {'items': [engineToDict(e) for e in db.execute(q).scalars().all()]}


@router.get('/engines/{engine_id}')
def getEngine(
    engine_id: str,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:read')),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    e = db.get(Engine, engine_id)
    if e is None:
        raise NotFound("engine '{}' not found".format(engine_id))
    d = engineToDict(e)
    info = ctx.registry.get(engine_id)
    d['docstring'] = info.docstring if info else ''
    d['providers'] = [
        {'id': p.id, 'label': p.label, 'enabled': p.enabled, 'health': p.health}
        for p in db.query(Provider).filter(Provider.engine_id == engine_id).all()
    ]
    return d


@router.post('/engines/refresh')
def refreshEngines(
    request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage), ctx: AppContext = Depends(getCtx)
) -> dict[str, Any]:
    ctx.registry.discover(force=True)
    from ocrroute.db.repo.engines import seedProviders

    seeded = seedProviders(db, ctx.registry) if ctx.settings.auto_seed_providers else []
    n = syncEngines(db, ctx.registry)
    audit(db, request, key, 'engines.refresh', 'engine', '*', {'count': n})
    db.commit()
    return {'seeded_providers': seeded, 'synced': n}


@router.patch('/engines/{engine_id}')
def patchEngine(
    engine_id: str, body: dict[str, Any], request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage)
) -> dict[str, Any]:
    e = db.get(Engine, engine_id)
    if e is None:
        raise NotFound('engine not found')
    for f in ('enabled', 'unit_price', 'cost_model', 'quality_score'):
        if f in body:
            setattr(e, f, body[f])
    audit(db, request, key, 'engine.update', 'engine', engine_id, body)
    db.commit()
    return engineToDict(e)


@router.post('/engines/{engine_id}/probe')
def probeEngine(
    engine_id: str, db: Session = Depends(getDb), key: ApiKey = Depends(manage), ctx: AppContext = Depends(getCtx)
) -> dict[str, Any]:
    """Run the engine with defaults against the bundled sample image and return the full envelope."""
    from ocrroute.pipeline.input import loadInput
    from ocrroute.runtime.executor import OcrRequest
    from ocrroute.runtime.sample import sampleImageBytes

    if ctx.registry.get(engine_id) is None:
        raise NotFound('engine not found')
    doc = loadInput(settings=ctx.settings, file_bytes=sampleImageBytes(), filename='sample.png')
    out = ctx.executor.execute(
        OcrRequest(doc=doc, engine=engine_id, cache=False, origin='panel_test', output=['overlay_png'])
    )
    return out.toDict()


# ---------------------------------------------------------------- providers
def providerToDict(p: Provider, ctx: AppContext | None = None) -> dict[str, Any]:
    d = {c.name: getattr(p, c.name) for c in Provider.__table__.columns}
    d['engine_name'] = p.engine.name if p.engine else p.engine_id
    d['engine_kind'] = p.engine.kind if p.engine else ''
    d['credentials'] = [
        {
            'id': c.id,
            'alias': c.alias,
            'enabled': c.enabled,
            'order_index': c.order_index,
            'last_used_at': c.last_used_at,
            'success_count': c.success_count,
            'failure_count': c.failure_count,
            'exhausted_until': c.exhausted_until,
            'masked': mask(ctx.secrets.decrypt(c.secret_enc)) if ctx else '…' + c.id[-4:],
        }
        for c in p.credentials
    ]
    return d


@router.get('/providers')
def listProviders(
    db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read')), ctx: AppContext = Depends(getCtx)
) -> dict[str, Any]:
    rows = (
        db.execute(
            select(Provider).options(selectinload(Provider.credentials)).order_by(Provider.priority, Provider.label)
        )
        .scalars()
        .all()
    )
    return {'items': [providerToDict(p, ctx) for p in rows]}


@router.post('/providers', status_code=201)
def createProvider(
    body: ProviderIn,
    request: Request,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(manage),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    if ctx.registry.get(body.engine_id) is None:
        raise BadInput("unknown engine '{}'".format(body.engine_id))
    p = Provider(**body.model_dump())
    db.add(p)
    db.flush()
    audit(db, request, key, 'provider.create', 'provider', p.id, {'label': p.label, 'engine': p.engine_id})
    db.commit()
    db.refresh(p)
    return providerToDict(p, ctx)


@router.get('/providers/{pid}')
def getProvider(
    pid: str,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:read')),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    p = db.get(Provider, pid)
    if p is None:
        raise NotFound('provider not found')
    return providerToDict(p, ctx)


@router.patch('/providers/{pid}')
def patchProvider(
    pid: str,
    body: ProviderPatch,
    request: Request,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(manage),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    p = db.get(Provider, pid)
    if p is None:
        raise NotFound('provider not found')
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(p, k, v)
    p.updated_at = utcnow()
    audit(db, request, key, 'provider.update', 'provider', pid, changes)
    db.commit()
    return providerToDict(p, ctx)


@router.delete('/providers/{pid}', status_code=204)
def deleteProvider(pid: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage)) -> Response:
    p = db.get(Provider, pid)
    if p is None:
        raise NotFound('provider not found')
    db.delete(p)
    audit(db, request, key, 'provider.delete', 'provider', pid)
    db.commit()
    return Response(status_code=204)


@router.post('/providers/{pid}/reset-circuit')
def resetCircuit(
    pid: str, db: Session = Depends(getDb), key: ApiKey = Depends(manage), ctx: AppContext = Depends(getCtx)
) -> dict[str, Any]:
    p = db.get(Provider, pid)
    if p is None:
        raise NotFound('provider not found')
    ctx.breaker.reset('provider:{}'.format(pid))
    p.consecutive_failures = 0
    p.circuit_open_until = ''
    p.health = 'unknown'
    db.commit()
    return {'reset': True}


@router.post('/providers/{pid}/test')
def testProvider(
    pid: str,
    request: Request,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(manage),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    """Real round-trip against a small bundled test image; logged with origin=panel_test (never billed to a client)."""
    from ocrroute.pipeline.input import loadInput
    from ocrroute.runtime.executor import OcrRequest
    from ocrroute.runtime.sample import sampleImageBytes as test_image_png

    p = db.get(Provider, pid)
    if p is None:
        raise NotFound('provider not found')
    doc = loadInput(settings=ctx.settings, file_bytes=test_image_png(), filename='ocrroute-test.png')
    outcome = ctx.executor.execute(
        OcrRequest(doc=doc, provider_id=pid, cache=False, origin='panel_test', output=['overlay_png'])
    )
    return outcome.toDict()


# ---------------------------------------------------------------- credentials
@router.post('/credentials', status_code=201)
def createCredential(
    body: CredentialIn,
    request: Request,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(manage),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    if db.get(Provider, body.provider_id) is None:
        raise NotFound('provider not found')
    c = Credential(
        provider_id=body.provider_id,
        alias=body.alias or mask(body.secret),
        enabled=body.enabled,
        order_index=body.order_index,
        secret_enc=ctx.secrets.encrypt(body.secret),
    )
    db.add(c)
    db.flush()
    audit(db, request, key, 'credential.create', 'credential', c.id, {'provider': body.provider_id})
    db.commit()
    return {'id': c.id, 'alias': c.alias, 'masked': mask(body.secret)}


@router.patch('/credentials/{cid}')
def patchCredential(
    cid: str, body: dict[str, Any], request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage)
) -> dict[str, Any]:
    c = db.get(Credential, cid)
    if c is None:
        raise NotFound('credential not found')
    for f in ('alias', 'enabled', 'order_index'):
        if f in body:
            setattr(c, f, body[f])
    if body.get('clear_exhausted'):
        c.exhausted_until = ''
    audit(db, request, key, 'credential.update', 'credential', cid, {k: v for k, v in body.items() if k != 'secret'})
    db.commit()
    return {'id': c.id, 'alias': c.alias, 'enabled': c.enabled}


@router.delete('/credentials/{cid}', status_code=204)
def deleteCredential(
    cid: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage)
) -> Response:
    c = db.get(Credential, cid)
    if c is None:
        raise NotFound('credential not found')
    db.delete(c)
    audit(db, request, key, 'credential.delete', 'credential', cid)
    db.commit()
    return Response(status_code=204)


@router.post('/credentials/{cid}/verify')
def verifyCredential(
    cid: str, db: Session = Depends(getDb), key: ApiKey = Depends(manage), ctx: AppContext = Depends(getCtx)
) -> dict[str, Any]:
    from ocrroute.pipeline.input import loadInput
    from ocrroute.runtime.executor import OcrRequest
    from ocrroute.runtime.sample import sampleImageBytes as test_image_png

    c = db.get(Credential, cid)
    if c is None:
        raise NotFound('credential not found')
    # temporarily make this the only enabled credential of the provider for the test
    doc = loadInput(settings=ctx.settings, file_bytes=test_image_png(), filename='ocrroute-test.png')
    others = [x for x in c.provider.credentials if x.id != cid and x.enabled]
    for x in others:
        x.enabled = False
    db.commit()
    try:
        outcome = ctx.executor.execute(OcrRequest(doc=doc, provider_id=c.provider_id, cache=False, origin='panel_test'))
    finally:
        with db.begin_nested() if False else db:  # re-enable
            for x in db.query(Credential).filter(Credential.id.in_([o.id for o in others])).all():
                x.enabled = True
            db.commit()
    return {
        'ok': outcome.status == 'succeeded',
        'status': outcome.status,
        'error_code': outcome.error_code,
        'error_message': outcome.error_message,
        'chars': outcome.usage.get('chars', 0),
    }


# ---------------------------------------------------------------- routes
def routeToDict(r: Route) -> dict[str, Any]:
    d = {c.name: getattr(r, c.name) for c in Route.__table__.columns}
    d['members'] = [
        {
            'id': m.id,
            'provider_id': m.provider_id,
            'provider_label': m.provider.label if m.provider else '',
            'engine_id': m.provider.engine_id if m.provider else '',
            'order_index': m.order_index,
            'weight': m.weight,
            'enabled': m.enabled,
            'condition': m.condition,
            'option_overrides': m.option_overrides,
        }
        for m in r.members
    ]
    return d


@router.get('/routes')
def listRoutes(db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read'))) -> dict[str, Any]:
    rows = (
        db.execute(
            select(Route).options(selectinload(Route.members).selectinload(RouteMember.provider)).order_by(Route.name)
        )
        .scalars()
        .all()
    )
    return {'items': [routeToDict(r) for r in rows], 'strategies': strategies.DESCRIPTIONS}


@router.get('/routes/catalog')
def routeCatalog(key: ApiKey = Depends(requireKey('ocr:read'))):
    """
    Built-in ``auto/*`` routes (no setup needed) plus strategy categories.
    """
    from ocrroute.routing.templates import CATEGORIES, catalog

    return {'templates': catalog(), 'categories': CATEGORIES}


@router.get('/routes/strategies')
def listStrategies(key: ApiKey = Depends(requireKey('ocr:read'))) -> dict[str, str]:
    return strategies.DESCRIPTIONS


def _setMembers(db: Session, route: Route, members: list[RouteMemberIn]) -> None:
    for m in list(route.members):
        db.delete(m)
    db.flush()
    for i, m in enumerate(members):
        if db.get(Provider, m.provider_id) is None:
            raise BadInput("provider '{}' not found".format(m.provider_id))
        db.add(
            RouteMember(
                route_id=route.id,
                provider_id=m.provider_id,
                order_index=m.order_index or i,
                weight=m.weight,
                enabled=m.enabled,
                condition=m.condition,
                option_overrides=m.option_overrides,
            )
        )


@router.post('/routes', status_code=201)
def createRoute(
    body: RouteIn, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage)
) -> dict[str, Any]:
    if body.strategy not in strategies.names():
        raise BadInput("unknown strategy '{}'".format(body.strategy))
    if db.query(Route).filter(Route.name == body.name).first():
        raise Conflict("route '{}' already exists".format(body.name))
    if body.is_default:
        for r in db.query(Route).filter(Route.is_default.is_(True)).all():
            r.is_default = False
    r = Route(**body.model_dump(exclude={'members'}))
    db.add(r)
    db.flush()
    _setMembers(db, r, body.members)
    audit(db, request, key, 'route.create', 'route', r.id, {'name': r.name})
    db.commit()
    r = db.execute(
        select(Route).options(selectinload(Route.members).selectinload(RouteMember.provider)).where(Route.id == r.id)
    ).scalar_one()
    return routeToDict(r)


def _loadRoute(db: Session, rid: str) -> Route:
    r = db.execute(
        select(Route)
        .options(selectinload(Route.members).selectinload(RouteMember.provider))
        .where((Route.id == rid) | (Route.name == rid))
    ).scalar_one_or_none()
    if r is None:
        raise NotFound("route '{}' not found".format(rid))
    return r


@router.get('/routes/{rid}')
def getRoute(rid: str, db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read'))) -> dict[str, Any]:
    return routeToDict(_loadRoute(db, rid))


@router.patch('/routes/{rid}')
def patchRoute(
    rid: str, body: RoutePatch, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage)
) -> dict[str, Any]:
    r = _loadRoute(db, rid)
    changes = body.model_dump(exclude_none=True)
    members = changes.pop('members', None)
    if 'strategy' in changes and changes['strategy'] not in strategies.names():
        raise BadInput("unknown strategy '{}'".format(changes['strategy']))
    if changes.get('is_default'):
        for o in db.query(Route).filter(Route.is_default.is_(True), Route.id != r.id).all():
            o.is_default = False
    for k, v in changes.items():
        setattr(r, k, v)
    if members is not None:
        _setMembers(db, r, [RouteMemberIn(**m) for m in members])
    r.updated_at = utcnow()
    audit(db, request, key, 'route.update', 'route', r.id, changes)
    db.commit()
    return routeToDict(_loadRoute(db, r.id))


@router.delete('/routes/{rid}', status_code=204)
def deleteRoute(rid: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(manage)) -> Response:
    r = _loadRoute(db, rid)
    db.delete(r)
    audit(db, request, key, 'route.delete', 'route', rid)
    db.commit()
    return Response(status_code=204)


@router.post('/routes/simulate')
def simulate(
    body: SimulateIn,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:read')),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    ctx_req = RequestContext(
        mime=body.mime,
        width=body.width,
        height=body.height,
        page_count=body.page_count,
        language=body.language,
        handwriting=body.handwriting,
        tables=body.tables,
        sensitive=body.sensitive,
        offline=body.offline,
        max_cost_cents=body.max_cost_cents,
    )
    try:
        return ctx.router.simulate(
            db, ctx_req, engine=body.engine, route_name=body.route, strategy_override=body.strategy
        )
    except NotFound as exc:
        return {'ok': False, 'error': exc.message, 'explain': []}


# ---------------------------------------------------------------- api keys
def keyToDict(k: ApiKey) -> dict[str, Any]:
    return {
        'id': k.id,
        'name': k.name,
        'prefix': k.key_prefix,
        'scopes': k.scopes,
        'route_id': k.route_id,
        'rpm_limit': k.rpm_limit,
        'rpd_limit': k.rpd_limit,
        'monthly_budget_cents': k.monthly_budget_cents,
        'enabled': k.enabled,
        'expires_at': k.expires_at,
        'last_used_at': k.last_used_at,
        'created_at': k.created_at,
    }


@router.get('/keys')
def listKeys(db: Session = Depends(getDb), key: ApiKey = Depends(admin)) -> dict[str, Any]:
    return {'items': [keyToDict(k) for k in db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()]}


@router.post('/keys', status_code=201)
def createKey(
    body: ApiKeyIn, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)
) -> dict[str, Any]:
    raw = newApiKey()
    k = ApiKey(
        name=body.name,
        key_hash=hashApiKey(raw),
        key_prefix=raw[:10],
        scopes=body.scopes,
        route_id=body.route_id,
        rpm_limit=body.rpm_limit,
        rpd_limit=body.rpd_limit,
        monthly_budget_cents=body.monthly_budget_cents,
        expires_at=body.expires_at,
    )
    db.add(k)
    db.flush()
    audit(db, request, key, 'key.create', 'api_key', k.id, {'name': k.name, 'scopes': k.scopes})
    db.commit()
    d = keyToDict(k)
    d['secret'] = raw  # shown exactly once
    return d


@router.patch('/keys/{kid}')
def patchKey(
    kid: str, body: ApiKeyPatch, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)
) -> dict[str, Any]:
    k = db.get(ApiKey, kid)
    if k is None:
        raise NotFound('key not found')
    changes = body.model_dump(exclude_none=True)
    for f, v in changes.items():
        setattr(k, f, v)
    audit(db, request, key, 'key.update', 'api_key', kid, changes)
    db.commit()
    return keyToDict(k)


@router.post('/keys/{kid}/rotate')
def rotateKey(kid: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)) -> dict[str, Any]:
    k = db.get(ApiKey, kid)
    if k is None:
        raise NotFound('key not found')
    raw = newApiKey()
    k.key_hash, k.key_prefix = hashApiKey(raw), raw[:10]
    audit(db, request, key, 'key.rotate', 'api_key', kid)
    db.commit()
    d = keyToDict(k)
    d['secret'] = raw
    return d


@router.delete('/keys/{kid}', status_code=204)
def revokeKey(kid: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)) -> Response:
    k = db.get(ApiKey, kid)
    if k is None:
        raise NotFound('key not found')
    db.delete(k)
    audit(db, request, key, 'key.revoke', 'api_key', kid)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- usage / stats / settings / audit
@router.get('/usage')
def usage(
    db: Session = Depends(getDb), key: ApiKey = Depends(admin), group_by: str = 'engine', since: str = ''
) -> dict[str, Any]:
    if since and since.endswith(('d', 'h')):
        n = int(since[:-1])
        delta = timedelta(days=n) if since.endswith('d') else timedelta(hours=n)
        since = (datetime.now(timezone.utc) - delta).replace(microsecond=0).isoformat()
    return {'group_by': group_by, 'items': groupedUsage(db, group_by, since)}


@router.get('/stats/summary')
def statsSummary(
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:read')),
    hours: float = Query(24, gt=0, le=24 * 365),
) -> dict[str, Any]:
    return {
        'summary': summary(db, hours),
        'daily': dailySeries(db, max(1, int(hours // 24) or 1)),
        'engines': engineStats(db, hours),
    }


@router.get('/settings')
def getSettingsApi(
    db: Session = Depends(getDb), key: ApiKey = Depends(admin), ctx: AppContext = Depends(getCtx)
) -> dict[str, Any]:
    stored = {s.key: s.value_json for s in db.query(Setting).all()}
    cfg = ctx.settings.model_dump(mode='json')
    cfg.pop('secret_key', None)
    return {'effective': cfg, 'overrides': stored, 'config': cfg, 'stored': stored}


@router.patch('/settings')
def patchSettings(
    body: SettingsPatch,
    request: Request,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(admin),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    allowed = {
        'privacy_mode',
        'store_inputs',
        'log_retention_days',
        'cache_ttl_seconds',
        'breaker_threshold',
        'breaker_cooldown_seconds',
        'url_allowlist',
        'url_denylist',
        'allow_private_urls',
        'max_upload_bytes',
        'max_pages',
        'default_timeout',
        'total_deadline_ms',
        'cors_origins',
        'theme',
        'ui_language',
    }
    for k, v in body.values.items():
        if k not in allowed:
            raise BadInput("setting '{}' is not editable at runtime".format(k))
        row = db.get(Setting, k) or Setting(key=k)
        row.value_json = v
        row.updated_at = utcnow()
        db.add(row)
        if hasattr(ctx.settings, k):
            setattr(ctx.settings, k, v)
    audit(db, request, key, 'settings.update', 'settings', '*', body.values)
    db.commit()
    return {'updated': list(body.values)}


@router.get('/audit')
def auditList(
    db: Session = Depends(getDb), key: ApiKey = Depends(admin), limit: int = Query(100, le=1000)
) -> dict[str, Any]:
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return {
        'items': [
            {
                'id': a.id,
                'actor': a.actor,
                'action': a.action,
                'target_type': a.target_type,
                'target_id': a.target_id,
                'detail': a.detail,
                'ip': a.ip,
                'created_at': a.created_at,
            }
            for a in rows
        ]
    }


@router.post('/cache/clear')
def clearCache(request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)) -> dict[str, Any]:
    from ocrroute.runtime import cache as cache_mod

    n = cache_mod.clear(db)
    audit(db, request, key, 'cache.clear', 'cache', '*', {'entries': n})
    db.commit()
    return {'cleared': n}
