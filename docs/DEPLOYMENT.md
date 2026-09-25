# Deployment

## pip

```
pip install "ocrroute[local]"           # + Tesseract binary: apt install tesseract-ocr / brew install tesseract
ocrroute setup && ocrroute serve        # http://127.0.0.1:20256
```

Extras: `[api]` requests-based cloud engines · `[local]` Tesseract/OpenCV · `[vlm]` torch/transformers for local
VLM engines (large) · `[desktop]` PyQt5 + keyring · `[dev]` tests/lint.

## Split-port mode

`ocrroute serve --port 20256 --panel-port 20257` serves the API and the panel on different ports (reverse
proxy / container networking). Port 20128 is refused.

## Docker

`docker compose up --build` → API+panel on 20256, data volume at `/data`. Set `OCRROUTE_SECRET_KEY` to keep
credentials decryptable across container rebuilds (otherwise the generated key lives in the volume).

## Behind a reverse proxy

Terminate TLS at the proxy; forward `/v1`, `/panel`, `/metrics`. The panel session cookie is `SameSite=Strict`,
`HttpOnly`. Set `OCRROUTE_CORS_ORIGINS` only if a browser client on another origin must call `/v1`.

## Data & maintenance

`~/.ocrroute/ocrroute.db` (WAL), `artifacts/`, `uploads/` (only with `store_inputs`), `backups/`, `secret.key`.
Hourly maintenance purges expired cache and rolls runs older than `log_retention_days` into `usage_daily`.
`ocrroute db backup|restore|vacuum|integrity`. Startup marks runs interrupted by a crash as `failed/interrupted`.

## Stand-alone executables

Locally: `pip install pyinstaller && python scripts/build_executable.py [--target server|desktop|all] [--onefile]`.
Outputs land in `dist/` as archives named `ocrroute-<target>-<version>-<os>-<arch>`. The server bundle embeds the
untouched `AioOCR/` tree and the panel assets; Tesseract still has to be installed on the target machine for the
zero-config engine (cloud engines need only a key).

CI: `.github/workflows/build.yml` builds on `ubuntu-22.04`, `windows-2022`, `macos-latest` (arm64) and `macos-15-intel` (x86_64),
runs the unit tests, verifies the frozen server binary (`version`, `engines list`), uploads artifacts (30-day
retention) with the artifact URL in the job summary, and on `v*` tags publishes a GitHub Release with `SHA256SUMS.txt`.
Manual runs (`workflow_dispatch`) can request `--onefile` bundles. Binaries are unsigned (known limitation).

### macOS runner labels

GitHub retired the `macos-13` image on 2025-12-04; a job requesting a retired label never gets a runner and stays
"Waiting for a runner to pick up this job" indefinitely. Intel builds use `macos-15-intel`, which GitHub describes as
its last x86_64 macOS image (announced as available until August 2027). When it is retired, remove the
`macos-x86_64` matrix entry: Apple silicon Macs run the arm64 build natively.

### Crash-isolated engine discovery

Frozen builds probe engine imports in a child process on first start. A module whose native dependency crashes the
interpreter is disabled (shown in the Engines page with the signal) instead of crashing the gateway. Force it on for
a pip install with `OCRROUTE_SAFE_DISCOVERY=1`, or off with `OCRROUTE_SAFE_DISCOVERY=0`. Results are cached in
`~/.ocrroute/cache/engine-probe-*.json`; delete that file after changing installed packages to probe again.

## Publishing a release

A push to `main` builds and tests every platform but does not publish ("Publish release: This job was skipped" is
expected). To publish, either push a tag matching `ocrroute/version.py`:

```bash
git tag v0.7.1
git push origin v0.7.1
```

or open **Actions > Build > Run workflow** and tick **publish**. The release job runs only when all build jobs succeed,
so a release never goes out with a platform missing, and it refuses a tag that does not match the code version.
