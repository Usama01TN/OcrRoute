# OcrRoute

**OcrRoute is an OCR gateway for multi-engine text extraction: one HTTP endpoint in front of local
and cloud OCR engines, with routing, load balancing, retries and fallbacks - plus quotas, caching,
cost tracking and observability for reliable, cost-aware document understanding.**

It imports the given `AioOCR` engine library **unchanged** (55 `OCRPlugin` classes: Tesseract, RapidOCR, PaddleOCR, Surya,
GOT-OCR, DeepSeek-OCR, Nougat, OCR.Space, Google Vision, Mistral OCR, Gemini/Claude/OpenAI-style VLM
OCR, …) in a FastAPI gateway with a web control panel, a PyQt5 desktop app and a CLI. Persistence is
a single SQLite file.

```
pip install -e ".[local,desktop,dev]"   # core + Tesseract/OpenCV extras + PyQt5 + tests
ocrroute setup                          # admin user, first provider, default route, API key
ocrroute serve                          # API  http://127.0.0.1:20256/v1/docs
                                        # panel http://127.0.0.1:20256/panel/
ocrroute ocr scan.png                   # zero-config OCR through the built-in auto route
ocrroute desktop                        # PyQt5 app (embedded server or remote)
```

```bash
curl -X POST http://127.0.0.1:20256/v1/ocr \
  -H "Authorization: Bearer ocrr_…" \
  -F file=@invoice.pdf \
  -F 'json={"route":"invoices","language":"en","pages":"1-3","output":["json","text","pdf"]}'
```

The response keeps the engine library's unified result **verbatim** under `result` and adds gateway
metadata beside it (`routing.attempts`, `routing.explain`, `usage`, `artifacts`). See
[docs/API.md](docs/API.md).

## What you get

| Area | Highlights |
|---|---|
| Routing | Built-in `auto/*` catalog (balanced, fast, accurate, cheapest, free, private, handwriting, tables, pdf, multilingual, consensus, best-of-two) plus named **Routes** (fallback chains) with 18 strategies: priority, round-robin, weighted, fill-first, least-used, least-latency, P2C, random, cost-optimised, local-first, quality-first, language-aware, ensemble-vote, auto (explainable). |
| Reliability | Bounded retries, per-provider circuit breaker with escalating cooldown, deadlines, credential rotation on auth/quota errors, terminal-error short-circuit, degraded partial results. |
| Control | Providers, encrypted credentials (Fernet), client API keys with scopes/RPM/RPD/budgets, panel users with roles (admin / operator / viewer), runtime settings, audit log, graceful restart / shutdown. |
| Cost & quotas | Per-engine price table, per-run cost estimate, monthly budgets per provider and per key, sliding-window limits. |
| Cache & dedupe | Result cache keyed by input hash + target + options, idempotency keys, in-flight single-flight. |
| Inputs / outputs | Path, URL (SSRF-guarded), base64, upload, PDF page ranges; exports: json, text, md, hOCR, ALTO, csv, xlsx, docx, searchable PDF, overlay PNG. |
| Observability | Runs & attempts in SQLite, live SSE feed, Prometheus `/metrics`, `doctor` diagnostics with copyable system report. |
| UIs | Web control panel (12 pages, **Bootstrap 5** + Bootstrap Icons vendored for offline use, mobile-first with an offcanvas sidebar) · PyQt5 desktop (10 pages, embedded/remote, region capture, overlay viewer, tray) · Typer CLI. Both UIs: **light / dark / system themes**, **English · Français · Español · Deutsch · Italiano · Português · Русский · 中文 · العربية (RTL)**, onboarding checklist, humanised times. |
| Endpoints | Active endpoints with copy, all LAN URLs, tunnels (Cloudflare / Tailscale / ngrok) with install / enable / disable, public URL, global OCR prompt, server restart / shutdown. |
| Auto-detection | Every `OCRPlugin` in `AioOCR` is discovered by the library's own discovery, seeded as a provider, and re-detected live when modules are added or dependencies installed (file watcher + periodic rescan). |
| Tools | Reserved, intentionally empty extension point (`/v1/tools`, `ocrroute/tools`, empty-state pages). See [docs/TOOLS.md](docs/TOOLS.md). |

