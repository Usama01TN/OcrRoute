# coding=utf-8
"""
`ocrroute` command-line cockpit (Typer + Rich).

Typer derives option types from annotations, so this module keeps them (as do the Pydantic settings and schemas).
"""
from __future__ import absolute_import, division, print_function

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from ocrroute.config import FORBIDDEN_PORTS, getSettings, resetSettings
from ocrroute.version import __version__

app = typer.Typer(
    help='OcrRoute - OCR gateway: one endpoint, many engines, routing and fallbacks.',
    no_args_is_help=True,
    rich_markup_mode='rich',
)
engines_app = typer.Typer(help='Engine catalogue')
provider_app = typer.Typer(help='Providers (engine + credentials + connection settings)')
cred_app = typer.Typer(help='Credentials')
route_app = typer.Typer(help='Routes (fallback chains)')
key_app = typer.Typer(help='Client API keys')
runs_app = typer.Typer(help='Runs')
db_app = typer.Typer(help='Database maintenance')
config_app = typer.Typer(help='Configuration')
for name, sub in (
    ('engines', engines_app),
    ('provider', provider_app),
    ('cred', cred_app),
    ('route', route_app),
    ('key', key_app),
    ('runs', runs_app),
    ('db', db_app),
    ('config', config_app),
):
    app.add_typer(sub, name=name)

console = Console(no_color=bool(os.environ.get('NO_COLOR')))
err = Console(stderr=True)
JSON_OPT = typer.Option(False, '--json', help='Machine-readable output')


def _ctx():
    from ocrroute.runtime.context import getContext

    return getContext()


def _closedPipe(exc):
    """
    :param exc: BaseException
    :return: bool  True when the reader of our stdout went away (``| head``, a closed pager). POSIX reports EPIPE
             (BrokenPipeError); Windows reports EINVAL (errno 22) for the same condition.
    """
    import errno

    return isinstance(exc, BrokenPipeError) or (isinstance(exc, OSError) and exc.errno in (errno.EPIPE, errno.EINVAL))


