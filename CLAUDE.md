# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenShorts is an AI-powered vertical video generator that transforms long YouTube videos or local uploads into viral-ready short clips (9:16 format) for TikTok, Instagram Reels, and YouTube Shorts. Uses Google Gemini for viral moment detection and title generation (default
model `gemini-3.1-flash-lite`, override with `GEMINI_MODEL`).

## Development Commands

### Local Development (Docker)
```bash
docker compose up --build   # Build and run full stack
```
- Backend: http://localhost:8000 (FastAPI/Uvicorn)
- Frontend: http://localhost:5175 (Vite proxies API calls to backend)

### Frontend Only (Dashboard)
```bash
cd dashboard
npm install
npm run dev       # Dev server with HMR (port 5173)
npm run build     # Production build
npm run lint      # BROKEN, see below
```

`npm run lint` cannot run as written: `package.json` pins `eslint ^8.57.0` while
`eslint.config.js` imports `eslint/config`, an ESLint 9 subpath, and the script
still passes `--ext`, which flat config removed. Both ends need fixing together;
until then the linter is not a gate anyone can use.

### Backend Only
```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

### What actually verifies a change

**Nothing runs on push.** `.github/workflows/ci.yml` exists and is active, but
`gh run list --workflow=CI` is empty — it has never executed once on this fork,
and PRs #3, #4 and #5 all merged with no check behind them. Wherever this file
says "in CI", read it as "in the dependency list ci.yml *would* install", not as
something that gates a merge.

So the two real gates are local, and both have to be run by hand:

```bash
pytest tests/          # the whole backend suite
cd dashboard && npm run build
```

**Neither runs anywhere as written.** `pytest tests/` on the host collapses at
collection: the host interpreter has no `boto3` and no `sqlalchemy`, so the four
modules that `import app` or `cloud.*` error out before a single test runs — and
pytest itself is not installed there either. The backend image has every
dependency and *also* has no pytest. What actually works is installing it into a
throwaway container:

```bash
docker run --rm -u 0 -v "$PWD:/app" -w /app -e BILLING_ENABLED=0 \
  openshorts-backend sh -c "pip install -q pytest; python -m pytest tests/ -q -p no:cacheprovider"
```

`-u 0` because `/opt/venv` is root-owned; `-p no:cacheprovider` so the run leaves
no `.pytest_cache` behind. The stdlib-only modules (`clip_selection`, `branding`,
`split_layout`…) do run on the host once pytest is present, which is the fast
loop while iterating — but only the container run proves the suite is green.

`import main` fails in both (see "How the clips are chosen"), so a test that
needs the pipeline must go through `pytest.importorskip("main")` and will
**silently skip** — logic put in `main.py` is untested logic, not covered logic.

## Architecture

### Core Processing Pipeline
1. **Ingest** - YouTube download (yt-dlp) or local upload
2. **Transcription** - faster-whisper with word-level timestamps
3. **Scene Detection** - PySceneDetect for segment boundaries
4. **AI Analysis** - Gemini picks the viral moments in two passes (15-60 sec each;
   how many is `clip_count_targets`, not a fixed 3-15 — see below)
5. **FFmpeg Extraction** - Precise clip cutting
6. **AI Cropping** - Vertical reframing with subject tracking
7. **Effects/Subtitles** - Optional AI-generated FFmpeg filters
8. **Hook Overlay** - Text overlays with styled fonts
9. **Voice Dubbing** - Optional ElevenLabs AI translation (30+ languages)
10. **S3 Backup** - Silent background upload
11. **Social Distribution** - Upload-Post API (async upload)

### Key Files
| File | Purpose |
|------|---------|
| `main.py` | Core video processing: transcription, scene detection, clip extraction, vertical reframing |
| `clip_selection.py` | Pure helpers behind the clip choice: windows, shortlist sizing, merging, word-snapping. Stdlib only — see below |
| `gemini_worker.py` | Every Gemini prompt and response schema for the pipeline (score, detail, visual, layout, wide-content) |
| `app.py` | FastAPI server with async job queue and REST endpoints |
| `editor.py` | Gemini AI integration for dynamic video effects (FFmpeg filter generation) |
| `hooks.py` | Hook text overlay generation with font rendering |
| `s3_uploader.py` | AWS S3 upload with caching |
| `subtitles.py` | SRT generation, FFmpeg subtitle burning, and dubbed video transcription |
| `translate.py` | ElevenLabs dubbing API for AI voice translation |
| `dashboard/src/App.jsx` | Main React component with state management |
| `dashboard/src/components/TranslateModal.jsx` | Voice dubbing UI with language selection |
| `dashboard/vite-plugin-seo.js` | Build-time SEO surface: injects crawler-visible homepage content, emits static pages, sitemap.xml and llms.txt |
| `dashboard/seo/data.js` | Single source of truth for pricing, pipeline and competitor facts used by every generated page |

### SEO / AI-crawler surface

The dashboard is a client-rendered SPA with hash routing, so the HTML served for
`/` used to contain an empty `<div id="root">`. Googlebot renders JavaScript and
saw the real page; GPTBot, ClaudeBot and PerplexityBot do not and measured the
homepage as zero characters of text. `vite-plugin-seo.js` fixes that at build time:

- Injects the content of `seo/landing-fallback.js` into `#root`. React's
  `createRoot().render()` replaces it on mount, so users get the app and
  non-executing clients get the copy. **Keep it in sync with `Landing.jsx`.**
