/* Page definitions for the static SEO surface.
 *
 * Two page shapes live here. Comparison pages ("X alternatives") are generated
 * from the competitor table, because commercial-investigation prompts such as
 * "best free Opus Clip alternative" are answered almost entirely out of listicle
 * and comparison content. Informational pages are hand-written, because
 * informational content is cited at a far higher rate than product pages and it
 * is the only surface where a small project can outrank a funded one.
 *
 * Every page follows the same internal shape: TL;DR, then one question per H2,
 * each answered inside a block that still makes sense when it is lifted out on
 * its own. That is the unit an engine retrieves; paragraphs that depend on the
 * one above them get quoted wrong or not at all.
 */

import { SITE, COMPETITORS, COMPARISON_ROWS, EDITIONS, PIPELINE_STEPS, CANONICAL_ANSWERS } from './data.js'
import { esc } from './render.js'

const li = (items) => `<ul>${items.map((i) => `<li>${i}</li>`).join('')}</ul>`

const faqBlock = (faq) =>
  `<h2>Common questions</h2><dl class="faq">${faq
    .map((f) => `<dt>${esc(f.q)}</dt><dd>${esc(f.a)}</dd>`)
    .join('')}</dl>`

const sources = (items) =>
  `<h2>Sources</h2><ul class="sources">${items
    .map((s) => `<li>${s}</li>`)
    .join('')}</ul>`

/* Pricing is restated in plain body text on every page, not only in the schema.
 * An engine that reads the raw HTML has no reason to prefer a JSON-LD offer over
 * a sentence, and the sentence is what gets quoted. */
const pricingParagraph = `
<p>OpenShorts comes in two editions and they are priced very differently, so it
is worth being precise. <strong>${esc(EDITIONS.selfHosted.name)}</strong> is free
and open source under the MIT licence: ${esc(EDITIONS.selfHosted.summary)}
<strong>${esc(EDITIONS.cloud.name)}</strong> is the hosted service:
${esc(EDITIONS.cloud.summary)}</p>`

