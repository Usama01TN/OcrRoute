# Tools - reserved, intentionally empty

This package is a scaffold for future **post-processing extensions**. In this release it ships
**no working tool**, and the `Tools` pages in the web panel and the desktop app show an empty state.

## Contract

A future tool is a module in `ocrroute/tools/builtin/` defining a subclass of
`ocrroute.tools.base.ToolPlugin`:

```python
class MyTool(ToolPlugin):
    name = 'my_tool'
    version = '1.0.0'
    description = 'What it does.'
    option_schema = [{'name': 'level', 'type': 'integer', 'default': 1}]

    def run(self, run_result, **options):
        # transform and return a unified OCR result dict
        return run_result
```

The registry (`ocrroute.tools.registry`) auto-discovers it; the API (`/v1/tools`) lists it; a Route's
`tool_chain` may reference it; `pipeline.postprocess.apply_tools` executes the chain after OCR.

## Candidate future tools (ideas, not commitments)

translation of extracted text · PII redaction · table reconstruction · spell/layout correction ·
entity extraction · document classification · summarisation · diff against a template ·
barcode/QR reading · signature detection · language detection.