- Emits the standalone pages (the `/alternatives` cluster, the clip-generator,
  open-source, use-case and automation pages, and `/mcp`; the full list is
  `buildPages()` in `seo/pages.js`) as flat `.html` files.
  nginx resolves the clean URL through `try_files $uri $uri.html`; serving them as
  directories instead makes nginx 301 to a trailing slash and every canonical
  would then point at a redirect.
- Generates `sitemap.xml` and `llms.txt` from the same page list, so they cannot
  drift. Do not add a static `public/sitemap.xml` back.

When editing pricing anywhere, edit `seo/data.js` too. Nothing on the site should
say "OpenShorts is free" without naming the Cloud price in the same breath: both
are true of different editions and quoting only the first one is what makes AI
answers describe the paid product as free.

### How the clips are chosen

Two Gemini passes over the transcript (`main.py:get_viral_clips`), with every
pure helper in `clip_selection.py` and every prompt in `gemini_worker.py`.

`build_transcript_windows` cuts the transcript into ~90s windows aligned to
Whisper segment boundaries, each overlapping the previous by ~30s so no moment
is ever split in half. **Pass 1** scores them in batches of `SCORE_BATCH`
(default 8); `shortlist_size` keeps the best 30%, floor 10, ceiling 24.
**Pass 2** turns that shortlist into clips and writes the copy. Cuts then land
on real word boundaries via `snap_clip_to_words`.

Four things there are load-bearing and were each paid for with a bug:

- **The scoring pass scores every window; it does not elect a few.** It used to
  say "choose up to 3 windows from this batch" against batches of 8, so it
  could never return more than 37.5% of the material and *that cap*, not the
  score, was what reached the shortlist — a 2h source scored 79 windows,
  returned 30, and `shortlist_size` picked 24 of them. The global sort was
  choosing between candidates that had already been chosen. The rubric anchors
  in the prompt (80-100 / 50-79 / 20-49 / 0-19) exist because batches are
  scored in **separate calls**: without a shared scale, a weak batch spreads
  40-80 and a strong one 60-95, and sorting them together compares two
  different markers.
- **Overlapping shortlisted windows are merged before the detail pass**
  (`merge_overlapping_windows`), through segment indices and never through
  string surgery on the joined text — rebuilding from `segments[seg_from:seg_to+1]`
  of the union is what guarantees shared prose appears once. Two adjacent
  windows both surviving the shortlist otherwise handed the model the same
  sentences twice under an instruction to work through every window, and the
  only thing against duplicate clips was a `DIVERSITY` line in the prompt.
  `drop_overlapping_clips` is the guard that does not depend on the model
  complying; its threshold is 1.0s rather than zero because `snap_clip_to_words`
  pads each bound with up to 0.35s of lead and 0.45s of trail, so back-to-back
  clips legitimately share ~0.8s of silence.
- **The clip-count target is computed BEFORE merging.** Merging reshapes the
  payload, it does not select less material, and the floor in
  `clip_count_targets` rests on a retention measurement (users who got 1-3
  clips returned 0.4% of the time against 16.1% for 4-9) that must not move
  because two windows happened to be adjacent.
- **The detail pass reads `[SECONDS]` anchors, not prose alone**
  (`window_text_with_anchors`, one marker per Whisper segment). It has to answer
  in absolute seconds and used to receive only the window's own start/end, so it
  interpolated a position inside 90s of text and was routinely wrong — which is
  most of what `snap_clip_to_words` was repairing. Per-word timings were the
  other option and cost ~40x more (~65k input tokens on a 24-window shortlist)
  while burying the prose the model is meant to be judging. The markers are
  **truncated, never rounded**: a marker rounded up lands inside the first word
  of the sentence it marks, the model returns that as `end`, and the snapper
  then reads it as speech and keeps the word the clip was meant to exclude.
  Only the sign of the error matters — 0.4ms late trips the same predicate as
  40ms late — so more decimals shrink it without removing it.