def _quietExit():
    """
    Point stdout at devnull so the interpreter's final flush cannot raise again, then exit successfully:
    a consumer that stops reading early is not an error of this program.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass
    raise SystemExit(0)


def emitJson(data):
    """
    Write machine-readable JSON to stdout: plain text (no Rich markup, colour or console emulation), UTF-8
    regardless of the Windows code page, newline-terminated, and tolerant of a reader that closes early.

    :param data: any JSON-serialisable value
    """
    text = json.dumps(data, default=str, indent=2, ensure_ascii=False) + '\n'
    stream = getattr(sys.stdout, 'buffer', None)
    try:
        if stream is not None:
            stream.write(text.encode('utf-8'))
            stream.flush()
        else:
            sys.stdout.write(text)
            sys.stdout.flush()
    except OSError as exc:
        if _closedPipe(exc):
            _quietExit()
        raise


def _out(data: Any, as_json: bool, render=None) -> None:
    if as_json or render is None:
        emitJson(data)
    else:
        try:
            render(data)
        except OSError as exc:
            if _closedPipe(exc):
                _quietExit()
            raise


def _table(columns: list[str], rows: list[list[Any]], title: str = '') -> None:
    t = Table(title=title or None, show_lines=False, header_style='bold')
    for c in columns:
        t.add_column(c)
    for r in rows:
        t.add_row(*[str(x) for x in r])
    console.print(t)


# ---------------------------------------------------------------- top level
@app.callback()
def _root(
    home: Optional[Path] = typer.Option(None, '--home', envvar='OCRROUTE_HOME', help='Data directory'),
    quiet: bool = typer.Option(False, '--quiet', '-q'),
    verbose: bool = typer.Option(False, '--verbose', '-v'),
) -> None:
    if home:
        os.environ['OCRROUTE_HOME'] = str(home)
        resetSettings()
    os.environ.setdefault('OCRROUTE_LOG_LEVEL', 'DEBUG' if verbose else ('ERROR' if quiet else 'WARNING'))


@app.command()
def version(as_json: bool = JSON_OPT) -> None:
    """Print the version."""
    _out(
        {'name': 'OcrRoute', 'version': __version__},
        as_json,
        lambda d: console.print('OcrRoute {}'.format(d['version'])),
    )


@app.command()
def serve(
    host: str = typer.Option(None, envvar='OCRROUTE_HOST'),
    port: int = typer.Option(None, envvar='OCRROUTE_PORT'),
    panel_port: Optional[int] = typer.Option(None, help='Serve the control panel on a separate port (split mode)'),
    reload: bool = typer.Option(False),
    workers: int = typer.Option(1),
) -> None:
    """Start the API and the control panel."""
    import uvicorn

    from ocrroute.runtime.doctor import portFree

    s = getSettings()
    host = host or s.host
    port = port or s.port
    if port in FORBIDDEN_PORTS or (panel_port in FORBIDDEN_PORTS if panel_port else False):
        err.print('[red]Port {} is reserved by another gateway on this host. Choose another --port.[/red]'.format(port))
        raise typer.Exit(2)
    if not portFree(host, port):
        err.print('[red]Port {} is already in use. Pass --port to choose another one.[/red]'.format(port))
        raise typer.Exit(2)
    os.environ['OCRROUTE_PORT'] = str(port)
    resetSettings()
    console.print(
        '[bold]OcrRoute {}[/bold] · API http://{}:{}/v1/docs · panel http://{}:{}/panel/'.format(
            __version__, host, port, host, panel_port or port
        )
    )
    if panel_port:
        import threading

        def _panel() -> None:
            uvicorn.run('ocrroute.api.app:createApp', factory=True, host=host, port=panel_port, log_level='warning')

        os.environ['OCRROUTE_PANEL_PORT'] = str(panel_port)
        threading.Thread(target=_panel, daemon=True).start()
        from ocrroute.api.app import createApp

        uvicorn.run(createApp(include_panel=False), host=host, port=port, log_level='warning')
        return
    from ocrroute.runtime import lifecycle

    if reload:
        uvicorn.run('ocrroute.api.app:createApp', factory=True, host=host, port=port, reload=True, log_level='info')
        return
    if workers and workers > 1:  # multi-process: Restart re-execs the whole process
        lifecycle.register(None, 'serve')
        uvicorn.run('ocrroute.api.app:createApp', factory=True, host=host, port=port, workers=workers, log_level='warning')
        return
    while True:  # single process: Restart stops uvicorn gracefully and starts a fresh app in place
        from ocrroute.api.app import createApp

        server = uvicorn.Server(uvicorn.Config(createApp(), host=host, port=port, log_level='warning'))
        lifecycle.register(server, 'serve')
        server.run()
        if not lifecycle.restartRequested():
            break
        console.print('[bold]Restarting OcrRoute…[/bold]')
        lifecycle.reset()
        from ocrroute.runtime.context import resetContext

        resetContext()
        resetSettings()


@app.command()
def setup() -> None:
    """Guided first run: admin user, first provider, client key, default route."""
    from ocrroute.panel import auth

    ctx = _ctx()
    console.print('[bold]OcrRoute setup[/bold]')
    console.print('Data directory: {}'.format(ctx.settings.home))
    if auth.userCount() == 0:
        u = typer.prompt('Panel admin username', default='admin')
        p = typer.prompt('Password (min 8)', hide_input=True, confirmation_prompt=True)
        while len(p) < 8:
            p = typer.prompt('Too short. Password (min 8)', hide_input=True, confirmation_prompt=True)
        auth.createUser(u, p, 'admin')
        console.print('[green]✓[/green] admin user created')
    else:
        console.print('• panel user already exists')
    from ocrroute.db.models import ApiKey, Provider, Route, RouteMember
    from ocrroute.db.session import sessionScope

    local = [e for e in ctx.registry.available() if e.kind == 'local' and not e.requires_key]
    with sessionScope() as s:
        if s.query(Provider).count() == 0 and local:
            names = [e.id for e in local]
            default = 'Tesseract' if 'Tesseract' in names else names[0]
            eid = typer.prompt('First provider engine ({}…)'.format(', '.join(names[:6])), default=default)
            if eid in names:
                prov = Provider(engine_id=eid, label='{}-local'.format(eid.lower()))
                s.add(prov)
                s.flush()
                if s.query(Route).count() == 0:
                    r = Route(name='default', strategy='priority', is_default=True, description='Created by setup')
                    s.add(r)
                    s.flush()
                    s.add(RouteMember(route_id=r.id, provider_id=prov.id, order_index=0))
                console.print("[green]✓[/green] provider '{}' and route 'default' created".format(prov.label))
        if s.query(ApiKey).count() == 0 and typer.confirm('Create a client API key now?', default=True):
            from ocrroute.crypto import hashApiKey, newApiKey

            raw = newApiKey()
            s.add(
                ApiKey(name='default', key_hash=hashApiKey(raw), key_prefix=raw[:10], scopes=['ocr:read', 'ocr:write'])
            )
            console.print('[green]✓[/green] API key (shown once): [bold]{}[/bold]'.format(raw))
    console.print(
        '\nRun [bold]ocrroute serve[/bold] and open http://{}:{}/panel/'.format(ctx.settings.host, ctx.settings.port)
    )


@app.command()
def doctor(as_json: bool = JSON_OPT, markdown: bool = typer.Option(False, '--md')) -> None:
    """Diagnose engines, database, ports and environment."""
    from ocrroute.runtime.doctor import report, toMarkdown

    ctx = _ctx()
    rep = report(ctx.settings, ctx.registry)
    if markdown:
        console.print(toMarkdown(rep), markup=False)
        return

    def render(d: dict[str, Any]) -> None:
        console.print('[bold]OcrRoute {}[/bold] · Python {} · {}'.format(d['ocrroute'], d['python'], d['platform']))
        console.print(
            'home {} · db {} B integrity={} journal={}'.format(
                d['home'], d['database']['size_bytes'], d['database']['integrity'], d['database']['journal_mode']
            )
        )
        console.print(
            'port {} free={} conflicts={} · tesseract={} · {}'.format(
                d['ports']['api'],
                d['ports']['api_free'],
                d['ports']['conflicts'],
                d['tesseract_binary'] or 'missing',
                d['gpu'],
            )
        )
        _table(
            ['engine', 'kind', 'status', 'hint'],
            [
                [e['id'], e['kind'], 'ok' if e['available'] else 'missing', '' if e['available'] else e['install_hint']]
                for e in d['engines']
            ],
            'engines {}/{} available'.format(d['engines_available'], d['engines_total']),
        )

    _out(rep, as_json, render)


@app.command()
def ocr(
    source: str = typer.Argument(..., help='File path or URL'),
    route: str = typer.Option('', '--route', '-r'),
    engine: str = typer.Option('', '--engine', '-e'),
    lang: str = typer.Option('en', '--lang', '-l'),
    pages: str = typer.Option('', help='PDF pages, e.g. 1-3,7'),
    out: str = typer.Option('text', '--out', '-o', help='text|json|hocr|alto|md|csv|xlsx|docx|pdf|overlay_png'),
    output_dir: Optional[Path] = typer.Option(None),
    no_cache: bool = typer.Option(False),
    prompt: str = typer.Option(''),
) -> None:
    """Run OCR on one file or URL and print/save the result."""
    from ocrroute.pipeline import export
    from ocrroute.pipeline.input import loadInput
    from ocrroute.runtime.executor import OcrRequest

    ctx = _ctx()
    kinds = [k.strip() for k in out.split(',') if k.strip()]
    doc = loadInput(
        settings=ctx.settings,
        url=source if source.startswith(('http://', 'https://')) else '',
        path='' if source.startswith(('http://', 'https://')) else source,
        pages=pages,
    )
    req = OcrRequest(
        doc=doc,
        route=route,
        engine=engine,
        language=[x.strip() for x in lang.split(',')],
        cache=not no_cache,
        output=kinds,
        prompt=prompt,
        origin='cli',
    )
    res = ctx.executor.execute(req)
    if res.status not in ('succeeded', 'cached'):
        err.print('[red]{}[/red]: {}'.format(res.error_code, res.error_message))
        for a in res.routing.get('attempts', []):
            err.print(
                '  · {}: {} {} {}'.format(
                    a['engine'], a['status'], a.get('error_code', ''), a.get('error_message', '')[:100]
                )
            )
        raise typer.Exit(1)
    err.print(
        '[dim]{} via {} · {} ms{}[/dim]'.format(
            res.routing.get('winning_engine'),
            res.routing.get('route'),
            res.usage['duration_ms'],
            ' · cached' if res.cached else '',
        )
    )
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(doc.filename or 'output').stem or 'output'
        meta = {
            'run_id': res.run_id,
            'width': doc.width,
            'height': doc.height,
            'image_bytes': (doc.pages or [doc.data])[0],
            'page_images': doc.pages or [doc.data],
            'engine': res.routing.get('winning_engine', ''),
        }
        for k in kinds:
            data, _mime, ext = export.write(k, res.result, meta)
            (output_dir / '{}{}'.format(stem, ext)).write_bytes(data)
            err.print('[green]✓[/green] {}'.format(output_dir / '{}{}'.format(stem, ext)))
    elif kinds == ['json']:
        emitJson(res.toDict())
    else:
        console.print(res.result.get('ParsedText', '').replace('\r\n', '\n'), markup=False, highlight=False)


@app.command()
def batch(
    target: str = typer.Argument(..., help='Directory or glob'),
    route: str = typer.Option('', '-r'),
    engine: str = typer.Option('', '-e'),
    lang: str = typer.Option('en', '-l'),
    out: str = typer.Option('text,json'),
    output_dir: Path = typer.Option(Path('ocrroute-out')),
    recursive: bool = typer.Option(False, '--recursive', '-R'),
    concurrency: int = typer.Option(2),
) -> None:
    """OCR every image/PDF in a directory or glob."""
    import concurrent.futures as cf
    import glob as globmod

    from rich.progress import Progress

    from ocrroute.pipeline import export
    from ocrroute.pipeline.input import loadInput
    from ocrroute.runtime.executor import OcrRequest

    ctx = _ctx()
    p = Path(target)
    if p.is_dir():
        pattern = '**/*' if recursive else '*'
        files = [
            f
            for f in p.glob(pattern)
            if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.pdf', '.gif')
        ]
    else:
        files = [Path(f) for f in globmod.glob(target, recursive=recursive)]
    if not files:
        err.print('[yellow]No matching files.[/yellow]')
        raise typer.Exit(1)
    kinds = [k.strip() for k in out.split(',') if k.strip()]
    output_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0

    def work(f: Path) -> tuple[Path, bool, str]:
        try:
            doc = loadInput(settings=ctx.settings, path=str(f))
            res = ctx.executor.execute(
                OcrRequest(doc=doc, route=route, engine=engine, language=lang.split(','), output=[], origin='cli')
            )
            if res.status not in ('succeeded', 'cached'):
                return f, False, '{}: {}'.format(res.error_code, res.error_message)
            meta = {
                'run_id': res.run_id,
                'width': doc.width,
                'height': doc.height,
                'image_bytes': (doc.pages or [doc.data])[0],
                'page_images': doc.pages or [doc.data],
            }
            for k in kinds:
                data, _m, ext = export.write(k, res.result, meta)
                (output_dir / '{}{}'.format(f.stem, ext)).write_bytes(data)
            return f, True, ''
        except Exception as exc:  # noqa: BLE001
            return f, False, str(exc)

    with Progress(console=err) as prog, cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        task = prog.add_task('OCR', total=len(files))
        for f, good, msg in pool.map(work, files):
            ok += good
            fail += not good
            if not good:
                err.print('[red]✗[/red] {}: {}'.format(f, msg[:120]))
            prog.advance(task)
    console.print('{} succeeded, {} failed → {}'.format(ok, fail, output_dir))
    raise typer.Exit(0 if fail == 0 else 1)


@app.command()
def desktop() -> None:
    """Launch the PyQt5 desktop application."""
    try:
        from ocrroute.desktop.app import main
    except ImportError as exc:
        err.print('[red]PyQt5 is not installed:[/red] {}\nInstall with: pip install ocrroute[desktop]'.format(exc))
        raise typer.Exit(2)
    raise SystemExit(main())


# ---------------------------------------------------------------- engines
@engines_app.command('install')
def enginesInstall(engine: str = typer.Argument(..., help='Engine id, e.g. EasyOCR, SuryaOcr, MistralOcr'),
                   as_json: bool = JSON_OPT) -> None:
    """Install an engine's dependencies with pip and make it available (source installs only)."""
    from ocrroute.runtime.engineinstall import install, planFor

    ctx = _ctx()
    info = ctx.registry.get(engine)
    if info is None:
        err.print('[red]Unknown engine {}[/red]'.format(engine))
        raise typer.Exit(2)
    if info.available:
        _out({'engine': engine, 'available': True, 'output': 'already available'}, as_json, lambda d: console.print('{} is already available'.format(engine)))
        return
    plan = planFor(info.module)
    if plan is None:
        err.print('[yellow]No install recipe.[/yellow] {}'.format(info.install_hint))
        raise typer.Exit(2)
    if plan['frozen']:
        _out({'engine': engine, 'available': False, 'installable': False, 'output': plan['hint']}, as_json,
             lambda d: err.print('[yellow]{}[/yellow]'.format(d['output'])))
        raise typer.Exit(2)
    if not as_json:
        console.print('Installing {} for {} ({})…'.format(', '.join(plan['packages']), engine, plan['size'] or 'small'))
    result = install(info.module)
    ctx.registry.discover(force=True)
    fresh = ctx.registry.get(engine)
    result['available'] = bool(fresh and fresh.available)
    _out(result, as_json, lambda d: console.print(('[green]{} is now available.[/green]' if d['available'] else '[red]Install finished but {} is still unavailable.[/red]').format(engine)))
    if not result['ok'] or not result['available']:
        if not as_json:
            err.print(result['output'][-1500:])
        raise typer.Exit(1)


