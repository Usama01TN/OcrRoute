# coding=utf-8
"""
FastAPI application factory: /v1 API, /panel control panel, /metrics, error mapping, security headers.

FastAPI binds request, body, form and dependency parameters from annotations, so the HTTP layer keeps them.
"""
from __future__ import absolute_import, division, print_function

import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from ocrroute.api.routers import admin, endpoints, jobs, ocr, runs, system, tools, users
from ocrroute.config import FORBIDDEN_PORTS, getSettings
from ocrroute.errors import OcrRouteError, RateLimited
from ocrroute.logsetup import getLogger, redact
from ocrroute.runtime.context import buildContext, getContext
from ocrroute.runtime.jobs import JobRunner
from ocrroute.runtime.maintenance import Scheduler
from ocrroute.sync import MANAGED_PREFIXES
from ocrroute.sync import validate as syncValidate
from ocrroute.version import __version__

log = getLogger(__name__)

REQUESTS = Counter('ocrroute_http_requests_total', 'HTTP requests', ['method', 'path', 'status'])
LATENCY = Histogram('ocrroute_http_request_seconds', 'HTTP latency', ['path'])
OCR_RUNS = Counter('ocrroute_runs_total', 'OCR runs', ['status', 'engine'])

_jobs: JobRunner | None = None
_scheduler: Scheduler | None = None


def getJobs():
    global _jobs
    if _jobs is None:
        _jobs = JobRunner(getContext().executor)
    return _jobs


def _runMetric(payload: dict[str, Any]) -> None:
    if payload.get('status') in ('succeeded', 'failed', 'cached'):
        OCR_RUNS.labels(payload['status'], payload.get('engine') or 'none').inc()


def _templatePath(request: Request) -> str:
    route = request.scope.get('route')
    return getattr(route, 'path', request.url.path) if route else request.url.path


def createApp(settings=None, include_panel=True, include_api=True, with_panel=None):
    """
    :param settings: Settings | None
    :param include_panel: bool  mount the control panel
    :param include_api: bool  mount /v1
    :param with_panel: bool | None  alias of include_panel
    :return: FastAPI
    """
    settings = settings or getSettings()
    if with_panel is not None:  # alias
        include_panel = with_panel
    if settings.port in FORBIDDEN_PORTS:
        raise SystemExit('Port {} is reserved by another gateway on this host; choose --port'.format(settings.port))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _scheduler
        ctx = buildContext(settings)
        app.state.ctx = ctx
        if _runMetric not in ctx.executor.events:
            ctx.executor.events.append(_runMetric)
        _scheduler = Scheduler(settings)
        _scheduler.start()
        app.state.follower = None
        problems = syncValidate(settings)
        if problems:
            log.error('cluster sync disabled: invalid configuration', problems=problems)
        elif settings.sync_role == 'follower':
            from ocrroute.db.session import sessionScope
            from ocrroute.sync import Follower

            app.state.follower = Follower(settings, sessionScope, ctx.secrets)
            app.state.follower.start()
            log.info('cluster sync: following', leader=settings.sync_leader_url)
        elif settings.sync_role == 'leader':
            log.info('cluster sync: serving snapshots to followers')
        log.info(
            'ocrroute started',
            version=__version__,
            port=settings.port,
            home=str(settings.home),
            engines=len(ctx.registry.available()),
        )
        yield
        if getattr(app.state, 'follower', None) is not None:
            app.state.follower.stop()
        if _scheduler:
            _scheduler.stop()

    app = FastAPI(
        title='OcrRoute',
        version=__version__,
        lifespan=lifespan,
        description='OCR gateway: one endpoint in front of local and cloud OCR engines with routing, '
        'fallbacks, quotas, caching and observability.',
        docs_url='/v1/docs' if include_api else None,
        openapi_url='/v1/openapi.json' if include_api else None,
        redoc_url=None,
    )

    @app.middleware('http')
    async def followerReadOnly(request, call_next):
        """A follower mirrors its leader: configuration edits here would be overwritten at the next sync."""
        if (settings.sync_role == 'follower' and request.method not in ('GET', 'HEAD', 'OPTIONS')
                and request.url.path.startswith(MANAGED_PREFIXES)):
            from fastapi.responses import JSONResponse

            return JSONResponse({'status': 'failed', 'error_code': 'conflict', 'error_message':
                                 'This server follows {} (cluster sync): change configuration on the leader.'.format(
                                     settings.sync_leader_url)}, status_code=409)
        return await call_next(request)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=['*'],
            allow_headers=['*'],
            allow_credentials=False,
        )

    @app.middleware('http')
    async def _observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        t0 = time.perf_counter()
        response = await call_next(request)
        path = _templatePath(request)
        REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        LATENCY.labels(path).observe(time.perf_counter() - t0)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        response.headers.setdefault('X-OcrRoute-Version', __version__)
        if request.url.path.startswith('/panel'):
            response.headers.setdefault(
                'Content-Security-Policy',
                "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-eval' 'unsafe-inline'; connect-src 'self'",
            )
        return response

    @app.exception_handler(OcrRouteError)
    async def _domainError(request: Request, exc: OcrRouteError) -> JSONResponse:
        headers = {'Retry-After': str(exc.retryAfter)} if isinstance(exc, RateLimited) else None
        return JSONResponse(
            {'status': 'failed', 'error_code': exc.code, 'error_message': redact(exc.message)},
            status_code=exc.status,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                'status': 'failed',
                'error_code': 'validation',
                'error_message': 'Invalid request',
                'detail': exc.errors(),
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception('unhandled', path=request.url.path)
        return JSONResponse(
            {
                'status': 'failed',
                'error_code': 'internal',
                'error_message': redact('{}: {}'.format(type(exc).__name__, exc))[:300],
            },
            status_code=500,
        )

    if include_api:
        from ocrroute.api.routers import sync as syncRouter

        for r in (system.router, ocr.router, runs.router, jobs.router, admin.router, tools.router, endpoints.router, users.router,
                  syncRouter.router):
            app.include_router(r, prefix='/v1')

        @app.get('/metrics', include_in_schema=False)
        def metrics() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if include_panel:
        from ocrroute.panel.app import mountPanel

        mountPanel(app, settings)

    @app.get('/favicon.ico', include_in_schema=False)
    def favicon():
        from pathlib import Path

        from fastapi.responses import FileResponse

        return FileResponse(Path(__file__).resolve().parents[1] / 'panel' / 'static' / 'favicon.ico', media_type='image/x-icon')

    @app.get('/', include_in_schema=False)
    def root() -> Any:
        if include_panel:
            return RedirectResponse('/panel/')
        return {'name': 'OcrRoute', 'version': __version__, 'docs': '/v1/docs'}

    return app
