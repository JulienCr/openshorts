"""Tests for hook overlay text handling (emoji runs, long-word wrapping)."""
import os

from PIL import Image, ImageDraw, ImageFont

from hooks import (
    _split_emoji_runs,
    _break_long_word,
    _EMOJI_RE,
    create_hook_image,
    hook_overlay_geometry,
)


def _draw_and_font():
    img = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(img)
    return draw, ImageFont.load_default()


class TestOverlayGeometry:
    """Where the hook card lands. Shared by /api/hook and the pipeline's
    automatic hook, so the two can never drift apart."""

    def test_centred_horizontally(self):
        x, _ = hook_overlay_geometry(1080, 1920, 600, 200, "top")
        assert x == (1080 - 600) // 2

    def test_top_sits_at_a_fifth_of_the_height(self):
        _, y = hook_overlay_geometry(1080, 1920, 600, 200, "top")
        assert y == int(1920 * 0.20)

    def test_bottom_sits_low_but_not_off_frame(self):
        _, y = hook_overlay_geometry(1080, 1920, 600, 200, "bottom")
        assert y == int(1920 * 0.70)

    def test_center_is_vertically_centred(self):
        _, y = hook_overlay_geometry(1080, 1920, 600, 200, "center")
        assert y == (1920 - 200) // 2

    def test_unknown_position_falls_back_to_top(self):
        assert hook_overlay_geometry(1080, 1920, 600, 200, "sideways") == \
               hook_overlay_geometry(1080, 1920, 600, 200, "top")


class TestHookFont:
    """The hook renders through PIL with its own font, entirely separate from
    the libass/fontconfig path the captions use. The style preset has to be
    able to set it, or 'one font everywhere' is unreachable."""

    ANTON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "fonts", "Anton-Regular.ttf")

    def test_font_path_changes_the_rendered_box(self, tmp_path):
        out = tmp_path / "hook.png"
        _, default_w, _ = create_hook_image("Stop doing this", 800, str(out))
        _, anton_w, _ = create_hook_image("Stop doing this", 800, str(out),
                                          font_path=self.ANTON)
        # Anton is a condensed display face; the same string cannot occupy the
        # same width as Noto Serif Bold. If these match, the parameter was
        # accepted and ignored.
        assert anton_w != default_w

    def test_missing_font_still_renders(self, tmp_path):
        # A preset naming a font that isn't installed must cost the hook's
        # looks, never the clip.
        out = tmp_path / "hook.png"
        path, w, h = create_hook_image("Stop doing this", 800, str(out),
                                       font_path="/nonexistent/Fake.ttf")
        assert os.path.exists(path)
        assert w > 0 and h > 0


class TestEmojiRuns:
    def test_split_mixed_text(self):
        assert _split_emoji_runs("Feuer 🔥 test") == [
            (False, "Feuer "),
            (True, "🔥"),
            (False, " test"),
        ]

    def test_plain_text_single_run(self):
        assert _split_emoji_runs("nur text") == [(False, "nur text")]

    def test_emoji_only(self):
        assert _split_emoji_runs("🔥🚀") == [(True, "🔥🚀")]

    def test_strip_regex(self):
        assert _EMOJI_RE.sub("", "Stop 🛑 doing this! 💯") == "Stop  doing this! "


class TestLongWordWrap:
    def test_pieces_fit_and_recombine(self):
        draw, font = _draw_and_font()
        word = "A" * 60
        max_width = 50
        pieces = _break_long_word(draw, word, font, None, max_width)
        assert len(pieces) > 1
        assert "".join(pieces) == word
        for piece in pieces:
            assert draw.textlength(piece, font=font) <= max_width

    def test_short_word_single_piece(self):
        draw, font = _draw_and_font()
        assert _break_long_word(draw, "kurz", font, None, 1000) == ["kurz"]