@engines_app.command('list')
def enginesList(
    kind: str = typer.Option('', help='api|local'), available: bool = typer.Option(False), as_json: bool = JSON_OPT
) -> None:
    ctx = _ctx()
    rows = [e.toDict() for e in ctx.registry.all() if (not kind or e.kind == kind) and (not available or e.available)]
    _out(
        rows,
        as_json,
        lambda d: _table(
            ['engine', 'kind', 'available', 'options', 'hint'],
            [
                [
                    e['id'],
                    e['kind'],
                    '✓' if e['available'] else '✗',
                    len(e['options']),
                    '' if e['available'] else e['install_hint'],
                ]
                for e in d
            ],
        ),
    )


@engines_app.command('refresh')
def enginesRefresh() -> None:
    from ocrroute.db.repo.engines import syncEngines
    from ocrroute.db.session import sessionScope

    ctx = _ctx()
    ctx.registry.discover(force=True)
    with sessionScope() as s:
        n = syncEngines(s, ctx.registry)
    console.print('synced {} engines, {} available'.format(n, len(ctx.registry.available())))


@engines_app.command('options')
def enginesOptions(engine: str, as_json: bool = JSON_OPT) -> None:
    info = _ctx().registry.get(engine)
    if info is None:
        err.print('[red]unknown engine {}[/red]'.format(engine))
        raise typer.Exit(1)
    rows = [o.toDict() for o in info.options]
    _out(
        rows,
        as_json,
        lambda d: _table(
            ['option', 'type', 'default', 'description'],
            [[o['name'], o['type'], o['default'], o['description']] for o in d],
            '{} options'.format(engine),
        ),
    )


