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
    import json as _json

    presented = request.headers.get(sync.TOKEN_HEADER) or ''
    try:
        info = _json.loads(request.headers.get(sync.NODE_HEADER) or '{}')
    except ValueError:
        info = {}
    try:
        key = sync.authenticatePoll(db, config.sync_token, presented, info)
    except sync.SyncRefused as exc:
        db.commit()
        if exc.status == 401:
            raise Unauthorized(str(exc))
        from fastapi.responses import JSONResponse

        return JSONResponse({'status': 'failed', 'error_code': 'forbidden', 'error_message': str(exc)}, status_code=403)
    # each server's secrets travel encrypted with the token IT presented (its own card token, or the shared one)
    snap = sync.buildSnapshot(db, request.app.state.ctx.secrets, presented)
    sync.recordPoll(db, key, info, request.client.host if request.client else '?', snap['digest'])
    db.commit()
    from ocrroute.runtime.endpoints import _serverId

    members = [{k: m.get(k) for k in ('label', 'name', 'role', 'status', 'version', 'last_seen')}
               for m in sync.clusterMembers(db, request.app.state.ctx.settings)]
    leader = _json.dumps({'id': _serverId(), 'addresses': sync.leaderAddresses(request.app.state.ctx.settings, db),
                          'cluster': getattr(config, 'cluster_name', '') or 'cluster', 'members': members[:200]})
    etag = '"{}"'.format(snap['digest'])
    if request.headers.get('If-None-Match') == etag:
        return Response(status_code=304, headers={'ETag': etag, sync.LEADER_HEADER: leader})
    response.headers['ETag'] = etag
    response.headers[sync.LEADER_HEADER] = leader
    return snap


def _config(request, db=None):
    from ocrroute.runtime.endpoints import localAddresses

    m = _manager(request)
    c = m.config
    settings = request.app.state.ctx.settings
    port = settings.port
    addresses = []
    if db is not None:
        addresses = sync.publicAddresses(settings, db)
    return {
        'public_addresses': addresses,
        'has_public_address': any(a['kind'] in ('tunnel', 'public') for a in addresses),
        'followers': sync.listNodes(db) if (db is not None and c.sync_role == 'leader') else [],
        'role': c.sync_role, 'source': c.source, 'leader_url': c.sync_leader_url,
        'leader_urls': list(getattr(c, 'sync_leader_urls', None) or []),
        'interval_seconds': c.sync_interval_seconds, 'token': c.sync_token, 'token_set': bool(c.sync_token),
        'problems': sync.validate(c) if c.sync_role != 'off' else [],
        'host': settings.host, 'port': port,
        'reachable_from_network': settings.host not in ('127.0.0.1', 'localhost', '::1'),
        'addresses': ['http://{}:{}'.format(ip, port) for ip in localAddresses()],
        'state': dict(m.follower.state) if m.follower is not None else None,
    }


