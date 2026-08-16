# Channel branding assets

Drop your own PNGs here and set `BRAND_WATERMARK=1` in `.env`. `branding.py`
burns them into every finished clip, on one line just under the platform's top
bar: logo on the left, badge on the right.

| File | Default ratio | Where it lands |
|---|---|---|
| `logo.png` | 22% of the clip width | left, at the left margin |
| `twitch.png` | 16% of the clip width | right, at the right margin |

Either file may be absent — a logo with no badge, or a badge alone, both render
fine. With neither, branding does nothing: silently if `BRAND_WATERMARK` is off,
and with one warning per run (not per clip) if it is on, so an enabled-but-empty
setup tells you rather than looking like it worked.

## What makes a good asset

- **Transparent background**, unless the mark is meant to read as a card. The
  PNG is alpha-composited straight onto the footage, so whatever is opaque in it
  shows up exactly as it is.
- **Any aspect ratio works.** A tall or square mark is scaled down until the
  band fits its strip (`MAX_BAND_HEIGHT_RATIO`), so it can never run into the
  hook card. Only the mark that would break the band is scaled — a square logo
  never shrinks the badge beside it. A wide lockup (3:1 or flatter) is still the
  best use of the space, since it stays at full size on a vertical clip.
- **At least ~500px wide natively**, ideally more. The mark is downscaled to a
  few hundred pixels and downscaling stays sharp; upscaling does not.
  `assets/watermark.png` is 1121×256 for exactly this reason.
- **Light marks with a soft dark shadow** survive bright and dark footage alike.
  `assets/make_watermark.py` shows the technique (render at 4×, paste the alpha
  as a blurred black layer underneath).

## Tuning

Every number is an env var, so none of this needs a code change:

| Variable | Default | Meaning |
|---|---|---|
| `BRAND_WATERMARK` | `0` | Master switch |
| `BRAND_DIR` | `assets/brand` | Where the PNGs live |
| `BRAND_LOGO` / `BRAND_BADGE` | `logo.png` / `twitch.png` | Filenames |
| `BRAND_Y_RATIO` | `0.13` | **Top** of the band, as a fraction of the height |
| `BRAND_MARGIN_RATIO` | `0.05` | Side inset |
| `BRAND_LOGO_WIDTH_RATIO` | `0.22` | Logo width, as a fraction of the width |
| `BRAND_BADGE_WIDTH_RATIO` | `0.16` | Badge width |
| `BRAND_OPACITY` | `0.85` | Alpha applied on top of the PNG's own |

`BRAND_Y_RATIO` has a real floor and ceiling: below ~0.12 the mark disappears
under TikTok's tabs and Shorts' search icons, and above ~0.55 it runs into the
burned captions. See the docstring in `branding.py` for the full band map.

## Regenerating the badge

`assets/make_brand_badge.py` renders `twitch.png` from the repo's own font, so
it runs in the container too:

```bash
python assets/make_brand_badge.py --handle la_scene_avolo --bg '#001979'
```

The script is versioned, the PNG it writes is not — same split as the rest of
this directory.

## Deploying

`docker-compose.yml` bind-mounts the repo at `/app`, so in local development
dropping a file here is enough — no rebuild. For a built image, `COPY . .` in
the Dockerfile picks the PNGs up at build time.

The PNGs themselves are gitignored (`assets/brand/*.png`): a channel's logo is
not something to publish to a public fork. This README is versioned so the
convention travels.