# ---------------------------------------------------------------- providers / credentials / routes / keys
def _db():
    from ocrroute.db.session import sessionScope

    _ctx()
    return sessionScope()


@provider_app.command('list')
def providerList(as_json: bool = JSON_OPT) -> None:
    from ocrroute.db.models import Provider

    with _db() as s:
        rows = [
            {
                'id': p.id,
                'label': p.label,
                'engine': p.engine_id,
                'enabled': p.enabled,
                'health': p.health,
                'credentials': len(p.credentials),
                'endpoint': p.endpoint,
                'model': p.model,
            }
            for p in s.query(Provider).all()
        ]
    _out(
        rows,
        as_json,
        lambda d: _table(
            ['id', 'label', 'engine', 'enabled', 'health', 'creds', 'endpoint'],
            [[r['id'], r['label'], r['engine'], r['enabled'], r['health'], r['credentials'], r['endpoint']] for r in d],
        ),
    )


@provider_app.command('add')
def providerAdd(
    engine: str,
    label: str,
    endpoint: str = '',
    model: str = '',
    language: str = '',
    timeout: int = 60,
    priority: int = 100,
    options: str = typer.Option('{}', help='JSON'),
) -> None:
    from ocrroute.db.models import Engine, Provider

    with _db() as s:
        if s.get(Engine, engine) is None:
            err.print('[red]unknown engine {}[/red] (run `ocrroute engines list`)'.format(engine))
            raise typer.Exit(1)
        p = Provider(
            engine_id=engine,
            label=label,
            endpoint=endpoint,
            model=model,
            language=language,
            timeout=timeout,
            priority=priority,
            options=json.loads(options),
        )
        s.add(p)
        s.flush()
        console.print('[green]✓[/green] provider {} ({})'.format(p.id, label))


