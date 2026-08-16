"""Tests for the channel-branding overlay geometry.

plan_marks/build_filter are pure, so the whole safe-zone argument is checked
here without rendering a frame. The numbers that matter are the *bands*: the
platform chrome above, the hook card below, the captions below that.
"""
import os
import subprocess

import pytest
from PIL import Image

import branding
from branding import build_filter, marks_collide, plan_marks, settings

# The bands the branding line has to thread between, as fractions of the height.
PLATFORM_CHROME_BOTTOM = 0.12   # TikTok's tabs / Shorts' search icons
HOOK_TOP_POSITION = 0.20        # hooks.py: int(video_height * 0.20)
CAPTION_TOP = 0.59              # subtitles.py SAFE_MARGIN_V, two lines


@pytest.fixture(autouse=True)
def _isolate_brand_env(monkeypatch):
    """Every BRAND_* override off, for every test in this module.

    Without it these assertions read the developer's own environment: anyone
    following the new .env documentation (BRAND_WATERMARK=1, a custom ratio)
    would see the hard-coded geometry numbers fail for reasons that have nothing
    to do with the code.
    """
    for key in [k for k in os.environ if k.startswith("BRAND_")]:
        monkeypatch.delenv(key, raising=False)


# Built from the DEFAULT_* constants rather than settings(), because module
# scope runs before the fixture above can clear anything.
CFG = branding.Settings(
    enabled=False,
    brand_dir=branding.DEFAULT_BRAND_DIR,
    logo_file=branding.DEFAULT_LOGO_FILE,
    badge_file=branding.DEFAULT_BADGE_FILE,
    y_ratio=branding.DEFAULT_Y_RATIO,
    margin_ratio=branding.DEFAULT_MARGIN_RATIO,
    logo_width_ratio=branding.DEFAULT_LOGO_WIDTH_RATIO,
    badge_width_ratio=branding.DEFAULT_BADGE_WIDTH_RATIO,
    opacity=branding.DEFAULT_OPACITY,
)

LOGO = ("assets/brand/logo.png", (900, 300), CFG.logo_width_ratio, "left")
BADGE = ("assets/brand/twitch.png", (1200, 200), CFG.badge_width_ratio, "right")
SQUARE_LOGO = ("assets/brand/logo.png", (900, 900), CFG.logo_width_ratio, "left")

# Every shape `output_format` can deliver. reframe_v2 never downscales, so 4K is
# in here too.
FRAME_SHAPES = [(1080, 1920), (2160, 3840), (720, 1280), (1920, 1080), (1080, 1080)]


class TestSettings:
    """Settings are read per call, never frozen at import.

    main.py's import block runs before its load_dotenv(), so an import-time read
    saw nothing from .env — the documented way to switch branding on. That made
    BRAND_WATERMARK=1 a silent no-op on every direct `python main.py` run.
    """

    def test_env_is_read_at_call_time(self, monkeypatch):
        assert settings().enabled is False
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        assert settings().enabled is True

    def test_ratios_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("BRAND_Y_RATIO", "0.42")
        assert settings().y_ratio == 0.42

    def test_defaults_apply_when_unset(self):
        assert settings().y_ratio == branding.DEFAULT_Y_RATIO
        assert settings().logo_file == branding.DEFAULT_LOGO_FILE

    def test_a_malformed_ratio_falls_back_instead_of_raising(self, monkeypatch):
        monkeypatch.setenv("BRAND_Y_RATIO", "not-a-number")
        assert settings().y_ratio == branding.DEFAULT_Y_RATIO

    @pytest.mark.parametrize("raw", [
        "nan", "inf", "-inf", "Infinity",   # float() accepts all of these
        "1e308",                            # finite, but int(vh * it) overflows
        "-0.5", "2", "1e9",                 # finite and in range for float(), not for a ratio
    ])
    def test_out_of_domain_ratios_fall_back(self, monkeypatch, raw):
        """Every setting here is a fraction, so the domain is [0, 1].

        Syntax checks alone were not enough, twice: nan/inf parse fine, and so
        does 1e308, which then overflows in plan_marks' int(vh * ratio) — past
        every guard, so the clip silently loses its captions.
        """
        monkeypatch.setenv("BRAND_Y_RATIO", raw)
        monkeypatch.setenv("BRAND_LOGO_WIDTH_RATIO", raw)
        assert settings().y_ratio == branding.DEFAULT_Y_RATIO
        assert settings().logo_width_ratio == branding.DEFAULT_LOGO_WIDTH_RATIO
        # And the geometry it feeds still produces a usable plan.
        assert plan_marks(1080, 1920, [LOGO])[0].width > 0

    @pytest.mark.parametrize("raw", ["0", "0.5", "1"])
    def test_the_domain_bounds_are_inclusive(self, monkeypatch, raw):
        monkeypatch.setenv("BRAND_OPACITY", raw)
        assert settings().opacity == float(raw)

    def test_module_never_froze_a_flag_at_import(self):
        """Regression guard: an ENABLED constant would reintroduce the bug."""
        assert not hasattr(branding, "ENABLED")


