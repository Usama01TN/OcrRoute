# Changelog

## 0.5.0 - 2026-09-24

- **Full edition**: new executables `ocrroute-server-full` and `OcrRoute-Desktop-Full` with **EasyOCR** (PyTorch CPU)
  and **PaddleOCR** (PaddlePaddle) built in, next to the lean ones. Built by CI on Linux, Windows, macOS arm64 and
  macOS x86_64 (PaddleOCR only: no PyTorch >= 2.3 for Intel Macs). CI runs a real OCR through every bundled engine
  and checks the recognised text, and guards the 2 GiB release-file limit. `version --json` reports the edition.
- **`ocrroute.opencv_alias`**: PaddleX refuses to start unless `opencv-contrib-python` is installed; the headless
  contrib build (same `cv2`, no Qt libraries that clash with PyQt5) now satisfies that check.
- **Fix (desktop executable)**: `RuntimeError: sys.stderr is None` at start. Windowed builds have no console
  streams; `faulthandler.enable()` required one. `ocrroute.stdio.ensureStreams()` now gives GUI processes real
  streams (`~/.ocrroute/logs/desktop.log`) before anything runs, and `faulthandler` is enabled only when possible.
  The crash-isolation probe reports through a file instead of stdout (windowed children have none) and never opens
  a console window on Windows.
- **Dashboard**: no more "EasyOCR / PaddleOCR unavailable" alerts for engines that are absent by design; the Engines
  page explains them (lean edition: points to the Full edition). Alerts remain for genuinely broken engines.

## 0.4.9 - 2026-09-24

- **Fix (macOS x86_64 executable)**: the frozen server failed with `Symbol not found: _SSL_get0_group_name`.
  cryptography 49+ publishes no macOS x86_64 wheel, so pip compiled it from source against the runner's newer
  OpenSSL, while the bundle carried the older `libssl.3.dylib` from Python. Intel Macs now use cryptography 48.x (the
  last universal2 wheel, which links OpenSSL statically; environment marker in `pyproject.toml`), and CI installs
  native packages from wheels only (`--only-binary`) and checks that cryptography imports before freezing.
- **Mistral OCR bundled**: `mistralai` added to the `api` extra and to the executables (49 of 56 engines built in).
- **Deep-learning engines install on demand**: per-engine extras (`easyocr`, `surya`, `transformers`, `olmocr`,
  `paddle`, `calamari`, `keras`), an **Install** button on the Engines page, `POST /v1/engines/{id}/install` and
  `ocrroute engines install <engine>`. Installation runs pip in OcrRoute's environment and hot-reloads the engine
  (no restart). PEP 668 (OS-managed Python) is detected and explained; `OCRROUTE_PIP_ARGS` is an explicit opt-in.
  In the executables these engines explain that they are not bundled and how to get them.
- AioOCR's "Could not import" console messages are captured (shown per engine in the UI; `OCRROUTE_VERBOSE_DISCOVERY=1`
  prints them). The CI verify step asserts Mistral OCR is bundled and lists the engines that are not, by design.

## 0.4.8 - 2026-09-24

- **CI fix**: the "Verify server binary" step still exited silently on failure. GitHub runs step scripts with
  `bash -e`, and `set -uo pipefail` does not disable `-e`, so a failing command ended the script before `check()`
  could read its exit code and print logs. The step now starts with `set +e -u -o pipefail`.
- **CI diagnostic**: the step first runs the engine-import probe directly (non-fatal) and reports how far imports
  get; if the interpreter dies, it names the module being imported. Probe output is included in the uploaded logs.

## 0.4.7 - 2026-09-24

- **Crash-isolated engine discovery**: a compiled dependency that crashes the interpreter on import (segfault or
  illegal instruction in a wheel built for another CPU) used to take the whole gateway down without a message,
  because it happens below Python where AioOCR's per-engine error handling cannot help. Frozen builds (and any
  install with `OCRROUTE_SAFE_DISCOVERY=1`) now probe engine imports in a child process first; a crashing module
  is disabled with the exact reason (for example "signal 11") and an explanation in the Engines page, and every
  other engine keeps working. The probe runs once per bundle, Python version and CPU architecture (cached in
  `~/.ocrroute/cache`). `faulthandler` is enabled in frozen builds, so native crashes print a Python stack.