@provider_app.command('rm')
def providerRm(provider_id: str) -> None:
    from ocrroute.db.models import Provider, RouteMember

    with _db() as s:
        p = s.get(Provider, provider_id) or s.query(Provider).filter(Provider.label == provider_id).first()
        if p is None:
            err.print('[red]provider not found[/red]')
            raise typer.Exit(1)
        s.query(RouteMember).filter(RouteMember.provider_id == p.id).delete()
        s.delete(p)
    console.print('[green]✓[/green] removed')


@provider_app.command('test')
def providerTest(provider_id: str) -> None:
    from ocrroute.db.models import Provider
    from ocrroute.pipeline.input import loadInput
    from ocrroute.runtime.executor import OcrRequest
    from ocrroute.runtime.sample import sampleImageBytes

    ctx = _ctx()
    with _db() as s:
        p = s.get(Provider, provider_id) or s.query(Provider).filter(Provider.label == provider_id).first()
        if p is None:
            err.print('[red]provider not found[/red]')
            raise typer.Exit(1)
        pid = p.id
    doc = loadInput(settings=ctx.settings, file_bytes=sampleImageBytes(), filename='sample.png')
    res = ctx.executor.execute(OcrRequest(doc=doc, provider_id=pid, cache=False, origin='panel_test'))
    console.print(
        '{} · {} ms · {}'.format(res.status, res.usage['duration_ms'], repr(res.result.get('ParsedText', '')[:80]))
        + (' · [red]{}[/red] {}'.format(res.error_code, res.error_message) if res.error_code else '')
    )