function competitorPage(slug) {
  const c = COMPETITORS[slug]
  const rows = COMPARISON_ROWS.map((r) => {
    const vendor = r.key ? c[r.key] : r.vendor
    return `<tr><td>${esc(r.feature)}</td><td class="os">${esc(r.os)}</td><td>${esc(vendor)}</td></tr>`
  }).join('')

  const body = `
<h2>Is OpenShorts a real alternative to ${esc(c.name)}?</h2>
<p>Yes, with one honest caveat. OpenShorts covers the same core job:
it takes a long video, finds the segments worth clipping, cuts them, reframes
them to 9:16 and burns in subtitles. It adds two things ${esc(c.name)} does not
have, AI voice dubbing into more than 30 languages and an AI UGC generator with
lip-synced actors. The caveat is that the free edition is self-hosted, which
means Docker and a machine to run it on. If you want a hosted product with no
setup, that is OpenShorts Cloud, and it is a paid service above 20 minutes a month.</p>

<h2>What does ${esc(c.name)} cost?</h2>
<p class="checked">Pricing checked ${esc(c.checked)}. Vendors change plans without notice; verify before you buy.</p>
${li(c.tiers.map(([n, d]) => `<strong>${esc(n)}</strong>: ${esc(d)}`))}
<div class="note"><span class="label">The part that catches people out</span><p>${esc(c.gotcha)}</p></div>

<h2>What does OpenShorts cost?</h2>
${pricingParagraph}

<h2>${esc(c.name)} vs OpenShorts, feature by feature</h2>
<table>
<thead><tr><th>Feature</th><th>OpenShorts</th><th>${esc(c.name)}</th></tr></thead>
<tbody>${rows}</tbody>
</table>

<h2>What ${esc(c.name)} does better</h2>
<p>A comparison that finds nothing good to say about the other tool is not worth
reading, so here is where ${esc(c.name)} genuinely wins:</p>
${li(c.strengths.map(esc))}

<h2>Where the two differ</h2>
${li(c.whereWeDiffer.map(esc))}

<h2>Which one should you pick?</h2>
<p>${esc(c.bestFor)}</p>

${faqBlock([
  {
    q: `Is there a free alternative to ${c.name}?`,
    a: `Yes. OpenShorts self-hosted is free and open source under MIT, with no watermark and no usage cap, and it runs on your own machine with Docker. OpenShorts Cloud also has a free tier of 20 minutes a month with a watermark and no credit card. ${c.name} starts at ${c.entryPrice}.`,
  },
  {
    q: `Is there an open source alternative to ${c.name}?`,
    a: `OpenShorts is MIT-licensed and the full source is on GitHub at github.com/mutonby/openshorts. ${c.name} is closed source. Being able to read the pipeline matters if you need to audit what happens to your video or change how the reframing behaves.`,
  },
  {
    q: `Can I switch from ${c.name} without losing quality?`,
    a: `The pipelines are comparable on the core job. OpenShorts transcribes with faster-whisper at word level, detects scenes with PySceneDetect, and scores moments with Google Gemini 3.0 Flash, then reframes with MediaPipe face tracking stabilised against jitter. The honest difference is caption styling, where the commercial tools generally ship more presets.`,
  },
  {
    q: `Does OpenShorts put a watermark on clips?`,
    a: `Self-hosted, never. On OpenShorts Cloud the free 20-minute tier is watermarked; every paid plan from $12/month is not.`,
  },
])}

${sources([
  `${esc(c.name)} pricing, checked ${esc(c.checked)} on the vendor's public pricing page.`,
  `OpenShorts pipeline details from the project source at <a href="${SITE.repo}" rel="noopener">github.com/mutonby/openshorts</a>.`,
])}
`

  return {
    path: `/alternatives/${slug}`,
    title: `Free & Open Source ${c.name} Alternative | OpenShorts`,
    description: `OpenShorts vs ${c.name}, compared feature by feature with current pricing. Self-hosted is free and open source; hosted starts at $12/month. ${c.name} starts at ${c.entryPrice}.`,
    h1: `The free, open source ${c.name} alternative`,
    breadcrumb: [{ name: 'Alternatives', path: '/alternatives' }, { name: c.name }],
    tldr: [
      `OpenShorts is an open source AI clip generator you can run yourself for free, or use hosted from $12/month. ${esc(c.name)} is a closed-source cloud product starting at ${esc(c.entryPrice)}.`,
      `Both find viral moments in long video and reframe them to 9:16 with face tracking. OpenShorts adds dubbing into 30+ languages and AI UGC video with lip-synced actors. ${esc(c.name)} has the more polished caption library.`,
      `Pick ${esc(c.name)} if you want zero setup and nothing else matters. Pick OpenShorts if you want to self-host for privacy, keep costs near zero, or change how the pipeline behaves.`,
    ],
    body,
    faq: [
      {
        q: `Is there a free alternative to ${c.name}?`,
        a: `Yes. OpenShorts self-hosted is free and open source under MIT, with no watermark and no usage cap. OpenShorts Cloud has a free tier of 20 minutes a month and paid plans from $12/month. ${c.name} starts at ${c.entryPrice}.`,
      },
      {
        q: `Is there an open source alternative to ${c.name}?`,
        a: `OpenShorts is MIT-licensed with full source on GitHub. ${c.name} is closed source.`,
      },
      {
        q: `Does OpenShorts put a watermark on clips?`,
        a: `Self-hosted, never. On OpenShorts Cloud the free 20-minute tier is watermarked and every paid plan from $12/month is not.`,
      },
    ],
  }
}

const ALTERNATIVES = Object.keys(COMPETITORS)

const hubPage = () => ({
  path: '/alternatives',
  title: 'Open Source Alternatives to Opus Clip, Klap, Vizard & Submagic | OpenShorts',
  description:
    'Side-by-side comparisons of OpenShorts against the four main AI clipping tools, with current pricing checked July 2026. Self-hosted free, hosted from $12/month.',
  h1: 'Open source alternatives to the main AI clipping tools',
  breadcrumb: [{ name: 'Alternatives' }],
  tldr: [
    'OpenShorts is the only open source, self-hostable tool in this category. Every other tool on this page is a closed-source cloud service.',
    'Entry prices as of July 2026: OpenShorts $0 self-hosted or $12/month hosted, Submagic from $14/month, Opus Clip $15/month, Vizard $19.99/month, Klap $29/month.',
    'The tools are not interchangeable. Submagic does not detect moments at all, Klap does not let you tune the output, and Vizard expects you in a timeline. The individual comparisons below say where each one genuinely wins.',
  ],
  body: `
