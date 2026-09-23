# Tools - reserved section

The **Tools** subsystem is a deliberate, wired, empty scaffold for future post-processing extensions.
In this release:

- `ocrroute/tools/base.py` defines the abstract `ToolPlugin` (`name`, `version`, `description`, `input_kinds`,
  `output_kinds`, `option_schema`, `run(result, **options)`, `getLastError/setLastError`). **No subclass ships.**
- `ocrroute/tools/registry.py` auto-discovers `ToolPlugin` subclasses under `ocrroute/tools/builtin/` - a package
  containing only `__init__.py`, so the registry correctly returns `[]`.
- Tables `tools` and `tool_runs` are created by migration `0001_initial`, indexed and unused.
- `GET /v1/tools` → `{"tools": [], "reserved": true, "note": …}`; `GET /v1/tools/{id}` → 404;
  `POST /v1/tools/{id}/run` → **501** `tools_reserved`.
- `pipeline.postprocess.apply_tools(result, chain)` is an identity function for an empty chain (tested).
  `routes.tool_chain` exists and stays empty.
- Web panel `/panel/tools` and the desktop **Tools** page show the same empty state with a disabled *Add tool*
  button. The nav entry is never hidden.

## Future tools (ideas, not commitments)

Translation, PII redaction, table reconstruction, spell/layout correction, entity extraction, document
classification, summarisation, template diff, barcode/QR reading, signature detection, language detection.

## How a tool would be added later

Create `ocrroute/tools/builtin/my_tool.py` with a `ToolPlugin` subclass. The registry lists it, `/v1/tools`
exposes it, a Route's `tool_chain` can reference `{"name": "my_tool", "options": {...}}`, and `apply_tools`
executes the chain after OCR. No gateway code changes are required.
