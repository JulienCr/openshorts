"""Tests for the channel-branding overlay geometry.

plan_marks/build_filter are pure, so the whole safe-zone argument is checked
here without rendering a frame. The numbers that matter are the *bands*: the
platform chrome above, the hook card below, the captions below that.
"""
import pytest
from PIL import Image

import branding
from branding import (Placement, build_filter, marks_collide, plan_marks)

# The bands the branding line has to thread between, as fractions of the height.
PLATFORM_CHROME_BOTTOM = 0.12   # TikTok's tabs / Shorts' search icons
HOOK_TOP_POSITION = 0.20        # hooks.py: int(video_height * 0.20)
CAPTION_TOP = 0.59              # subtitles.py SAFE_MARGIN_V, two lines

LOGO = ("assets/brand/logo.png", (900, 300), branding.LOGO_WIDTH_RATIO, "left")
BADGE = ("assets/brand/twitch.png", (1200, 200), branding.BADGE_WIDTH_RATIO, "right")


class TestHorizontalPlacement:
    def test_logo_hugs_the_left_margin(self):
        logo, _ = plan_marks(1080, 1920, [LOGO, BADGE])
        assert logo.x == int(1080 * branding.MARGIN_RATIO)

    def test_badge_hugs_the_right_margin(self):
        _, badge = plan_marks(1080, 1920, [LOGO, BADGE])
        margin = int(1080 * branding.MARGIN_RATIO)
        assert badge.x + badge.width == 1080 - margin

    def test_nothing_leaves_the_frame(self):
        for p in plan_marks(1080, 1920, [LOGO, BADGE]):
            assert p.x >= 0
            assert p.x + p.width <= 1080

    def test_defaults_do_not_collide(self):
        assert not marks_collide(plan_marks(1080, 1920, [LOGO, BADGE]))

    def test_collision_is_detected_when_ratios_are_pushed(self, monkeypatch):
        monkeypatch.setattr(branding, "LOGO_WIDTH_RATIO", 0.55)
        monkeypatch.setattr(branding, "BADGE_WIDTH_RATIO", 0.50)
        wide = [(LOGO[0], LOGO[1], 0.55, "left"), (BADGE[0], BADGE[1], 0.50, "right")]
        assert marks_collide(plan_marks(1080, 1920, wide))


class TestVerticalPlacement:
    def test_marks_share_a_centre_line_despite_different_aspects(self):
        logo, badge = plan_marks(1080, 1920, [LOGO, BADGE])
        # Different aspect ratios (3:1 vs 6:1) mean different heights; aligning
        # tops instead of centres is what would look lopsided.
        assert logo.height != badge.height
        assert abs((logo.y + logo.height / 2) - (badge.y + badge.height / 2)) <= 1

    def test_band_clears_the_platform_chrome(self):
        for p in plan_marks(1080, 1920, [LOGO, BADGE]):
            assert p.y >= 1920 * PLATFORM_CHROME_BOTTOM

    @pytest.mark.parametrize("native", [(900, 300), (900, 900), (900, 150)])
    def test_top_edge_is_pinned_whatever_the_logo_aspect(self, native):
        """Y_RATIO is the band's top edge, not its centre.

        Regression: anchoring the centre made the top edge depend on the logo's
        aspect ratio, and a 3:1 logo then reached y=0.109 — back under TikTok's
        tabs. A taller asset must grow downwards, never up.
        """
        logo = (LOGO[0], native, LOGO[2], "left")
        placements = plan_marks(1080, 1920, [logo, BADGE])
        assert min(p.y for p in placements) == int(1920 * branding.Y_RATIO)

    def test_band_clears_the_hook_card(self):
        for p in plan_marks(1080, 1920, [LOGO, BADGE]):
            assert p.y + p.height <= 1920 * HOOK_TOP_POSITION

    def test_band_is_far_above_the_captions(self):
        for p in plan_marks(1080, 1920, [LOGO, BADGE]):
            assert p.y + p.height < 1920 * CAPTION_TOP


