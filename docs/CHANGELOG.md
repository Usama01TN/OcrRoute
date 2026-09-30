# Changelog

## 0.8.0 - 2026-09-28

### In short

- **Languages**: one vocabulary for every engine (`auto` or ISO codes); each engine translates to its own format.
  Every engine answers the picker (list, prompt hint, or fixed script); several languages at once where supported,
  with EasyOCR's combination rules. Baidu publishes 25 languages; RapidOCR picks its model by language.
- **Models and prompts**: a model picker for the 33 engines that take one (known models, live list from the
  provider's account, any id accepted); the extra prompt shown only where it applies; Batch carries both.
  Providers form: each setting once.
- **Cluster**: credential status shared across servers (a key over quota on one server is skipped everywhere).
- **Fixes**: real engine names in the catalog; images reach every cloud engine as real PNG/JPEG (OCR.Space engine 3
  returned no text for GIF input) and OCR.Space shrinks uploads over the plan's limit; empty option values keep
  engine defaults; Baidu kept the requested language; TensorFlow start-up noise silenced; macOS sync stalls fixed.

### Details

- **Packaging for PyPI**: `pip install ocrroute` (the wheel carries the AioOCR engine library, templates, translations
  and fonts; verified from a clean environment: 56 engines, server and panel run). PEP 639 license metadata, project
  URLs, classifiers, absolute README links. `publish.yml` uploads to PyPI on each GitHub release with trusted
  publishing (`docs/PUBLISHING.md`).

- **Fix: OCR.Space engine 3 returned no text ("Overlay requested but no regions were detected") for a GIF unless a
  preprocessing option was ticked.** Cause: for in-memory input, `imageBytes()` returned the original bytes and 21
  cloud engines labelled them `image/png`; a GIF (or WebP, BMP, TIFF) thus reached the API mislabelled. Engines 1 and
  2 sniff the real format, engine 3 does not, and strict APIs (Claude, Gemini) reject the mismatch. `imageBytes()`
  now keeps PNG and JPEG as they are and turns every other image format into PNG (first frame of an animated GIF),
  `imageMime()` / `imageDataUrl()` label what is really sent, PDFs pass through, and OCR.Space converts a file path
  the same way. Verified end to end: a GIF sent through OcrRoute arrives at the provider as a real PNG.
- OCR.Space: uploads larger than the plan's limit are shrunk first (new option `maxBytes`, default 1 MB, the free
  plan's limit; 0 disables): re-encoded as JPEG, then downscaled step by step.
- Fix (CI): a unit test assumed no network and expected OpenRouter's model list to be the known one; on online runners
  the live catalogue answered. The fallback is now tested deterministically (a failing live call falls back to the
  known list) and a live answer is accepted.
- **Providers form: each setting once.** The separate "Model" box (the provider's `model` column) duplicated the
  engine option `model`, and the option silently won; the form now has one model field (the engine option, with the
  known-model suggestions and live refresh) and the column follows it. "Language" was a free-text `en` box: it is the
  language dropdown (with "Also:" for engines that read several), labelled "Default language: used when a request
  sends none or auto". The "Custom endpoint" label no longer talks about "any OpenAI-compatible base URL".
- **Credential status shared across the cluster.** A key marked exhausted on one server (quota, rate limit or auth
  error) is skipped by every server within seconds: members report their exhausted keys with each poll (a member that
  just exhausted one polls at once), the primary merges and applies them and answers with the merged list (on 200 and
  304). Only the timestamp travels, keyed by credential id plus a fingerprint of the secret, so a status applies only
  where the secret is identical and never blocks a replaced key; a known later time is never moved earlier. Verified
  with a primary and two members: a key that failed on one member was skipped by the others in a single attempt.
- Empty option values (`ocrPrompt: ""`, `model: ""`, a blank extra prompt) are treated as "not set" on every path
  (provider options, route overrides, request options), so an engine always keeps its built-in default; booleans and
  0 are kept. A plugin created without a language now uses its own default (`auto` for engines that detect it: a
  vision-language engine no longer gets an "English" hint by default; `en` for Tesseract).
- **Fix: engine names in the catalog.** `OpenRouterOcr` was labelled "OpenAI-compatible VLM (custom endpoint)" with a
  wrong vendor: it is **OpenRouter**. 36 engines had no catalog entry at all and showed names mangled from their class
  names ("Glm OCR H F", "M M OCR", "Tr OCR", "Qwen2 Vl2b OCR", "Api4 Ai OCR"...). Every one of the 56 engines now has
  its real name, vendor and homepage, taken from the plugin's own description (e.g. "GLM-OCR (local)" by Zhipu AI,
  "MMOCR (OpenMMLab)", "TrOCR (printed)" by Microsoft, "Qwen-OCR (Alibaba Cloud Model Studio)", "NVIDIA
  Nemotron-OCR", "AI/ML API (Mistral OCR route)", "OCR - Extract text (RapidAPI)").
- **Model picker for the 33 engines that take a model** (cloud VLMs, Mistral OCR, Nemotron, local HF models...).
  Every plugin declares `DEFAULT_MODEL` and its known `MODELS` (from its own constants and documentation), exposed by
  `getModels()` and `GET /v1/engines/{id}/models`; any other model id is accepted. **Live listing** with `?live=1` and
  a `provider_id`: the plugin asks the provider's catalogue through that provider's credential (ChatGPT, Grok, Groq,
  OmniRoute, SiliconFlow via their existing discovery; new for OpenRouter, Gemini, Claude and Mistral), likely vision
  models first. The Playground, Batch and Providers pages and the desktop app show a model box with the known models
  as suggestions, the default as placeholder, and a Refresh button for the live list; empty = the engine's default.
- **Extra prompt where it applies**: the Playground and desktop prompt fields (and a new one on the Batch page) are
  shown only for engines that use a prompt (the vision-language engines) or for routes; the Providers form edits
  `prompt` / `ocrPrompt` in a multi-line box. Batch jobs carry the prompt and options into each request; the batch
  spec accepts `language` as a string or a list (the picker sends a string for one language).
- **Languages: one vocabulary for every engine.** You always use canonical codes (`auto`, `en`, `ar`, `fr`, `zh`,
  `zh-Hant`...); each engine translates them into its own format (`eng`, `fra`, `ch_sim`, `FRE`, `arabic`, a language
  name in a prompt...). Every engine now answers the language picker (`GET /v1/engines/{id}/languages`), in one of
  three modes: a **list** (Tesseract, EasyOCR, PaddleOCR, OCR.Space per engine 1-3, Google Vision, ScanDocFlow, Baidu,
  RapidOCR), a **hint** (25 vision-language engines: they detect the language; a chosen language is sent to the model as
  "The text is in Arabic: read it in that language and do not translate it"), or **fixed** (engines whose models read a
  set script, e.g. TrOCR / keras-ocr / Nougat: English; GOT-OCR, MMOCR, OpenOCR: English and Chinese), which now say
  what they read. The base plugin's `getLanguages()` stays an empty list for engines without a language setting.
- **Several languages at once** where the engine reads them in one run: Tesseract (`eng+fra+ara`), EasyOCR, Google
  Vision (hints) and the vision-language engines ("The text is in English, French and Arabic"). Engines declare it
  (`MULTI_LANGUAGE`, reported as `multiple` by the languages endpoint); single-language engines (OCR.Space, Baidu,
  PaddleOCR, RapidOCR...) keep one. The Playground, Batch and desktop pickers show an "Also:" row with removable chips
  and a "+ Add a language" dropdown; the request carries the list. **EasyOCR's combination rules** are enforced
  (`LANGUAGE_GROUPS`): Chinese, Japanese, Korean, Thai, Tamil, Telugu and Kannada only with English; Arabic-script,
  Devanagari, Bengali and Cyrillic languages within their script plus English; Latin-script languages freely. The adder
  only offers allowed combinations. EasyOCR's plugin now imports the library lazily (it can be described without it).
- **All 56 engines audited** for how the language really reaches the request: 8 translate a list (Tesseract, EasyOCR,
  PaddleOCR, OCR.Space, Google Vision, ScanDocFlow, Baidu, RapidOCR), 27 vision-language engines take a prompt hint
  (including OpenRouter, OmniRoute, GLM-OCR and olmOCR, the last two added in this pass), 20 read a fixed script or have
  no language parameter (Mistral OCR, AIML's Mistral route, Nemotron, API Ninjas, Api4AI, RapidAPI, EasyOCR.org,
  Nanonets: their requests carry only the image), Surya detects. The table is generated into `docs/ENGINES.md`.
  Verified end to end: a request with `["fr", "ar"]` makes OmniRoute send "The text is in French and Arabic".
- **Baidu OCR** publishes its languages (25, all documented `language_type` values, `auto` = `auto_detect`) (`auto` = `auto_detect`) and no longer drops the requested language (it was
  removed before the base class saw it, so every request went out as English).
- **RapidOCR** selects its recognition model from the language (`Rec.lang_type`: `arabic`, `latin`, `cyrillic`,
  `devanagari`, `japan`, `korean`...; Chinese + English stay on the default model); an explicit `engineParams` override
  wins. The list is checked against the installed `rapidocr.LangRec` when available.
- Playground, Batch and desktop pickers explain hints ("sent to the model as a hint") and fixed scripts ("reads
  English, no language setting"); `auto` is preselected when available, else `en`. Tested in a real browser across
  Gemini, TrOCR, OCR.Space engines 1/2 and Baidu.

## 0.8.0 - 2026-09-27

- **Languages, one format everywhere.** Always send canonical codes (`auto`, `en`, `ar`, `fr`, `zh`, `zh-Hant`...;
  any spelling is accepted: `eng`, `English`, `fre`, `chi_sim`, `en-US`...). Each engine translates them into its own
  format: OCR.Space `ara` / `chs` / `fre` (per engine), Tesseract `ara` / `chi_sim`, EasyOCR `ch_sim` / `rs_latin`,
  PaddleOCR `ch` / `japan` / `chinese_cht`, Google Vision BCP-47 hints, ScanDocFlow `chi` / `ara`. The requests'
  default is now `auto` (the engine's own default when it cannot detect languages: English).
- **AioOCR plugin API** (`engines/ocrplugin.py`, shared table `engines/languages.py`): `getLanguages(engine=None)`
  (empty list by default: the engine takes no language setting), `getEngines()` (variants whose languages differ,
  e.g. OCR.Space 1-3), `defaultLanguage()` (`auto` when supported, else `en`), `toEngineLanguage()`,
  `describeLanguages()` and `getEngineLanguages()`. Language lists come from the libraries' own tables (EasyOCR 1.7.2,
  PaddleOCR 3.3.0, Surya 0.17), OCR.Space's documentation and, for Tesseract, the installed traineddata.
- **Language pickers** replace the text fields in the Playground, Batch and the desktop Scan page: the chosen engine's
  languages (`auto` preselected when available, else `en`); an **Engine variant** picker for OCR.Space whose choice
  changes the list; "automatic" for engines that detect the language themselves; every language for routes.
- An unsupported language is replaced by the engine's default and explained in the routing trace ("Tesseract: Arabic
  not available (install its traineddata to use it) -> used English").
- API: `GET /v1/languages`, `GET /v1/engines/{id}/languages?engine=`.
- Fixes: ScanDocFlow ignored the requested language (set before the base initialiser, which reset it to English);
  OCR.Space sent the raw value (`en`, a list) instead of its 3-letter codes.

## 0.7.1 - 2026-09-25

- **Desktop app on ManyQt** (https://github.com/Usama01TN/ManyQt): every Qt import goes through ManyQt, so the app
  runs on PyQt5, PyQt6, PySide2 or PySide6 (`QT_API`); `pyqtSignal` became the portable `Signal`. The executables ship
  PyQt5 only (`QT_API=pyqt5` pinned, other bindings excluded), and every desktop build now self-tests its Qt layer.
- README: screenshots (Playground, Engines) and a "Support the project" section (ba9chich, Ko-fi).
- **Cluster sync from the dashboard**: new **System > Cluster sync** page to choose Off / Leader / Follower and apply it
  live (no restart): token generation and copy, this server's addresses, a warning when it only listens on 127.0.0.1,
  **Test connection** before saving, live status and **Sync now**. `.env` settings keep priority (the page is then
  read-only). API: `GET/PUT /v1/sync/config`, `POST /v1/sync/token`, `POST /v1/sync/test`. Translated into 9 languages.
- **Sync across tunnels and many servers**: the leader's page lists its addresses best first (running tunnels, public
  URL, LAN) and flags temporary tunnel URLs (`*.trycloudflare.com`, `*.ngrok-free.app`) that change on restart; the
  127.0.0.1 warning no longer shows when a tunnel is up. Followers identify themselves on every poll (server id, name,
  version, digest) and the leader lists them with last contact and up-to-date status.
- **Server cards with full CRUD** on the leader's Cluster sync page: add a server (its own token, shown once), edit
  (name, notes), pause / resume, new token, delete (revokes it; shared-token servers are refused by server id). Shared-
  token followers appear as cards automatically; secrets travel encrypted with each server's own token.
- **Fix: `UNIQUE constraint failed: users.username` during sync** when a follower had a local panel user (or provider,
  route, key) with the same unique value as a leader row under another id: local rows missing on the leader are now
  removed and flushed before the leader's rows are inserted.
- **Fix: the Cluster sync page switched back to the saved role every 10 seconds** (e.g. "Leader") while you were editing:
  the periodic refresh now updates only the status and server cards, never the form, and pauses while there are unsaved
  edits. Followers explain refusals from the leader (paused, removed, token replaced).
- **Fix (macOS): cluster sync stalled and servers would not stop.** Finding this machine's addresses used a hostname
  lookup (`getaddrinfo(gethostname())`), which goes through mDNS on macOS and can block for many seconds; sync needs the
  addresses on every poll (member), every snapshot (primary) and in the address watcher, so members never synced and
  shutdown waited on blocked threads. The lookup is gone (the default-route trick and the interface list find the same
  addresses without DNS) and the result is cached for 30 s. Reproduced on Linux by making hostname lookups as slow as on
  macOS: the old code failed exactly like CI, the fixed code passes every cluster test.
- **Fix: `Exception in thread ocrroute-sync-watch ... AttributeError: 'NoneType' object has no attribute 'is_set'`**
  after changing the cluster role: the old address watcher read the manager's stop event, which the reconfiguration had
  just cleared. Each watcher now owns its stop event (tested with five quick role changes).
- **Pasted panel / API links are cleaned**: `http://host:20256/panel`, `.../panel/cluster`, `.../v1/docs` become the
  server's base URL (a reverse-proxy prefix such as `/ocr` is kept), in join codes, member addresses and the public URL.
  Loopback addresses (`127.0.0.1`, `localhost`) come last in join codes and are marked "this computer only".
- **"is not a sync leader" is explained**: the member asks the address what it is and says whether it is this server
  itself, a member, or a server that is not the primary of a cluster.
- **TensorFlow start-up noise removed for good**: newer TensorFlow prints "oneDNN custom operations are on" and the absl
  "All log messages before absl::InitializeLog()" line from C++ (ignoring `TF_CPP_MIN_LOG_LEVEL`). While engines load,
  OcrRoute now filters file descriptor 2 itself and passes every other line (real warnings, errors) through.
  `OCRROUTE_VERBOSE_DISCOVERY=1` shows everything.
- **Cluster sync reworked into a standard cluster model** (like Proxmox "create / join cluster", Docker Swarm join
  tokens): **Create a cluster** on the primary, **Add servers** (one or many) to get one **join code** each, paste it on
  the other server under **Join a cluster**. No roles, tokens or addresses to type. Every server shows the member list
  with statuses; on the primary each member card has **Edit** and **Remove**, plus Show join code, New join code and
  Pause. Members can **Leave cluster**; the primary can **Delete cluster**. Join codes carry all primary addresses
  (failover, address updates) and a checksum (damaged copies are refused with a clear message).
- Fix: a server joining for the first time failed the primary's proof check (its card was not linked to a server id
  yet); the join now identifies its card by the stored token hash. A removed server is told it was removed instead of
  "invalid token".
- **Several leader addresses on a follower**: add one or many (address + optional label), each card with **Edit**,
  **Delete**, **Make primary** and **Test**, and its own health (last success / last error, in use). The follower uses
  the first that works in your order, stays on a working backup and retries the primary every 10 minutes; addresses
  learned from the leader are appended (marked) instead of replacing yours. Changes apply at once on a server that
  already follows. `.env`: comma-separated `OCRROUTE_SYNC_LEADER_URL`. API: `leader_urls` in `PUT /v1/sync/config`.
- **Add one or many followers at once** (rows with name, optional address, notes; Enter adds a row); every new token is
  shown once with **Copy all**. Every follower card has **Edit** (in-card form: name, address, notes) and **Delete**
  (in-card confirmation), plus Pause / Resume and New token. No browser pop-ups (`prompt` / `confirm`) any more.
- Fix: the copy buttons failed on plain `http://` pages (LAN addresses) and when clipboard permission is refused;
  they fall back to the classic copy, or ask to press Ctrl+C.
- **Leader address changes are handled automatically** (quick-tunnel restarts: "Failed to resolve ...trycloudflare.com").
  Followers report how to reach them; the leader pushes its new address when it changes (watcher, ~20 s) or on **Notify
  followers**. Followers remember all leader addresses and fail over, updating the saved address. Pushed / learned
  addresses are verified by challenge / response (HMAC of a nonce with the follower's token) before the token is sent,
  so an impostor learns nothing. Card tokens are also stored encrypted for these proofs.
- **Plain-language sync errors** (DNS gone, quick-tunnel URL changed, refused, timeout, certificate, tunnel up but
  leader stopped) instead of raw `HTTPSConnectionPool` text; each distinct error is logged once, then every 10 minutes.
- **Fix: each server's public URL (Endpoints page) was overwritten by the leader's** through settings sync; it is now
  per-server, like the sync state.
- TensorFlow / gRPC C++ start-up noise ("oneDNN custom operations are on", "All log messages before
  absl::InitializeLog()") is hidden by default (`TF_CPP_MIN_LOG_LEVEL=2`); set it to 0 to see it.
- Server cards show the follower's reported addresses and the last address notice; the follower status shows the
  address in use and the leader's known addresses. Tested end to end with a killed "tunnel", a dead DNS name, and an
  impostor leader.
- **Fix: "fill is not defined" on the Cluster sync page.** An editing step deleted the middle of the page script (form
  filling, live refresh, card actions); the script still parsed, so the check missed it. The script was rewritten, and a
  new test drives the page in a real headless Chromium: no JavaScript error, the chosen role survives the refresh, and
  every server-card action works from the buttons.
- Fix: `server_id` (Endpoints page) was the same for every installation on a host because it read a misnamed settings
  attribute and fell back to the hostname; it now derives from the secret key file (or the home path).
- **Automatic releases**: a push to `main` whose `ocrroute/version.py` carries a version without a `v<version>` tag is
  published automatically once every build succeeds (the release creates the tag). Pushes with an already-released
  version publish nothing. To release: bump `__version__`, push. A new "New version?" job checks the tag; the
  "Why no release?" job explains every outcome, including "already released: bump the version".
- Publishing runs one at a time (`concurrency: publish-release`), so two quick pushes cannot race on one version.
- Tag pushes and **Run workflow** with *publish* (and *allow_incomplete*) keep working as before.

- **Fix (CI, Full edition)**: `test_surya_collection_skips_its_demo_scripts` failed wherever Surya is installed. It read
  every flag value and flagged the `--exclude-module surya.scripts` / `surya.debug` values themselves; locally it passed
  only because Surya was absent and the function returned nothing. The test now uses a fake `surya` package, so it runs
  everywhere, and checks included and excluded modules separately.
- `suryaArgs()` lists Surya's modules from its files instead of `pkgutil.walk_packages`, which imported every
  subpackage at build time and silently skipped any that failed to import.
- **Releases**: publishing still happens only for a version tag (`git tag v0.7.1 && git push origin v0.7.1`) and only
  when every build job succeeds; a push to `main` skips it by design. New: **Run workflow** with *publish* ticked
  publishes `v<version>` from that run, and the release job refuses a tag that does not match
  `ocrroute/version.py`. The release notes now describe the Full edition files.
- **Fix: "Publish release" was skipped even when a release was requested.** An `if:` without a status function gets an
  implicit `success()`, so one failed build job (of eight) silently skipped publishing. The condition is now explicit,
  a new **"Why no release?"** job always runs and states the reason in the run summary, and *Run workflow* with
  **publish** + **allow_incomplete** publishes the builds that succeeded as a pre-release. Releases now take only
  `ocrroute-*` artifacts: the `verify-logs-*` diagnostics of failed jobs were being downloaded into releases.

## 0.7.0 - 2026-09-25

- **Cluster sync** (`docs/CLUSTER.md`): one leader, any number of followers. Followers mirror providers, credentials,
  routes, API keys, panel users, runtime settings and engine enablement; runs, usage, cache, logs and health stay per
  server. Configure with `OCRROUTE_SYNC_ROLE`, `OCRROUTE_SYNC_TOKEN`, `OCRROUTE_SYNC_LEADER_URL`,
  `OCRROUTE_SYNC_INTERVAL_SECONDS`; `ocrroute sync token` generates a token.
- Secrets travel encrypted with a key derived from the sync token and are re-encrypted with each follower's own
  master key. ETag digests make unchanged polls a `304`; failures back off.
- Followers refuse configuration edits (`409`, naming the leader) and do not auto-seed providers.
- API: `GET /v1/sync/snapshot` (sync token), `GET /v1/sync/status`, `POST /v1/sync/now` (admin). CLI: `ocrroute sync status`.
- End-to-end test with a real leader and follower process: data, deletions, a re-encrypted credential used in a real
  OCR request, the read-only guard, and a follower with a wrong token.

## 0.6.1 - 2026-09-25

- **OmniRoute engine integrated** (`AioOCR/engines/api/omniroute.py`, class `OmniRouteOcr`). OcrRoute's catalog now
  describes it properly: name "OmniRoute (local AI gateway)", vendor, handwriting / tables / overlay support, default
  model `auto`, homepage. It needs its dashboard key as a credential before auto routes use it, and it is never part
  of `auto/private` (images are forwarded to the provider OmniRoute picks).
- Integration test against an in-process stand-in gateway: key forwarding, `model=auto`, image upload, 0-1000
  `box_2d` grid scaled to pixels, exclusion from `auto/private`.
- `docs/ENGINES.md`: OmniRoute setup.

## 0.6.0 - 2026-09-24

- **Surya OCR in the Full edition** (Linux, Windows, macOS arm64). Bundled as **Surya 0.17.1**, the last release that
  runs OCR entirely in PyTorch: Surya 2 (0.20+) delegates OCR to a vLLM (NVIDIA + Docker) or llama.cpp server. The
  AioOCR engine supports both; `pip install "ocrroute[surya2]"` for Surya 2 with your own backend.
- Surya is installed without pip's resolver: its exact pins (`opencv-python-headless==4.11.0.86`, `pypdfium2==4.30.0`,
  `pillow<11`, `pre-commit`) would replace the single OpenCV that PaddleX needs and OcrRoute's pypdfium2. Only the
  dependencies it imports are installed, with `transformers>=4.56.1,<5` (Transformers 5 arrived with Surya 2).
  Verified: every module Surya's engine path imports is installed; `flash_attn` / `torch_xla` are optional; the set
  resolves with PaddleOCR 3.7 / PaddleX 3.7.2 (Transformers 4.57.6, NumPy 2.3.5).
- Build: Surya's modules are collected without `surya.scripts` / `surya.debug` (Streamlit, datasets, boto3...);
  Transformers is bundled only when Surya is, with the metadata of every dependency it declares. The frozen self-test
  imports Surya's recognition and detection predictors and its engine; CI runs a real OCR through it.
- Not on macOS x86_64: Surya needs PyTorch >= 2.7.
- License note: Surya's model weights use a modified AI Pubs Open Rail-M license (free for research, personal use and
  startups under $5M; other commercial use needs a license from Datalab).

## 0.5.6 - 2026-09-24

- **Fix (Full edition, PaddleOCR on macOS x86_64)**: model downloads failed with `403 Forbidden` for
  `.../official_inference_model/paddle3.0.0/...`. Paddle's own model server (BOS) now refuses those Paddle-3.0 exports
  on every network, including GitHub's runners (0.5.4 wrongly routed Intel Macs to it). Paddle 3.0 now downloads from
  Hugging Face, whose PP-OCRv5 and auxiliary models were published for Paddle 3.0.
- **PaddleX 3.0.3 patches, applied when it is imported** (`ocrroute.paddleenv.installPaddleXPatches`, an import hook, so
  every process gets them): `PP-LCNet_x1_0_textline_ori` and other pipeline models are added to its Hugging Face
  list (they fell back to the dead server), and its 1-second "is Hugging Face reachable?" probe is replaced by a
  tolerant 10-second check. Newer PaddleX versions are left untouched.
- Self-test adds `paddleocr:model-sources`: in a Paddle 3.0 bundle, every OCR-pipeline model must resolve to
  Hugging Face.

## 0.5.5 - 2026-09-24

- **Fix (Full edition install on macOS x86_64)**: `install_full_edition.py` failed at its final `import paddleocr`
  check with `403 Forbidden` for `.../PaddleX3.0/fonts/PingFang-SC-Regular.ttf`. PaddleX 3.0 downloads that font at
  import time, and Paddle's server now refuses the URL on every network (GitHub's runners included), not just
  offline. The check ran in a fresh interpreter without OcrRoute's Paddle settings; it now gets them.
- **Bundled font**: OcrRoute ships DejaVu Sans (`ocrroute/assets/fonts`, Bitstream Vera license, license text
  included) and points PaddleX at it first, so the font is identical on every OS and never downloaded. It is part of
  the pip package and of both executable editions.

## 0.5.4 - 2026-09-24

- **Fix (Full edition, PaddleOCR on macOS x86_64)**: `Type of attribute: strides is not right` when loading models.
  Intel Macs stop at PaddlePaddle 3.0.0, while the newest PaddleOCR downloads PP-OCRv6 models exported for Paddle
  3.3. Intel Macs now pin the releases made for Paddle 3.0 (paddlex 3.0.3 + paddleocr 3.0.3, PP-OCRv5 models) and
  download from BOS (`PADDLE_PDX_MODEL_SOURCE=BOS`), which serves the `paddle3.0.0` exports; Hugging Face serves only
  the latest export.
- **PaddleX 3.0 offline**: it downloaded two fonts at import time (used only for visualisations), so PaddleOCR was
  unavailable offline, and during a build PyInstaller's module scan of PaddleX failed silently (0 modules, so
  `colorlog` and other PaddleX imports were not bundled). A system font is used instead
  (`PADDLE_PDX_LOCAL_FONT_FILE_PATH`), in the app and in the build environment.
