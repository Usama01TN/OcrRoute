# Engines

Engines come from the **unmodified `AioOCR/` library**: `AioOCR.AVAILABLE_PLUGINS` (its own discovery over
`engines/api/` - 22 network engines - and `engines/local/` - 29 on-device engines) is the source of truth; a few
modules define alias classes, so 55 classes appear in `GET /v1/engines`. An engine whose module fails to import is listed as `available: false` with the import
error and a `pip install …` hint (taken from the engine's own message when it states one).

Curated metadata lives in `ocrroute/catalog/engines.toml` (name, vendor, capabilities, languages, cost model,
unit price, quality score, docs). Everything missing there is introspected: option names/defaults from
`kwargs.pop('name', default)` calls, descriptions from `:param name:` docstrings.

## Adding an engine

Drop a module into `AioOCR/engines/api/` or `AioOCR/engines/local/` defining an `OCRPlugin` subclass that implements
`_run(image)` and returns word dicts (`makeWord(text, left, top, width, height)`). Nothing else changes:
discovery, option forms, CLI, panel and desktop pick it up. Optionally add a `[engines.YourClass]` section to
`engines.toml`.

## Engine kwargs the gateway sets

`image` (bytes, per page) · `language` · `timeout` · `retries` · `api` / `apiList` (decrypted credentials)
· `endpoint` · `model` · `prompt` · `proxy` - plus provider `options`, member `option_overrides` and request
`options`, merged in that order.

## Runtime demotion

If an engine imports but its runtime dependency is missing (`engine_missing`), it is marked unavailable in
the registry and the database with the hint it printed, so `auto` routes skip it until the next rescan.