<h2>How these tools actually differ</h2>
<p>All five are described as "AI clipping tools", which hides the fact that they
do different jobs. Two of them take a long video and decide what to cut. One of
them only styles captions on a clip you cut yourself. One is really an editor
with an AI first pass. Choosing on price alone is how people end up paying for
two tools that each do half the work.</p>

<h2>Entry pricing side by side</h2>
<p class="checked">Pricing checked 2026-07-27. Verify on the vendor's site before buying.</p>
<table>
<thead><tr><th>Tool</th><th>Entry price</th><th>Open source</th><th>Finds moments for you</th></tr></thead>
<tbody>
<tr><td class="os">OpenShorts</td><td class="os">$0 self-hosted, $12/mo hosted</td><td class="yes">Yes, MIT</td><td>Yes</td></tr>
<tr><td>Submagic</td><td>From $14/mo</td><td>No</td><td>No, captions only</td></tr>
<tr><td>Opus Clip</td><td>$15/mo</td><td>No</td><td>Yes</td></tr>
<tr><td>Vizard</td><td>$19.99/mo</td><td>No</td><td>Yes, then you edit</td></tr>
<tr><td>Klap</td><td>$29/mo</td><td>No</td><td>Yes</td></tr>
</tbody>
</table>

<h2>What does OpenShorts cost?</h2>
${pricingParagraph}

${faqBlock([
  {
    q: 'What is the cheapest AI clip generator?',
    a: 'OpenShorts self-hosted is free with no cap, but you supply the machine and your own Google Gemini API key, whose free tier covers 1,500 requests a day. Among hosted products, OpenShorts Cloud is the cheapest paid entry at $12/month, followed by Submagic from $14/month and Opus Clip at $15/month.',
  },
  {
    q: 'Which AI clipping tools are open source?',
    a: 'OpenShorts is MIT-licensed with full source on GitHub. Opus Clip, Klap, Vizard and Submagic are all closed-source commercial products.',
  },
])}
`,
  faq: [
    {
      q: 'What is the cheapest AI clip generator?',
      a: 'OpenShorts self-hosted is free with no cap. Among hosted products OpenShorts Cloud is the cheapest paid entry at $12/month, followed by Submagic from $14/month and Opus Clip at $15/month.',
    },
    {
      q: 'Which AI clipping tools are open source?',
      a: 'OpenShorts is MIT-licensed with full source on GitHub. Opus Clip, Klap, Vizard and Submagic are closed-source commercial products.',
    },
  ],
})

const freeClipGenerator = () => ({
  path: '/free-ai-clip-generator',
  title: 'Free AI Clip Generator (Open Source, No Watermark) | OpenShorts',
  description:
    'A genuinely free AI clip generator: MIT-licensed, self-hosted with Docker, no watermark and no usage cap. Hosted option from $12/month if you would rather not run it.',
  h1: 'A free AI clip generator that is actually free',
  breadcrumb: [{ name: 'Free AI clip generator' }],
  tldr: [
    'OpenShorts self-hosted is a free AI clip generator under the MIT licence. No watermark, no usage cap, no subscription. You run it with Docker and supply your own Google Gemini API key, whose free tier covers 1,500 requests a day.',
    'It turns a long video into 3 to 15 vertical clips: faster-whisper transcribes at word level, PySceneDetect finds the cuts, Gemini 3.0 Flash scores the moments, and MediaPipe face tracking reframes each one to 9:16.',
    'If you do not want to run anything, OpenShorts Cloud gives you 20 free minutes a month with a watermark, and paid plans from $12/month without one.',
  ],
  body: `
<h2>What does "free" actually mean here?</h2>
<p>Most tools marketed as free clip generators are free trials with a watermark
and a monthly cap. This one is different in a specific way that is worth stating
precisely, because the two editions are not the same offer.</p>
${pricingParagraph}
<p>The self-hosted edition has no watermark and no cap because there is no
metering code in it. It is the same pipeline the hosted service runs, released
under MIT, and you can read all of it.</p>