- `ocrroute/paddleenv.py`: one place for the Paddle environment defaults (MKLDNN, model source, font).
- The Full build copies the metadata of every dependency PaddleX, PaddleOCR and EasyOCR *declare* (followed
  recursively), because PaddleX checks its `ocr` extra by distribution metadata at runtime.
- Self-test adds `paddleocr:requirements` (PaddleX's own OCR-requirements check) to the import and computation checks.
- The install script also installs `setuptools`, which Paddle 3.0 imports without declaring it.

## 0.5.3 - 2026-09-24

- **Fix (Full edition, PaddleOCR on Linux)**: `libmklml_intel.so: cannot open shared object file`. Paddle finds its
  native libraries through `site.getsitepackages()` and `FLAGS_mklml_dir`; in a frozen app both pointed at the
  build machine. A PyInstaller runtime hook (`scripts/pyi_rth_ocrroute_site.py`) now puts the bundle first in the
  site paths and exports `FLAGS_mklml_dir` for the bundled `paddle/libs`, which is collected at its exact path.
- **Fix (Full edition, PaddleOCR on macOS x86_64)**: `import paddle` crashed with `TypeError: sequence item 0:
  expected str instance` because Paddle 3.0 joins `site.USER_SITE`, which PyInstaller sets to `None`. The runtime
  hook gives it a real path.
- **Fix (PaddleOCR on Windows)**: `ConvertPirAttribute2RuntimeAttribute not support`, a Paddle 3.3 bug in its oneDNN
  (MKLDNN) CPU executor. OcrRoute defaults `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT` to off; set it to `True` to opt in.
- **Self-test runs real computations**: a Paddle CPU run, a PyTorch matrix product and a torchvision NMS operator,
  in addition to imports (the MKL failure imported fine and only broke when computing).
- **CLI**: `ocrroute ocr` keeps stdout for the result; engine chatter (EasyOCR's download progress bar) goes to stderr.

## 0.5.2 - 2026-09-24

- **Fix (Full edition, EasyOCR)**: the frozen self-test reported `RuntimeError: operator torchvision::nms does not
  exist`. torchvision 0.29 renamed its compiled extension to `_C_stable` / `image_stable` and loads it *by path*
  (`torch.ops.load_library`), so neither PyInstaller's analysis nor the community hook (which still lists
  `torchvision._C`) collected it, and torchvision silently swallows the load failure. The build now collects every
  native file of torchvision, plus the vendored `torchvision.libs/` (Linux) and `.dylibs/` (macOS) libraries, at
  their original relative paths, so the extensions' `$ORIGIN`-relative library paths keep working.
- The frozen self-test sets `TORCHVISION_WARN_WHEN_EXTENSION_LOADING_FAILS=1`, so any remaining extension load
  error is printed with its real cause instead of the generic "operator does not exist".

## 0.5.1 - 2026-09-24

- **Fix (Full edition)**: EasyOCR was bundled but failed to import inside the executables. EasyOCR imports
  `six.moves` without declaring `six`, and PyInstaller's `six.moves` handling left the real `six` module out of the
  bundle (`ModuleNotFoundError: No module named 'six'`). `six` is now bundled explicitly.
- **Frozen self-test**: after every Full build, `build_executable.py` runs the executable with
  `--ocrroute-selftest` and imports PyTorch, torchvision, EasyOCR, PaddlePaddle, PaddleX, PaddleOCR and both engine
  modules inside it; any failure aborts the build with the full traceback, so an incomplete bundle cannot be
  archived or released.
- **CI**: the verify step prints the actual import error for any engine the edition claims to bundle.

## 0.5.0 - 2026-09-24

- **Full edition**: new executables `ocrroute-server-full` and `OcrRoute-Desktop-Full` with **EasyOCR** (PyTorch CPU)
  and **PaddleOCR** (PaddlePaddle) built in, next to the lean ones. Built by CI on Linux, Windows, macOS arm64 and
  macOS x86_64 (PaddleOCR only: no PyTorch >= 2.3 for Intel Macs). CI runs a real OCR through every bundled engine
  and checks the recognised text, and guards the 2 GiB release-file limit. `version --json` reports the edition.
- **`ocrroute.opencv_alias`**: PaddleX refuses to start unless `opencv-contrib-python` is installed; the headless
  contrib build (same `cv2`, no Qt libraries that clash with PyQt5) now satisfies that check.
- **Fix (desktop executable)**: `RuntimeError: sys.stderr is None` at start. Windowed builds have no console
  streams; `faulthandler.enable()` required one. `ocrroute.stdio.ensureStreams()` now gives GUI processes real
  streams (`~/.ocrroute/logs/desktop.log`) before anything runs, and `faulthandler` is enabled only when possible.
  The crash-isolation probe reports through a file instead of stdout (windowed children have none) and never opens
  a console window on Windows.
- **Dashboard**: no more "EasyOCR / PaddleOCR unavailable" alerts for engines that are absent by design; the Engines
  page explains them (lean edition: points to the Full edition). Alerts remain for genuinely broken engines.

## 0.4.9 - 2026-09-24

- **Fix (macOS x86_64 executable)**: the frozen server failed with `Symbol not found: _SSL_get0_group_name`.
  cryptography 49+ publishes no macOS x86_64 wheel, so pip compiled it from source against the runner's newer
  OpenSSL, while the bundle carried the older `libssl.3.dylib` from Python. Intel Macs now use cryptography 48.x (the
  last universal2 wheel, which links OpenSSL statically; environment marker in `pyproject.toml`), and CI installs
  native packages from wheels only (`--only-binary`) and checks that cryptography imports before freezing.
- **Mistral OCR bundled**: `mistralai` added to the `api` extra and to the executables (49 of 56 engines built in).
- **Deep-learning engines install on demand**: per-engine extras (`easyocr`, `surya`, `transformers`, `olmocr`,
  `paddle`, `calamari`, `keras`), an **Install** button on the Engines page, `POST /v1/engines/{id}/install` and
  `ocrroute engines install <engine>`. Installation runs pip in OcrRoute's environment and hot-reloads the engine
  (no restart). PEP 668 (OS-managed Python) is detected and explained; `OCRROUTE_PIP_ARGS` is an explicit opt-in.
  In the executables these engines explain that they are not bundled and how to get them.
- AioOCR's "Could not import" console messages are captured (shown per engine in the UI; `OCRROUTE_VERBOSE_DISCOVERY=1`
  prints them). The CI verify step asserts Mistral OCR is bundled and lists the engines that are not, by design.