`snap_clip_to_words` walks to the nearest speech when a bound lands in a
silence, forward for the start and backward for the end, capped at
`MAX_SILENCE_SKIP`. The direction is the point: never open or close on dead
air, and the *nearest* word can be on the wrong side of the gap. Past the cap
the model's timestamp is not slightly off inside a pause, it is wrong, and
moving the bound that far would change what is in the clip. It also picks the
most-snapped **valid pair** rather than discarding both bounds when the
duration repair fails, which is what used to hand back a raw clip because one
side could not be fixed.

**Everything testable lives in `clip_selection.py` because `import main` fails
in CI** — main.py imports cv2/torch/ultralytics/mediapipe at module scope and
`.github/workflows/ci.yml` installs none of them (tests that need it use
`pytest.importorskip("main")` and silently skip). New selection logic put in
main.py is untested logic.

Knobs, none of them needed in normal operation: `GEMINI_MODEL`,
`GEMINI_THINKING_SCORE` (score stage only), `SCORE_BATCH`, `CLIP_SHORTLIST_MAX`,
`CLIP_TARGET_MIN` / `CLIP_TARGET_MAX`.

### Cómo se elige el layout

`POST /api/process` acepta `layouts`: una lista (JSON) o cadena separada por
comas con `auto`, `split`, `screencast`, `speaker_cut`, `punch_in`. Cada nombre
enciende su variable de entorno para **ese** trabajo (`app.py:layout_env`); sin
`layouts` el pipeline se comporta exactamente como antes.

`auto` activa `layout_picker.py`: **una** llamada a Gemini por vídeo de origen
(no por clip) que elige entre `none` / `screencast` / `split`. Medido sobre el
corpus de 48 contra etiquetas revisadas a mano: 94% / 92% / 96% en tres pasadas,
con 0-1 falsos positivos sobre los 28 clips que no deben tocarse, y solo 2 clips
que cambian de respuesta entre pasadas.

**Manda 12 fotogramas a 1024px, no el vídeo.** Gemini factura vídeo a ~300
tokens por segundo: una hora de fuente son ~1,08M de tokens (no cabe en una
ventana de 1M) y una subida de 1-2 GB para recibir una palabra. Doce fotogramas
cuestan ~3k tokens **dure lo que dure la fuente**, que es lo que hace viable
esto con los podcasts de una hora que entran de verdad. La resolución importa y
el número de fotogramas no: a 640px detecta 15 de 20 (una hoja de cálculo es
ilegible), a 1024px sube a 17, y pasar a 24 fotogramas lo empeora. A 1024px la
diferencia con mandar el vídeo entero cae dentro de la varianza que ya tiene el
propio modo vídeo, a 2,2 s por clip en vez de ~15 s.

Lo que hace que funcione, y que conviene no deshacer: se le pide una **decisión
entre opciones cerradas**, no una medida. Los cuatro intentos anteriores (Canny,
MSER, cobertura temporal, anchura) le pedían un número y ninguno separó una hoja
de cálculo de un marcador de esquina. La varianza que este repo atribuía a
Gemini era de las medidas continuas, no del modelo.

`layout_picker.apply()` sólo **añade**: una elección explícita del usuario nunca
se desactiva porque el modelo diga `none`.

### Video Reframing Modes
- **TRACK Mode** (single subject): MediaPipe face detection + YOLOv8 fallback with "Heavy Tripod" stabilization
  (`camera.py`, extracted from `main.py` so CI actually runs its tests — `main`
  cannot be imported without mediapipe/torch, which silently skipped every
  regression test the camera has). A TRACK scene shorter than
  `MIN_TRACK_SECONDS` (1.5) is rendered GENERAL instead: it costs a snap plus
  a pan and there is no time to establish a subject. On real 60fps multicam
  podcasts, runs of sub-second cuts were the main source of "the frame
  lurches" — 72 → 47 px/s of on-screen crop motion, worst cut jump 517 → 125px.
  The tracker's two damping windows are **durations** (`TRACKER_FORGET_SECONDS`,
  `TRACKER_COOLDOWN_SECONDS`, both 1.0s); they used to be hard-coded 30-frame
  counts whose comments said "1s", so at 60fps they ran at half strength.
- **GENERAL Mode** (groups/landscapes): Blurred background layout preserving full width
- **SPLIT Mode** (two-shot conversation, `split_layout.py`): both speakers stacked
  in half-frames. Off by default (`SPLIT_LAYOUT=1`); v2 engine only, so a
  fallback to the v1 loop silently renders GENERAL instead. It upgrades scenes
  the classifier already sent to GENERAL, never TRACK ones, and needs both faces
  visible **in the same frame** for at least half the sampled frames — that is
  what separates a real two-shot from a plano/contraplano, where stacking would
  show the same person twice. `SPLIT_TIGHTNESS` (default 0.8) trades a little
  upscale for keeping the other speaker out of each half.
