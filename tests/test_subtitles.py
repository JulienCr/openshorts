"""Tests for subtitle word merging, SRT generation and style sanitizing."""
import json
import os

from subtitles import (
    AUTO_CAPTION_STYLE,
    build_burn_command,
    generate_auto_captions,
    merge_continuation_words,
    generate_srt,
    hex_to_ass_color,
    resolve_caption_style,
    _sanitize_font_name,
    _clamp_number,
)


def _w(text, start, end):
    return {"word": text, "start": start, "end": end}


class TestMergeContinuationWords:
    def test_merges_compound_fragments(self):
        # faster-whisper splits "YouTube-Kanal." into two tokens; the second
        # one has no leading space and belongs to the first.
        words = [_w(" YouTube", 0.0, 0.5), _w("-Kanal.", 0.5, 0.9), _w(" ist", 1.0, 1.2)]
        merged = merge_continuation_words(words)
        assert [m["word"] for m in merged] == [" YouTube-Kanal.", " ist"]
        assert merged[0]["start"] == 0.0
        assert merged[0]["end"] == 0.9

    def test_keeps_real_word_boundaries(self):
        # Words with a leading space are separate words and must never be glued.
        words = [_w(" ich", 0.0, 0.2), _w(" habe", 0.2, 0.4)]
        merged = merge_continuation_words(words)
        assert [m["word"] for m in merged] == [" ich", " habe"]

    def test_first_word_without_space_stays(self):
        words = [_w("Hallo", 0.0, 0.2), _w(" Welt", 0.2, 0.4)]
        merged = merge_continuation_words(words)
        assert [m["word"] for m in merged] == ["Hallo", " Welt"]

    def test_number_fragments(self):
        words = [_w(" 1", 0.0, 0.2), _w(".200", 0.2, 0.4)]
        merged = merge_continuation_words(words)
        assert [m["word"] for m in merged] == [" 1.200"]

    def test_input_not_mutated(self):
        words = [_w(" a", 0.0, 0.1), _w("-b", 0.1, 0.2)]
        merge_continuation_words(words)
        assert words[0]["word"] == " a"
        assert words[1]["word"] == "-b"


class TestBuildBurnCommand:
    """Captions and the hook overlay are burned in ONE ffmpeg pass.

    Not an optimisation: auto_caption_clip writes `subtitled_<ts>_<stem>.mp4`,
    and that name is a contract — the subtitle modal's walk-back and
    _canonical_clip_file reconstruct the clean original from that exact prefix.
    A second pass for the hook would have to write `hooked_subtitled_...`, which
    breaks the reconstruction (and doubles the render time per clip).
    """

    VF = "ass=filename='/tmp/s.ass':fontsdir='/app/fonts'"

    def _cmd(self, **kw):
        return build_burn_command("clip.mp4", "out.mp4", self.VF, **kw)

    def test_without_overlay_keeps_the_single_input_form(self):
        cmd = self._cmd()
        assert cmd.count("-i") == 1
        assert "-vf" in cmd
        assert "-filter_complex" not in cmd
        assert cmd[-1] == "out.mp4"

    def test_overlay_adds_the_png_as_a_second_input(self):
        cmd = self._cmd(overlay_png="hook.png", overlay_xy=(12, 34))
        assert cmd.count("-i") == 2
        assert cmd[cmd.index("-i") + 1] == "clip.mp4"
        assert "hook.png" in cmd

    def test_overlay_switches_to_filter_complex(self):
        # -vf and -filter_complex are mutually exclusive in ffmpeg; emitting
        # both makes it refuse the whole command.
        cmd = self._cmd(overlay_png="hook.png", overlay_xy=(12, 34))
        assert "-filter_complex" in cmd
        assert "-vf" not in cmd

    def test_overlay_chains_captions_then_hook(self):
        cmd = self._cmd(overlay_png="hook.png", overlay_xy=(12, 34))
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.startswith(f"[0:v]{self.VF}[")
        assert "[1:v]overlay=x=12:y=34" in graph
        assert graph.endswith("[v]")

    def test_video_is_mapped_from_the_filter_graph(self):
        cmd = self._cmd(overlay_png="hook.png", overlay_xy=(0, 0))
        assert "[v]" in cmd

    def test_audio_mapping_tolerates_a_silent_source(self):
        # The single-input form got away with a bare `-c:a copy`. Two inputs
        # make the mapping explicit, and without the trailing `?` ffmpeg aborts
        # on a source that has no audio stream at all — which the pipeline
        # already handles everywhere else.
        cmd = self._cmd(overlay_png="hook.png", overlay_xy=(0, 0))
        assert "0:a?" in cmd

    def test_hook_is_time_limited_when_a_duration_is_given(self):
        cmd = self._cmd(overlay_png="hook.png", overlay_xy=(0, 0), overlay_until=3.0)
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "enable='between(t,0,3.0)'" in graph

    def test_hook_without_a_duration_lasts_the_whole_clip(self):
        # /api/hook has always treated duration_seconds=None as "show it all
        # the way through"; the preset keeps that meaning.
        cmd = self._cmd(overlay_png="hook.png", overlay_xy=(0, 0), overlay_until=None)
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "enable=" not in graph


