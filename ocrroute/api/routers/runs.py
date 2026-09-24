# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ocrroute.api.deps import getCtx, getDb
from ocrroute.api.security import requireKey
from ocrroute.db.models import ApiKey, Artifact, Run
from ocrroute.errors import BadInput, NotFound
from ocrroute.pipeline import export
from ocrroute.runtime.context import AppContext

router = APIRouter(prefix='/runs', tags=['runs'])


def runToDict(run: Run, full: bool = True) -> dict[str, Any]:
    d = {
        'id': run.id,
        'status': run.status,
        'created_at': run.created_at,
        'finished_at': run.finished_at,
        'route': run.route_name,
        'strategy': run.strategy,
        'requested_engine': run.requested_engine,
        'winning_engine': run.winning_engine,
        'winning_provider': run.winning_provider,
        'input_kind': run.input_kind,
        'mime': run.mime,
        'bytes': run.bytes,
        'page_count': run.page_count,
        'language': run.language,
        'attempt_count': run.attempt_count,
        'chars': run.chars,
        'lines': run.lines,
        'words': run.words,
        'duration_ms': run.duration_ms,
        'cost_cents': run.cost_cents,
        'cache_hit': run.cache_hit,
        'degraded': run.degraded,
        'error_code': run.error_code,
        'error_message': run.error_message,
        'origin': run.origin,
        'metadata': run.metadata_json or {},
        'api_key_id': run.api_key_id,
    }
    if full:
        d['result'] = run.result_json
        d['routing'] = run.routing_json
        d['attempts'] = [
            {
                'id': a.id,
                'order': a.order_index,
                'engine': a.engine_id,
                'provider': a.provider_label,
                'status': a.status,
                'duration_ms': a.duration_ms,
                'error_code': a.error_code,
                'error_message': a.error_message,
                'cost_cents': a.cost_cents,
                'started_at': a.started_at,
            }
            for a in run.attempts
        ]
        d['artifacts'] = [
            {'kind': a.kind, 'bytes': a.bytes, 'url': '/v1/runs/{}/artifacts/{}'.format(run.id, a.kind)}
            for a in run.artifacts
        ]
    return d


@router.get('', summary='List runs')
def listRuns(
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:read')),
    status: str = '',
    engine: str = '',
    route: str = '',
    q: str = '',
    error_code: str = '',
    since: str = '',
    until: str = '',
    limit: int = Query(50, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(Run).order_by(Run.created_at.desc())
    if 'admin' not in key.scopes and key.id:
        stmt = stmt.where(Run.api_key_id == key.id)
    if status:
        stmt = stmt.where(Run.status == status)
    if engine:
        stmt = stmt.where(Run.winning_engine == engine)
    if route:
        stmt = stmt.where(Run.route_name == route)
    if error_code:
        stmt = stmt.where(Run.error_code == error_code)
    if since:
        stmt = stmt.where(Run.created_at >= since)
    if until:
        stmt = stmt.where(Run.created_at <= until)
    if q:
        stmt = stmt.where(
            or_(
                Run.id.like('%{}%'.format(q)),
                Run.winning_engine.like('%{}%'.format(q)),
                Run.route_name.like('%{}%'.format(q)),
            )
        )
    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return {'items': [runToDict(r, full=False) for r in rows], 'limit': limit, 'offset': offset}


def _get(db: Session, run_id: str, key: ApiKey) -> Run:
    run = db.get(Run, run_id)
    if run is None or ('admin' not in key.scopes and key.id and run.api_key_id != key.id):
        raise NotFound("Run '{}' not found".format(run_id))
    return run


@router.get('/{run_id}')
def getRun(run_id: str, db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read'))) -> dict[str, Any]:
    return runToDict(_get(db, run_id, key))


@router.get('/{run_id}/artifacts/{kind}')
def artifact(
    run_id: str,
    kind: str,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:read')),
    ctx: AppContext = Depends(getCtx),
) -> Response:
    run = _get(db, run_id, key)
    for a in run.artifacts:
        if a.kind == kind:
            p = Path(a.path).resolve()
            if ctx.settings.artifactRoot.resolve() not in p.parents:
                raise NotFound('artifact path outside store')
            return FileResponse(
                p,
                media_type=export.WRITERS[kind][1] if kind in export.WRITERS else 'image/png',
                filename='{}-{}{}'.format(run.id, kind, export.WRITERS[kind][2] if kind in export.WRITERS else '.png'),
            )
    if run.result_json and kind in export.WRITERS:  # render on demand
        data, mime, ext = export.write(
            kind, run.result_json, {'run_id': run.id, 'width': 0, 'height': 0, 'engine': run.winning_engine}
        )
        return Response(
            data,
            media_type=mime,
            headers={'Content-Disposition': 'attachment; filename="{}-{}{}"'.format(run.id, kind, ext)},
        )
    raise NotFound("No '{}' artifact for run {}".format(kind, run_id))


@router.get('/{run_id}/overlay.png')
def overlay(run_id: str, db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read'))) -> Response:
    run = _get(db, run_id, key)
    for a in run.artifacts:
        if a.kind == 'overlay_png':
            return FileResponse(a.path, media_type='image/png')
    raise NotFound("No overlay was stored for this run (request output=['overlay_png'])")


@router.post('/{run_id}/retry')
async def retry(
    run_id: str,
    request: Request,
    db: Session = Depends(getDb),
    key: ApiKey = Depends(requireKey('ocr:write')),
    ctx: AppContext = Depends(getCtx),
) -> dict[str, Any]:
    run = _get(db, run_id, key)
    body = await request.json() if request.headers.get('content-length', '0') not in ('0', '') else {}
    from ocrroute.pipeline.input import loadInput
    from ocrroute.runtime.executor import OcrRequest

    src = ctx.settings.home / 'uploads'
    cands = list(src.glob('{}.*'.format(run.id)))
    if not cands:
        raise BadInput('Original input was not stored (enable store_inputs) - resubmit the file instead')
    doc = loadInput(settings=ctx.settings, path=str(cands[0]))
    req = OcrRequest(
        doc=doc,
        engine=body.get('engine', ''),
        route=body.get('route', run.route_name if not run.requested_engine else ''),
        language=run.language.split(',') if run.language else ['en'],
        cache=False,
        api_key=key if key.id else None,
        metadata={'retry_of': run.id},
    )
    import anyio

    outcome = await anyio.to_thread.run_sync(ctx.executor.execute, req)
    return outcome.toDict()


@router.delete('/{run_id}', status_code=204)
def purge(run_id: str, db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:write'))) -> Response:
    run = _get(db, run_id, key)
    for a in db.query(Artifact).filter(Artifact.run_id == run.id).all():
        Path(a.path).unlink(missing_ok=True)
    db.delete(run)
    db.commit()
    return Response(status_code=204)
