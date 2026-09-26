# coding=utf-8
"""Cluster sync endpoints: the snapshot a leader serves to followers, and configuration / status for admins."""
from __future__ import absolute_import, division, print_function

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ocrroute import sync
from ocrroute.api.deps import getDb
from ocrroute.api.security import requireKey
from ocrroute.errors import BadInput, NotFound, Unauthorized

router = APIRouter(tags=['sync'])
admin = requireKey('admin')


def _manager(request):
    return request.app.state.sync


@router.get('/sync/snapshot', include_in_schema=False)
def snapshot(request: Request, response: Response, db: Session = Depends(getDb)):
    """Configuration snapshot for followers. Authenticated by the shared sync token, not by an API key."""
    config = _manager(request).config
    if config is None or config.sync_role != 'leader':
        raise NotFound('this server is not a sync leader')
    if not sync.tokenMatches(config.sync_token, request.headers.get(sync.TOKEN_HEADER)):
        raise Unauthorized('invalid sync token')
    snap = sync.buildSnapshot(db, request.app.state.ctx.secrets, config.sync_token)
    etag = '"{}"'.format(snap['digest'])
    if request.headers.get('If-None-Match') == etag:
        return Response(status_code=304, headers={'ETag': etag})
    response.headers['ETag'] = etag
    return snap


def _config(request):
    from ocrroute.runtime.endpoints import localAddresses

    m = _manager(request)
    c = m.config
    settings = request.app.state.ctx.settings
    port = settings.port
    return {
        'role': c.sync_role, 'source': c.source, 'leader_url': c.sync_leader_url,
        'interval_seconds': c.sync_interval_seconds, 'token': c.sync_token, 'token_set': bool(c.sync_token),
        'problems': sync.validate(c) if c.sync_role != 'off' else [],
        'host': settings.host, 'port': port,
        'reachable_from_network': settings.host not in ('127.0.0.1', 'localhost', '::1'),
        'addresses': ['http://{}:{}'.format(ip, port) for ip in localAddresses()],
        'state': dict(m.follower.state) if m.follower is not None else None,
    }


@router.get('/sync/status')
def status(request: Request, key=Depends(admin)):
    return _config(request)


@router.get('/sync/config')
def getConfig(request: Request, key=Depends(admin)):
    return _config(request)


class SyncConfigIn(BaseModel):
    role: str
    token: str = ''
    leader_url: str = ''
    interval_seconds: int = 30


@router.put('/sync/config')
def putConfig(body: SyncConfigIn, request: Request, key=Depends(admin)):
    """Save this server's sync role and apply it immediately (no restart)."""
    token = body.token.strip()
    if body.role == 'leader' and not token:
        token = sync.newToken()
    problems = _manager(request).configure(body.role, token, body.leader_url.strip(), body.interval_seconds)
    if problems:
        raise BadInput(' '.join(problems))
    return _config(request)


@router.post('/sync/token')
def newToken(key=Depends(admin)):
    """A fresh random token (not saved: save it with PUT /v1/sync/config)."""
    return {'token': sync.newToken()}


class SyncTestIn(BaseModel):
    leader_url: str
    token: str


@router.post('/sync/test')
def testLeader(body: SyncTestIn, key=Depends(admin)):
    """Check that a leader is reachable and accepts the token, without changing anything."""
    return sync.testLeader(body.leader_url.strip(), body.token.strip())


@router.post('/sync/now')
def syncNow(request: Request, key=Depends(admin)):
    follower = _manager(request).follower
    if follower is None:
        raise BadInput('this server is not a sync follower')
    return follower.syncOnce()