class TestGenerateAutoCaptions:
    """Which generator the pipeline's automatic captions go through.

    AUTO_CAPTION_STYLE has always carried style="karaoke" and nothing ever read
    it — harmless while the value was unreachable. The default-style panel makes
    "classic" selectable, so ignoring the key now means the panel promises a
    look it does not deliver.
    """

    def _transcript(self):
        return {"segments": [{"start": 0, "end": 2, "text": "a b", "words": [
            {"word": " hook", "start": 0.0, "end": 0.5},
            {"word": " word", "start": 0.5, "end": 1.0},
        ]}]}

    def _style(self, **over):
        return {**AUTO_CAPTION_STYLE, **over}

    def test_karaoke_writes_an_ass_file(self, tmp_path):
        path = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 1,
                                      self._style(style="karaoke"))
        assert path.endswith(".ass")

    def test_classic_writes_an_srt_file(self, tmp_path):
        # burn_subtitles keys its filter off the extension: .ass carries its own
        # styles, anything else gets force_style. Writing karaoke content to a
        # .srt name would not degrade gracefully, it would render the tags.
        path = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 1,
                                      self._style(style="classic"))
        assert path.endswith(".srt")

    def test_classic_output_carries_no_karaoke_tags(self, tmp_path):
        path = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 1,
                                      self._style(style="classic"))
        body = open(path, encoding="utf-8-sig").read()
        assert "\\c&H" not in body

    def test_no_words_in_range_returns_none(self, tmp_path):
        assert generate_auto_captions(self._transcript(), 90, 99, str(tmp_path), 1,
                                      self._style()) is None

    def test_names_are_unique_per_call(self, tmp_path):
        # Clips render in parallel threads (CLIP_WORKERS); a bare timestamp
        # would let one clip burn another's captions.
        a = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 1, self._style())
        b = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 1, self._style())
        assert a != b

    def test_name_never_carries_the_clip_title(self, tmp_path):
        # The path is interpolated into an ffmpeg filter string, where an
        # apostrophe closes the quote. Titles carry them constantly.
        path = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 1, self._style())
        assert os.path.basename(path).startswith("autosubs_1_")


class TestResolveCaptionStyle:
    """The caption look the renderer actually uses: built-in defaults, with the
    server's style preset merged over the top."""

    def _with_preset(self, monkeypatch, captions):
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"captions": captions}))

    def test_no_preset_keeps_the_built_in_look(self, monkeypatch):
        monkeypatch.delenv("OPENSHORTS_STYLE", raising=False)
        assert resolve_caption_style() == AUTO_CAPTION_STYLE

    def test_preset_overrides_named_keys(self, monkeypatch):
        self._with_preset(monkeypatch, {"highlight_color": "#FF00FF"})
        assert resolve_caption_style()["highlight_color"] == "#FF00FF"

    def test_untouched_keys_keep_their_defaults(self, monkeypatch):
        self._with_preset(monkeypatch, {"highlight_color": "#FF00FF"})
        style = resolve_caption_style()
        assert style["font_name"] == AUTO_CAPTION_STYLE["font_name"]
        assert style["effect"] == AUTO_CAPTION_STYLE["effect"]

    def test_unknown_keys_are_ignored_not_rejected(self, monkeypatch):
        # Same rule as layout_env: a newer dashboard writing a key this renderer
        # has never heard of must not break the job.
        self._with_preset(monkeypatch, {"font_name": "Anton", "wobble": 3})
        style = resolve_caption_style()
        assert style["font_name"] == "Anton"
        assert "wobble" not in style

    def test_defaults_are_never_mutated(self, monkeypatch):
        # Clips render in parallel threads inside one process (CLIP_WORKERS), so
        # a merge that wrote through to the module constant would let one clip
        # restyle every clip rendered after it.
        original = dict(AUTO_CAPTION_STYLE)
        self._with_preset(monkeypatch, {"font_name": "Impact"})
        resolve_caption_style()
        assert AUTO_CAPTION_STYLE == original

    def test_preset_without_a_captions_section(self, monkeypatch):
        # A preset that only sets layouts is legitimate.
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"layouts": ["auto"]}))
        assert resolve_caption_style() == AUTO_CAPTION_STYLE


