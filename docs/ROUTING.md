# Routing

A **Route** is an ordered list of **members** (each a Provider) plus a **Strategy**, a **stop condition**,
`max_attempts`, a **deadline** and a **cache TTL**. A request resolves to a route in this order:

1. explicit `engine` (all its providers, or engine defaults if none) or `provider_id`
2. explicit `route` (name or id)
3. route pinned to the calling API key
4. the `is_default` route
5. the built-in `auto` route: every enabled provider + every available local engine without a provider

## Candidate filtering

Dropped with a reason in `routing.explain`: member disabled, engine disabled, engine unavailable, circuit
open, over RPM/RPD/monthly budget, offline mode (API engines), member conditions (`language_in`, `mime_in`,
`min_pages`, `max_pages`, `max_cost_cents`), request `max_cost_cents`. An empty list → `503 no_candidate`.

## Strategies

| name | behaviour |
|---|---|
| `priority` | strict member order (classic fallback chain) |
| `round_robin` | rotate the starting member per run |
| `weighted` | random order, probability ∝ weight |
| `fill_first` | exhaust one member's quota before the next (limits enforced by filtering) |
| `least_used` | fewest recent runs first |
| `least_latency` | lowest EWMA of recent successful attempt latency first |
| `p2c` | power of two choices on in-flight counters |
| `random` | uniform shuffle |
| `cost_optimised` | cheapest estimated cost first (local = 0) |
| `local_first` | local engines before API engines |
| `quality_first` | highest curated quality score first |
| `language_aware` | members declaring the requested language first |
| `ensemble_vote` | run first *k* (default 3) in parallel; cluster word boxes by IoU ≥ 0.4, majority text per cluster, ties to higher quality; `routing.votes` reports clusters/disagreements |
| `auto` | explainable heuristic (below) |

### `auto`

```
sensitive or offline      → local_first
handwriting hint          → handwriting-capable engines, then by quality
tables requested          → table-capable engines, then by quality
PDF / multi-page          → PDF-capable engines, cheapest first
small single image        → fast classic engines (Tesseract, RapidOcr, PaddleOcr, EasyOCR, OpenOcr) by latency
otherwise                 → healthy providers by recent latency
```

Every decision is appended to `routing.explain`. `POST /v1/routes/simulate` (or `ocrroute route simulate`)
resolves a hypothetical input with no OCR executed.

## Attempts, retries, rotation, breaker

- Engine-internal retries stay in `OCRPlugin.parse` (0.5 s ×2 backoff). The gateway treats an exhausted
  engine as **one failed Attempt** and moves on.
- Errors are classified (`errors.classify`) into `bad_input`, `unsupported_input`, `auth`, `quota`,
  `rate_limit`, `timeout`, `network`, `server_error`, `engine_missing`, `empty_result`, `deadline`, `internal`.
  `bad_input`/`unsupported_input` are **terminal**: the run fails after exactly one attempt.
- On `auth`/`quota`/`rate_limit` the same provider is retried with its next enabled credential before falling
  through; the failing credential gets `exhausted_until` (60 min, or 2 min for rate limits). All enabled
  credentials are also passed to the engine as `apiList` so its own rotation works.
- Circuit breaker per provider: `breaker_threshold` consecutive failures open it for
  `breaker_cooldown_seconds × 2^(trips-1)` (capped at 1 h); one half-open probe is allowed after cooldown.
- An engine that fails with `engine_missing` at runtime is demoted to unavailable (with the install hint it
  printed) so `auto` stops trying it.
- Stop condition (`min_chars`, `min_mean_confidence`, `require_overlay`): unmet results are kept as the best
  partial; if nothing meets it, the best partial is returned with `routing.degraded = true`.
