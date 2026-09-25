# coding=utf-8
"""Cluster sync endpoints: the snapshot a leader serves to followers, plus status and manual sync for admins."""
from __future__ import absolute_import, division, print_function

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ocrroute import sync
from ocrroute.api.deps import getDb
from ocrroute.api.security import requireKey
from ocrroute.errors import BadInput, NotFound, Unauthorized

router = APIRouter(tags=['sync'])
admin = requireKey('admin')


@router.get('/sync/snapshot', include_in_schema=False)
def snapshot(request: Request, response: Response, db: Session = Depends(getDb)):
    """Configuration snapshot for followers. Authenticated by the shared sync token, not by an API key."""
    settings = request.app.state.ctx.settings
    if settings.sync_role != 'leader':
        raise NotFound('this server is not a sync leader')
    if not sync.tokenMatches(settings.sync_token, request.headers.get(sync.TOKEN_HEADER)):
        raise Unauthorized('invalid sync token')
    snap = sync.buildSnapshot(db, request.app.state.ctx.secrets, settings.sync_token)
    etag = '"{}"'.format(snap['digest'])
    if request.headers.get('If-None-Match') == etag:
        return Response(status_code=304, headers={'ETag': etag})
    response.headers['ETag'] = etag
    return snap


@router.get('/sync/status')
def status(request: Request, key=Depends(admin)):
    settings = request.app.state.ctx.settings
    follower = getattr(request.app.state, 'follower', None)
    out = {'role': settings.sync_role, 'leader_url': settings.sync_leader_url if settings.sync_role == 'follower' else '',
           'interval_seconds': settings.sync_interval_seconds, 'problems': sync.validate(settings)}
    if follower is not None:
        out['state'] = dict(follower.state)
    return out


@router.post('/sync/now')
def syncNow(request: Request, key=Depends(admin)):
    follower = getattr(request.app.state, 'follower', None)
    if follower is None:
        raise BadInput('this server is not a sync follower')
    return follower.syncOnce()