## 0.4.8 - 2026-09-24

- **CI fix**: the "Verify server binary" step still exited silently on failure. GitHub runs step scripts with
  `bash -e`, and `set -uo pipefail` does not disable `-e`, so a failing command ended the script before `check()`
  could read its exit code and print logs. The step now starts with `set +e -u -o pipefail`.
- **CI diagnostic**: the step first runs the engine-import probe directly (non-fatal) and reports how far imports
  get; if the interpreter dies, it names the module being imported. Probe output is included in the uploaded logs.

## 0.4.7 - 2026-09-24

- **Crash-isolated engine discovery**: a compiled dependency that crashes the interpreter on import (segfault or
  illegal instruction in a wheel built for another CPU) used to take the whole gateway down without a message,
  because it happens below Python where AioOCR's per-engine error handling cannot help. Frozen builds (and any
  install with `OCRROUTE_SAFE_DISCOVERY=1`) now probe engine imports in a child process first; a crashing module
  is disabled with the exact reason (for example "signal 11") and an explanation in the Engines page, and every
  other engine keeps working. The probe runs once per bundle, Python version and CPU architecture (cached in
  `~/.ocrroute/cache`). `faulthandler` is enabled in frozen builds, so native crashes print a Python stack.
- **CI**: the "Verify server binary" step explains every failure (command, exit code, signal name, stderr and
  stdout), warns when an engine was disabled by the probe, prints the server log when the health check fails, and
  uploads all logs as a `verify-logs-<platform>` artifact. Fixed `grep -c` failing the step when it counted zero.

