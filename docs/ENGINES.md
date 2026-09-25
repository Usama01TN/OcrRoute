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

## Full edition (EasyOCR + PaddleOCR built in)

`python scripts/install_full_edition.py && python scripts/build_executable.py --edition full` builds it locally;
CI builds it for every platform. Details that make the two engines coexist with the rest of OcrRoute:

- PyTorch comes from the CPU-only index on Linux (the PyPI Linux wheel pulls ~2.5 GB of CUDA libraries).
- PaddleX (PaddleOCR 3) caps NumPy below 2.4 and pins OpenCV 4.10.0.84. The Full edition keeps exactly one OpenCV,
  the **headless** contrib build: the GUI builds ship Qt libraries that break PyQt5 on Linux.
- PaddleX checks for the distribution name `opencv-contrib-python`. `ocrroute.opencv_alias` answers that lookup with
  the installed headless contrib build (same `cv2` module); a genuinely installed GUI build always wins.

## Deep-learning engines: install on demand

Seven local engines need a deep-learning framework. They are **not bundled in the stand-alone executables**:
together they add several GB (a release file is capped at 2 GB) and pin mutually incompatible versions (Surya pins
OpenCV 4.11 while the others use OpenCV 5; keras-ocr needs `imgaug`, which does not work with NumPy 2).

| Engine | Framework | Install |
|---|---|---|
| EasyOCR | PyTorch | `pip install "ocrroute[easyocr]"` |
| SuryaOcr | PyTorch (Surya 0.17.x; bundled in Full) | `pip install "ocrroute[surya]"`; Surya 2 with a vLLM / llama.cpp server: `"ocrroute[surya2]"` |
| GlmOcrHF | PyTorch (transformers) | `pip install "ocrroute[transformers]"` |
| OlmOcrLib | PyTorch (transformers) | `pip install "ocrroute[olmocr]"` |
| PaddleOcr | PaddlePaddle | `pip install "ocrroute[paddle]"` |
| CalamariOcr | TensorFlow | `pip install "ocrroute[calamari]"` |
| KerasOcr | TensorFlow (legacy, NumPy < 2) | separate environment: `pip install "ocrroute[keras]"` |

With a pip installation you can also click **Install** on the engine's card in the Engines page, or run
`ocrroute engines install EasyOCR`. OcrRoute runs pip in its own environment and re-runs discovery, so the engine
becomes available immediately, without a restart. On an operating-system Python that refuses pip (PEP 668: Debian,
Ubuntu, Homebrew), the install is refused with instructions: use a virtual environment
(`python3 -m venv ~/.ocrroute/venv`), or opt in explicitly with `OCRROUTE_PIP_ARGS="--user --break-system-packages"`.

Mistral OCR needs only the small `mistralai` SDK, which is bundled (and in the `api` extra).
AioOCR's own "Could not import ..." messages are captured instead of printed; set `OCRROUTE_VERBOSE_DISCOVERY=1` to
see them on stderr. The Engines page and `ocrroute doctor` show the same information per engine.

## OmniRoute (local AI gateway)

`OmniRouteOcr` sends images to an [OmniRoute](https://omniroute.online) gateway you run yourself
(`npm install -g omniroute`, then `omniroute setup` and `omniroute`). OmniRoute exposes one OpenAI-compatible endpoint in
front of 339+ providers (90+ with free tiers) and falls back automatically when one fails or hits its quota.

1. Start OmniRoute and connect at least one vision-capable provider in its dashboard (http://localhost:20128).
2. In OcrRoute, open **Providers**, pick *OmniRoute (local AI gateway)* and add the dashboard API key as a credential.
   Set the `base` option if the gateway is not on `http://localhost:20128`, and `model` to pin one routed model
   (default `auto`: OmniRoute chooses and falls back).

Auto routes use OmniRoute only once it has a credential, so installations without a gateway never pay a failed
attempt. The image leaves the machine (OmniRoute forwards it to the provider it picks), so OmniRoute is never part of
`auto/private`. OcrRoute listens on 20256 and never binds 20128, so both run side by side. Costs are billed by the
providers you connect in OmniRoute, not by OcrRoute (its cost estimate for OmniRoute is 0).