<h2>How do you generate clips from a long video for free?</h2>
<ol>
<li>Clone the repository from GitHub and start it with <code>docker compose up --build</code>.</li>
<li>Create a Google Gemini API key. The free tier covers 1,500 requests a day, which is far more than a single creator uses.</li>
<li>Paste a YouTube link or upload a local file. Podcasts, webinars, livestreams, interviews and vlogs all work.</li>
<li>The pipeline transcribes, detects scenes, scores moments and returns 3 to 15 clips of 15 to 60 seconds each, already cropped to 9:16 with subtitles burned in.</li>
<li>Download them, or connect an account and post straight to TikTok, Instagram Reels and YouTube Shorts.</li>
</ol>

<h2>What do you need to run it?</h2>
<p>Any machine with Docker. 8GB of RAM and a modern multi-core CPU is the
realistic floor. An NVIDIA GPU is optional and changes the numbers a lot: on CPU
an 8-minute video takes roughly 5 to 8 minutes to process, and on a GPU the same
video takes about 50 seconds. Linux, macOS and Windows via WSL2 all work, and
Docker Compose pulls Python 3.11, FFmpeg, YOLOv8, MediaPipe and faster-whisper
for you.</p>

<h2>Is a free clip generator good enough for real posting?</h2>
<p>It depends on what you are comparing against. The moment detection uses the
same class of model the paid tools use, Google Gemini 3.0 Flash, and the
reframing uses MediaPipe with a YOLOv8 fallback and a stabiliser that holds the
camera still inside a safe zone rather than chasing every head movement. Where
the commercial tools are ahead is caption styling: they ship more presets and
more polish. If your clips live or die on animated caption design, budget for
that either in time or in a second tool.</p>

<h2>Why does this matter for reach?</h2>
<p>Short-form video delivers the highest ROI of any content format, according to
HubSpot's State of Marketing 2025 report, and 91% of businesses use video as a
marketing tool according to Wyzowl's 2025 Video Marketing Statistics. The
constraint for most people is not whether short video works, it is that cutting a
60-minute recording into 12 posts by hand takes longer than recording it did.</p>

${faqBlock([
  {
    q: 'Is OpenShorts free forever or a trial?',
    a: 'The self-hosted edition is free forever under the MIT licence, with no watermark and no cap. It is not a trial and there is no metering in it. OpenShorts Cloud is a separate hosted service with a permanently free 20 minute per month tier and paid plans from $12/month.',
  },
  {
    q: 'Does the free version add a watermark?',
    a: 'The self-hosted edition never adds a watermark. The free tier of OpenShorts Cloud does; paid Cloud plans from $12/month do not.',
  },
  {
    q: 'Do I need to pay for an API key?',
    a: 'You need a Google Gemini API key for the self-hosted edition. Its free tier covers 1,500 requests a day, which is more than enough for individual use. ElevenLabs for dubbing and fal.ai for AI UGC video are optional and billed by those vendors. OpenShorts Cloud includes the keys.',
  },
  {
    q: 'How many clips does it generate per video?',
    a: 'Between 3 and 15, each 15 to 60 seconds long. The number depends on how much of the source actually holds up as a standalone clip rather than on a fixed quota.',
  },
])}
`,
  faq: [
    {
      q: 'Is OpenShorts free forever or a trial?',
      a: 'The self-hosted edition is free forever under MIT, with no watermark and no cap. OpenShorts Cloud is a separate hosted service with a free 20 minute per month tier and paid plans from $12/month.',
    },
    {
      q: 'Does the free version add a watermark?',
      a: 'The self-hosted edition never adds a watermark. The free tier of OpenShorts Cloud does; paid Cloud plans do not.',
    },
    {
      q: 'How many clips does it generate per video?',
      a: 'Between 3 and 15 clips, each 15 to 60 seconds long.',
    },
  ],
})

const openSourceClipper = () => ({
  path: '/open-source-video-clipper',
  title: 'Open Source Video Clipper, Self-Hosted with Docker | OpenShorts',
  description:
    'An MIT-licensed open source video clipper you can self-host. AI moment detection with Gemini, face-tracked 9:16 reframing, word-level subtitles and 30+ language dubbing.',
  h1: 'An open source video clipper you can self-host',
  breadcrumb: [{ name: 'Open source video clipper' }],
  tldr: [
    'OpenShorts is an MIT-licensed video clipper that runs entirely on your own hardware via Docker Compose. Source video never leaves the machine.',
    'The stack is Python 3.11, FastAPI, faster-whisper, PySceneDetect, MediaPipe, YOLOv8, FFmpeg and Google Gemini 3.0 Flash, with a React dashboard.',
    'It is the only open source tool in this category. Opus Clip, Klap, Vizard and Submagic are all closed-source cloud services.',
  ],
  body: `