@cred_app.command('add')
def credAdd(
    provider: str = typer.Option(..., '--provider', '-p'),
    secret: str = typer.Option(None, hide_input=True, prompt=True),
    alias: str = '',
) -> None:
    from ocrroute.db.models import Credential, Provider

    ctx = _ctx()
    with _db() as s:
        p = s.get(Provider, provider) or s.query(Provider).filter(Provider.label == provider).first()
        if p is None:
            err.print('[red]provider not found[/red]')
            raise typer.Exit(1)
        s.add(
            Credential(
                provider_id=p.id, alias=alias, secret_enc=ctx.secrets.encrypt(secret), order_index=len(p.credentials)
            )
        )
    console.print('[green]✓[/green] credential stored (encrypted)')


@cred_app.command('list')
def credList(provider: str = typer.Option('', '--provider', '-p'), as_json: bool = JSON_OPT) -> None:
    from ocrroute.crypto import mask
    from ocrroute.db.models import Credential, Provider

    ctx = _ctx()
    with _db() as s:
        q = s.query(Credential)
        if provider:
            p = s.get(Provider, provider) or s.query(Provider).filter(Provider.label == provider).first()
            q = q.filter(Credential.provider_id == (p.id if p else '-'))
        rows = [
            {
                'id': c.id,
                'provider': c.provider.label,
                'alias': c.alias,
                'masked': mask(ctx.secrets.decrypt(c.secret_enc)),
                'enabled': c.enabled,
                'ok': c.success_count,
                'fail': c.failure_count,
                'exhausted_until': c.exhausted_until,
            }
            for c in q.all()
        ]
    _out(
        rows,
        as_json,
        lambda d: _table(
            ['id', 'provider', 'alias', 'secret', 'enabled', 'ok', 'fail', 'exhausted'],
            [
                [
                    r['id'],
                    r['provider'],
                    r['alias'],
                    r['masked'],
                    r['enabled'],
                    r['ok'],
                    r['fail'],
                    r['exhausted_until'],
                ]
                for r in d
            ],
        ),
    )


@cred_app.command('rm')
def credRm(credential_id: str) -> None:
    from ocrroute.db.models import Credential

    with _db() as s:
        c = s.get(Credential, credential_id)
        if c is None:
            err.print('[red]credential not found[/red]')
            raise typer.Exit(1)
        s.delete(c)
    console.print('[green]✓[/green] removed')


@route_app.command('list')
def routeList(as_json: bool = JSON_OPT) -> None:
    from ocrroute.db.models import Route

    with _db() as s:
        rows = [
            {
                'id': r.id,
                'name': r.name,
                'strategy': r.strategy,
                'default': r.is_default,
                'enabled': r.enabled,
                'members': ['{}({})'.format(m.provider.label, m.provider.engine_id) for m in r.members if m.provider],
            }
            for r in s.query(Route).all()
        ]
    _out(
        rows,
        as_json,
        lambda d: _table(
            ['name', 'strategy', 'default', 'enabled', 'members'],
            [[r['name'], r['strategy'], r['default'], r['enabled'], ' → '.join(r['members'])] for r in d],
        ),
    )