class TestGenerateSrt:
    def _transcript(self, words):
        return {"segments": [{"start": 0, "end": 99, "text": "", "words": words}]}

    def test_no_orphan_fragments_in_srt(self, tmp_path):
        out = tmp_path / "subs.srt"
        words = [
            _w(" Mein", 0.0, 0.3),
            _w(" YouTube", 0.3, 0.8),
            _w("-Kanal.", 0.8, 1.1),
            _w(" ich", 1.2, 1.4),
            _w(" habe", 1.4, 1.7),
        ]
        assert generate_srt(self._transcript(words), 0, 10, str(out)) is True
        srt = out.read_text(encoding="utf-8-sig")
        assert "YouTube-Kanal." in srt
        assert " -Kanal" not in srt
        assert "ich habe" in srt
        assert "ichhabe" not in srt

    def test_empty_range_returns_false(self, tmp_path):
        out = tmp_path / "subs.srt"
        words = [_w(" spaet", 50.0, 50.5)]
        assert generate_srt(self._transcript(words), 0, 10, str(out)) is False


class TestStyleSanitizing:
    def test_invalid_hex_falls_back_to_white(self):
        assert hex_to_ass_color("#GGGGGG") == hex_to_ass_color("#FFFFFF")
        assert hex_to_ass_color("abc") == hex_to_ass_color("#FFFFFF")
        assert hex_to_ass_color(None) == hex_to_ass_color("#FFFFFF")

    def test_invalid_hex_custom_fallback(self):
        assert hex_to_ass_color("nope", fallback="000000") == hex_to_ass_color("#000000")

    def test_valid_hex_converts(self):
        # #RRGGBB -> &HAABBGGRR
        assert hex_to_ass_color("#FF0000", 1.0) == "&H000000FF"
        assert hex_to_ass_color("00FF00", 1.0) == "&H0000FF00"

    def test_opacity_clamped(self):
        assert hex_to_ass_color("#FFFFFF", 5.0) == hex_to_ass_color("#FFFFFF", 1.0)
        assert hex_to_ass_color("#FFFFFF", -1) == hex_to_ass_color("#FFFFFF", 0.0)

    def test_font_name_injection_stripped(self):
        assert _sanitize_font_name("Arial,Fontsize=99{\\b1}") == "ArialFontsize99b1"
        assert _sanitize_font_name("Comic Sans MS") == "Comic Sans MS"

    def test_font_name_empty_falls_back(self):
        assert _sanitize_font_name("") == "Verdana"
        assert _sanitize_font_name(",,{}") == "Verdana"
        assert _sanitize_font_name(None) == "Verdana"

    def test_clamp_number(self):
        assert _clamp_number(5, 0, 10, 1) == 5
        assert _clamp_number(99, 0, 10, 1) == 10
        assert _clamp_number(-3, 0, 10, 1) == 0
        assert _clamp_number("kaputt", 0, 10, 1) == 1
        assert _clamp_number(None, 0, 10, 1) == 1


