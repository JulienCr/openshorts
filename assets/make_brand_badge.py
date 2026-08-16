"""Render the secondary brand badge (a Twitch handle) to assets/brand/twitch.png.

Same idea as make_watermark.py — pre-render at 4x so it downscales cleanly onto
any clip size — but portable: it uses the font this repo ships in fonts/ rather
than a macOS system font, so it runs in the container too.

    python assets/make_brand_badge.py --handle la_scene_avolo

The output is gitignored along with the rest of assets/brand/, so this script is
the versioned part: the badge can always be regenerated, and the channel's
artwork never lands in a public fork.

Defaults are Avolo's palette, sampled from the logo. Override for another
channel with --fg / --bg.
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 4                       # supersampling
HEIGHT = 56 * S             # badge height before downscaling
PAD_X = 22 * S
RADIUS = 14 * S

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(REPO, "fonts", "Anton-Regular.ttf")


def twitch_glyph(height, colour, bg):
    """The Twitch mark, drawn as polygons on a 20x22 grid.

    Drawn rather than embedded: bundling Twitch's actual logo file would put a
    third-party trademark in the repo, and at badge size the silhouette is what
    reads anyway. The two knocked-out bars are what make it read as Twitch
    rather than as a generic speech bubble, so they are punched in the badge's
    own background colour instead of being left as holes.
    """
    u = height / 22.0
    img = Image.new("RGBA", (int(round(20 * u)), height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Chamfered top-left, chamfered bottom-right, tail hanging bottom-left.
    d.polygon([(0, 4 * u), (4 * u, 0), (20 * u, 0), (20 * u, 12 * u),
               (14 * u, 18 * u), (10 * u, 18 * u), (6 * u, 22 * u),
               (6 * u, 18 * u), (0, 18 * u)], fill=colour)
    for x in (8, 13):
        d.rectangle([x * u, 5 * u, (x + 1.8) * u, 12 * u], fill=bg)
    return img


def build(handle, fg, bg, out_path):
    font = ImageFont.truetype(FONT_PATH, int(26 * S))
    text = handle if handle.startswith("/") else f"/{handle}"

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tb = probe.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]

    glyph = twitch_glyph(int(HEIGHT * 0.52), fg, bg)
    gap = int(10 * S)
    width = PAD_X * 2 + glyph.width + gap + tw

    card = Image.new("RGBA", (width, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([0, 0, width - 1, HEIGHT - 1], radius=RADIUS, fill=bg)

    card.alpha_composite(glyph, (PAD_X, (HEIGHT - glyph.height) // 2))
    d.text((PAD_X + glyph.width + gap - tb[0], (HEIGHT - th) // 2 - tb[1]),
           text, font=font, fill=fg)

    # Soft shadow so the badge separates from bright footage, exactly as the
    # OpenShorts lockup does.
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 150), (0, 0), card.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(2.5 * S)))
    out = Image.alpha_composite(shadow, card)

    out.save(out_path)
    print(f"{out_path} {out.size} ({out.size[0] / out.size[1]:.1f}:1)")


def _colour(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--handle", default="la_scene_avolo")
    # Sampled from the Avolo logo: white on navy, with the orange kept for the
    # logo itself so the two marks do not compete.
    p.add_argument("--fg", type=_colour, default="#FFFFFF")
    p.add_argument("--bg", type=_colour, default="#001979")
    p.add_argument("--out", default=os.path.join(REPO, "assets", "brand", "twitch.png"))
    a = p.parse_args()
    # abspath first: a bare filename gives dirname "" and os.makedirs("") raises
    # before anything is written.
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    build(a.handle, a.fg, a.bg, out)