- **SCREENCAST / WIDE Modes** (`screencast_layout.py`, `SCREENCAST_LAYOUT=1`).
  ⚠️ **Currently unreachable in production**, whatever `layouts=` says:
  `main.py` calls `reframe_v2.render()` without `content_ranges`, and
  `screencast_layout.detect_content_ranges()` has no caller anywhere outside
  the tests, so the branches that would select these never run. The same is
  true of INSET, which chains behind the screencast decision. Only TRACK,
  GENERAL, SPLIT and ALTERNATE are actually reachable today. Everything below
  describes the design, not the shipped behaviour.
  For scenes whose meaning lives outside the centre. Gemini reports each range's
  **width_fraction**, and that is the gate — coverage was tried before and did
  not separate a spreadsheet from a corner ticker, while width does (a bug spans
  ~15% and survives any crop, a spreadsheet spans ~100% and cannot). Content
  narrower than 0.5 moves nothing. Between 0.5 and 0.85 there is room beside the
  content, so SCREENCAST stacks it over the presenter. Above 0.85 the presenter
  is composited **on top of** the content and stacking would show it twice, so
  those scenes get WIDE: the GENERAL layout with side-cropping disabled.
- **INSET Mode** (`camera_inset.py`): pantalla a ancho completo arriba, el
  recuadro de la webcam ampliado abajo. Para el caso de una sola fuente con la
  cámara compuesta en una esquina (OBS, VOD de stream). Se encadena detrás de
  la decisión `screencast`, **no** se le pregunta a Gemini: ofrecido como cuarta
  opción respondió `screencast` en los 5 clips que tienen recuadro, en dos
  pasadas, y la exactitud global cayó de 92% a 83-85%. El detector geométrico
  encuentra esos 5 sin falsos positivos. Los tres filtros que hacen falta, cada
  uno pagado con una iteración: sujeto **pequeño**, **descentrado en
  horizontal** (una cara de talking head está centrada aunque esté alta), y
  **quieto entre muestras** (3-11px frente a 316px de una persona real).
- **ALTERNATE Mode** (`active_speaker.py`, `SPEAKER_SIGNAL=1` + `SPEAKER_CUT=1`):
  hard cuts to whoever is talking, rendered through the TRACK path as a
  trajectory with jumps. `SPEAKER_SIGNAL=1` alone just gates SPLIT on both people
  actually speaking. Mouth activity **must** be normalised per speaker before
  comparing (`normalise_activity`): raw frame-difference magnitude scales with
  local contrast and lighting, and on a real two-shot it handed one speaker
  90-100% of the scene.
- **Punch-in** (`punch_in.py`, `PUNCH_IN=1`): not a layout. A ~12% push on the
  clip's beats, riding the TRACK path by widening its per-frame crop command
  from x-only to w/h/x/y. Beats currently come from the audio envelope;
  `emphasis_times` is a plain list of seconds so the transcript's hook words can
  replace it without touching the module.

### Channel branding (`branding.py`, `BRAND_WATERMARK=1`)

Burns the operator's own logo + a secondary badge into every finished clip: one
line, logo left and badge right, from PNGs in `assets/brand/` (gitignored; see
the README there). Self-host branding, **not** the hosted free plan's
`apply_watermark` in `main.py` — do not merge the two. That one is defensive and
parked at 40% of the height so a free user cannot crop it off; this one is
cosmetic and deliberately stays out of the way. Both flags can be on at once and
their bands never touch.

**The vertical band is the whole design, and `BRAND_Y_RATIO` is its TOP edge,
not its centre.** Three things already own parts of a 9:16 frame: the platform
chrome (down to y≈12%), the burned captions (`subtitles.SAFE_MARGIN_V`, y≈59-85%)
and the hook's default `top` card (`hooks.py`, y=20%). Anchoring the centre was
tried first and is wrong: the band's height depends on the logo's aspect ratio,
which the operator picks, so a 3:1 logo at a 0.13 centre put its top edge at
0.109 — back under TikTok's tabs. The tallest mark's top edge is pinned to
`BRAND_Y_RATIO` and shorter marks are centred against it, so a taller asset
grows downwards instead.

**`MAX_BAND_HEIGHT_RATIO` is the other half of that.** Widths are a fraction of
the clip *width*, but the safe band is measured in *height*, and the two only
relate through the aspect ratio of the frame **and** of the asset — neither of
which this code picks. `output_format` also delivers 1920×1080 and 1080×1080,
where a 3:1 logo at 22% width is 13% of the height instead of 4.1%, so the band
runs from 13% to 26% and crosses the hook card at 20%. Measured across both
frame shapes and both asset shapes, every
combination except the 9:16 wide lockup collided. The clamp scales **only the
mark that breaks the band**, not the whole band: scaling everything by the
tallest one's excess was tried and dragged the badge down to 83×19, which is
unreadable on a phone.