@route_app.command('add')
def routeAdd(
    name: str,
    members: list[str] = typer.Argument(..., help='provider ids or labels in order'),
    strategy: str = typer.Option('priority'),
    default: bool = typer.Option(False, '--default'),
    min_chars: int = typer.Option(0),
) -> None:
    from ocrroute.db.models import Provider, Route, RouteMember
    from ocrroute.routing import strategies

    if strategy not in strategies.names():
        err.print('[red]unknown strategy[/red]; known: {}'.format(', '.join(strategies.names())))
        raise typer.Exit(1)
    with _db() as s:
        if s.query(Route).filter(Route.name == name).first():
            err.print('[red]route exists[/red]')
            raise typer.Exit(1)
        r = Route(
            name=name,
            strategy=strategy,
            is_default=default,
            stop_condition={'min_chars': min_chars} if min_chars else {},
        )
        s.add(r)
        s.flush()
        if default:
            s.query(Route).filter(Route.id != r.id).update({'is_default': False})
        for i, m in enumerate(members):
            p = s.get(Provider, m) or s.query(Provider).filter(Provider.label == m).first()
            if p is None:
                err.print("[red]provider '{}' not found[/red]".format(m))
                raise typer.Exit(1)
            s.add(RouteMember(route_id=r.id, provider_id=p.id, order_index=i))
    console.print('[green]✓[/green] route {}'.format(name))


@route_app.command('rm')
def routeRm(name: str) -> None:
    from ocrroute.db.models import Route

    with _db() as s:
        r = s.query(Route).filter((Route.name == name) | (Route.id == name)).first()
        if r is None:
            err.print('[red]route not found[/red]')
            raise typer.Exit(1)
        s.delete(r)
    console.print('[green]✓[/green] removed')


@route_app.command('simulate')
def routeSimulate(
    route: str = typer.Argument(''),
    mime: str = 'image/png',
    width: int = 1200,
    height: int = 800,
    pages: int = 1,
    lang: str = 'en',
    handwriting: bool = False,
    tables: bool = False,
    offline: bool = False,
    as_json: bool = JSON_OPT,
) -> None:
    from ocrroute.routing.candidates import RequestContext

    ctx = _ctx()
    with _db() as s:
        res = ctx.router.simulate(
            s,
            RequestContext(
                mime=mime,
                width=width,
                height=height,
                page_count=pages,
                language=lang.split(','),
                handwriting=handwriting,
                tables=tables,
                offline=offline,
            ),
            route_name=route,
        )

    def render(d: dict[str, Any]) -> None:
        if not d['ok']:
            err.print('[red]{}[/red]'.format(d['error']))
            return
        console.print('route [bold]{}[/bold] · strategy {}'.format(d['route'], d['strategy']))
        for i, c in enumerate(d['candidates'], 1):
            console.print(
                '  {}. {} / {}  ({}, ~{}¢, q{}, {})'.format(
                    i, c['engine'], c['provider'], c['kind'], c['est_cost_cents'], c['quality'], c['health']
                )
            )
        for x in d['explain']:
            console.print('  [dim]· {}[/dim]'.format(x))

    _out(res, as_json, render)


@key_app.command('create')
def keyCreate(
    name: str = typer.Option(..., '--name'),
    scope: list[str] = typer.Option(['ocr:read', 'ocr:write'], '--scope'),
    rpm: int = 0,
    budget_cents: float = 0.0,
    route: str = '',
) -> None:
    from ocrroute.crypto import hashApiKey, newApiKey
    from ocrroute.db.models import ApiKey, Route

    with _db() as s:
        rid = None
        if route:
            r = s.query(Route).filter(Route.name == route).first()
            rid = r.id if r else None
        raw = newApiKey()
        s.add(
            ApiKey(
                name=name,
                key_hash=hashApiKey(raw),
                key_prefix=raw[:10],
                scopes=list(scope),
                rpm_limit=rpm,
                monthly_budget_cents=budget_cents,
                route_id=rid,
            )
        )
    console.print('API key for [bold]{}[/bold] (shown once):\n{}'.format(name, raw))


@key_app.command('list')
def keyList(as_json: bool = JSON_OPT) -> None:
    from ocrroute.db.models import ApiKey

    with _db() as s:
        rows = [
            {
                'id': k.id,
                'name': k.name,
                'prefix': k.key_prefix,
                'scopes': k.scopes,
                'enabled': k.enabled,
                'last_used': k.last_used_at,
            }
            for k in s.query(ApiKey).all()
        ]
    _out(
        rows,
        as_json,
        lambda d: _table(
            ['id', 'name', 'prefix', 'scopes', 'enabled', 'last used'],
            [[r['id'], r['name'], r['prefix'] + '…', ','.join(r['scopes']), r['enabled'], r['last_used']] for r in d],
        ),
    )


@key_app.command('revoke')
def keyRevoke(key_id: str) -> None:
    from ocrroute.db.models import ApiKey

    with _db() as s:
        k = s.get(ApiKey, key_id) or s.query(ApiKey).filter(ApiKey.name == key_id).first()
        if k is None:
            err.print('[red]key not found[/red]')
            raise typer.Exit(1)
        s.delete(k)
    console.print('[green]✓[/green] revoked')


