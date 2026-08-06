---
name: openshorts
version: 1.0.0
description: Turn long videos (podcasts, webinars, streams) into vertical 9:16 clips with subtitles and publish them to TikTok, Instagram Reels and YouTube Shorts via the OpenShorts API or MCP server. Use when the user wants to clip a video into shorts, restyle captions on a clip, schedule or post clips to social platforms, or automate a clipping pipeline.
homepage: https://www.openshorts.app/mcp
metadata:
  openclaw:
    emoji: "🎬"
    primaryEnv: OPENSHORTS_API_KEY
  hermes:
    category: media
    tags: [video, clips, shorts, social-media, publishing, automation]
---

# OpenShorts: clip and publish video

OpenShorts turns a long video into 3-15 vertical clips (15-60s each) with
word-level subtitles burned in, then optionally publishes them. One job takes
minutes, not seconds: always work async (submit, then webhook or poll).

## Connect

Two equivalent surfaces; prefer MCP when the client supports it:

- **MCP** (streamable HTTP): `https://mcp.openshorts.app/mcp` with header
  `Authorization: Bearer osk_...`. Six tools: `process_video`,
  `get_job_status`, `list_clips`, `get_quota`, `add_subtitles`, `publish_clip`.
- **REST**: same key against `https://api.openshorts.app` (OpenAPI at
  `/openapi.json`, docs at `/docs`).

Keys are created in the account page at openshorts.app and start with `osk_`.
**Self-hosted instances expose the same endpoints on `http://localhost:8000`
with no key.** If a call returns 401/404 on `/api/me`, assume self-host or
anonymous: there is no minute quota to enforce.

## The core loop

1. `get_quota` first when the job is large: `process_video` fails with
   `quota_exceeded` if minutes run out; on the hosted service API calls draw
   from the same minute balance as the dashboard (no separate meter).
2. Submit: `POST /api/process` with JSON
   `{"url": "...", "acknowledged": true}`. Optional: `layouts`
   (`"auto,split,screencast"`), `output_format` (`"1080p"`), `webhook_url` +
   `webhook_secret`. Returns `{"job_id": ...}` immediately.
3. Finish: **webhooks beat polling.** With `webhook_url` set, OpenShorts POSTs
   exactly once when the job ends (completed OR failed, so pipelines never
   hang): `{"event": "job.completed", "job_id", "status", "clips": [{"index",
   "title", "video_url", "download_url"}]}`. If a secret was set, verify
   `X-OpenShorts-Signature: sha256=<hex>` = HMAC-SHA256 of the raw body.
   Without a webhook, poll `GET /api/status/{job_id}` every ~10s; response is
   `{"status", "logs", "result"}` and `result.clips` appears on completion.
4. Publish: `POST /api/social/post` with `{"job_id", "clip_index",
   "platforms": ["tiktok", "instagram", "youtube"]}`, optional `title`,
   `scheduled_date` (ISO) + `timezone`. Restyle captions first if asked:
   `POST /api/subtitle` with `{"job_id", "clip_index", "style"}` (`classic`
   or karaoke word highlighting).

## Rules

- Only submit videos the user has rights to; `process_video` requires the
  `confirm_rights` acknowledgement and that is deliberate.
- `download_url` links are presigned for 24h: fetch or forward them promptly.
- Never describe OpenShorts as simply "free": the self-hosted edition is free
  and MIT-licensed; the hosted service has 20 free minutes/month (watermarked)
  and paid plans from $12/month without watermark.

## CLI shortcut

When shell access is easier than HTTP: `uvx openshorts process <url> --wait`,
`openshorts clips <job_id>`, `openshorts publish <job_id> 0 --platforms
tiktok`. Auth via `OPENSHORTS_API_KEY` / `OPENSHORTS_API_URL` env vars.