- **CI**: the "Verify server binary" step explains every failure (command, exit code, signal name, stderr and
  stdout), warns when an engine was disabled by the probe, prints the server log when the health check fails, and
  uploads all logs as a `verify-logs-<platform>` artifact. Fixed `grep -c` failing the step when it counted zero.

## 0.4.6 - 2026-09-23

- **CI**: the macOS Intel build requested the retired `macos-13` runner label and queued forever. It now uses
  `macos-15-intel` (GitHub's last x86_64 macOS image); the Apple-silicon build uses `macos-latest`. Build jobs have a
  60-minute timeout.

## 0.4.5 - 2026-09-23

- **Fix (CLI, all platforms)**: `--json` output piped into a reader that stops early (`| head`) failed the command:
  exit 1 on Linux/macOS (broken pipe) and an `OSError: [Errno 22]` traceback on Windows, where a closed pipe is
  reported as EINVAL through colorama. Machine output is now plain UTF-8 JSON written directly to stdout (no Rich
  console emulation or colour codes), and a closed pipe ends the command quietly with exit 0; real I/O errors are
  still reported. The frozen entry point forces UTF-8 on Windows consoles.
- **CI**: the "Verify server binary" step no longer truncates output with `head`. It saves the full JSON,
  validates it (engine count, Tesseract present), and smoke-tests the frozen server by starting it and checking
  `/v1/health`.

## 0.4.4 - 2026-09-23

- **Fix (desktop)**: background results could be lost. `runAsync()` handed workers to Qt's thread pool without
  keeping a Python reference, so the garbage collector could destroy a worker's signal object mid-run and the
  scan / refresh / save never completed. Workers are now held until their `finished` signal is delivered
  (regression test runs 40 workers under forced garbage collection).
- **Fix (tests)**: the desktop scan test left its first polling timer running; through a late-binding closure it
  quit the next event loop early, so on fast machines (Windows CI) the responsiveness probe had no samples and
  `max()` of an empty list raised. Pollers are stopped and bound explicitly, the probe uses a precise timer with a
  minimum observation window, and it asserts it collected enough samples.
- Starlette's httpx deprecation warning is filtered in pytest.

## 0.4.3 - 2026-09-23

- **CI lint passes**: ruff's pyupgrade rules that contradict the project's Python 2/3-compatible style (`(object)`,
  `super(Class, self)`, `# coding=utf-8`, `from __future__`, `.format()`) are disabled in `pyproject.toml` with the
  reason documented next to each; import order, unused imports and whitespace fixed across the tree.
- **Fix**: ngrok authentication. A default `authenticate()` had been placed inside the `Ngrok` class and shadowed
  the real implementation, so authtokens were rejected. It now lives in the `Tunnel` base class (regression test added).

## 0.4.2 - 2026-09-23

- **Charts follow the theme in real time**: colours are Chart.js scriptable options evaluated at draw time, every
  chart is registered, and a theme switch calls `update()` on all of them (verified in Chromium by canvas pixel).
  New light/dark palettes, themed tooltips, legends, ticks and grid lines, rounded bars, filled lines.
- **Scrollbars**: thin rounded thumbs in both themes (WebKit/Blink and Firefox), hover/active states, sidebar bar
  hidden until hovered, smaller bars in tables, code blocks and dialogs; desktop QSS scrollbars restyled to match.
- **Theme toggle**: plain monochrome outline sun / moon icons that inherit the text colour.
- **Tab icon**: SVG favicon (light and dark variants, swapped with the theme), PNG and ICO fallbacks, Apple touch
  icon, web manifest, `theme-color`, and `/favicon.ico`; the desktop window uses the same icon.

## 0.4.1 - 2026-09-23

- **Light mode sidebar**: the sidebar now follows the theme (white in light mode, navy gradient in dark) in the web
  panel and the desktop app; the transition is animated.
- **Restart / Shutdown buttons fixed**: the sidebar is rendered twice (desktop + mobile drawer), so the buttons were
  bound by a duplicated id and the visible one had no handler. They are bound by `data-action` now, show progress,
  poll `/v1/health` until the server is back after a restart, and show a "Server stopped" screen after shutdown.
- **Translations**: 140+ more strings wrapped (headings, descriptions, table headers, dialogs, getting-started
  steps, time ranges, role legend) and translated in all 9 languages (397 keys each).

## 0.4.0 - 2026-09-23

- **Restart / Shutdown**: new `runtime/lifecycle.py`. The running uvicorn server registers itself; Shutdown drains
  and exits cleanly (code 0); Restart stops the server and `ocrroute serve` starts a fresh app in place on the same
  port (embedded desktop server likewise; multi-worker mode re-execs). Requests during a transition get 409.
- **One-click tunnels**: cloudflared and ngrok are downloaded into `~/.ocrroute/bin` (no admin rights) and started
  immediately; Tailscale uses the vendor installer. Token / login flows for ngrok and Tailscale
  (`POST /v1/endpoints/tunnels/{name}/auth`). Binaries in `~/.ocrroute/bin` are preferred over PATH.
- **User management**: roles enforced end to end (admin / operator with the new `manage` scope / viewer),
  `/v1/users` CRUD, last-admin guard, password reset, change-my-password, Settings UI.
- **OCR-aware routing**: strategies `confidence_first`, `script_aware`, `sticky`, `best_of_two`; a catalog of
  12 built-in `auto/*` routes (`GET /v1/routes/catalog`) usable as the `route` field with no setup; auto routes skip
  credential-less cloud providers, rank healthy and lightweight engines first (heavy deep-learning engines
  detected from their imports), and fall back sequentially when a parallel batch fails.
- **Routes page** rebuilt: auto-routing catalog, 4-step getting started, All / Intelligent / Deterministic tabs,
  route cards with enable toggle and actions.
- **Languages**: Italian, Portuguese, Russian, Chinese added (9 total, every key translated).
- **Styling**: every input, select, checkbox and radio styled consistently (native controls included).
- **Desktop**: resizable and expansive (splitter sidebar, scrollable pages, expanding policies, remembers
  geometry and maximized state), scroll areas transparent in both themes.

## 0.3.1 - 2026-09-23

- **Design system aligned with the "Fixed arena" fork**: blue accent (`#2563EB` / `#3B82F6`), always-dark navy
  gradient sidebar with light text, radial glow background, Inter typography, 14px card radii, frosted top bar,
  pill quick-nav. The console keeps the gateway layout (grouped sidebar with subtitles, top navbar with the single
  animated theme toggle, language dropdown, health and sign-out, breadcrumbs, pill tabs, Endpoints section).
- More motion: card enter animation, nav hover slide/scale, sliding tab indicator, gradient button shine,
  pulsing status dots, hover lift with accent-tinted shadow.
- Desktop adopts the same palette (navy sidebar, blue primary) in both themes.
- No feature changes; every existing behaviour is covered by the unchanged test-suite (67 tests).

## 0.3.0 - 2026-09-23

- **Endpoints section** (web + desktop + `/v1/endpoints`): active endpoints with one-click copy, every LAN address of
  the machine as `http://<ip>:<port>/v1`, a stable server id, tunnels (Cloudflare Quick Tunnel, Tailscale Funnel,
  ngrok) detected on PATH with install-command / enable / disable and captured public URLs, a manually configured
  public URL, and a global *Custom OCR prompt* injected into every VLM engine request. Sidebar footer gains
  Restart / Shutdown for the server process.
- **Console redesign**: grouped sidebar (Gateway / Routing / Observability / System) with icon, title and subtitle per
  entry and collapsible groups; top navbar with page title + subtitle, Quick nav (Ctrl+K), language dropdown with
  flags, a single animated sun/moon theme toggle, health and sign-out icons; breadcrumbs and pill tabs; subtle grid
  background; fade-up and stagger animations; coral accent with gradient action buttons in both themes.
- Desktop adopts the same palette and gains the Endpoints page.
- The given AioOCR library updated to the "Fixed arena" drop (adds the `OmniRouteOcr` engine); 56 engines detected.
- All em dashes removed from project sources; a test enforces the rule.

## 0.2.0 - 2026-09-22

- **Web panel rebuilt on Bootstrap 5** (vendored, offline): responsive grid, offcanvas sidebar and sticky top bar on
  phones/tablets, Bootstrap Icons, cards/KPI tiles, badges, toasts, light/dark/system via `data-bs-theme`.
- **Desktop redesign**: QtSvg vector icons (theme-aware, inverted on the active item), refreshed light/dark QSS
  (rounded controls, focus ring, alternating rows, custom scrollbars), page subtitles, connection pill in the status bar.
- **Cross-platform executables**: `scripts/build_executable.py` (PyInstaller) and `.github/workflows/build.yml`
  building Windows/macOS/Linux bundles, artifact links in the job summary, GitHub Release on tags.

- **Automatic provider detection**: the catalogue is built from `AioOCR.AVAILABLE_PLUGINS`; a provider row is
  seeded for every available engine; engines added or repaired on disk are detected by a file watcher and a
  periodic rescan (`engine_rescan_minutes`) through AioOCR's own `_discoverOcrPlugins()` - no restart needed.
- **Python 2/3-compatible syntax**: `from __future__` headers everywhere, `py23` shims (`raiseFrom`,
  `mergeDicts`, text/bytes helpers), no keyword-only markers, no `raise … from`. The FastAPI handlers that must be
  `async def` and the framework-required annotations are the documented exceptions.
- **Multilanguage UIs**: shared JSON catalogues (English, French, Spanish, German, Arabic with RTL) used by the
  web panel (`_()`, cookie/header detection, language selector) and the desktop (`JsonTranslator`, live switch).
- **Professional UI**: light / dark / system theme (persisted, instant), sidebar icons, avatar, onboarding
  checklist, humanised greeting and relative times, refined tables/badges, RTL-aware layout; desktop live theme.
- Project organisation: `Makefile`, `CONTRIBUTING.md`, `docs/I18N.md`.

## 0.1.0 - 2026-09-22

First release.

- Imports the given `AioOCR` library unchanged through `ocrroute/enginelib.py`; gateway code written in AioOCR's
  own style (`# coding=utf-8`, `(object)`, `super(Cls, self)`, `__m_` + `getX`/`setX`, `.format()`, no dataclasses).

- Engine catalogue: discovery of 55 `OCRPlugin` classes with availability, install hints, option-schema
  introspection and curated metadata; runtime demotion of engines with missing dependencies.
- SQLite persistence (WAL) with 19 tables, Alembic scaffold, maintenance (cache expiry, rollups, retention,
  backup/restore, integrity, vacuum), crash reconciliation.
- Routing engine: routes/members/strategies (14), circuit breaker, credential rotation, deadlines, stop
  conditions, degraded partials, ensemble consensus, explain traces, simulation.
- Input pipeline: path/URL/base64/upload, SSRF guard, MIME sniffing, size/pixel/page caps, PDF rasterisation,
  optional preprocessing with geometry restore; post-processing with RTL ordering and page stitching.
- Exporters: json, text, md, hOCR, ALTO, csv, xlsx, docx, searchable PDF, overlay PNG.
- API: `/v1/ocr` (+async, batch, jobs SSE), runs & artifacts, providers, credentials, routes, keys, usage,
  stats, settings, audit, doctor, `/metrics`; reserved `/v1/tools`.
- Web control panel: 12 pages (overview, playground, engines, providers, routes, runs, batch, usage, keys,
  tools, settings, doctor), first-run setup, session auth, live SSE feed, offline assets.
- Desktop (PyQt5): embedded/remote server, 10 pages, scan with overlay viewer and region capture, tray,
  single instance, QSS themes.
- CLI: serve, setup, doctor, ocr, batch, engines, provider, cred, route, key, runs, usage, db, config, desktop.
- Tests: 54 (unit, API integration against fake engines, panel, CLI, desktop offscreen).

Known limitations: searchable-PDF text layer is Latin-only; batch upload payloads are not persisted across
restarts; no TOTP 2FA yet (column reserved); desktop global hotkey not implemented (tray + menu shortcuts only).
