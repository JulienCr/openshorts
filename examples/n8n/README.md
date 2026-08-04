# OpenShorts + n8n

Importable workflow: a video URL goes in through a form, OpenShorts clips it,
and a signed webhook brings back the finished clips. No polling.

## Import

1. In n8n: **Workflows → Import from file** → `openshorts-clip-and-notify.json`.
2. Create a **Header Auth** credential named `OpenShorts API key`:
   - Name: `Authorization`
   - Value: `Bearer osk_...` (create the key in your account page at
     [openshorts.app](https://www.openshorts.app/))
3. Open the **Clips ready (webhook)** node, copy its production URL, and paste
   it as `webhook_url` inside the **Start OpenShorts job** node body.
4. Optional: change `webhook_secret`. OpenShorts signs the webhook body with
   HMAC-SHA256 and sends it as `X-OpenShorts-Signature: sha256=<hex>`; verify
   it in a Code node with:

   ```javascript
   const crypto = require('crypto');
   const expected = 'sha256=' + crypto
     .createHmac('sha256', 'change-me')
     .update(JSON.stringify($json.body))
     .digest('hex');
   ```

## Webhook payload

```json
{
  "event": "job.completed",
  "job_id": "…",
  "status": "completed",
  "clips": [
    { "index": 0, "title": "…", "video_url": "…", "download_url": "…" }
  ]
}
```

Failed jobs fire the same webhook with `"event": "job.failed"` and an `error`
field, so the flow never hangs waiting.

`download_url` is a 24-hour presigned link to the archived clip (hosted
service). On the self-hosted edition the same workflow runs against
`http://localhost:8000` with no API key.

## Publishing the clips

Each item after **One item per clip** is a finished 9:16 clip. Chain whatever
comes next: Slack/email notification, a Sheets log, or direct posting via
`POST /api/social/post`. Full API reference:
[api.openshorts.app/docs](https://api.openshorts.app/docs). Agent-native
version of the same pipeline: [openshorts.app/mcp](https://www.openshorts.app/mcp).
