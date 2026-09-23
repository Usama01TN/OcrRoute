# HTTP API (`/v1`)

OpenAPI: `GET /v1/openapi.json`, Swagger UI: `/v1/docs`. Auth: `Authorization: Bearer ocrr_…`
(or `X-Api-Key`). Scopes: `ocr:read`, `ocr:write`, `admin`. A logged-in panel session may also call the API
from the browser when it sends `X-Requested-With: OcrRoute` (CSRF guard).

## OCR

| Method | Path | Notes |
|---|---|---|
| POST | `/ocr` | multipart (`file` + optional `json` field) or JSON (`url` / `base64` / admin-only `path`) |
| POST | `/ocr/async` | queue a run; returns `placeholder_id` to poll |
| POST | `/batch` | multipart files + `body` JSON; `/batch/json` for URL lists |
| GET | `/runs`, `/runs/{id}` | filters: status, engine, route, q, error_code, since, until |
| GET | `/runs/{id}/artifacts/{kind}` | json, text, md, hocr, alto, csv, xlsx, docx, pdf, overlay_png (rendered on demand if not stored) |
| POST | `/runs/{id}/retry` | needs `store_inputs` |
| DELETE | `/runs/{id}` | purge run + artifacts |
| GET | `/jobs`, `/jobs/{id}`, `/jobs/{id}/events` (SSE) · POST `/jobs/{id}/cancel` |

### Request (JSON form)

```json
{"url": "https://…/invoice.pdf", "route": "invoices", "engine": null, "provider_id": null,
 "language": ["en","ar"], "pages": "1-3", "pdf_dpi": 150, "prompt": "Preserve table layout.",
 "options": {"minConfidence": 60, "isTable": true},
 "preprocess": {"auto_rotate": true, "grayscale": false, "upscale": true, "region": [x,y,w,h]},
 "output": ["json","text","hocr"], "stop_condition": {"min_chars": 20}, "strategy": "",
 "hints": {"handwriting": false, "tables": false, "sensitive": false, "offline": false, "max_cost_cents": null},
 "cache": true, "metadata": {"tenant": "acme"}}
```

`engine` and `route` are mutually exclusive. Unknown `options` are passed through to the engine.
Headers: `Idempotency-Key`, `X-OcrRoute-No-Cache: 1`.

### Envelope

```json
{"run_id": "01J…", "status": "succeeded", "cached": false,
 "result": { …exact unified OCR result: TextOverlay.Lines[].Words[], ParsedText, FileParseExitCode… },
 "routing": {"route": "invoices", "strategy": "cost_optimised", "winning_engine": "OcrSpace",
             "winning_provider": "ocrspace-main", "attempt_count": 2, "degraded": false,
             "explain": ["route=invoices strategy=cost_optimised", "skipped X (circuit open)", "order: …"],
             "attempts": [{"order": 0, "engine": "RapidOcr", "status": "failed", "error_code": "empty_result", "duration_ms": 420}],
             "options_applied": {}, "warnings": []},
 "usage": {"pages": 3, "chars": 4821, "lines": 142, "words": 900, "mean_confidence": 0.93, "duration_ms": 1730, "cost_cents": 0.6},
 "artifacts": [{"kind": "text", "url": "/v1/runs/01J…/artifacts/text"}], "metadata": {"tenant": "acme"}}
```

Failures use the same envelope with `status: "failed"`, the errored unified result (`FileParseExitCode = -1`)
and top-level `error_code` / `error_message`. Status mapping: 400 bad_input · 401 client auth · 403 scope ·
404 · 409 · 413 too large · 415 unsupported media · 422 validation · 429 rate_limit/budget (with
`Retry-After`) · 501 tools_reserved · 502 upstream engine failure · 503 no_candidate/queue full · 504 timeout/deadline.

## Management (admin scope unless noted)

`/engines` (list - read scope; `PATCH /engines/{id}` enabled/cost; `POST /engines/refresh`; `POST /engines/{id}/probe`)
· `/providers` (CRUD, `/test`, `/reset-circuit`) · `/credentials` (write-only secrets, `/verify`)
· `/routes` (CRUD, `/strategies`, `/simulate`) · `/keys` (create shows the secret once, `/rotate`, revoke)
· `/usage?group_by=engine|route|key|day` · `/stats/summary` · `/settings` · `/audit` · `/cache/clear`
· `/tools` (reserved) · `/health` · `/ready` · `/version` · `/doctor` · `/metrics` (Prometheus, unauthenticated).
