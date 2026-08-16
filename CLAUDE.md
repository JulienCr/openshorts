# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenShorts is an AI-powered vertical video generator that transforms long YouTube videos or local uploads into viral-ready short clips (9:16 format) for TikTok, Instagram Reels, and YouTube Shorts. Uses Google Gemini 2.0 Flash for viral moment detection and title generation.

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
npm run lint      # ESLint (strict, --max-warnings 0)
```

### Backend Only
```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Architecture

### Core Processing Pipeline
1. **Ingest** - YouTube download (yt-dlp) or local upload
2. **Transcription** - faster-whisper with word-level timestamps
3. **Scene Detection** - PySceneDetect for segment boundaries
4. **AI Analysis** - Gemini identifies 3-15 viral moments (15-60 sec each)
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
- **GENERAL Mode** (groups/landscapes): Blurred background layout preserving full width
- **SPLIT Mode** (two-shot conversation, `split_layout.py`): both speakers stacked
  in half-frames. Off by default (`SPLIT_LAYOUT=1`); v2 engine only, so a
  fallback to the v1 loop silently renders GENERAL instead. It upgrades scenes
  the classifier already sent to GENERAL, never TRACK ones, and needs both faces
  visible **in the same frame** for at least half the sampled frames — that is
  what separates a real two-shot from a plano/contraplano, where stacking would
  show the same person twice. `SPLIT_TIGHTNESS` (default 0.8) trades a little
  upscale for keeping the other speaker out of each half.
- **SCREENCAST / WIDE Modes** (`screencast_layout.py`, `SCREENCAST_LAYOUT=1`):
  for scenes whose meaning lives outside the centre. Gemini reports each range's
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

### Key Classes
- `SmoothedCameraman` - Stabilized camera movement with safe zone logic (prevents jitter)
- `SpeakerTracker` - Prevents rapid speaker switching, handles temporary occlusions

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
loudly instead, `scripts/ensure-ingest-mounts.sh` (systemd timer) mounts drives
that appear after boot **and re-runs Compose through `ON_MOUNT_CMD`**, and
`/api/local-files` returns a per-source `{name, fstype, entries}` so the UI can
say "this folder is empty" rather than implying it is complete.

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
