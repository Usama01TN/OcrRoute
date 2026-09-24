# coding=utf-8
"""Control panel: server-rendered Jinja2 + HTMX shell. Interactive parts call the /v1 API with the session."""
from __future__ import absolute_import, division, print_function

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ocrroute import i18n
from ocrroute.api.deps import getDb
from ocrroute.config import Settings
from ocrroute.crypto import loadOrCreateKey
from ocrroute.db.models import ApiKey, AuditLog, Engine, Job, Provider, Route, RouteMember, Run, User
from ocrroute.db.repo.stats import dailySeries, engineStats, summary
from ocrroute.db.repo.usage import groupedUsage
from ocrroute.panel import auth
from ocrroute.pipeline import export
from ocrroute.routing import strategies
from ocrroute.runtime.context import getContext
from ocrroute.runtime.doctor import report, toMarkdown
from ocrroute.tools import getToolRegistry
from ocrroute.version import __version__

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / 'templates'))
templates.env.globals.update(version=__version__, export_kinds=export.KINDS, strategies=strategies.DESCRIPTIONS)
templates.env.filters['tojson_pretty'] = lambda v: json.dumps(v, indent=2, ensure_ascii=False, default=str)

_sessions: auth.SessionManager | None = None

NAV_GROUPS = [
    ('gateway', 'Gateway', [
        ('endpoints', 'Endpoints', 'Your OCR connection URLs', '/panel/endpoints', 'hdd-network'),
        ('keys', 'API keys', 'Manage API keys and access', '/panel/keys', 'key'),
        ('providers', 'Providers', 'Engines with credentials', '/panel/providers', 'boxes'),
        ('engines', 'Engines', 'Detected OCR engines', '/panel/engines', 'cpu'),
    ]),
    ('routing', 'Routing', [
        ('routes', 'Routes', 'Fallback chains and strategies', '/panel/routes', 'diagram-3'),
        ('playground', 'Playground', 'Try any engine or route', '/panel/playground', 'crop'),
        ('batch', 'Batch', 'Process many files', '/panel/batch', 'collection'),
    ]),
    ('observability', 'Observability', [
        ('overview', 'Overview', 'Dashboard overview', '/panel/', 'speedometer2'),
        ('runs', 'Runs', 'Every request and attempt', '/panel/runs', 'list-task'),
        ('usage', 'Usage & cost', 'Spend and quotas', '/panel/usage', 'graph-up'),
    ]),
    ('system', 'System', [
        ('tools', 'Tools', 'Reserved for extensions', '/panel/tools', 'tools'),
        ('settings', 'Settings', 'Runtime configuration', '/panel/settings', 'sliders'),
        ('doctor', 'Doctor', 'Diagnostics and health', '/panel/doctor', 'heart-pulse'),
    ]),
]
NAV = [(key, label, href) for _g, _gl, items in NAV_GROUPS for key, label, _sub, href, _icon in items]
PAGE_INFO = {key: (label, sub, icon) for _g, _gl, items in NAV_GROUPS for key, label, sub, href, icon in items}


class _Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def currentLanguage(request):
    """
    :param request: Request
    :return: str  cookie → ?lang= → Accept-Language → configured default
    """
    lang = request.query_params.get('lang') or request.cookies.get('ocrroute_lang')
    if not lang:
        try:
            lang = i18n.pickFromHeader(request.headers.get('accept-language')) or getContext().settings.default_language
        except Exception:  # noqa: BLE001
            lang = i18n.DEFAULT
    return i18n.normalise(lang)


def langContext(request):
    """
    :param request: Request
    :return: dict  translator and layout facts for pages rendered outside ``render`` (login, setup)
    """
    lang = currentLanguage(request)
    return {'_': lambda text, **params: i18n.translate(text, lang, **params), 'lang': lang, 'rtl': i18n.isRtl(lang),
            'languages': i18n.languages()}