**Settings are read per call (`settings()`), never frozen at import.** `main.py`
is also a CLI, and there the import block runs *before* `load_dotenv()`, so an
import-time read saw nothing from `.env` — the documented way to switch this on.
Reordering that import would have papered over it; reading at call time removes
the ordering constraint instead of documenting one no autoformatter can see.
The sibling modules (`punch_in`, `layout_picker`, `screencast_layout`) still
freeze at import and still have this hole on the direct-CLI path.

Applied in `main.py` right before `auto_caption_clip`, in place on the canonical
clip, like the watermark — `/api/subtitle` walks back through `subtitled_`
prefixes to re-style captions, so a mark burned into a derived file would be
lost there. The `--skip-analysis` path needs its own call; it never reaches the
per-clip loop.

Per job, the dashboard checkbox sends a **tri-state**: absent means "inherit
`BRAND_WATERMARK`", which is what keeps older callers (CLI, MCP) working. The
resolved decision is persisted in the resume manifest, because env is rebuilt
from `os.environ` on resume and an unticked box would otherwise come back ticked.

**Turning a per-job flag OFF means writing `"0"`, never `env.pop()`** — this
holds for any flag `_build_job_env` or `_resume_mark_env` sets, not just this
one. The child process runs `main.py`, which calls `load_dotenv()`, and dotenv
fills in every variable that is **absent** (its default is `override=False`).
Deleting the key therefore hands `.env` the last word, so `BRAND_WATERMARK=1`
there silently re-enabled a job the user had explicitly unticked. Measured, not
theorised. Note this bug did *not* exist while settings were frozen at import —
freezing read the environment **before** `load_dotenv()`, so a popped variable
stayed popped. Fixing the direct-CLI hole above is what opened this one, which
is the argument for testing what the renderer decides rather than which keys the
env dict happens to contain: the tests asserting `"BRAND_WATERMARK" not in env`
passed happily throughout.

### Key Classes

Both live in `camera.py` and are re-exported from `main.py`. They are pure
state machines over numbers, deliberately importable without the ML stack.

- `SmoothedCameraman` - Stabilized camera movement with safe zone logic (prevents jitter)
- `SpeakerTracker` - Prevents rapid speaker switching, handles temporary occlusions.
  Note `stabilization_frames` is **dead**: `stabilization_threshold`,
  `last_seen` and `locked_counter` are written and never read.

### Tuning the camera