## Editions and more engines

The stand-alone executables come in two editions:

| Edition | Files | Engines | Unpacked size |
|---|---|---|---|
| **Lean** | `ocrroute-server-*`, `ocrroute-desktop-*` | 49 of 56: every engine without a deep-learning framework (Tesseract, RapidOCR, Mistral OCR, all cloud engines) | ~150 MB |
| **Full** | `ocrroute-server-full-*`, `ocrroute-desktop-full-*` | Lean + **EasyOCR** and **Surya** (PyTorch, CPU) + **PaddleOCR** (PaddlePaddle) | ~1.7 GB |

On Intel Macs the Full edition includes PaddleOCR but not EasyOCR or Surya: PyTorch publishes no Intel-Mac build newer
than 2.2, which predates NumPy 2. EasyOCR, Surya and PaddleOCR download their model weights on first use.

**Surya** is bundled as 0.17.1, the last release that runs OCR entirely in PyTorch; Surya 2 (0.20+) needs a vLLM or
llama.cpp server (`pip install "ocrroute[surya2]"`). Surya's code is Apache-2.0, but its **model weights** use a
modified AI Pubs Open Rail-M license: free for research, personal use and startups under $5M in funding or revenue;
other commercial use needs a license from Datalab.

The remaining deep-learning engines (Calamari, keras-ocr, GLM, olmOCR) install on demand in a pip
installation: click **Install** in the Engines page, run `ocrroute engines install SuryaOcr`, or
`pip install "ocrroute[surya]"`. See `docs/ENGINES.md`.

## Stand-alone executables

`python scripts/build_executable.py` builds two PyInstaller bundles - `ocrroute-server` (CLI + API + web panel;
double-click starts the server) and `OcrRoute-Desktop` (PyQt5 app with the embedded server) - and archives them as
`dist/ocrroute-<target>-<version>-<os>-<arch>.zip|.tar.gz`.

The GitHub Actions workflow `.github/workflows/build.yml` does the same on **Windows, macOS (Intel + Apple silicon)
and Linux** on every push/PR, uploads each bundle as a workflow artifact, writes the download link into the job
summary, and - on a `v*` tag - publishes all of them with SHA-256 checksums to a GitHub Release:

```
git tag v0.2.0 && git push --tags     # → https://github.com/<you>/ocrroute/releases/tag/v0.2.0
```

## Code style

Syntax is kept Python 2/3-portable (`from __future__` headers, `py23` shims, no f-strings or keyword-only args);
see `docs/DECISIONS.md` #18 for the exact boundaries imposed by the frameworks.

The gateway is written in the same conventions as AioOCR: `# coding=utf-8` headers, `class X(object)`,
`super(Class, self)`, `__m_` private attributes with `getX`/`setX` accessors, camelCase methods, `:param:`
docstrings and `.format()` strings. Type hints appear only where SQLAlchemy, Pydantic, FastAPI or Typer need them.

## Documentation

`docs/ARCHITECTURE.md` · `docs/ROUTING.md` · `docs/API.md` · `docs/ENGINES.md` · `docs/DEPLOYMENT.md` ·
`docs/SECURITY.md` · `docs/TOOLS.md` · `docs/DECISIONS.md` · `docs/CHANGELOG.md`

## Independence note

Design patterns for gateway routing (fallback chains, balancing strategies, key management, dashboards)
are widely used in the ecosystem; OcrRoute applies them to OCR. OcrRoute is an independent Python project
with its own namespace (`ocrroute`, `~/.ocrroute`, `OCRROUTE_*`), its own default port (**20256**; it
refuses to bind 20128), and no chat/completions, LLM proxying or agent-protocol surface. It is not a fork,
plugin or companion of any other gateway.

## Third-party assets

`ocrroute/assets/fonts/DejaVuSans.ttf` is DejaVu Sans (Bitstream Vera license; see `LICENSE-DejaVu.txt` next to it),
used by PaddleOCR for visualisations.

## License

MIT - see `LICENSE`. The `AioOCR/` library is included as given and keeps its original authorship.