def render(request, name, page, **ctx):
    """
    :param request: Request
    :param name: str  template file
    :param page: str  nav key
    :param ctx: template variables
    :return: HTMLResponse
    """
    sess = auth.currentSession(request) or {}
    lang = currentLanguage(request)

    def _(text, **params):
        return i18n.translate(text, lang, **params)

    onboarding = ctx.pop('onboarding', None)
    return templates.TemplateResponse(request, name, dict(
        request=request, page=page, nav=NAV, nav_groups=NAV_GROUPS, page_info=PAGE_INFO.get(page, (page, '', 'grid')), user=sess.get('u'), role=sess.get('r'), csrf=sess.get('c', ''),
        _=_, lang=lang, rtl=i18n.isRtl(lang), languages=i18n.languages(),
        rel=lambda ts: i18n.relativeTime(ts, lang), onboarding=onboarding, **ctx))


def _require(request: Request) -> dict[str, Any]:
    sess = auth.currentSession(request)
    if sess is None:
        raise _Redirect('/panel/login?next=' + request.url.path)
    return sess


def mountPanel(app: FastAPI, settings: Settings) -> None:
    global _sessions
    key = loadOrCreateKey(settings.secretFile, settings.secret_key)
    _sessions = auth.SessionManager(settings, key.decode())
    app.mount('/panel/static', StaticFiles(directory=str(HERE / 'static')), name='panel-static')

    @app.middleware('http')
    async def _sessionMw(request: Request, call_next):
        sess = _sessions.load(request.cookies.get(settings.session_cookie)) if _sessions else None
        request.state.panel_session = sess
        request.state.panel_user = None
        request.state.panel_role = sess.get('r') if sess else None
        if sess is not None:
            # cookie-authenticated API calls must be same-origin fetches (custom header) or safe methods
            if (
                request.method in ('GET', 'HEAD', 'OPTIONS')
                or request.headers.get('x-requested-with') == 'OcrRoute'
                or request.headers.get('x-csrf-token') == sess.get('c')
            ):
                request.state.panel_user = sess.get('u')
        return await call_next(request)

    @app.exception_handler(_Redirect)
    async def _redir(request: Request, exc: _Redirect) -> RedirectResponse:
        return RedirectResponse(exc.url, status_code=303)

    app.include_router(router)


router = APIRouter(prefix='/panel', include_in_schema=False)


# ---------------------------------------------------------------- auth pages
@router.get('/login', response_class=HTMLResponse)
def loginForm(request: Request, next: str = '/panel/') -> Any:
    if auth.userCount() == 0:
        return RedirectResponse('/panel/setup')
    return templates.TemplateResponse(request, 'login.html', {**langContext(request), 'request': request, 'next': next, 'error': ''})


@router.post('/login')
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form('/panel/')) -> Any:
    ip = request.client.host if request.client else '?'
    if auth.tooManyAttempts(ip):
        return templates.TemplateResponse(
            request,
            'login.html',
            {**langContext(request), 'request': request, 'next': next, 'error': 'Too many attempts. Try again later.'},
            status_code=429,
        )
    user = auth.authenticate(username, password)
    if user is None:
        auth.recordAttempt(ip)
        return templates.TemplateResponse(
            request,
            'login.html',
            {**langContext(request), 'request': request, 'next': next, 'error': 'Invalid username or password.'},
            status_code=401,
        )
    assert _sessions is not None
    resp = RedirectResponse(next if next.startswith('/panel') else '/panel/', status_code=303)
    resp.set_cookie(
        _sessions.settings.session_cookie,
        _sessions.issue(user.username, user.role),
        httponly=True,
        samesite='strict',
        max_age=_sessions.settings.session_max_age,
        path='/',
    )
    return resp


@router.get('/setup', response_class=HTMLResponse)
def setupForm(request: Request) -> Any:
    if auth.userCount() > 0:
        return RedirectResponse('/panel/login')
    return templates.TemplateResponse(request, 'setup.html', {**langContext(request), 'request': request, 'error': ''})


@router.post('/setup')
def setup(request: Request, username: str = Form(...), password: str = Form(...), confirm: str = Form(...)) -> Any:
    if auth.userCount() > 0:
        return RedirectResponse('/panel/login', status_code=303)
    if len(password) < 8 or password != confirm:
        return templates.TemplateResponse(
            request, 'setup.html', {**langContext(request), 'request': request, 'error': 'Passwords must match and be at least 8 characters.'}
        )
    auth.createUser(username.strip(), password, 'admin')
    return RedirectResponse('/panel/login', status_code=303)