<h2>Why self-host a video clipper at all?</h2>
<p>Three reasons come up repeatedly. The first is that unreleased footage,
client work and internal recordings should not be uploaded to a third party
whose retention policy you have not read. The second is cost at volume: a
per-minute cloud tool gets expensive quickly if you process long recordings
every week, whereas self-hosting costs electricity. The third is that the output
is opinionated, and if you disagree with how it reframes or where it cuts, having
the source means you can change it rather than file a feature request.</p>

<h2>What is in the pipeline?</h2>
${PIPELINE_STEPS.map((s) => `<h3>${esc(s.title)}</h3><p>${esc(s.body)}</p>`).join('')}

<h2>What does it run on?</h2>
<p>Docker Compose brings up the FastAPI backend and the React dashboard together.
The realistic floor is 8GB of RAM and a modern multi-core CPU; an NVIDIA GPU is
optional and takes an 8-minute video from roughly 5 to 8 minutes of processing
down to about 50 seconds. Linux, macOS and Windows via WSL2 are all supported.
Concurrency is controlled by a semaphore configured with MAX_CONCURRENT_JOBS.</p>

<h2>What is the licence?</h2>
<p>MIT for the core application, which means you can use it commercially, modify
it and redistribute it. The <code>cloud/</code> directory, which contains
billing, managed keys and the hosted-service infrastructure, is carved out under
a separate commercial licence and is not needed to self-host.</p>

<h2>How does it compare to the closed-source tools?</h2>
<p>OpenShorts is the only open source option in this category. As of July 2026,
Opus Clip starts at $15/month, Submagic from $14/month, Vizard at $19.99/month
and Klap at $29/month, and none of them can be self-hosted or audited. The
trade-off is real in both directions: they ship more caption presets and require
no setup, and you cannot read a line of what they do with your video.</p>

${faqBlock([
  {
    q: 'Is there an open source alternative to Opus Clip?',
    a: 'Yes. OpenShorts is MIT-licensed and self-hostable with Docker, and covers the same core job: AI moment detection, face-tracked 9:16 reframing and word-level subtitles. Opus Clip is closed source and cloud only, starting at $15/month.',
  },
  {
    q: 'Can I run it without sending video to any third party?',
    a: 'Transcription, scene detection, reframing and encoding all run locally. Moment scoring calls the Google Gemini API, which receives the transcript rather than the video file. Dubbing and AI UGC generation are optional and call ElevenLabs and fal.ai respectively; leave them off and nothing but transcript text leaves the machine.',
  },
  {
    q: 'What licence is OpenShorts released under?',
    a: 'MIT for the core application. The cloud/ directory covering billing and hosted infrastructure is under a separate commercial licence and is not required for self-hosting.',
  },
])}
`,
  faq: [
    {
      q: 'Is there an open source alternative to Opus Clip?',
      a: 'Yes. OpenShorts is MIT-licensed and self-hostable with Docker, covering AI moment detection, face-tracked 9:16 reframing and word-level subtitles. Opus Clip is closed source and cloud only.',
    },
    {
      q: 'What licence is OpenShorts released under?',
      a: 'MIT for the core application. The cloud/ directory covering billing and hosted infrastructure is under a separate commercial licence and is not required for self-hosting.',
    },
  ],
})

const howItWorks = () => ({
  path: '/how-openshorts-works',
  title: 'How OpenShorts Turns Long Video Into Vertical Clips | OpenShorts',
  description:
    'The full pipeline, stage by stage: word-level transcription, scene detection, Gemini moment scoring, face-tracked 9:16 reframing, subtitles, dubbing and publishing.',
  h1: 'How a long video becomes a vertical clip',
  breadcrumb: [{ name: 'How it works' }],
  tldr: [
    CANONICAL_ANSWERS.howItWorks,
    'The two stages that decide whether a clip is usable are moment scoring and reframing. Everything else is mechanical.',
    'OpenShorts self-hosted is free and open source under MIT, so every stage below can be read and changed. OpenShorts Cloud runs the same pipeline on a GPU from $12/month.',
  ],
  body: `
