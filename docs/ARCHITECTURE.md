# Architecture

```
client ──HTTP──▶ FastAPI (/v1)  ──▶ Executor ──▶ Router ──▶ Strategy ──▶ Attempt(s) ──▶ OCRPlugin subclass
   ▲                 │                 │            │                                     (AioOCR/engines/*)
panel (HTMX)         │                 │            └── candidates ← providers/credentials/routes (SQLite)
desktop (PyQt5) ─────┘                 └── cache · limits · breaker · artifacts · runs/attempts (SQLite)
CLI (Typer) ──── direct in-process ────┘
```

## Two code layers, two styles

- **`AioOCR/`** is the engine library exactly as it was given - **not one file is modified**. Its contract
  stays: a subclass implements `_run(image) -> list[word dicts]`; `OCRPlugin.parse()` adds bounded retries and
  builds the unified OCR.Space-shaped result; `AioOCR/__init__.py` discovers the plugins into
  `AVAILABLE_PLUGINS`. Adding an engine is still "drop a module in `AioOCR/engines/api` or `local`".
- **`ocrroute/`** is written in the **same style as AioOCR**: `# coding=utf-8` header and module docstring,
  `class X(object)`, `super(Class, self).__init__(...)`, `__m_`-prefixed private attributes with `getX`/`setX`
  accessors (exposed as `property()` for attribute-style reads), camelCase method names, `:param:`/`:return:`
  docstrings, `'...'.format()` instead of f-strings, no dataclasses, no `from __future__ import annotations`.
  Type annotations remain **only** where a framework requires them at runtime: SQLAlchemy `Mapped[...]`
  columns (`db/models.py`), Pydantic models and settings (`api/schemas/*`, `config.py`), FastAPI endpoint and
  Typer command signatures.

`ocrroute/enginelib.py` is the single bridge to the library. AioOCR's modules were written to run from inside
their own folder (`from engines.ocrplugin import …` fallback + `sys.path` appends), so the bridge puts the
`AioOCR/` folder on `sys.path` and registers the top-level `engines` package under `AioOCR.engines` *before*
importing `AioOCR`. There is then exactly one `OCRPlugin` class and AioOCR's own `_discoverOcrPlugins()` works
as its author intended (47 plugins here). The registry reads `AioOCR.AVAILABLE_PLUGINS` and only re-imports the
modules AioOCR rejected, to record the error text and an install hint.

## Modules

| Package | Responsibility |
|---|---|
| `catalog/` | Discovery of `OCRPlugin` subclasses, import-failure capture with install hints, option-schema introspection (`kwargs.pop('x', default)` + `:param x:` docs), curated metadata overlay (`engines.toml`). |
| `db/` | SQLAlchemy 2.0 models (19 tables incl. reserved `tools`/`tool_runs`), session factory with WAL/foreign keys/busy timeout, retry-on-locked scope, stats and usage aggregations. Alembic scaffold with `0001_initial`. |
| `routing/` | `Candidate`/`RequestContext`, filters, 14 strategies, circuit breaker, cost estimate, IoU consensus, explain trace, `Router.resolve()`/`simulate()`. |
| `pipeline/` | Input acquisition (SSRF guard, MIME sniffing, size/pixel/page limits, PDF rasterisation), preprocessing with coordinate restore, post-processing (whitespace, RTL, page stitching, Tools no-op hook), exporters, overlay renderer. |
| `runtime/` | `Executor` (the run loop), cache, sliding-window limits, in-flight counters, batch `JobRunner`, webhooks, maintenance/backup, doctor, app context. |
| `api/` | FastAPI app factory, API-key security, Pydantic schemas, routers (`ocr`, `runs`, `jobs`, `admin`, `tools`, `system`), Prometheus metrics. |
| `panel/` | Session auth (argon2 + signed cookie + CSRF header rule), Jinja2 pages, HTMX/vanilla JS that calls `/v1` with the session. |
| `desktop/` | PyQt5 client: `OcrRouteClient`, `EmbeddedServer` (uvicorn in a QThread), `Worker`/`run_async`, pages, `ImageViewer`, `RegionCapture`, QSS themes. |
| `cli/` | Typer commands. |
| `tools/` | Reserved, intentionally empty extension point. |
| `compat/` | `OcrBase` subclass of `AioOCR.ocrbase.OcrBase` keeping the legacy `parse(Engine=[{...}])` call shape. |
| `enginelib.py` | The bridge that imports the untouched AioOCR library (see above). |

## Run lifecycle

1. `load_input` → `InputDocument` (bytes, mime, sha256, dims, rasterised PDF pages).
2. Idempotency key → cache key (sha256 | target | normalised options) → cache lookup → single-flight.
3. `Router.resolve` → explicit engine/provider, named route, key-pinned route, default route, or built-in
   `auto` route → filter candidates (disabled, unavailable, circuit-open, over limit, conditions) → order by
   strategy → `RouteSpec` with an `explain` trace.
4. For each candidate (or *k* in parallel for `ensemble_vote`): load enabled credentials, run the engine per
   page in the worker pool with a per-page time budget, restore geometry, stitch pages, classify errors,
   rotate credentials on auth/quota/rate-limit, record `Attempt`, update provider health + breaker, demote
   engines whose runtime dependency is missing.
5. Stop condition → accept, else keep as best partial and continue; terminal input errors stop immediately.
6. Post-process → persist `Run` → write artifacts → store cache → emit SSE event → envelope.

## Threading model

All engine work runs in a bounded `ThreadPoolExecutor`; FastAPI endpoints hand off with
`anyio.to_thread.run_sync`. SQLite is opened per session with `check_same_thread=False`, WAL and a 5 s busy
timeout; `session_scope()` retries on "database is locked". The desktop app never touches the executor
directly - it is an HTTP client; embedded mode just hosts uvicorn in a `QThread`.