# ---------------------------------------------------------------- runs / usage
@runs_app.command('list')
def runsList(limit: int = 20, status: str = '', as_json: bool = JSON_OPT) -> None:
    from ocrroute.db.models import Run

    with _db() as s:
        q = s.query(Run).order_by(Run.created_at.desc())
        if status:
            q = q.filter(Run.status == status)
        rows = [
            {
                'id': r.id,
                'created_at': r.created_at,
                'status': r.status,
                'route': r.route_name,
                'engine': r.winning_engine,
                'ms': r.duration_ms,
                'chars': r.chars,
                'error': r.error_code,
            }
            for r in q.limit(limit).all()
        ]
    _out(
        rows,
        as_json,
        lambda d: _table(
            ['id', 'time', 'status', 'route', 'engine', 'ms', 'chars', 'error'],
            [
                [
                    r['id'][-8:],
                    r['created_at'][11:19],
                    r['status'],
                    r['route'],
                    r['engine'],
                    r['ms'],
                    r['chars'],
                    r['error'],
                ]
                for r in d
            ],
        ),
    )


@runs_app.command('show')
def runsShow(run_id: str) -> None:
    from ocrroute.api.routers.runs import runToDict
    from ocrroute.db.models import Run

    with _db() as s:
        r = s.get(Run, run_id) or s.query(Run).filter(Run.id.like('%{}'.format(run_id))).first()
        if r is None:
            err.print('[red]run not found[/red]')
            raise typer.Exit(1)
        emitJson(runToDict(r))


@runs_app.command('purge')
def runsPurge(run_id: str) -> None:
    from ocrroute.db.models import Run

    with _db() as s:
        r = s.get(Run, run_id)
        if r is None:
            err.print('[red]run not found[/red]')
            raise typer.Exit(1)
        for a in r.artifacts:
            Path(a.path).unlink(missing_ok=True)
        s.delete(r)
    console.print('[green]✓[/green] purged')


@app.command()
def usage(
    group_by: str = typer.Option('engine', help='engine|route|key|day'), since: str = '', as_json: bool = JSON_OPT
) -> None:
    """Usage and estimated cost."""
    from ocrroute.db.repo.usage import groupedUsage

    with _db() as s:
        rows = groupedUsage(s, group_by, since)
    _out(
        rows,
        as_json,
        lambda d: _table(
            [group_by, 'runs', 'ok', 'pages', 'chars', 'avg ms', 'cost ¢'],
            [[r['group'], r['runs'], r['successes'], r['pages'], r['chars'], r['avg_ms'], r['cost_cents']] for r in d],
        ),
    )


# ---------------------------------------------------------------- db / config
@db_app.command('migrate')
def dbMigrate() -> None:
    from ocrroute.db.session import initDb

    initDb()
    console.print('[green]✓[/green] schema is current')


@db_app.command('backup')
def dbBackup(dest: Optional[Path] = typer.Argument(None)) -> None:
    from ocrroute.runtime.maintenance import backup

    path = backup(getSettings(), dest)
    console.print('[green]✓[/green] {}'.format(path))


@db_app.command('restore')
def dbRestore(src: Path) -> None:
    from ocrroute.runtime.maintenance import restore

    if not typer.confirm('Overwrite the current database with {}?'.format(src)):
        raise typer.Exit(1)
    restore(getSettings(), src)
    console.print('[green]✓[/green] restored - restart the server')


@db_app.command('vacuum')
def dbVacuum() -> None:
    from ocrroute.runtime.maintenance import runOnce, vacuum

    s = getSettings()
    _ctx()
    res = runOnce(s)
    vacuum(s)
    console.print('[green]✓[/green] maintenance {}'.format(res))


@db_app.command('integrity')
def dbIntegrity() -> None:
    from ocrroute.runtime.maintenance import integrity

    console.print(integrity(getSettings()))


@config_app.command('get')
def configGet(key: str = typer.Argument('')) -> None:
    s = getSettings().model_dump()
    s.pop('secret_key', None)
    emitJson(s if not key else {key: s.get(key)})


@config_app.command('export')
def configExport() -> None:
    """Print a .env with the effective settings."""
    for k, v in getSettings().model_dump().items():
        if k == 'secret_key':
            continue
        console.print(
            'OCRROUTE_{}={}'.format(k.upper(), json.dumps(v) if isinstance(v, (list, dict)) else v), markup=False
        )


if __name__ == '__main__':
    app()