<h2>What is OpenShorts?</h2>
<p>${esc(CANONICAL_ANSWERS.whatIsIt)}</p>

<h2>The pipeline, stage by stage</h2>
${PIPELINE_STEPS.map((s) => `<h3>${esc(s.title)}</h3><p>${esc(s.body)}</p>`).join('')}

<h2>Why does moment scoring need the transcript and the scenes together?</h2>
<p>A transcript alone finds a good sentence but has no idea whether the shot cuts
halfway through it. Scene boundaries alone find clean cuts with nothing worth
saying between them. Passing both to the model at once is what lets it pick a
segment that is both quotable and visually intact, which is the difference
between a clip a person would watch and one that merely starts and stops in the
right places.</p>

<h2>Why does the camera hold still instead of following the face exactly?</h2>
<p>Because a crop that tracks a face frame by frame produces visible swinging,
and the swinging reads as amateur even when the framing is technically correct.
The reframing keeps a safe zone around the subject and only moves the crop when
they leave it, then damps the movement on the way. A speaker tracker sits on top
to stop the crop from flipping between people every time someone nods, and to
hold position through brief occlusions.</p>

<h2>How long does it take?</h2>
<p>On a typical CPU, an 8-minute source video takes roughly 5 to 8 minutes end to
end. On an NVIDIA GPU the same video takes about 50 seconds. The gap is almost
entirely transcription and encoding; the model call is a small fraction of it.</p>

<h2>What does it cost to run?</h2>
${pricingParagraph}

${faqBlock([
  {
    q: 'What AI model does OpenShorts use to find viral moments?',
    a: 'Google Gemini 3.0 Flash. It receives the word-level transcript with timestamps together with PySceneDetect scene boundaries, and returns 3 to 15 segments of 15 to 60 seconds scored on hook strength, emotional payload and whether the segment stands alone without surrounding context.',
  },
  {
    q: 'How does the automatic vertical cropping work?',
    a: 'Two modes. TRACK mode follows a single subject using MediaPipe face detection with a YOLOv8 fallback, stabilised so the crop holds still inside a safe zone instead of following every movement. GENERAL mode handles group shots and landscapes by preserving the full width over a blurred backdrop.',
  },
  {
    q: 'Can it dub clips into other languages?',
    a: 'Yes, into more than 30 languages via ElevenLabs, preserving the original speaker\'s voice characteristics. The dubbed audio is then re-transcribed so the burned-in subtitles match the new language rather than the original.',
  },
])}
`,
  faq: [
    {
      q: 'What AI model does OpenShorts use to find viral moments?',
      a: 'Google Gemini 3.0 Flash, which receives the word-level transcript with timestamps together with PySceneDetect scene boundaries and returns 3 to 15 segments of 15 to 60 seconds.',
    },
    {
      q: 'How does the automatic vertical cropping work?',
      a: 'TRACK mode follows a single subject with MediaPipe face detection and a YOLOv8 fallback, stabilised to hold still inside a safe zone. GENERAL mode preserves full width over a blurred backdrop for group shots and landscapes.',
    },
  ],
})

const mcpAgentsPage = () => ({
  path: '/mcp',
  title: 'Automate Video Clipping with AI Agents: MCP Server, API & Webhooks | OpenShorts',
  description:
    'OpenShorts ships a built-in MCP server, so Claude, ChatGPT, Cursor or n8n can clip and publish videos for you. REST API with keys, completion webhooks, self-hostable. Hosted from $12/month.',
  h1: 'Clip and publish video from an AI agent',
  breadcrumb: [{ name: 'MCP server and API' }],
  tldr: [
    'OpenShorts has a native MCP server at mcp.openshorts.app/mcp. Connect any MCP client, Claude, ChatGPT, Cursor or a custom agent, and a prompt like "clip this podcast and schedule the best three to TikTok" becomes one instruction instead of an afternoon in an editor.',
    'Six tools cover the whole pipeline: process_video, get_job_status, list_clips, get_quota, add_subtitles and publish_clip. There is also a plain REST API with per-user keys, and completion webhooks so pipelines never poll.',
    'The hosted service includes 20 free minutes a month and paid plans from $12/month; API calls draw from the same minutes as the dashboard. The self-hosted edition, free and MIT-licensed, serves the same MCP endpoint on your own machine.',
  ],
  body: `