`scripts/replay_camera.py` records a clip's detections once and replays them
through cameraman variants, because MediaPipe is not deterministic and
rendering twice measures the detector as much as the change. Two metrics, and
picking the wrong one costs you the answer: `scene_metrics` excludes scene
boundaries (bdd9e5d's definition — "a cut is supposed to reframe"), while
`screen_motion` includes them. On multicam material the first says nothing is
wrong and the second finds 517px lurches at the cuts; consecutive shots of the
same room are not a reframe.

**Record at stride 1.** A trace holding only every 4th frame, replayed with a
different stride, consults frames with no detections at all — the camera then
receives no target and the travel metric collapses, which reads as a
spectacular win. That artefact produced a fake -57% before it was caught.

Three plausible fixes measured WORSE or neutral and are not in the code: a
Schmitt-trigger controller that settles on the subject instead of the deadzone
edge (54.5 → 79.1 px/s, 0 scenes calmer, 4 busier), phasing the detection
stride on scene starts (54.4 → 58.2), and resetting tracker identities at cuts
(54.5 → 56.3, one scene 188 → 356).

### API Endpoints
| Method | Route | Purpose |
|--------|-------|---------|
| POST | `/api/process` | Submit video for processing |
| GET | `/api/status/{job_id}` | Poll job status and logs |
| POST | `/api/edit` | Apply AI video effects |
| POST | `/api/subtitle` | Generate and apply subtitles (auto-transcribes dubbed videos) |
| POST | `/api/hook` | Add text hook overlays |
| POST | `/api/translate` | AI voice dubbing via ElevenLabs |
| GET | `/api/translate/languages` | List supported dubbing languages |
| POST | `/api/social/post` | Post to social media (async upload) |
| POST | `/mcp` | MCP server (JSON-RPC): the pipeline as agent tools |
| POST/GET/DELETE | `/api/keys` | User API keys (cloud mode, session JWT only) |
| GET/PUT | `/api/style` | The server's default look (PUT is self-host only) |

### Agent access (MCP, API keys, webhooks)

- **API keys** (`cloud/api_keys.py`): `osk_...` tokens, sha256-stored, created in
  the dashboard account page. `cloud/auth.get_current_user_optional` accepts
  them (`Bearer osk_...` or `X-API-Key`) and resolves the owner, so metering,
  entitlement, plan priority and job ownership apply to agents with zero
  endpoint changes. Key management itself refuses API-key auth: a leaked key
  cannot mint replacements.
- **MCP server** (`mcp_server.py`, mounted always): stateless Streamable-HTTP
  JSON-RPC at `/mcp` — no SDK dependency, ~3 methods + 6 tools. Each tool calls
  back into this same app in-process (`httpx.ASGITransport`) forwarding the
  caller's auth headers, so it can never drift from the REST behavior. Cloud
  mode 401s without a resolvable user; self-host stays BYOK-open.
- **Webhooks**: `POST /api/process` takes `webhook_url` + optional
  `webhook_secret` (HMAC-SHA256, `X-OpenShorts-Signature`). Validated with
  `security_utils.assert_public_url` at submit AND at delivery (DNS rebinding).
  Fired once per job from `run_job_wrapper` after the R2 archive so the payload
  can carry durable download links; survives redeploys via the resume manifest.
  `PUBLIC_API_URL` env sets the absolute-URL base when behind a proxy.

### Two renders per clip

`POST /api/process` takes `variants`: `auto` (the pipeline's own per-scene
framing) and/or `safe` (the whole 16:9 frame on a blurred background, camera
fixed, no subject choice). `auto` is always delivered; the dashboard offers
`safe` as a pre-checked box and shows an A/B toggle on each result card.

The safe render is a dedicated `reframe_v2.render_general()`, **not** a flag
threaded through `render()`. Three reasons, all load-bearing: `render()` has
already paid `detect_scenes` and `analyze_scenes_strategy` (MediaPipe behind
`DETECT_LOCK`) before its segment loop, and this needs no detection at all;
`process_video_to_vertical` swallows any v2 exception and silently falls back
to the v1 loop, which would ignore the flag and ship a tracked render as
"safe"; and the layout flags are module globals that `layout_picker.apply()`
mutates process-wide while three clips render in threads. Reading no flag
makes "the camera never moves" structural rather than conditional.

It renders from the same 16:9 temp as the auto variant, **before**
`_process_one_clip`'s `finally` deletes it, and uses
`full_width_content_height` rather than the default 0.42 ratio — the default
crops the sides and throws away ~24% of the width, which is the opposite of
what this variant promises.

**A new per-clip finishing step goes in `finish_rendered_clip`, never at a call
site.** It owns watermark → branding → captions+hook, in that order, for both
variants. This is not tidiness: the branding and the automatic hook were each
added to that sequence by a different branch, both landed on the auto path only,
and **neither produced a merge conflict**. The result was a safe variant
shipping with no channel logo and no hook card — an A/B lost on differences that
have nothing to do with framing, which is the one thing it exists to compare.
Two call sites of a growing sequence drift silently; one cannot.

Naming is a **suffix** (`<base>_clip_3_safe.mp4`), never a prefix: the caption
glob for a clip is `subtitled_*_<base>_clip_3.mp4` and requires the name to END
there, so the two variants cannot capture each other's files. `clip["video_url"]`
is untouched and a sibling `clip["variants"]` appears only when there are two,
so webhook, MCP, ZIP and the local library are unaffected.

Cloud collapses to auto-only: R2 archives one object per clip index and
`archive_clip_edit` deletes the superseded one. **Disk doubles** (~82MB/clip →
~164MB with captions on both), and in self-host `_enforce_output_size_cap`
rmtree's the oldest projects permanently — raise `OUTPUT_MAX_GB` before
turning this on.

### Concurrency Model

Async job queue with semaphore-based concurrency control, `MAX_CONCURRENT_JOBS`
(default 5). Retention is `JOB_RETENTION_SECONDS`: one hour in cloud mode, **0 —
never — when self-hosting**, because `output/` *is* the project library and there
is nowhere else to restore it from. `OUTPUT_MAX_GB` is the real bound there.

There are **two lanes**, each a queue + a semaphore + a dispatcher. Batch jobs
(`/api/process/batch`) go in the second one, capped by `LOCAL_BATCH_CONCURRENCY`
(default 1), so thirty queued recordings never make a hand-launched file wait.
The lane is chosen by priority in `_enqueue_job` — which is also what puts a
resumed batch job back where it belongs, since the manifest already replays its
priority.

The one rule to keep: **the global semaphore is always acquired last.** A second
semaphore taken inside `run_job_wrapper` would look equivalent and is not — the
dispatcher hands out a global slot *before* the job runs, so queued batch jobs
would each sit on a slot while blocked on the batch cap, and an interactive job
dequeued later would find nothing free. A `PriorityQueue` does not preempt.

### Ingesting files that are already on the server

`LOCAL_INGEST_DIR` (see `docker-compose.ingest.yml`) enables the dashboard's "On
Server" tab and the `local_path` / `local_paths` parameters. Self-host only —
`local_ingest_enabled()` is false whenever billing is on, so a paying tenant can
never read the operator's disk. Sources are mounted read-only as siblings under
one root, so adding one is a compose line and `app.py` learns nothing.

`local_stage.py` copies a source to local disk first **when its filesystem is
slow**, decided by reading the fstype from `/proc/self/mountinfo` against an
allowlist (a Docker bind mount reports the underlying superblock, so
`/ingest/replays` on a WSL drive shows up as `9p`). That is not one read being
optimised: the pipeline re-reads the source for ffprobe, the audio extraction,
the scene detector, the layout picker's twelve seeks, then once per clip at
extraction and again at reframe.

Two things there are load-bearing and easy to undo by accident:
- the copy lands at `stage/<key>/<original basename>`, **never** a hashed
  filename — `main.py` derives the project title from the input's basename, so a
  hash would rename every project in the library to gibberish;
- the copy happens in `run_job`, after the concurrency slot is taken, not at
  submit time. A batch of thirty 8GB files would otherwise try to land 240GB
  inside an HTTP handler.

**A missing mount is the failure mode to design against.** Docker creates a bind
source that does not exist, so an unmounted drive silently becomes an empty
folder and the tab just looks short. `create_host_path: false` makes Compose fail
loudly instead, `scripts/ensure-ingest-mounts.sh` (systemd timer, installed by
`scripts/install-ingest-mount-timer.sh`) mounts drives that appear after boot
**and re-runs Compose through `ON_MOUNT_CMD`**, and `/api/local-files` returns a
per-source `{name, fstype, entries, status}` so the UI can say "this folder is
empty" rather than implying it is complete.

**A mount can also die without going away, and that is a different bug in every
layer.** The cloud client restarts, the drive letter returns on the Windows side,
and the 9p session WSL held does not: the mountpoint stays in `/proc/mounts`
while every syscall against it answers `ENODEV`. Each layer had been built for
the *unmounted* case and mishandled this one, all four measured on 17 Aug 2026:

- `ensure-ingest-mounts.sh` — `mountpoint -q` says "not mounted" (it cannot stat
  it either) so it does not skip, but `[ -d ]` is then false and `mkdir -p` fails
  on the unstattable path, so the loop hit `continue` and never reached `mount`.
  It now `umount -l`s a mountpoint it cannot stat before rebuilding it.
- `_local_ingest_sources` — filtered on `os.path.isdir()`, which swallows the
  `OSError` and returns False, so the source did not come back marked broken, it
  did not come back **at all**. `local_stage.dir_state` replaces it and returns
  `"ok" | "dead" | "unreadable" | "absent"`; **only `absent` is dropped**, and
  its errno list (`ENOENT`, `ENOTDIR`) is an allowlist for the same reason
  `FAST_FSTYPES` is one — the first cut folded `EACCES` in with "not there" and
  reintroduced the disappearance through another errno, since `os.path.isdir()`
  answers True on a directory it cannot read (stat only needs to traverse the
  parent) and such a source had always reached the picker. A UID/GID mismatch on
  a bind mount is the ordinary way to hit it. Keep the logic there and not in
  `app.py`: `local_stage` is stdlib-only and testable on the host, `app.py`
  needs the container.
- `ON_MOUNT_CMD` — a plain `up -d` is a **no-op** here, because the bind path
  string is unchanged and that is all Compose diffs. It answers `Running` and
  leaves the container on the dead mount; it needs `--force-recreate`.
- and the timer itself was never installed — the fix for the previous outage was
  a copy-paste block in `scripts/README.md`. Hence the installer script.

`ON_MOUNT_CMD` is load-bearing, not garnish: `restart: unless-stopped` does *not*
recover from a missing bind source. With the source absent, Docker fails at
container **creation**, so `ps -a` lists nothing and there is no container for a
restart policy to act on; and a container created earlier whose source went away
stays `exited` after a failed start, with no daemon retry. Both measured.

### The server's default style

Every clip already ships captioned, but the look was hard-coded in
`subtitles.AUTO_CAPTION_STYLE`: identical for everyone, and changeable only one
clip at a time, after the render, from a modal. `style.json` at the repo root
replaces that (`style_preset.py`, template in `style.example.json`, move it with
`OPENSHORTS_STYLE_FILE`). It carries the caption look, the automatic hook,
layouts, output format and the quality gate.

Three properties to keep:

- **Read at submit, never at import.** Editing the file applies to the next job
  without restarting the container. A module constant would make a colour change
  need a redeploy.
- **A broken preset costs nothing.** Missing file, malformed JSON, unknown key:
  each reads as "no preset" and the built-in defaults stand. Same doctrine
  `auto_caption_clip` already follows for captions.
- **The request beats the file.** `layouts=`, `output_format=`, and a whole
  inline `style` object (the CLI's `--style`, the MCP tool's `style`) override it
  for one job. An inline style *replaces* the file rather than merging into it,
  because a half-file half-request hybrid is unpredictable from either side.

It reaches the renderer through `_build_job_env` (`app.py`), the seam
`/api/process` and `/api/process/batch` already shared, in a single
`OPENSHORTS_STYLE` variable rather than fifteen. Batch is the surface where this
pays: thirty queued recordings carry no style fields at all.

`GET/PUT /api/style` backs the dashboard's "Default style" panel. The PUT refuses
when `BILLING_ENABLED` — one server-wide default is meaningless once tenants
share an instance, since whoever saved last would restyle everybody's clips — and
cloud **ignores the file on read too**. Refusing the write alone is not
isolation: a `style.json` baked into the image or left over from a self-host run
would still be read on every submission. Inline per-job styles keep working
there, since they only affect their own job. The PUT is guarded by
`Sec-Fetch-Site`, not by comparing `Origin` to the request host: Vite proxies
`/api` with `changeOrigin: true`, so the two can never match and comparing them
rejects every save from the documented dashboard.

**The automatic hook.** Gemini has always written a `viral_hook_text` for every
clip and nothing ever burned it; it waited for someone to open the modal. Under
`hook.enabled`, it is composited in the **same ffmpeg pass** as the captions.
That is a naming constraint, not a speed tweak: `auto_caption_clip` writes
`subtitled_<ts>_<clip>.mp4`, and `_canonical_clip_file` / `_strip_burned_captions`
rebuild the clean original from that exact prefix. A separate pass would have to
invent a name outside it. Two inputs also make the audio mapping explicit, and
the `?` in `-map 0:a?` is load-bearing: without it ffmpeg aborts on a silent
source.

Re-deriving a clip (an effect, a caption re-style) drops the automatic hook.
`_reapply_captions` refuses to replay it on purpose, because one of its two
callers is `/api/hook`, and replaying would stack the preset's card on top of the
one the user just wrote.

**Resume.** The manifest carries the job's `layouts` **and its resolved style**,
and `_resume_job_env` replays both. Before that, a job interrupted by a redeploy
came back without SPLIT, screencast or punch-in — invisible, and worst on a
thirty-file batch, which delivered two halves that did not match. The style is
carried rather than re-read for the same reason: a job submitted with an inline
style would otherwise resume wearing the server default, and editing
`style.json` while a job sits in the queue would split that job's own clips.

## Environment Variables

**Server-side (.env):**
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_S3_BUCKET` - For S3 backup
- `MAX_CONCURRENT_JOBS` - Concurrent processing limit (default: 5)
- `OPENSHORTS_STYLE_FILE` - Where the default style lives (default: `style.json` at the repo root)
- `AUTO_CAPTIONS` - `0` turns off the captions every clip ships with
- `VITE_API_URL` - Production API URL override
- `VITE_OPENPANEL_API_URL`, `VITE_OPENPANEL_CLIENT_ID` - Optional product analytics, read at **build** time. Unset (the default, including every self-hosted build) means no analytics is initialised and no third-party script is loaded. `dashboard/index.html` also gates reporting on an `ANALYTICS_HOSTS` allowlist, so a build carrying credentials stays inert on any other host.
- `OPENPANEL_CLIENT_ID`, `OPENPANEL_CLIENT_SECRET` - Optional **server-side** analytics (`cloud/analytics.py`), same opt-in rule: unset means a silent no-op. Reports job outcomes with the user's job index, which the browser cannot do reliably — a render finishes after the tab is often gone, and ad-blockers eat a share of client events. Needs a *write* client; the read client used for querying is a different credential.

**Client-side (localStorage, encrypted):**
- `GEMINI_API_KEY` - Google Gemini API key (required)
- `ELEVENLABS_API_KEY` - ElevenLabs API key for voice dubbing (optional)
- `UPLOAD_POST_API_KEY` - Upload-Post API key for social posting (optional)

> API keys are stored encrypted in the browser and sent via headers only when needed. Never stored server-side.

## Tech Stack
- **Backend:** Python 3.11, FastAPI, google-genai, faster-whisper, ultralytics (YOLOv8), mediapipe, opencv-python, yt-dlp, FFmpeg, httpx
- **Frontend:** React 18, Vite 4, Tailwind CSS 3.4
- **External APIs:** Google Gemini, ElevenLabs Dubbing, Upload-Post
- **Infrastructure:** Docker + Docker Compose, AWS S3