class TestGenerateAss:
    from subtitles import generate_ass  # noqa: F401 (import check)

    def _transcript(self, words):
        return {"segments": [{"start": 0, "end": 99, "text": "", "words": words}]}

    def test_karaoke_events_highlight_each_word(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" Erst", 0.0, 0.3), _w(" mal", 0.3, 0.6), _w(" hier", 0.6, 0.9)]
        assert generate_ass(self._transcript(words), 0, 10, str(out),
                            highlight_color="#22C55E", font_color="#FFFFFF") is True
        content = out.read_text(encoding="utf-8-sig")
        # One dialogue event per word, highlight moves through the block
        assert content.count("Dialogue:") == 3
        assert content.count("\\c&H5EC522&") == 3  # #22C55E -> BGR 5EC522
        assert content.count("{\\r}") == 3          # reset to dimmed base style
        assert "Style: Default,Verdana," in content

    def test_karaoke_merges_fragments_too(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" YouTube", 0.0, 0.5), _w("-Kanal.", 0.5, 0.9)]
        assert generate_ass(self._transcript(words), 0, 10, str(out)) is True
        content = out.read_text(encoding="utf-8-sig")
        assert "YouTube-Kanal." in content
        assert content.count("Dialogue:") == 1

    def test_invalid_highlight_falls_back(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" test", 0.0, 0.5)]
        assert generate_ass(self._transcript(words), 0, 10, str(out),
                            highlight_color="#NOPE!!") is True
        content = out.read_text(encoding="utf-8-sig")
        assert "\\c&H00D7FF&" in content  # falls back to gold #FFD700

    def test_empty_range_returns_false(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" spaet", 50.0, 50.5)]
        assert generate_ass(self._transcript(words), 0, 10, str(out)) is False

    def test_ass_injection_neutralized(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" {\\b1}evil", 0.0, 0.5)]
        assert generate_ass(self._transcript(words), 0, 10, str(out)) is True
        content = out.read_text(encoding="utf-8-sig")
        assert "{\\b1}evil" not in content

    def test_glow_effect_tags(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" neon", 0.0, 0.5)]
        assert generate_ass(self._transcript(words), 0, 10, str(out),
                            effect="glow", highlight_color="#00FF88") is True
        content = out.read_text(encoding="utf-8-sig")
        assert "\\blur4" in content
        assert "\\3c&H88FF00&" in content  # glow outline in highlight color

    def test_pop_effect_animates_scale(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" pop", 0.0, 0.5)]
        assert generate_ass(self._transcript(words), 0, 10, str(out), effect="pop") is True
        content = out.read_text(encoding="utf-8-sig")
        # Gentle range: the old 75->112 pop was so wide that a frame caught
        # mid-animation read as a sizing bug rather than a beat.
        assert "\\fscx90\\fscy90" in content
        assert "\\t(0,110,\\fscx108\\fscy108)" in content

    def test_uppercase_transform(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" hallo", 0.0, 0.5), _w(" welt", 0.5, 1.0)]
        assert generate_ass(self._transcript(words), 0, 10, str(out), uppercase=True) is True
        content = out.read_text(encoding="utf-8-sig")
        assert "HALLO" in content and "WELT" in content
        assert "hallo" not in content.split("[Events]")[1]

    def test_base_opacity_dims_style_color(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" dim", 0.0, 0.5)]
        assert generate_ass(self._transcript(words), 0, 10, str(out),
                            font_color="#FFFFFF", base_opacity=0.4) is True
        content = out.read_text(encoding="utf-8-sig")
        # Dimming is fully-opaque scaled RGB (alpha would blend with the black
        # outline into muddy grey): factor 0.5 + 0.5*0.4 = 0.7 -> 0xB2
        assert "&H00B2B2B2" in content
        # no alpha-based dimming anywhere
        assert "\\1a" not in content

    def test_full_opacity_keeps_color_unchanged(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        words = [_w(" voll", 0.0, 0.5)]
        assert generate_ass(self._transcript(words), 0, 10, str(out),
                            font_color="#FFFFFF", base_opacity=1.0) is True
        content = out.read_text(encoding="utf-8-sig")
        assert "&H00FFFFFF" in content  # pure white, no dimming


class TestBurnFilterFonts:
    """The ffmpeg filter must point libass at the bundled fonts dir — without
    it every UI font choice silently falls back to DejaVu (issue #57)."""

    def _captured_cmd(self, monkeypatch, tmp_path, srt_name):
        import subtitles as m
        captured = {}

        class _Ok:
            returncode = 0
            stderr = b""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Ok()

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        srt = tmp_path / srt_name
        srt.write_text("stub", encoding="utf-8")
        m.burn_subtitles("in.mp4", str(srt), "out.mp4", font_name="Impact")
        return " ".join(str(c) for c in captured["cmd"])

    def test_srt_filter_includes_fontsdir(self, monkeypatch, tmp_path):
        cmd = self._captured_cmd(monkeypatch, tmp_path, "subs.srt")
        assert "fontsdir=" in cmd
        assert "force_style=" in cmd

    def test_ass_filter_includes_fontsdir(self, monkeypatch, tmp_path):
        cmd = self._captured_cmd(monkeypatch, tmp_path, "subs.ass")
        assert "fontsdir=" in cmd
        # ASS carries its own styles; force_style must NOT override them
        assert "force_style" not in cmd


class TestAutoCaptionDefaults:
    """The caption look every clip now ships with (chosen 25-jul-2026)."""

    def test_style_is_complete(self):
        from subtitles import AUTO_CAPTION_STYLE, generate_ass
        required = {"alignment", "font_name", "font_size", "font_color",
                    "highlight_color", "border_color", "border_width",
                    "effect", "base_opacity", "uppercase",
                    "max_chars", "max_duration"}
        assert required <= set(AUTO_CAPTION_STYLE)

    def test_font_is_one_the_image_actually_ships(self):
        # libass falls back to DejaVu SILENTLY when the font is missing (#57),
        # so the default must be a family baked into the image.
        from subtitles import AUTO_CAPTION_STYLE
        assert AUTO_CAPTION_STYLE["font_name"] in {
            "Anton", "Liberation Sans", "Liberation Serif", "DejaVu Sans"}

    def test_highlight_differs_from_body_text(self):
        # The whole point of the karaoke look: the active word must stand out.
        from subtitles import AUTO_CAPTION_STYLE as s
        assert s["highlight_color"].lower() != s["font_color"].lower()

    def test_captions_clear_the_platform_ui(self, tmp_path):
        from subtitles import SAFE_MARGIN_V, generate_ass
        # PlayResY is 288, so the margin must be a meaningful share of it —
        # the old hardcoded 25 (8.7%) sat under TikTok's own bottom chrome.
        assert SAFE_MARGIN_V / 288 >= 0.12
        out = tmp_path / "subs.ass"
        words = [_w(" hola", 0.0, 0.5)]
        assert generate_ass(self._t(words), 0, 10, str(out)) is True
        style_line = [l for l in out.read_text(encoding="utf-8-sig").splitlines()
                      if l.startswith("Style: Default")][0]
        assert f",10,10,{SAFE_MARGIN_V},1" in style_line

    def test_margin_is_overridable(self, tmp_path):
        from subtitles import generate_ass
        out = tmp_path / "subs.ass"
        assert generate_ass(self._t([_w(" hola", 0.0, 0.5)]), 0, 10, str(out),
                            margin_v=90) is True
        assert ",10,10,90,1" in out.read_text(encoding="utf-8-sig")

    @staticmethod
    def _t(words):
        return {"segments": [{"words": words}]}


class TestFilterQuoting:
    """Paths are interpolated INTO a single-quoted ffmpeg filter argument.

    Regression cover for captions silently failing in prod on 29-jul-2026: a
    clip named "Inside Earth's Most Mysterious Temple" produced an .ass path
    carrying that apostrophe, which ends the quoted argument early and kills
    the burn. Apostrophes are constant in English titles.

    The fix is NOT smarter escaping. The shell idiom "'\\''" was tried and is
    worse — ffmpeg's filtergraph parser is not a shell, so it dropped the
    apostrophe and swallowed the following ":fontsdir=" option into the
    filename. The fix is to keep apostrophes out of filter paths entirely.
    """

    # These two used to grep main.py for the f-string that built the path. The
    # construction now lives in generate_auto_captions, so they call it instead
    # — which tests the property itself rather than the shape of the source that
    # happens to produce it.

    def _transcript(self):
        return {"segments": [{"start": 0, "end": 2, "text": "a", "words": [
            {"word": " word", "start": 0.0, "end": 0.5},
        ]}]}

    def test_generated_subtitle_paths_carry_no_apostrophe(self, tmp_path):
        # The generator must name its own file, never derive it from a video
        # title. This is the property that actually prevents the bug.
        path = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 1,
                                      AUTO_CAPTION_STYLE)
        assert "'" not in os.path.basename(path)

    def test_auto_caption_name_is_unique_per_clip(self, tmp_path):
        # Clips render in parallel; a bare timestamp collides and lets one clip
        # burn another's captions.
        first = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 7,
                                       AUTO_CAPTION_STYLE)
        second = generate_auto_captions(self._transcript(), 0, 2, str(tmp_path), 7,
                                        AUTO_CAPTION_STYLE)
        assert first != second, "same generation_id must not collide"

    def test_colon_is_escaped(self):
        from subtitles import _escape_ffmpeg_filter_value
        assert "\\:" in _escape_ffmpeg_filter_value("C:/out/subs.ass")

    def test_plain_path_untouched(self):
        from subtitles import _escape_ffmpeg_filter_value
        assert _escape_ffmpeg_filter_value("/out/subs_0_123.ass") == "/out/subs_0_123.ass"