@router.post('/logout')
def logout() -> Any:
    assert _sessions is not None
    resp = RedirectResponse('/panel/login', status_code=303)
    resp.delete_cookie(_sessions.settings.session_cookie, path='/')
    return resp


# ---------------------------------------------------------------- pages
@router.get('/lang/{code}')
def setLanguage(code: str, next: str = '/panel/'):
    """
    Persist the UI language in a cookie and go back.
    """
    resp = RedirectResponse(next if next.startswith('/panel') else '/panel/', status_code=303)
    resp.set_cookie('ocrroute_lang', i18n.normalise(code), max_age=365 * 86400, samesite='lax', path='/')
    return resp


@router.get('/', response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(getDb), hours: float = 24) -> Any:
    _require(request)
    ctx = getContext()
    alerts: list[str] = []
    for e in ctx.registry.all():
        if not e.available and e.kind == 'local' and e.id in ('Tesseract', 'RapidOcr', 'PaddleOcr', 'EasyOCR'):
            alerts.append('{} unavailable - {}'.format(e.name, e.install_hint))
    provs = (
        db.execute(select(Provider).where(Provider.enabled.is_(True)).options(selectinload(Provider.engine)))
        .scalars()
        .all()
    )
    for p in provs:
        if p.health == 'down':
            alerts.append('Provider {} is down (circuit open until {})'.format(p.label, p.circuit_open_until or 'n/a'))
    if not provs:
        alerts.append('No providers configured yet - the built-in auto route uses local engines only.')
    from ocrroute.db.models import ApiKey, Credential
    onboarding = [
        ('Create your first provider', bool(provs), '/panel/providers'),
        ('Add a credential to a cloud engine', db.query(Credential).count() > 0, '/panel/providers'),
        ('Build a route with fallbacks', db.query(Route).count() > 0, '/panel/routes'),
        ('Create an API key for your app', db.query(ApiKey).filter(ApiKey.name != 'desktop-embedded-session').count() > 0, '/panel/keys'),
        ('Run your first OCR in the Playground', db.query(Run).count() > 0, '/panel/playground'),
    ]
    return render(
        request,
        'overview.html',
        'overview',
        onboarding=onboarding,
        summary=summary(db, hours),
        daily=dailySeries(db, 7),
        engines=engineStats(db, hours),
        providers=provs,
        alerts=alerts,
        hours=hours,
        recent=db.execute(select(Run).order_by(Run.created_at.desc()).limit(12)).scalars().all(),
    )


@router.get('/playground', response_class=HTMLResponse)
def playground(request: Request, db: Session = Depends(getDb)) -> Any:
    _require(request)
    ctx = getContext()
    engines = [e.toDict() for e in ctx.registry.all()]
    routes = db.execute(select(Route).where(Route.enabled.is_(True)).order_by(Route.name)).scalars().all()
    return render(request, 'playground.html', 'playground', engines=engines, routes=routes)


@router.get('/engines', response_class=HTMLResponse)
def engines(request: Request, db: Session = Depends(getDb), q: str = '', kind: str = '') -> Any:
    _require(request)
    stmt = select(Engine).order_by(Engine.available.desc(), Engine.kind, Engine.name)
    if kind:
        stmt = stmt.where(Engine.kind == kind)
    rows = db.execute(stmt).scalars().all()
    if q:
        ql = q.lower()
        rows = [e for e in rows if ql in e.id.lower() or ql in e.name.lower() or ql in e.vendor.lower()]
    stats = {s['engine']: s for s in engineStats(db, 24)}
    counts: dict[str, int] = {}
    for _pid, eid in db.execute(select(Provider.id, Provider.engine_id)).all():
        counts[eid] = counts.get(eid, 0) + 1
    return render(request, 'engines.html', 'engines', engines=rows, stats=stats, counts=counts, q=q, kind=kind)


