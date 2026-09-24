# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ocrroute.api.deps import getCtx, getDb
from ocrroute.api.schemas.admin import BatchIn
from ocrroute.api.security import requireKey
from ocrroute.db.models import ApiKey, Job
from ocrroute.errors import BadInput, NotFound
from ocrroute.runtime.context import AppContext

router = APIRouter(tags=['jobs'])


def jobToDict(job: Job) -> dict[str, Any]:
    return {
        'id': job.id,
        'name': job.name,
        'status': job.status,
        'total': job.total,
        'done': job.done,
        'failed': job.failed,
        'options': job.options,
        'webhook_url': job.webhook_url,
        'created_at': job.created_at,
        'finished_at': job.finished_at,
        'items': [
            {'id': i.id, 'source': i.source, 'status': i.status, 'run_id': i.run_id, 'error_message': i.error_message}
            for i in job.items
        ],
    }


@router.post('/batch', summary='Create a batch job from URLs/paths (JSON) or uploaded files (multipart)')
async def createBatch(
    body: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    ctx: AppContext = Depends(getCtx),
    key: ApiKey = Depends(requireKey('ocr:write')),
) -> dict[str, Any]:
    from ocrroute.api.app import getJobs

    spec = BatchIn(**(json.loads(body) if body else {}))
    uploaded = [(f.filename or 'file', await f.read()) for f in files]
    if not spec.sources and not uploaded:
        raise BadInput("Provide 'sources' or upload files")
    if (
        spec.sources
        and any(not s.startswith(('http://', 'https://')) for s in spec.sources)
        and 'admin' not in key.scopes
    ):
        raise BadInput('Local paths in batch sources require admin scope')
    job_id = getJobs().create(
        name=spec.name,
        sources=spec.sources,
        route=spec.route,
        engine=spec.engine,
        language=spec.language,
        output=spec.output,
        options=spec.options,
        webhook_url=spec.webhook_url,
        api_key_id=key.id,
        uploaded=uploaded,
    )
    return {'job_id': job_id, 'poll': '/v1/jobs/{}'.format(job_id), 'events': '/v1/jobs/{}/events'.format(job_id)}


@router.post('/batch/json', summary='Create a batch job (pure JSON body)')
def createBatchJson(
    spec: BatchIn, ctx: AppContext = Depends(getCtx), key: ApiKey = Depends(requireKey('ocr:write'))
) -> dict[str, Any]:
    from ocrroute.api.app import getJobs

    if not spec.sources:
        raise BadInput("'sources' is empty")
    if any(not s.startswith(('http://', 'https://')) for s in spec.sources) and 'admin' not in key.scopes:
        raise BadInput('Local paths in batch sources require admin scope')
    job_id = getJobs().create(
        name=spec.name,
        sources=spec.sources,
        route=spec.route,
        engine=spec.engine,
        language=spec.language,
        output=spec.output,
        options=spec.options,
        webhook_url=spec.webhook_url,
        api_key_id=key.id,
    )
    return {'job_id': job_id, 'poll': '/v1/jobs/{}'.format(job_id), 'events': '/v1/jobs/{}/events'.format(job_id)}


@router.get('/jobs')
def listJobs(
    db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read')), limit: int = 50
) -> dict[str, Any]:
    q = db.query(Job).order_by(Job.created_at.desc())
    if 'admin' not in key.scopes and key.id:
        q = q.filter(Job.api_key_id == key.id)
    return {'items': [jobToDict(j) for j in q.limit(limit).all()]}


@router.get('/jobs/{job_id}')
def getJob(job_id: str, db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read'))) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFound('job not found')
    return jobToDict(job)


@router.post('/jobs/{job_id}/cancel')
def cancel(job_id: str, db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:write'))) -> dict[str, Any]:
    from ocrroute.api.app import getJobs

    if db.get(Job, job_id) is None:
        raise NotFound('job not found')
    getJobs().cancel(job_id)
    return {'cancelling': True}


@router.get('/jobs/{job_id}/events')
async def events(job_id: str, key: ApiKey = Depends(requireKey('ocr:read'))) -> StreamingResponse:
    from ocrroute.api.app import getJobs

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def cb(payload: dict[str, Any]) -> None:
        if payload.get('job_id') == job_id:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

    jobs = getJobs()
    jobs.listeners.append(cb)

    async def gen():
        try:
            while True:
                try:
                    p = await asyncio.wait_for(queue.get(), timeout=15)
                    yield 'data: {}\n\n'.format(json.dumps(p))
                    if p.get('status') in ('succeeded', 'failed', 'cancelled') and 'item' not in p:
                        break
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
        finally:
            if cb in jobs.listeners:
                jobs.listeners.remove(cb)

    return StreamingResponse(gen(), media_type='text/event-stream')
