from reframe_v2 import (
    DELIVERY_MIN_WIDTH,
    GENERAL_CONTENT_HEIGHT_RATIO,
    concat_list_content,
    dedupe_sendcmd_lines,
    delivery_size,
    full_width_content_height,
    general_filtergraph,
    general_render_cmd,
    parse_variants,
    scene_frame_ranges,
    variant_filename,
)

VERTICAL = 9 / 16


def test_sendcmd_lines_dedupe_to_change_points():
    xs = [100, 100, 100, 104, 104, 110]
    lines = dedupe_sendcmd_lines(xs, fps=30.0)
    assert lines == [
        "0.0000 crop@c x 100;",
        "0.1000 crop@c x 104;",
        "0.1667 crop@c x 110;",
    ]


def test_sendcmd_static_camera_is_single_command():
    assert len(dedupe_sendcmd_lines([250] * 300, fps=30.0)) == 1


def test_scene_ranges_clamped_and_keep_strategy():
    ranges = scene_frame_ranges(
        [(0, 100), (100, 250), (250, 400)],
        ['TRACK', 'GENERAL', 'TRACK'],
        total_frames=200,
    )
    # Third scene starts past the decoded frame count -> dropped, and the
    # GENERAL strategy stays attached to its own range.
    assert ranges == [(0, 100, 'TRACK'), (100, 200, 'GENERAL')]


def test_scene_ranges_missing_strategy_defaults_to_track():
    assert scene_frame_ranges([(0, 10)], [], 10) == [(0, 10, 'TRACK')]


def test_concat_list_format():
    content = concat_list_content(["/tmp/a/seg_000.mp4", "/tmp/a/seg_001.mp4"])
    assert content == "file '/tmp/a/seg_000.mp4'\nfile '/tmp/a/seg_001.mp4'\n"


class TestDeliverySize:
    """A 720p source must not produce a 406x720 clip (prod audit, 25-jul-2026)."""

    def test_720p_source_is_upscaled_to_the_delivery_floor(self):
        assert delivery_size(1280, 720, VERTICAL) == (1080, 1920)

    def test_1080p_source_lands_on_the_floor_exactly(self):
        assert delivery_size(1920, 1080, VERTICAL) == (1080, 1920)

    def test_tiny_source_still_reaches_the_floor(self):
        # 360x640 sources were shipping as-is; platforms treat that as junk.
        assert delivery_size(640, 360, VERTICAL) == (1080, 1920)

    def test_high_res_source_is_never_downscaled(self):
        # 4K source: the native crop is already well past the floor, so keep it.
        assert delivery_size(3840, 2160, VERTICAL) == (1216, 2160)

    def test_already_vertical_source_keeps_its_full_height(self):
        assert delivery_size(1080, 1920, VERTICAL) == (1080, 1920)

    def test_square_output_uses_the_same_floor(self):
        assert delivery_size(1280, 720, 1.0) == (1080, 1080)

    def test_dimensions_are_always_even(self):
        for w, h in [(1280, 720), (1920, 1080), (854, 480), (3840, 2160), (641, 361)]:
            out_w, out_h = delivery_size(w, h, VERTICAL)
            assert out_w % 2 == 0 and out_h % 2 == 0, (w, h)

    def test_never_returns_below_the_floor(self):
        for w, h in [(320, 180), (640, 360), (1280, 720), (1920, 1080)]:
            assert delivery_size(w, h, VERTICAL)[0] >= DELIVERY_MIN_WIDTH

    def test_aspect_ratio_is_preserved(self):
        for w, h in [(1280, 720), (640, 360), (3840, 2160)]:
            out_w, out_h = delivery_size(w, h, VERTICAL)
            assert abs(out_w / out_h - VERTICAL) < 0.01, (w, h)


def test_general_filtergraph_targets_output_geometry():
    graph = general_filtergraph(1080, 1920)
    assert "gblur" in graph
    assert "overlay=x=(W-w)/2:y=(H-h)/2" in graph


class TestGeneralLayout:
    """GENERAL used to leave the content at 32% of the frame, floating in blur."""

    def test_content_is_scaled_by_height_not_width(self):
        from reframe_v2 import GENERAL_CONTENT_HEIGHT_RATIO
        graph = general_filtergraph(1080, 1920)
        expected = int(1920 * GENERAL_CONTENT_HEIGHT_RATIO)
        expected += expected % 2
        assert f"scale=-2:{expected}" in graph

    def test_content_height_is_even(self):
        # Odd dimensions are rejected by the encoder. Read the FOREGROUND
        # scale — the background line also contains a scale=-2: term.
        for out_h in (1920, 1080, 1078, 976):
            graph = general_filtergraph(1080, out_h)
            fg = graph.split("[fga]scale=-2:")[1]
            h = int(fg.split(",")[0])
            assert h % 2 == 0, (out_h, h)

    def test_overflow_is_trimmed_never_padded(self):
        # min() keeps it a no-op for sources narrower than the frame.
        graph = general_filtergraph(1080, 1920)
        assert "crop=w=min(iw\\,1080):h=ih" in graph

    def test_content_fills_more_than_the_old_full_width_fit(self):
        # A 16:9 source fitted to full width was 9/16 of it — 0.5625 * 1080
        # = 608px, i.e. 32% of a 1920 frame. The new layout must beat that.
        from reframe_v2 import GENERAL_CONTENT_HEIGHT_RATIO
        old_ratio = (1080 * 9 / 16) / 1920
        assert GENERAL_CONTENT_HEIGHT_RATIO > old_ratio

    def test_keeps_most_of_the_source_width(self):
        # GENERAL is for groups and landscapes: cropping the sides too hard
        # cuts people out of frame, which is the whole thing it exists to avoid.
        from reframe_v2 import GENERAL_CONTENT_HEIGHT_RATIO
        scaled_w = 1920 * GENERAL_CONTENT_HEIGHT_RATIO * (16 / 9)
        assert 1080 / scaled_w > 0.70, "keeps less than 70% of the source width"