@router.get('/providers', response_class=HTMLResponse)
def providers(request: Request, db: Session = Depends(getDb)) -> Any:
    _require(request)
    ctx = getContext()
    rows = (
        db.execute(
            select(Provider)
            .options(selectinload(Provider.credentials), selectinload(Provider.engine))
            .order_by(Provider.priority, Provider.label)
        )
        .scalars()
        .all()
    )
    engines_ = db.execute(select(Engine).order_by(Engine.kind, Engine.name)).scalars().all()
    providers_json = [
        {
            'id': p.id,
            'engine_id': p.engine_id,
            'label': p.label,
            'endpoint': p.endpoint,
            'model': p.model,
            'language': p.language,
            'timeout': p.timeout,
            'retries': p.retries,
            'priority': p.priority,
            'weight': p.weight,
            'rpm_limit': p.rpm_limit,
            'rpd_limit': p.rpd_limit,
            'concurrency_limit': p.concurrency_limit,
            'monthly_budget_cents': p.monthly_budget_cents,
            'options': p.options,
            'proxy': p.proxy,
            'notes': p.notes,
        }
        for p in rows
    ]
    return render(
        request,
        'providers.html',
        'providers',
        providers=rows,
        engines=engines_,
        providers_json=providers_json,
        engine_options={e.id: e.option_schema for e in engines_},
        secrets=ctx.secrets,
    )


@router.get('/routes', response_class=HTMLResponse)
def routes(request: Request, db: Session = Depends(getDb)) -> Any:
    _require(request)
    rows = (
        db.execute(
            select(Route).options(selectinload(Route.members).selectinload(RouteMember.provider)).order_by(Route.name)
        )
        .scalars()
        .all()
    )
    provs = db.execute(select(Provider).options(selectinload(Provider.engine)).order_by(Provider.label)).scalars().all()
    routes_json = [
        {
            'id': r.id,
            'name': r.name,
            'description': r.description,
            'strategy': r.strategy,
            'enabled': r.enabled,
            'is_default': r.is_default,
            'stop_condition': r.stop_condition,
            'max_attempts': r.max_attempts,
            'total_deadline_ms': r.total_deadline_ms,
            'cache_ttl_seconds': r.cache_ttl_seconds,
            'members': [
                {
                    'provider_id': m.provider_id,
                    'weight': m.weight,
                    'enabled': m.enabled,
                    'condition': m.condition,
                    'option_overrides': m.option_overrides,
                    'label': m.provider.label if m.provider else '?',
                    'engine': m.provider.engine_id if m.provider else '?',
                }
                for m in r.members
            ],
        }
        for r in rows
    ]
    from ocrroute.routing.templates import catalog, categoryOf

    categories = {name: categoryOf(name) for name in strategies.DESCRIPTIONS}
    return render(request, 'routes.html', 'routes', routes=rows, providers=provs, routes_json=routes_json, catalog=catalog(),
                  categories=categories)


