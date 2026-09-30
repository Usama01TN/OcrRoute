# Publishing OcrRoute on PyPI

The package name is `ocrroute` (free on PyPI as of 2026-09-30). The wheel contains `ocrroute` and the `AioOCR`
engine library with all its plugins, templates, translations and fonts; `pip install ocrroute` gives the `ocrroute`
command.

## Automatic (recommended): trusted publishing

`.github/workflows/publish.yml` builds, checks (`twine check`), smoke-tests the wheel in a clean environment and
uploads it whenever a GitHub release is published. No API token is stored: PyPI trusts this repository's workflow.

One-time setup:

1. Create a PyPI account (https://pypi.org/account/register/) and enable two-factor authentication.
2. Before the first release, register a **pending publisher**: https://pypi.org/manage/account/publishing/ ->
   *Add a new pending publisher* with PyPI project name `ocrroute`, owner `Usama01TN`, repository `OcrRoute`,
   workflow `publish.yml`, environment `pypi`.
3. In GitHub: *Settings > Environments > New environment* named `pypi` (optionally require a reviewer).
4. Push a new version to `main`: the Build workflow publishes the GitHub release, the Publish workflow uploads it
   to PyPI. Check https://pypi.org/project/ocrroute/ a few minutes later.

To try TestPyPI first: register the same pending publisher on https://test.pypi.org (environment `testpypi`),
create that environment in GitHub, then *Actions > Publish to PyPI > Run workflow* with "TestPyPI" ticked.

## By hand

```bash
python -m pip install --upgrade build twine
rm -rf dist && python -m build
python -m twine check dist/*
python -m twine upload dist/*            # username: __token__, password: a PyPI API token (pypi-...)
```

Then `pip install ocrroute` in a fresh environment and run `ocrroute version`.

## Before each release

- `ocrroute/version.py` and `pyproject.toml` carry the same version; PyPI never accepts the same version twice.
- The README uses absolute GitHub URLs for images and docs, so it renders on the PyPI page.
- Heavy engines are extras (`ocrroute[easyocr]`, `[paddle]`, `[surya]`, `[transformers]`, `[desktop]`...); the core
  install stays small.