class TestHorizontalPlacement:
    def test_logo_hugs_the_left_margin(self):
        logo, _ = plan_marks(1080, 1920, [LOGO, BADGE])
        assert logo.x == int(1080 * CFG.margin_ratio)

    def test_badge_hugs_the_right_margin(self):
        _, badge = plan_marks(1080, 1920, [LOGO, BADGE])
        margin = int(1080 * CFG.margin_ratio)
        assert badge.x + badge.width == 1080 - margin

    def test_nothing_leaves_the_frame(self):
        for p in plan_marks(1080, 1920, [LOGO, BADGE]):
            assert p.x >= 0
            assert p.x + p.width <= 1080

    def test_defaults_do_not_collide(self):
        assert not marks_collide(plan_marks(1080, 1920, [LOGO, BADGE]))

    def test_collision_is_detected_when_ratios_are_pushed(self):
        # Very flat assets, so the band-height clamp never kicks in and the
        # widths alone are what overlap.
        flat = [(LOGO[0], (2000, 100), 0.55, "left"),
                (BADGE[0], (2000, 100), 0.50, "right")]
        assert marks_collide(plan_marks(1080, 1920, flat))


class TestVerticalPlacement:
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
        assert min(p.y for p in placements) == int(1920 * CFG.y_ratio)

    def test_marks_share_a_centre_line_despite_different_aspects(self):
        logo, badge = plan_marks(1080, 1920, [LOGO, BADGE])
        assert logo.height != badge.height
        assert abs((logo.y + logo.height / 2) - (badge.y + badge.height / 2)) <= 1

    def test_band_is_far_above_the_captions(self):
        for p in plan_marks(1080, 1920, [LOGO, BADGE]):
            assert p.y + p.height < 1920 * CAPTION_TOP


class TestBandHeightClamp:
    """Widths scale with the frame WIDTH, but the safe band is measured in
    HEIGHT — and `output_format` also delivers 1920x1080 and 1080x1080, where
    those two diverge by a factor of three. Unclamped, every combination except
    a wide lockup on a vertical clip printed over the hook card.
    """

    @pytest.mark.parametrize("vw,vh", FRAME_SHAPES)
    @pytest.mark.parametrize("logo", [LOGO, SQUARE_LOGO])
    def test_band_clears_the_hook_on_every_frame_and_asset_shape(self, vw, vh, logo):
        for p in plan_marks(vw, vh, [logo, BADGE]):
            assert p.y + p.height <= vh * HOOK_TOP_POSITION, \
                f"{vw}x{vh} logo={logo[1]} overruns the hook card"

    @pytest.mark.parametrize("vw,vh", FRAME_SHAPES)
    def test_band_never_exceeds_the_ceiling(self, vw, vh):
        placements = plan_marks(vw, vh, [SQUARE_LOGO, BADGE])
        top = min(p.y for p in placements)
        bottom = max(p.y + p.height for p in placements)
        assert bottom - top <= vh * branding.MAX_BAND_HEIGHT_RATIO + 1

    def test_the_intended_case_is_untouched_by_the_clamp(self):
        """A wide lockup on a vertical clip is 4.1% of the height, well under
        the 6% ceiling — the clamp must not change the case it was built for."""
        logo, badge = plan_marks(1080, 1920, [LOGO, BADGE])
        assert (logo.width, logo.height) == (237, 79)
        assert (badge.width, badge.height) == (172, 29)

    def test_a_tall_logo_does_not_shrink_the_badge(self):
        """Only the mark that breaks the band gets clamped.

        Regression: scaling every mark by the tallest one's excess made a square
        logo drag the handle down to 83x19 on a 1080x1920 clip — unreadable on a
        phone. The badge already fits; there is nothing to gain by touching it.
        """
        with_wide = plan_marks(1080, 1920, [LOGO, BADGE])[1]
        with_square = plan_marks(1080, 1920, [SQUARE_LOGO, BADGE])[1]
        assert (with_square.width, with_square.height) == \
               (with_wide.width, with_wide.height)

    def test_only_the_offending_mark_is_scaled(self):
        square, badge = plan_marks(1080, 1920, [SQUARE_LOGO, BADGE])
        assert square.height == int(1920 * branding.MAX_BAND_HEIGHT_RATIO)
        assert square.width == square.height  # a 1:1 asset stays 1:1
        assert badge.height < square.height   # untouched, already fitted


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

    def test_min_width_floor_lifts_a_mark_on_a_small_frame(self):
        # 0.22 of a 500px-wide frame is 110px; drop the ratio so the floor is
        # what decides, with room left under the height ceiling.
        small = (LOGO[0], LOGO[1], 0.05, "left")
        assert plan_marks(500, 889, [small])[0].width == branding.MIN_MARK_WIDTH

    def test_the_band_ceiling_outranks_the_legibility_floor(self):
        """Documented priority: an unreadably small mark is cosmetic, a mark
        printed over the hook card is a broken frame. The ceiling wins."""
        p = plan_marks(200, 356, [LOGO])[0]
        assert p.width < branding.MIN_MARK_WIDTH
        assert p.height <= 356 * branding.MAX_BAND_HEIGHT_RATIO + 1


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
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        assert len(branding._collect_marks()) == 2
        assert branding.assets_present()

    def test_badge_alone_is_a_valid_setup(self, tmp_path, monkeypatch):
        self._write_png(tmp_path / "twitch.png", (1200, 200))
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        marks = branding._collect_marks()
        assert len(marks) == 1
        assert marks[0][3] == "right"
        assert plan_marks(1080, 1920, marks)[0].x > 540

    def test_no_assets_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        assert branding._collect_marks() == []
        assert not branding.assets_present()

    def test_custom_filenames_are_honoured(self, tmp_path, monkeypatch):
        self._write_png(tmp_path / "avolo.png", (900, 300))
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        monkeypatch.setenv("BRAND_LOGO", "avolo.png")
        assert len(branding._collect_marks()) == 1


