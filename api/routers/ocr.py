# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from ocrroute.api.deps import getCtx
from ocrroute.api.schemas.ocr import OcrJsonRequest
from ocrroute.api.security import requireKey
from ocrroute.db.models import ApiKey
from ocrroute.errors import BadInput, QueueFull
from ocrroute.pipeline.input import loadInput, parsePageSpec
from ocrroute.runtime.context import AppContext
from ocrroute.runtime.executor import OcrRequest

router = APIRouter(tags=['ocr'])


def _buildRequest(
    ctx: AppContext,
    body: OcrJsonRequest,
    request: Request,
    key: ApiKey, file_bytes: bytes | None,
    filename: str,
    origin: str = 'api',
) -> OcrRequest:
    if body.path and origin == 'api' and 'admin' not in (key.scopes or []):
        raise BadInput("'path' is only accepted from the control panel / desktop (admin scope)")
    doc = loadInput(
        settings=ctx.settings,
        file_bytes=file_bytes,
        filename=filename,
        url=body.url or '',
        b64=body.base64 or '',
        path=body.path or '',
        pages=body.pages,
        pdf_dpi=body.pdf_dpi,
    )
    page_indices = parsePageSpec(body.pages, 10000)[: doc.page_count] if body.pages else []
    return OcrRequest(
        doc=doc,
        engine=body.engine or '',
        provider_id=body.provider_id or '',
        route=body.route or '',
        language=list(body.language) if isinstance(body.language, list) else [body.language],
        prompt=body.prompt,
        options=body.options,
        preprocess=body.preprocess,
        output=body.output,
        stop_condition=body.stop_condition,
        strategy=body.strategy,
        cache=body.cache,
        hints=body.hints,
        metadata=body.metadata,
        api_key=key if key.id else None,
        client_ip=request.client.host if request.client else '',
        user_agent=request.headers.get('user-agent', ''),
        idempotency_key=request.headers.get('idempotency-key', ''),
        origin=origin,
        page_indices=page_indices,
    )


def _respond(outcome: Any) -> JSONResponse:
    return JSONResponse(outcome.toDict(), status_code=outcome.http_status)


@router.post('/ocr', summary='Synchronous OCR (multipart file or JSON url/base64)')
async def ocr(
    request: Request,
    ctx: AppContext = Depends(getCtx),
    key: ApiKey = Depends(requireKey('ocr:write')),
    x_ocrroute_no_cache: str | None = Header(default=None),
) -> JSONResponse:
    ctype = request.headers.get('content-type', '')
    file_bytes: bytes | None = None
    filename = ''
    if ctype.startswith('multipart/form-data'):
        form = await request.form()
        up = form.get('file')
        if up is None or not hasattr(up, 'read'):
            raise BadInput("multipart request needs a 'file' part")
        file_bytes = await up.read()
        filename = getattr(up, 'filename', '') or ''
        payload: dict[str, Any] = {}
        raw = form.get('json')
        if raw:
            payload = json.loads(str(raw))
        for k in ('route', 'engine', 'provider_id', 'language', 'pages', 'prompt', 'strategy', 'output'):
            v = form.get(k)
            if v is not None and k not in payload:
                payload[k] = str(v).split(',') if k in ('output',) else str(v)
        for k in ('options', 'preprocess', 'stop_condition', 'hints', 'metadata'):
            v = form.get(k)
            if v:
                payload[k] = json.loads(str(v))
        body = OcrJsonRequest(**payload)
    else:
        body = OcrJsonRequest(**(await request.json()))
    if x_ocrroute_no_cache:
        body.cache = False
    req = _buildRequest(ctx, body, request, key, file_bytes=file_bytes, filename=filename)
    import anyio

    outcome = await anyio.to_thread.run_sync(ctx.executor.execute, req)
    return _respond(outcome)


@router.post('/ocr/async', summary='Enqueue one run and return immediately')
async def ocrAsync(
    request: Request,
    body: OcrJsonRequest,
    ctx: AppContext = Depends(getCtx),
    key: ApiKey = Depends(requireKey('ocr:write')),
) -> dict[str, Any]:
    from ocrroute.api.app import getJobs

    req = _buildRequest(ctx, body, request, key, file_bytes=None, filename='')
    jobs = getJobs()
    if jobs.pool._work_queue.qsize() > ctx.settings.queue_size:  # noqa: SLF001
        raise QueueFull('Queue is full; retry later')
    from ocrroute.db.models import Run
    from ocrroute.db.session import sessionScope

    with sessionScope() as s:
        run = Run(
            status='queued',
            input_kind=req.doc.kind,
            mime=req.doc.mime,
            bytes=len(req.doc.data),
            image_sha256=req.doc.sha256,
            api_key_id=key.id,
            origin='async',
            metadata_json=body.metadata,
        )
        s.add(run)
        s.flush()
        placeholder = run.id

    def _go() -> None:
        outcome = ctx.executor.execute(req)
        with sessionScope() as s:
            ph = s.get(Run, placeholder)
            if ph is not None:
                ph.status = 'superseded'
                ph.metadata_json = dict(ph.metadata_json or {}, **dict(run_id=outcome.run_id))
        if body.webhook_url:
            from ocrroute.runtime.webhooks import deliver

            deliver(body.webhook_url, {'event': 'run.finished', **outcome.toDict()})

    jobs.pool.submit(_go)
    return {'queued': True, 'placeholder_id': placeholder, 'poll': '/v1/runs/{}'.format(placeholder)}