class TestResolutionIndependence:
    @pytest.mark.parametrize("vw,vh", [(1080, 1920), (2160, 3840), (720, 1280)])
    def test_geometry_scales_with_the_frame(self, vw, vh):
        """reframe_v2.delivery_size() never downscales, so a 4K source is
        delivered at 2160x3840 — nothing here may be pinned to 1080."""
        for p in plan_marks(vw, vh, [LOGO, BADGE]):
            assert 0 <= p.x and p.x + p.width <= vw
            assert vh * PLATFORM_CHROME_BOTTOM <= p.y
            assert p.y + p.height <= vh * HOOK_TOP_POSITION

    def test_4k_marks_are_twice_the_1080_marks(self):
        small = plan_marks(1080, 1920, [LOGO])[0]
        large = plan_marks(2160, 3840, [LOGO])[0]
        # ±1 for integer truncation: int(2160*0.22)=475, not 2*int(1080*0.22).
        assert abs(large.width - small.width * 2) <= 1
        assert abs(large.y - small.y * 2) <= 1

    def test_tiny_frame_still_gets_a_legible_mark(self):
        # 0.22 of a 200px-wide frame is 44px, below the legibility floor.
        assert plan_marks(200, 356, [LOGO])[0].width == branding.MIN_MARK_WIDTH


class TestFilterGraph:
    def test_two_marks_chain_through_an_intermediate_label(self):
        graph = build_filter(plan_marks(1080, 1920, [LOGO, BADGE]), opacity=0.85)
        assert "[1:v]scale=" in graph and "[2:v]scale=" in graph
        assert "[0:v][m0]overlay=" in graph
        assert "[b0]" in graph
        # The final overlay must stay unlabelled — that is what makes it the
        # graph's implicit output.
        assert not graph.rstrip().endswith("]")

    def test_single_mark_needs_no_intermediate_label(self):
        graph = build_filter(plan_marks(1080, 1920, [LOGO]), opacity=0.85)
        assert graph.count("overlay=") == 1
        assert "[b0]" not in graph
        assert "[2:v]" not in graph

    def test_opacity_reaches_the_graph(self):
        graph = build_filter(plan_marks(1080, 1920, [LOGO]), opacity=0.5)
        assert "colorchannelmixer=aa=0.5" in graph

    def test_scale_is_explicit_on_both_axes(self):
        """Not scale=W:-1 — the plan and the graph must agree on the height the
        vertical centring was computed from."""
        p = plan_marks(1080, 1920, [LOGO])[0]
        assert f"scale={p.width}:{p.height}" in build_filter([p])


class TestAssetDiscovery:
    def _write_png(self, path, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", size, (255, 255, 255, 255)).save(path)

    def test_both_assets_found(self, tmp_path, monkeypatch):
        self._write_png(tmp_path / "logo.png", (900, 300))
        self._write_png(tmp_path / "twitch.png", (1200, 200))
        monkeypatch.setattr(branding, "BRAND_DIR", str(tmp_path))
        assert len(branding._collect_marks()) == 2
        assert branding.assets_present()

    def test_badge_alone_is_a_valid_setup(self, tmp_path, monkeypatch):
        self._write_png(tmp_path / "twitch.png", (1200, 200))
        monkeypatch.setattr(branding, "BRAND_DIR", str(tmp_path))
        marks = branding._collect_marks()
        assert len(marks) == 1
        assert marks[0][3] == "right"
        assert plan_marks(1080, 1920, marks)[0].x > 540

    def test_no_assets_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(branding, "BRAND_DIR", str(tmp_path))
        assert branding._collect_marks() == []
        assert not branding.assets_present()


class TestApplyBrandingGuards:
    """apply_branding must never fail a job — an unbranded clip still ships."""

    def test_disabled_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(branding, "ENABLED", False)
        monkeypatch.setattr(branding, "probe_size",
                            lambda p: pytest.fail("must not probe when disabled"))
        assert branding.apply_branding("/nonexistent.mp4") is False

    def test_enabled_without_assets_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setattr(branding, "ENABLED", True)
        monkeypatch.setattr(branding, "BRAND_DIR", str(tmp_path))
        monkeypatch.setattr(branding, "probe_size",
                            lambda p: pytest.fail("must not probe without assets"))
        assert branding.apply_branding("/nonexistent.mp4") is False

    def test_unprobeable_clip_is_a_no_op(self, tmp_path, monkeypatch):
        Image.new("RGBA", (900, 300)).save(tmp_path / "logo.png")
        monkeypatch.setattr(branding, "ENABLED", True)
        monkeypatch.setattr(branding, "BRAND_DIR", str(tmp_path))
        monkeypatch.setattr(branding, "probe_size", lambda p: None)
        assert branding.apply_branding("/nonexistent.mp4") is False
