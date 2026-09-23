# coding=utf-8
"""
Endpoints API: where the gateway is reachable, tunnel control, public URL, global OCR prompt, restart/shutdown.
"""
from __future__ import absolute_import, division, print_function

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ocrroute.api.deps import getCtx, getDb
from ocrroute.api.security import requireKey
from ocrroute.db.base import utcnow
from ocrroute.db.models import ApiKey, AuditLog, Setting
from ocrroute.errors import BadInput, Conflict, NotFound
from ocrroute.runtime.context import AppContext
from ocrroute.runtime.endpoints import getEndpointManager

router = APIRouter(prefix='/endpoints', tags=['endpoints'])
admin = requireKey('admin')
SETTING_KEYS = ('public_base_url', 'custom_prompt', 'custom_prompt_enabled')


def readSettings(db):
    """
    :param db: Session
    :return: dict  the three endpoint-related runtime settings
    """
    rows = {s.key: s.value_json for s in db.query(Setting).filter(Setting.key.in_(SETTING_KEYS)).all()}
    return {'public_base_url': rows.get('public_base_url', ''), 'custom_prompt': rows.get('custom_prompt', ''),
            'custom_prompt_enabled': bool(rows.get('custom_prompt_enabled', False))}


def _audit(db, request, key, action, detail=None):
    db.add(AuditLog(actor=key.name, action=action, target_type='endpoints', target_id='*', detail=detail or {},
                    ip=request.client.host if request.client else ''))


@router.get('')
def endpoints(db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read')),
              ctx: AppContext = Depends(getCtx)):
    """
    Local URLs, tunnels, public URL and the global prompt.
    """
    cfg = readSettings(db)
    return getEndpointManager(ctx.settings).snapshot(cfg['public_base_url'], cfg['custom_prompt'], cfg['custom_prompt_enabled'])


class EndpointPatch(BaseModel):
    public_base_url: str | None = None
    custom_prompt: str | None = None
    custom_prompt_enabled: bool | None = None


@router.patch('')
def patchEndpoints(body: EndpointPatch, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin),
                   ctx: AppContext = Depends(getCtx)):
    """
    Update the public URL and the global OCR prompt.
    """
    changes = body.model_dump(exclude_none=True)
    if 'public_base_url' in changes and changes['public_base_url'] and not changes['public_base_url'].startswith(('http://', 'https://')):
        raise BadInput('public_base_url must start with http:// or https://')
    for k, v in changes.items():
        row = db.get(Setting, k) or Setting(key=k)
        row.value_json = v
        row.updated_at = utcnow()
        db.add(row)
    _audit(db, request, key, 'endpoints.update', changes)
    db.commit()
    cfg = readSettings(db)
    return getEndpointManager(ctx.settings).snapshot(cfg['public_base_url'], cfg['custom_prompt'], cfg['custom_prompt_enabled'])


@router.post('/tunnels/{name}/enable')
def enableTunnel(name: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin),
                 ctx: AppContext = Depends(getCtx)):
    """
    Start a tunnel publishing the gateway port; returns its status (URL when captured).
    """
    mgr = getEndpointManager(ctx.settings)
    try:
        tunnel = mgr.getTunnel(name)
    except KeyError:
        raise NotFound("unknown tunnel '{}'".format(name))
    try:
        tunnel.start(ctx.settings.port)
    except RuntimeError as exc:
        _audit(db, request, key, 'tunnel.enable_failed', {'tunnel': name, 'error': str(exc)})
        db.commit()
        raise BadInput(str(exc))
    _audit(db, request, key, 'tunnel.enable', {'tunnel': name, 'url': tunnel.getUrl()})
    db.commit()
    return tunnel.status()


@router.post('/tunnels/{name}/disable')
def disableTunnel(name: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin),
                  ctx: AppContext = Depends(getCtx)):
    mgr = getEndpointManager(ctx.settings)
    try:
        tunnel = mgr.getTunnel(name)
    except KeyError:
        raise NotFound("unknown tunnel '{}'".format(name))
    tunnel.stop()
    _audit(db, request, key, 'tunnel.disable', {'tunnel': name})
    db.commit()
    return tunnel.status()


class TunnelAuth(BaseModel):
    token: str = ''


@router.post('/tunnels/{name}/install')
def installTunnel(name: str, request: Request, enable: bool = True, db: Session = Depends(getDb),
                  key: ApiKey = Depends(admin), ctx: AppContext = Depends(getCtx)):
    """
    One click: download/install the tunnel binary into ~/.ocrroute/bin (no admin rights) and start it.
    Providers that need authentication first report ``needs_auth``.
    """
    mgr = getEndpointManager(ctx.settings)
    try:
        mgr.getTunnel(name)
    except KeyError:
        raise NotFound("unknown tunnel '{}'".format(name))
    result = mgr.install(name, enable=enable, port=ctx.settings.port)
    _audit(db, request, key, 'tunnel.install', {'tunnel': name, 'ok': result['ok'], 'installed': result['installed']})
    db.commit()
    return result


@router.post('/tunnels/{name}/auth')
def authTunnel(name: str, body: TunnelAuth, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin),
               ctx: AppContext = Depends(getCtx)):
    """
    Authenticate a tunnel provider (ngrok authtoken, Tailscale login / auth key) and start it when possible.
    """
    mgr = getEndpointManager(ctx.settings)
    try:
        tunnel = mgr.getTunnel(name)
    except KeyError:
        raise NotFound("unknown tunnel '{}'".format(name))
    if not tunnel.installed():
        raise BadInput('{} is not installed'.format(tunnel.title))
    result = tunnel.authenticate(body.token)
    result['url'] = ''
    if result.get('ok') and tunnel.authenticated():
        try:
            result['url'] = tunnel.start(ctx.settings.port)
        except RuntimeError as exc:
            result['output'] = (result.get('output') or '') + '\n' + str(exc)
    result['status'] = tunnel.status()
    _audit(db, request, key, 'tunnel.auth', {'tunnel': name, 'ok': result.get('ok')})
    db.commit()
    return result


@router.post('/server/restart')
def restartServer(request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)):
    """
    Restart the gateway: ``ocrroute serve`` starts again in place, the desktop's embedded server re-creates itself,
    multi-worker deployments re-exec the process. Tunnels are stopped and restarted by the new instance's state.
    """
    from ocrroute.runtime import lifecycle

    if lifecycle.busy():
        raise Conflict('The server is already restarting or shutting down; retry in a few seconds')
    _audit(db, request, key, 'server.restart', {'mode': lifecycle.getMode()})
    db.commit()
    mechanism = lifecycle.requestRestart()
    return {'restarting': True, 'mode': lifecycle.getMode(), 'mechanism': mechanism}


@router.post('/server/shutdown')
def shutdownServer(request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin),
                   ctx: AppContext = Depends(getCtx)):
    """
    Stop tunnels and shut the server down gracefully (uvicorn drains in-flight requests first).
    """
    from ocrroute.runtime import lifecycle

    if lifecycle.busy():
        raise Conflict('The server is already restarting or shutting down; retry in a few seconds')
    _audit(db, request, key, 'server.shutdown', {'mode': lifecycle.getMode()})
    db.commit()
    mechanism = lifecycle.requestShutdown()
    return {'shutting_down': True, 'mode': lifecycle.getMode(), 'mechanism': mechanism}