<h2>What can an agent actually do with OpenShorts?</h2>
<p>Everything the dashboard does. The MCP server is not a wrapper around a
subset of features: each tool calls the same pipeline the web app uses, with the
same account, the same minutes and the same job history. An agent can take a
YouTube URL, turn it into 3 to 15 vertical clips with word-level captions,
restyle those captions, and publish or schedule the result to TikTok, Instagram
Reels and YouTube Shorts.</p>

<h2>How do I connect Claude or another MCP client?</h2>
<ol>
<li>Sign in at openshorts.app and create an API key in your account page. The key is shown once and starts with <code>osk_</code>.</li>
<li>Add the server to your client. With Claude Code:</li>
</ol>
<pre><code>claude mcp add --transport http openshorts https://mcp.openshorts.app/mcp \\
  --header "Authorization: Bearer osk_..."</code></pre>
<p>Any client that speaks Streamable HTTP works the same way: the endpoint is
<code>https://mcp.openshorts.app/mcp</code> and the key travels as a Bearer
token. The server describes itself over the protocol, tool schemas included, so
there is nothing else to configure.</p>

<h2>What tools does the MCP server expose?</h2>
<table>
<thead><tr><th>Tool</th><th>What it does</th></tr></thead>
<tbody>
<tr><td><code>process_video</code></td><td>Starts clipping a video from a URL. Returns a job id immediately; processing takes minutes.</td></tr>
<tr><td><code>get_job_status</code></td><td>Progress, recent log lines, and the clips once the job completes.</td></tr>
<tr><td><code>list_clips</code></td><td>Titles, durations, platform-ready descriptions and download URLs for a finished job.</td></tr>
<tr><td><code>get_quota</code></td><td>Plan and remaining minutes, so an agent can check before starting a large job.</td></tr>
<tr><td><code>add_subtitles</code></td><td>Restyles the burned-in captions of one clip, classic or karaoke word highlighting.</td></tr>
<tr><td><code>publish_clip</code></td><td>Posts or schedules one clip to TikTok, Instagram or YouTube through the connected account.</td></tr>
</tbody>
</table>

<h2>Can I use a plain REST API instead of MCP?</h2>
<p>Yes. The same <code>osk_</code> key authenticates against the REST API, and
interactive documentation lives at
<a href="https://api.openshorts.app/docs" rel="noopener">api.openshorts.app/docs</a>
with the OpenAPI spec at <code>/openapi.json</code>. A processing job is one
request:</p>
<pre><code>curl -X POST https://api.openshorts.app/api/process \\
  -H "Authorization: Bearer osk_..." -H "Content-Type: application/json" \\
  -d '{"url": "https://youtube.com/watch?v=...", "acknowledged": true,
       "webhook_url": "https://your-server.com/hooks/openshorts"}'</code></pre>

<h2>How do completion webhooks work?</h2>
<p>Pass <code>webhook_url</code> when starting a job and OpenShorts sends
exactly one POST when the job finishes or fails, with clip titles and download
links in the body. Add a <code>webhook_secret</code> and the body is signed with
HMAC-SHA256 in the <code>X-OpenShorts-Signature</code> header so your receiver
can verify the sender. This is what lets an n8n, Zapier or cron pipeline run
without a polling loop.</p>

<h2>Does this work on the self-hosted edition?</h2>
<p>Yes. The self-hosted edition serves the same <code>/mcp</code> endpoint on
your own machine, with no API key required because there is no account system:
it follows the same bring-your-own-key rules as the rest of the self-hosted app.
Point your MCP client at <code>http://localhost:8000/mcp</code> and the same six
tools appear.</p>