class TestApplyBrandingGuards:
    """apply_branding must never fail a job — an unbranded clip still ships."""

    @pytest.fixture
    def one_asset(self, tmp_path, monkeypatch):
        Image.new("RGBA", (900, 300)).save(tmp_path / "logo.png")
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        return tmp_path

    def test_disabled_is_a_no_op(self, monkeypatch):
        monkeypatch.delenv("BRAND_WATERMARK", raising=False)
        monkeypatch.setattr(branding, "probe_size",
                            lambda p: pytest.fail("must not probe when disabled"))
        assert branding.apply_branding("/nonexistent.mp4") is False

    def test_enabled_without_assets_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        monkeypatch.setattr(branding, "probe_size",
                            lambda p: pytest.fail("must not probe without assets"))
        assert branding.apply_branding("/nonexistent.mp4") is False

    def test_unprobeable_clip_is_a_no_op(self, one_asset, monkeypatch):
        monkeypatch.setattr(branding, "probe_size", lambda p: None)
        assert branding.apply_branding("/nonexistent.mp4") is False

    def test_ffmpeg_timeout_does_not_escape(self, one_asset, monkeypatch, tmp_path):
        """subprocess.run RAISES on timeout instead of returning.

        Letting TimeoutExpired out would fail a clip that had already rendered,
        skip its captions, and abort a --skip-analysis run outright.
        """
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x")
        leftover = str(clip) + ".brand.mp4"
        open(leftover, "wb").write(b"partial")

        monkeypatch.setattr(branding, "probe_size", lambda p: (1080, 1920))

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1800)

        monkeypatch.setattr(branding.subprocess, "run", boom)
        assert branding.apply_branding(str(clip)) is False
        assert not os.path.exists(leftover), "temp file must be cleaned up"

    def test_ffmpeg_missing_does_not_escape(self, one_asset, monkeypatch, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x")
        monkeypatch.setattr(branding, "probe_size", lambda p: (1080, 1920))
        monkeypatch.setattr(branding.subprocess, "run",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError("no ffmpeg")))
        assert branding.apply_branding(str(clip)) is False

    def test_a_failing_swap_does_not_escape(self, one_asset, monkeypatch, tmp_path):
        """os.replace can fail too — a full disk, a transient FS error. Letting
        it through would abort an already-rendered clip before its captions."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x")
        tmp = str(clip) + ".brand.mp4"

        class _Ok:
            returncode = 0
            stderr = b""

        def fake_run(*a, **kw):
            open(tmp, "wb").write(b"branded")
            return _Ok()

        monkeypatch.setattr(branding, "probe_size", lambda p: (1080, 1920))
        monkeypatch.setattr(branding.subprocess, "run", fake_run)
        monkeypatch.setattr(branding.os, "replace",
                            lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        assert branding.apply_branding(str(clip)) is False
        assert not os.path.exists(tmp), "temp file must be cleaned up"
