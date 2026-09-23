# Contributing

## Layout

```
AioOCR/          the engine library, imported as given - never edited here (send fixes upstream)
ocrroute/        the gateway
  enginelib.py     the only bridge to AioOCR (discovery, hot rediscovery, module aliasing)
  catalog/         engine catalogue + curated metadata (engines.toml)
  routing/         candidates, strategies, breaker, consensus, router
  pipeline/        input guard, preprocessing, post-processing, exporters, overlay
  runtime/         executor, cache, limits, jobs, maintenance, doctor, app context
  api/             FastAPI (/v1) · panel/  web control panel · desktop/  PyQt5 app · cli/  Typer
  i18n/            shared JSON catalogues (en, fr, es, de, ar)
  tools/           reserved, intentionally empty extension point
  compat/          OcrBase shim (subclass of AioOCR's) and py23 helpers
docs/            architecture, routing, API, engines, deployment, security, i18n, tools, decisions, changelog
tests/           unit · integration (API, panel, CLI, against fake engines) · desktop (offscreen Qt)
```

## Style (matches AioOCR)

`# coding=utf-8` + module docstring + `from __future__ import absolute_import, division, print_function`;
`class X(object)`; `super(Class, self).__init__(...)`; private state in `self.__m_name` with `getName()`/`setName()`
accessors (snake_case `property()` aliases are fine for attribute-style reads); camelCase methods;
`:param:`/`:return:` docstrings; `'…'.format()` - never f-strings; no dataclasses; no `raise … from` (use
`py23.raiseFrom`); no keyword-only `*` markers. Type annotations only where SQLAlchemy `Mapped`, Pydantic,
FastAPI or Typer read them at runtime.

## Adding an OCR provider

Drop a module defining an `OCRPlugin` subclass into `AioOCR/engines/api/` or `local/`. The running gateway
detects it on the next rescan (`engine_rescan_minutes`, the *Rescan* button, or `ocrroute engines refresh`),
seeds a provider for it, and both UIs list it. Optional metadata goes in `ocrroute/catalog/engines.toml`.

## Before a pull request

`make lint && make test`, add a line to `docs/CHANGELOG.md`, and add new UI strings to every catalogue.