<h2>What does it cost?</h2>
${pricingParagraph}
<p>API and MCP calls are not billed separately: they draw from the same minute
balance as the dashboard, so automation does not change the price of anything.</p>

${faqBlock([
  {
    q: 'Does OpenShorts have an MCP server?',
    a: 'Yes, a native one at mcp.openshorts.app/mcp using the Streamable HTTP transport. It exposes six tools covering the full pipeline: process_video, get_job_status, list_clips, get_quota, add_subtitles and publish_clip. Authentication is an API key created in the dashboard, sent as a Bearer token.',
  },
  {
    q: 'Can Claude or ChatGPT create video clips with OpenShorts?',
    a: 'Yes. Any MCP-capable client, including Claude and ChatGPT, can connect to mcp.openshorts.app/mcp with an API key and drive the whole flow: submit a video URL, wait for processing, list the generated clips and publish them to TikTok, Instagram or YouTube.',
  },
  {
    q: 'Is there an API for OpenShorts?',
    a: 'Yes, a REST API documented at api.openshorts.app/docs, authenticated with per-user osk_ keys created in the dashboard. It covers processing, status, subtitles, publishing and completion webhooks.',
  },
  {
    q: 'Do API calls cost extra?',
    a: 'No. API and MCP usage draws from the same minute balance as the dashboard: 20 free minutes a month on the hosted free tier, and paid plans from $12/month. The self-hosted edition is free under MIT and serves the same endpoints with no metering.',
  },
])}

${sources([
  `MCP specification and transports at <a href="https://modelcontextprotocol.io" rel="noopener">modelcontextprotocol.io</a>.`,
  `OpenShorts server implementation in the project source at <a href="${SITE.repo}" rel="noopener">github.com/mutonby/openshorts</a>.`,
])}
`,
  faq: [
    {
      q: 'Does OpenShorts have an MCP server?',
      a: 'Yes, a native MCP server at mcp.openshorts.app/mcp with six tools covering the full pipeline, authenticated with an API key created in the dashboard.',
    },
    {
      q: 'Can Claude or ChatGPT create video clips with OpenShorts?',
      a: 'Yes. Any MCP-capable client can connect with an API key and drive the whole flow from video URL to published clip.',
    },
    {
      q: 'Do API calls cost extra?',
      a: 'No. API and MCP usage draws from the same minute balance as the dashboard: 20 free minutes a month on the hosted free tier, paid plans from $12/month, and the self-hosted edition is free under MIT.',
    },
  ],
})

export function buildPages() {
  return [
    hubPage(),
    ...ALTERNATIVES.map(competitorPage),
    freeClipGenerator(),
    openSourceClipper(),
    howItWorks(),
    mcpAgentsPage(),
  ]
}

/* Each page links to three siblings. Small, described clusters beat a single
 * dump of every URL: the described link tells an engine what it will find. */
export function relatedFor(page, all) {
  const blurb = {
    '/alternatives': 'All four tools compared, with entry pricing.',
    '/alternatives/opus-clip': 'Per-minute credits, 720p vs 1080p, and where each one wins.',
    '/alternatives/klap': 'Fastest URL-to-clip path, and what you give up for it.',
    '/alternatives/vizard': 'Timeline editing after the AI pass, and who needs it.',
    '/alternatives/submagic': 'Captions only, so it does not replace a clipper.',
    '/free-ai-clip-generator': 'What free means when there is no metering code.',
    '/open-source-video-clipper': 'Self-hosting with Docker, and the MIT licence carve-out.',
    '/how-openshorts-works': 'The full pipeline, stage by stage.',
    '/mcp': 'Drive the whole pipeline from Claude, ChatGPT or n8n.',
  }
  // Walk the ring starting after this page so each page links to a different
  // three. Slicing the same head every time would leave the last pages in the
  // list with no inbound links at all.
  const i = all.findIndex((p) => p.path === page.path)
  return [1, 2, 3]
    .map((n) => all[(i + n) % all.length])
    .filter((p) => p && p.path !== page.path)
    .map((p) => ({ path: p.path, title: p.h1, blurb: blurb[p.path] || p.description }))
}
