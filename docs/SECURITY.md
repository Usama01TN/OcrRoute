# Security

- **Client API keys** are stored as SHA-256 hashes; the secret is shown once. Scopes, RPM/RPD sliding windows,
  monthly budgets, expiry, rotate/revoke.
- **Provider credentials** are encrypted at rest with Fernet (`OCRROUTE_SECRET_KEY` or a generated
  `~/.ocrroute/secret.key`, mode 0600). The API returns only a masked suffix; the panel never renders plaintext.
- **Log redaction**: a structlog processor masks `sk-…`, `AIza…`, `ocrr_…` and `api_key=…`-style tokens; engine
  error strings (which may embed response bodies) pass through `redact()` before storage or return.
- **SSRF guard** on user URLs and webhooks: http(s) only, allow/deny lists, DNS resolution checked against
  loopback/private/link-local/metadata ranges, redirects re-validated, download size capped.
- **Uploads**: MIME sniffed from content, byte/pixel/page caps, PDF rasterised in-process.
- **Paths**: `path` inputs need admin scope; artifact serving is confined to the artifact root.
- **Panel**: argon2 passwords, signed session cookie (HttpOnly, SameSite=Strict), login lockout (8 attempts /
  10 min per IP), CSRF via `X-Requested-With: OcrRoute` (or `X-CSRF-Token`) for cookie-authenticated API calls,
  CSP and X-Frame-Options headers, audit log for every mutation.
- **Privacy mode**: inputs never persisted, results not cached on disk, API engines refused (offline flag).
- **Reporting**: open an issue marked *security*; do not include credentials in reports - `ocrroute doctor --md`
  already redacts `OCRROUTE_*KEY*` values.