@router.get('/runs', response_class=HTMLResponse)
def runs(
    request: Request,
    db: Session = Depends(getDb),
    status: str = '',
    engine: str = '',
    route: str = '',
    q: str = '',
    limit: int = 50,
    offset: int = 0,
) -> Any:
    _require(request)
    stmt = select(Run).order_by(Run.created_at.desc())
    if status:
        stmt = stmt.where(Run.status == status)
    if engine:
        stmt = stmt.where(Run.winning_engine == engine)
    if route:
        stmt = stmt.where(Run.route_name == route)
    if q:
        stmt = stmt.where((Run.id.like('%{}%'.format(q))) | (Run.error_message.like('%{}%'.format(q))))
    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return render(
        request,
        'runs.html',
        'runs',
        runs=rows,
        status=status,
        engine=engine,
        route=route,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get('/runs/{run_id}', response_class=HTMLResponse)
def runDetail(request: Request, run_id: str, db: Session = Depends(getDb)) -> Any:
    _require(request)
    run = db.get(Run, run_id)
    if run is None:
        return render(request, 'empty.html', 'runs', title='Run not found', message='No run has this id.')
    return render(request, 'run_detail.html', 'runs', run=run)


@router.get('/batch', response_class=HTMLResponse)
def batch(request: Request, db: Session = Depends(getDb)) -> Any:
    _require(request)
    jobs = (
        db.execute(select(Job).options(selectinload(Job.items)).order_by(Job.created_at.desc()).limit(30))
        .scalars()
        .all()
    )
    routes_ = db.execute(select(Route).where(Route.enabled.is_(True))).scalars().all()
    return render(request, 'batch.html', 'batch', jobs=jobs, routes=routes_)


@router.get('/usage', response_class=HTMLResponse)
def usage(request: Request, db: Session = Depends(getDb), group_by: str = 'engine', days: int = 30) -> Any:
    _require(request)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    engines_ = db.execute(select(Engine).where(Engine.kind == 'api').order_by(Engine.name)).scalars().all()
    return render(
        request,
        'usage.html',
        'usage',
        rows=groupedUsage(db, group_by, since),
        group_by=group_by,
        days=days,
        daily=dailySeries(db, days),
        engines=engines_,
        summary=summary(db, days * 24),
    )


@router.get('/keys', response_class=HTMLResponse)
def keys(request: Request, db: Session = Depends(getDb)) -> Any:
    _require(request)
    rows = db.execute(select(ApiKey).order_by(ApiKey.created_at.desc())).scalars().all()
    routes_ = db.execute(select(Route).order_by(Route.name)).scalars().all()
    return render(request, 'keys.html', 'keys', keys=rows, routes=routes_)


@router.get('/tools', response_class=HTMLResponse)
def tools(request: Request) -> Any:
    _require(request)
    return render(request, 'tools.html', 'tools', tools=getToolRegistry().list())


@router.get('/settings', response_class=HTMLResponse)
def settingsPage(request: Request, db: Session = Depends(getDb)) -> Any:
    _require(request)
    ctx = getContext()
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    audit = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(50)).scalars().all()
    eff = ctx.settings.model_dump()
    eff.pop('secret_key', None)
    return render(request, 'settings.html', 'settings', settings=eff, users=users, audit=audit)


@router.post('/settings/users')
def addUser(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form('operator'),
    csrf: str = Form(''),
) -> Any:
    sess = _require(request)
    if csrf != sess.get('c') or sess.get('r') != 'admin':
        return Response('forbidden', status_code=403)
    if len(password) >= 8:
        auth.createUser(username.strip(), password, role if role in ('admin', 'operator', 'viewer') else 'viewer')
    return RedirectResponse('/panel/settings', status_code=303)


@router.get('/doctor', response_class=HTMLResponse)
def doctor(request: Request, format: str = '') -> Any:
    _require(request)
    ctx = getContext()
    rep = report(ctx.settings, ctx.registry)
    if format == 'md':
        return Response(toMarkdown(rep), media_type='text/markdown')
    return render(request, 'doctor.html', 'doctor', rep=rep, md=toMarkdown(rep))


@router.get('/endpoints', response_class=HTMLResponse)
def endpointsPage(request: Request, db: Session = Depends(getDb), tab: str = 'api'):
    """
    Where the gateway is reachable: local URLs, tunnels, public URL, global OCR prompt.
    """
    _require(request)
    from ocrroute.api.routers.endpoints import readSettings
    from ocrroute.runtime.endpoints import getEndpointManager

    cfg = readSettings(db)
    ctx = getContext()
    snap = getEndpointManager(ctx.settings).snapshot(cfg['public_base_url'], cfg['custom_prompt'], cfg['custom_prompt_enabled'])
    keys = db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()).limit(5)).scalars().all()
    return render(request, 'endpoints.html', 'endpoints', ep=snap, tab=tab, keys=keys,
                  engines_available=len(ctx.registry.available()), engines_total=len(ctx.registry.all()))


@router.get('/stream')
async def stream(request: Request) -> Any:
    _require(request)
    ctx = getContext()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def cb(payload: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    ctx.executor.events.append(cb)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    p = await asyncio.wait_for(queue.get(), timeout=15)
                    yield 'event: run\ndata: {}\n\n'.format(json.dumps(p))
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
        finally:
            if cb in ctx.executor.events:
                ctx.executor.events.remove(cb)

    return StreamingResponse(gen(), media_type='text/event-stream')