@router.get('/sync/status')
def status(request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    return _config(request, db)


@router.get('/sync/config')
def getConfig(request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    return _config(request, db)


class LeaderAddressIn(BaseModel):
    url: str
    label: str = ''
    learned: bool = False


class SyncConfigIn(BaseModel):
    role: str
    token: str = ''
    leader_url: str = ''                      # one address (older clients)
    leader_urls: list[LeaderAddressIn] = []   # the follower's leader addresses, primary first
    interval_seconds: int = 30


@router.put('/sync/config')
def putConfig(body: SyncConfigIn, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    """Save this server's sync role and apply it immediately (no restart)."""
    token = body.token.strip()
    if body.role == 'leader' and not token:
        token = sync.newToken()
    problems = _manager(request).configure(body.role, token, body.leader_url.strip(), body.interval_seconds,
                                           [a.model_dump() for a in body.leader_urls])
    if problems:
        raise BadInput(' '.join(problems))
    return _config(request, db)


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


# --------------------------------------------------------------------------------------------------------------- #
# server cards (leader): create / read / update / delete                                                          #
# --------------------------------------------------------------------------------------------------------------- #
class NodeIn(BaseModel):
    label: str
    notes: str = ''
    address: str = ''


class NodesIn(BaseModel):
    items: list[NodeIn]


class NodePatch(BaseModel):
    label: str | None = None
    notes: str | None = None
    address: str | None = None
    paused: bool | None = None
    regenerate_token: bool = False


def _leaderOnly(request):
    c = _manager(request).config
    if c is None or c.sync_role != 'leader':
        raise BadInput('server cards are managed on the leader: set this server\'s role to Leader first')


@router.get('/sync/nodes')
def listNodes(request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    _leaderOnly(request)
    return {'items': sync.listNodes(db)}


@router.post('/sync/nodes', status_code=201)
def createNode(body: NodeIn, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    """A new server card with its own token (returned once)."""
    _leaderOnly(request)
    if not body.label.strip():
        raise BadInput('a follower needs a name')
    node, token = sync.createNode(db, body.label, body.notes, request.app.state.ctx.secrets, body.address)
    db.commit()
    return {'node': [n for n in sync.listNodes(db) if n['key'] == node['key']][0], 'token': token}


@router.post('/sync/nodes/bulk', status_code=201)
def createNodes(body: NodesIn, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    """Add one or many followers at once: one card and one token each (tokens are returned once)."""
    _leaderOnly(request)
    items = [i for i in body.items if i.label.strip()]
    if not items:
        raise BadInput('add at least one follower with a name')
    if len(items) > 100:
        raise BadInput('at most 100 followers at once')
    created = []
    for item in items:
        node, token = sync.createNode(db, item.label, item.notes, request.app.state.ctx.secrets, item.address)
        created.append((node['key'], token))
    db.commit()
    views = {n['key']: n for n in sync.listNodes(db)}
    settings, secrets = request.app.state.ctx.settings, request.app.state.ctx.secrets
    return {'items': [{'node': views[k], 'token': tok, 'join_code': sync.joinCodeForNode(db, settings, secrets, k)}
                      for k, tok in created]}


@router.patch('/sync/nodes/{nodeKey}')
def patchNode(nodeKey: str, body: NodePatch, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    _leaderOnly(request)
    try:
        node, token = sync.updateNode(db, nodeKey, body.label, body.notes, body.paused, body.regenerate_token,
                                      request.app.state.ctx.secrets, body.address)
    except KeyError:
        raise NotFound('server card not found')
    except ValueError as exc:
        raise BadInput(str(exc))
    db.commit()
    out = {'node': [n for n in sync.listNodes(db) if n['key'] == nodeKey][0]}
    if token:
        out['token'] = token
        out['join_code'] = sync.joinCodeForNode(db, request.app.state.ctx.settings, request.app.state.ctx.secrets, nodeKey)
    return out


@router.delete('/sync/nodes/{nodeKey}', status_code=204)
def deleteNode(nodeKey: str, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    _leaderOnly(request)
    try:
        sync.deleteNode(db, nodeKey)
    except KeyError:
        raise NotFound('server card not found')
    db.commit()
    return Response(status_code=204)


class HelloIn(BaseModel):
    server_id: str = ''
    nonce: str
    token_hash: str = ''


@router.post('/sync/hello', include_in_schema=False)
def hello(body: HelloIn, request: Request, db: Session = Depends(getDb)):
    """Challenge / response: prove this leader knows the follower's token, without either side sending it."""
    from ocrroute.runtime.endpoints import _serverId

    config = _manager(request).config
    if config is None or config.sync_role != 'leader' or not (4 <= len(body.nonce) <= 200):
        raise NotFound('this server is not a sync leader')
    token = sync.tokenForServer(db, request.app.state.ctx.secrets, config.sync_token, body.server_id, body.token_hash)
    if not token:
        raise NotFound('no token for this server')
    leaderId = _serverId()
    return {'leader_id': leaderId, 'proof': sync.proofFor(token, body.nonce, leaderId)}


class PushIn(BaseModel):
    leader_id: str = ''
    addresses: list[str] = []


@router.post('/sync/leader-moved', include_in_schema=False)
def leaderMoved(body: PushIn, request: Request):
    """A leader announces its new addresses. Harmless by design: nothing is trusted before challenge / response."""
    return sync.receivePush(_manager(request), body.model_dump())


@router.post('/sync/notify')
def notify(request: Request, key=Depends(admin)):
    """Leader: push this server's current addresses to every follower now."""
    _leaderOnly(request)
    from ocrroute.db.session import sessionScope

    return {'results': sync.notifyFollowers(request.app.state.ctx.settings, sessionScope)}


# --------------------------------------------------------------------------------------------------------------- #
# cluster (the dashboard's model): standalone -> create / join; primary manages members; members can leave        #
# --------------------------------------------------------------------------------------------------------------- #
def _cluster(request, db):
    import socket

    from ocrroute.runtime.endpoints import _serverId
    from ocrroute.version import __version__

    m = _manager(request)
    c = m.config
    settings = request.app.state.ctx.settings
    mode = {'leader': 'primary', 'follower': 'member'}.get(c.sync_role, 'standalone')
    out = {'mode': mode, 'source': c.source, 'cluster_name': getattr(c, 'cluster_name', '') or '',
           'this': {'name': socket.gethostname(), 'server_id': _serverId(), 'version': __version__},
           'problems': sync.validate(c) if c.sync_role != 'off' else []}
    if mode == 'primary':
        addrs = sync.publicAddresses(settings, db)
        out.update(members=sync.clusterMembers(db, settings), addresses=addrs,
                   has_public_address=any(a['kind'] in ('tunnel', 'public') for a in addrs),
                   reachable_from_network=settings.host not in ('127.0.0.1', 'localhost', '::1'))
    elif mode == 'member':
        st = dict(m.follower.state) if m.follower is not None else {}
        out.update(members=st.get('members') or [], primary_addresses=list(getattr(c, 'sync_leader_urls', None) or []),
                   state={k: st.get(k) for k in ('via', 'last_success_at', 'last_change_at', 'last_error', 'last_result',
                                                  'address_health', 'leader_addresses', 'primary_name')})
    return out


@router.get('/cluster')
def getCluster(request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    return _cluster(request, db)


class CreateIn(BaseModel):
    name: str = ''


@router.post('/cluster/create')
def createCluster(body: CreateIn, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    """This server becomes the primary of a new cluster."""
    problems = _manager(request).createCluster(body.name)
    if problems:
        raise BadInput(' '.join(problems))
    return _cluster(request, db)


class JoinIn(BaseModel):
    code: str


@router.post('/cluster/join')
def joinCluster(body: JoinIn, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    """Join a cluster with a join code copied from the primary."""
    try:
        problems, _info = _manager(request).join(body.code)
    except ValueError as exc:
        raise BadInput(str(exc))
    if problems:
        raise BadInput(' '.join(problems))
    return _cluster(request, db)


@router.post('/cluster/leave')
def leaveCluster(request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    """Member: leave the cluster. The configuration copied so far stays on this server."""
    if _manager(request).role != 'follower':
        raise BadInput('this server is not a cluster member')
    problems = _manager(request).leave()
    if problems:
        raise BadInput(' '.join(problems))
    return _cluster(request, db)


@router.post('/cluster/delete')
def deleteCluster(request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    """Primary: dissolve the cluster; every member is refused from now on."""
    _leaderOnly(request)
    problems = _manager(request).deleteCluster()
    if problems:
        raise BadInput(' '.join(problems))
    return _cluster(request, db)


@router.get('/cluster/members/{nodeKey}/join-code')
def memberJoinCode(nodeKey: str, request: Request, db: Session = Depends(getDb), key=Depends(admin)):
    _leaderOnly(request)
    try:
        return {'join_code': sync.joinCodeForNode(db, request.app.state.ctx.settings, request.app.state.ctx.secrets, nodeKey)}
    except KeyError:
        raise NotFound('member not found')
    except ValueError as exc:
        raise BadInput(str(exc))


@router.get('/sync/identity', include_in_schema=False)
def identity(request: Request):
    """Public and harmless: lets a server that reached this address explain what it found (itself, a member...)."""
    from ocrroute.runtime.endpoints import _serverId

    role = _manager(request).role
    return {'server_id': _serverId(), 'mode': {'leader': 'primary', 'follower': 'member'}.get(role, 'standalone')}