## 0.4.6 - 2026-09-23

- **CI**: the macOS Intel build requested the retired `macos-13` runner label and queued forever. It now uses
  `macos-15-intel` (GitHub's last x86_64 macOS image); the Apple-silicon build uses `macos-latest`. Build jobs have a
  60-minute timeout.

## 0.4.5 - 2026-09-23

- **Fix (CLI, all platforms)**: `--json` output piped into a reader that stops early (`| head`) failed the command:
  exit 1 on Linux/macOS (broken pipe) and an `OSError: [Errno 22]` traceback on Windows, where a closed pipe is
  reported as EINVAL through colorama. Machine output is now plain UTF-8 JSON written directly to stdout (no Rich
  console emulation or colour codes), and a closed pipe ends the command quietly with exit 0; real I/O errors are
  still reported. The frozen entry point forces UTF-8 on Windows consoles.
- **CI**: the "Verify server binary" step no longer truncates output with `head`. It saves the full JSON,
  validates it (engine count, Tesseract present), and smoke-tests the frozen server by starting it and checking
  `/v1/health`.

## 0.4.4 - 2026-09-23

- **Fix (desktop)**: background results could be lost. `runAsync()` handed workers to Qt's thread pool without
  keeping a Python reference, so the garbage collector could destroy a worker's signal object mid-run and the
  scan / refresh / save never completed. Workers are now held until their `finished` signal is delivered
  (regression test runs 40 workers under forced garbage collection).
- **Fix (tests)**: the desktop scan test left its first polling timer running; through a late-binding closure it
  quit the next event loop early, so on fast machines (Windows CI) the responsiveness probe had no samples and
  `max()` of an empty list raised. Pollers are stopped and bound explicitly, the probe uses a precise timer with a
  minimum observation window, and it asserts it collected enough samples.
- Starlette's httpx deprecation warning is filtered in pytest.

## 0.4.3 - 2026-09-23

- **CI lint passes**: ruff's pyupgrade rules that contradict the project's Python 2/3-compatible style (`(object)`,
  `super(Class, self)`, `# coding=utf-8`, `from __future__`, `.format()`) are disabled in `pyproject.toml` with the
  reason documented next to each; import order, unused imports and whitespace fixed across the tree.
- **Fix**: ngrok authentication. A default `authenticate()` had been placed inside the `Ngrok` class and shadowed
  the real implementation, so authtokens were rejected. It now lives in the `Tunnel` base class (regression test added).

## 0.4.2 - 2026-09-23

- **Charts follow the theme in real time**: colours are Chart.js scriptable options evaluated at draw time, every
  chart is registered, and a theme switch calls `update()` on all of them (verified in Chromium by canvas pixel).
  New light/dark palettes, themed tooltips, legends, ticks and grid lines, rounded bars, filled lines.
- **Scrollbars**: thin rounded thumbs in both themes (WebKit/Blink and Firefox), hover/active states, sidebar bar
  hidden until hovered, smaller bars in tables, code blocks and dialogs; desktop QSS scrollbars restyled to match.
- **Theme toggle**: plain monochrome outline sun / moon icons that inherit the text colour.
- **Tab icon**: SVG favicon (light and dark variants, swapped with the theme), PNG and ICO fallbacks, Apple touch
  icon, web manifest, `theme-color`, and `/favicon.ico`; the desktop window uses the same icon.

## 0.4.1 - 2026-09-23

- **Light mode sidebar**: the sidebar now follows the theme (white in light mode, navy gradient in dark) in the web
  panel and the desktop app; the transition is animated.
- **Restart / Shutdown buttons fixed**: the sidebar is rendered twice (desktop + mobile drawer), so the buttons were
  bound by a duplicated id and the visible one had no handler. They are bound by `data-action` now, show progress,
  poll `/v1/health` until the server is back after a restart, and show a "Server stopped" screen after shutdown.
- **Translations**: 140+ more strings wrapped (headings, descriptions, table headers, dialogs, getting-started
  steps, time ranges, role legend) and translated in all 9 languages (397 keys each).

## 0.4.0 - 2026-09-23

- **Restart / Shutdown**: new `runtime/lifecycle.py`. The running uvicorn server registers itself; Shutdown drains
  and exits cleanly (code 0); Restart stops the server and `ocrroute serve` starts a fresh app in place on the same
  port (embedded desktop server likewise; multi-worker mode re-execs). Requests during a transition get 409.
- **One-click tunnels**: cloudflared and ngrok are downloaded into `~/.ocrroute/bin` (no admin rights) and started
  immediately; Tailscale uses the vendor installer. Token / login flows for ngrok and Tailscale
  (`POST /v1/endpoints/tunnels/{name}/auth`). Binaries in `~/.ocrroute/bin` are preferred over PATH.
- **User management**: roles enforced end to end (admin / operator with the new `manage` scope / viewer),
  `/v1/users` CRUD, last-admin guard, password reset, change-my-password, Settings UI.
- **OCR-aware routing**: strategies `confidence_first`, `script_aware`, `sticky`, `best_of_two`; a catalog of
  12 built-in `auto/*` routes (`GET /v1/routes/catalog`) usable as the `route` field with no setup; auto routes skip
  credential-less cloud providers, rank healthy and lightweight engines first (heavy deep-learning engines
  detected from their imports), and fall back sequentially when a parallel batch fails.
- **Routes page** rebuilt: auto-routing catalog, 4-step getting started, All / Intelligent / Deterministic tabs,
  route cards with enable toggle and actions.
- **Languages**: Italian, Portuguese, Russian, Chinese added (9 total, every key translated).
- **Styling**: every input, select, checkbox and radio styled consistently (native controls included).
- **Desktop**: resizable and expansive (splitter sidebar, scrollable pages, expanding policies, remembers
  geometry and maximized state), scroll areas transparent in both themes.

## 0.3.1 - 2026-09-23

- **Design system aligned with the "Fixed arena" fork**: blue accent (`#2563EB` / `#3B82F6`), always-dark navy
  gradient sidebar with light text, radial glow background, Inter typography, 14px card radii, frosted top bar,
  pill quick-nav. The console keeps the gateway layout (grouped sidebar with subtitles, top navbar with the single
  animated theme toggle, language dropdown, health and sign-out, breadcrumbs, pill tabs, Endpoints section).
- More motion: card enter animation, nav hover slide/scale, sliding tab indicator, gradient button shine,
  pulsing status dots, hover lift with accent-tinted shadow.
- Desktop adopts the same palette (navy sidebar, blue primary) in both themes.
- No feature changes; every existing behaviour is covered by the unchanged test-suite (67 tests).

## 0.3.0 - 2026-09-23

- **Endpoints section** (web + desktop + `/v1/endpoints`): active endpoints with one-click copy, every LAN address of
  the machine as `http://<ip>:<port>/v1`, a stable server id, tunnels (Cloudflare Quick Tunnel, Tailscale Funnel,
  ngrok) detected on PATH with install-command / enable / disable and captured public URLs, a manually configured
  public URL, and a global *Custom OCR prompt* injected into every VLM engine request. Sidebar footer gains
  Restart / Shutdown for the server process.
- **Console redesign**: grouped sidebar (Gateway / Routing / Observability / System) with icon, title and subtitle per
  entry and collapsible groups; top navbar with page title + subtitle, Quick nav (Ctrl+K), language dropdown with
  flags, a single animated sun/moon theme toggle, health and sign-out icons; breadcrumbs and pill tabs; subtle grid
  background; fade-up and stagger animations; coral accent with gradient action buttons in both themes.
- Desktop adopts the same palette and gains the Endpoints page.
- The given AioOCR library updated to the "Fixed arena" drop (adds the `OmniRouteOcr` engine); 56 engines detected.
- All em dashes removed from project sources; a test enforces the rule.

## 0.2.0 - 2026-09-22

- **Web panel rebuilt on Bootstrap 5** (vendored, offline): responsive grid, offcanvas sidebar and sticky top bar on
  phones/tablets, Bootstrap Icons, cards/KPI tiles, badges, toasts, light/dark/system via `data-bs-theme`.
- **Desktop redesign**: QtSvg vector icons (theme-aware, inverted on the active item), refreshed light/dark QSS
  (rounded controls, focus ring, alternating rows, custom scrollbars), page subtitles, connection pill in the status bar.
- **Cross-platform executables**: `scripts/build_executable.py` (PyInstaller) and `.github/workflows/build.yml`
  building Windows/macOS/Linux bundles, artifact links in the job summary, GitHub Release on tags.

- **Automatic provider detection**: the catalogue is built from `AioOCR.AVAILABLE_PLUGINS`; a provider row is
  seeded for every available engine; engines added or repaired on disk are detected by a file watcher and a
  periodic rescan (`engine_rescan_minutes`) through AioOCR's own `_discoverOcrPlugins()` - no restart needed.
- **Python 2/3-compatible syntax**: `from __future__` headers everywhere, `py23` shims (`raiseFrom`,
  `mergeDicts`, text/bytes helpers), no keyword-only markers, no `raise … from`. The FastAPI handlers that must be
  `async def` and the framework-required annotations are the documented exceptions.
- **Multilanguage UIs**: shared JSON catalogues (English, French, Spanish, German, Arabic with RTL) used by the
  web panel (`_()`, cookie/header detection, language selector) and the desktop (`JsonTranslator`, live switch).
- **Professional UI**: light / dark / system theme (persisted, instant), sidebar icons, avatar, onboarding
  checklist, humanised greeting and relative times, refined tables/badges, RTL-aware layout; desktop live theme.
- Project organisation: `Makefile`, `CONTRIBUTING.md`, `docs/I18N.md`.

## 0.1.0 - 2026-09-22

First release.

- Imports the given `AioOCR` library unchanged through `ocrroute/enginelib.py`; gateway code written in AioOCR's
  own style (`# coding=utf-8`, `(object)`, `super(Cls, self)`, `__m_` + `getX`/`setX`, `.format()`, no dataclasses).

- Engine catalogue: discovery of 55 `OCRPlugin` classes with availability, install hints, option-schema
  introspection and curated metadata; runtime demotion of engines with missing dependencies.
- SQLite persistence (WAL) with 19 tables, Alembic scaffold, maintenance (cache expiry, rollups, retention,
  backup/restore, integrity, vacuum), crash reconciliation.
- Routing engine: routes/members/strategies (14), circuit breaker, credential rotation, deadlines, stop
  conditions, degraded partials, ensemble consensus, explain traces, simulation.
- Input pipeline: path/URL/base64/upload, SSRF guard, MIME sniffing, size/pixel/page caps, PDF rasterisation,
  optional preprocessing with geometry restore; post-processing with RTL ordering and page stitching.
- Exporters: json, text, md, hOCR, ALTO, csv, xlsx, docx, searchable PDF, overlay PNG.
- API: `/v1/ocr` (+async, batch, jobs SSE), runs & artifacts, providers, credentials, routes, keys, usage,
  stats, settings, audit, doctor, `/metrics`; reserved `/v1/tools`.
- Web control panel: 12 pages (overview, playground, engines, providers, routes, runs, batch, usage, keys,
  tools, settings, doctor), first-run setup, session auth, live SSE feed, offline assets.
- Desktop (PyQt5): embedded/remote server, 10 pages, scan with overlay viewer and region capture, tray,
  single instance, QSS themes.
- CLI: serve, setup, doctor, ocr, batch, engines, provider, cred, route, key, runs, usage, db, config, desktop.
- Tests: 54 (unit, API integration against fake engines, panel, CLI, desktop offscreen).

Known limitations: searchable-PDF text layer is Latin-only; batch upload payloads are not persisted across
restarts; no TOTP 2FA yet (column reserved); desktop global hotkey not implemented (tray + menu shortcuts only).