class TestVariantParsing:
    """The delivery-variant list: what a caller asks for vs what ships."""

    def test_nothing_asked_still_ships_the_auto_render(self):
        for raw in (None, "", [], "   ", ","):
            assert parse_variants(raw) == ("auto",)

    def test_asking_only_for_safe_still_ships_auto(self):
        # "safe" is a SECOND delivery, never a replacement. Every other code
        # path in the app resolves a clip to its auto file; dropping it would
        # leave webhook/MCP/ZIP pointing at nothing.
        assert parse_variants("safe") == ("auto", "safe")

    def test_order_is_the_constant_not_the_request(self):
        assert parse_variants("safe,auto") == ("auto", "safe")

    def test_unknown_names_are_ignored_not_rejected(self):
        # Same doctrine as `layouts`: a typo costs the caller the feature,
        # never the job.
        assert parse_variants("safe,bogus,SAFE, auto ") == ("auto", "safe")

    def test_accepts_a_list_as_well_as_csv(self):
        assert parse_variants(["safe"]) == ("auto", "safe")


class TestVariantFilename:
    """Suffix, never prefix — the property the whole A/B isolation rests on."""

    def test_auto_keeps_the_canonical_name(self):
        assert variant_filename("Talk_clip_3.mp4", "auto") == "Talk_clip_3.mp4"

    def test_safe_is_suffixed(self):
        assert variant_filename("Talk_clip_3.mp4", "safe") == "Talk_clip_3_safe.mp4"

    def test_safe_file_escapes_the_auto_caption_glob(self):
        # app.py finds a clip's newest burned copy with
        # `subtitled_*_<base>_clip_<n>.mp4`, which requires the name to END
        # there. If the safe variant were PREFIXED it would land inside that
        # glob and a safe render could win the auto slot.
        import fnmatch
        safe = "subtitled_1_" + variant_filename("Talk_clip_3.mp4", "safe")
        assert not fnmatch.fnmatch(safe, "subtitled_*_Talk_clip_3.mp4")
        assert fnmatch.fnmatch(safe, "subtitled_*_Talk_clip_3_safe.mp4")

    def test_strip_burned_prefix_still_recovers_the_safe_name(self):
        # _strip_burned_captions / stripBurns only ever remove a PREFIX, so
        # they stay correct for both variants without knowing about them.
        import re
        safe = "subtitled_1_Talk_clip_3_safe.mp4"
        assert re.match(r'^subtitled_\d+_(.+)$', safe).group(1) == "Talk_clip_3_safe.mp4"


class TestGeneralRenderCmd:
    """The safe variant's ffmpeg invocation."""

    def _cmd(self):
        return general_render_cmd("in.mp4", "out.mp4", 1080, 1920, 608)

    def test_single_input_single_pass(self):
        assert self._cmd().count("-i") == 1

    def test_audio_is_copied_and_optional(self):
        cmd = self._cmd()
        # The clip cut already encoded audio with loudnorm applied, so a
        # re-encode would only lose quality. The `?` keeps a silent source
        # from aborting the render.
        assert "-map" in cmd and "0:a:0?" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "copy"

    def test_faststart_is_present(self):
        # Without it the moov atom lands at the end and the dashboard <video>
        # spins forever on a file that downloads fine.
        cmd = self._cmd()
        assert cmd[cmd.index("-movflags") + 1] == "+faststart"

    def test_carries_no_camera_movement(self):
        # The guarantee this variant sells. sendcmd/crop@c are how the auto
        # render moves the frame; neither may appear here.
        blob = " ".join(self._cmd())
        assert "sendcmd" not in blob
        assert "crop@c" not in blob

    def test_filter_is_exactly_the_general_graph(self):
        cmd = self._cmd()
        assert cmd[cmd.index("-filter_complex") + 1] == general_filtergraph(1080, 1920, 608)

    def test_keeps_the_whole_width(self):
        # content_h comes from full_width_content_height, NOT the default 0.42
        # ratio. The default crops the sides to buy presence and throws away
        # ~24% of the width — the exact opposite of what "safe" promises.
        out_w, out_h = delivery_size(1920, 1080, VERTICAL)
        content_h = full_width_content_height(1920, 1080, out_w)
        cmd = general_render_cmd("in.mp4", "out.mp4", out_w, out_h, content_h)
        default_h = int(out_h * GENERAL_CONTENT_HEIGHT_RATIO)
        assert content_h < default_h, "safe variant must shrink to fit, not crop"
        assert general_filtergraph(out_w, out_h, content_h) in cmd


def test_general_graph_has_no_time_varying_construct():
    """What licenses rendering the safe variant in ONE pass, no scene detection.

    general_filtergraph interpolates only out_w/out_h/fg_h, all computed once
    per clip from the source resolution — so every GENERAL scene of a clip
    produces the identical filter string, and concatenating N identical
    segments is the same picture as one pass over the whole clip (minus a
    forced keyframe per boundary). If anyone ever adds a temporal construct
    here, that equivalence breaks and this test is the tripwire.
    """
    graph = general_filtergraph(1080, 1920)
    assert "sendcmd" not in graph
    assert "enable=" not in graph
