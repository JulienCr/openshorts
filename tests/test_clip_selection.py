"""Tests for the pure clip-selection helpers (windows, snapping, pricing)."""
import re

import pytest

from clip_selection import (
    build_transcript_windows,
    clip_count_targets,
    drop_overlapping_clips,
    merge_overlapping_windows,
    reconcile_scores,
    shortlist_size,
    snap_clip_to_words,
    lookup_model_prices,
    transcript_segments,
    window_text_with_anchors,
)


def _seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def _word(w, s, e):
    return {"w": w, "s": s, "e": e}


class TestMergeOverlappingWindows:
    """build_transcript_windows overlaps windows by ~30s on purpose. That is
    right for scoring and wrong for the detail pass, which was being handed the
    same sentences twice under an instruction to work through every window.
    """

    def _transcript(self, n=12):
        return {"segments": [_seg(i * 10, (i + 1) * 10, f"s{i}") for i in range(n)]}

    def test_adjacent_windows_merge_and_prose_appears_once(self):
        transcript = self._transcript()
        segments = transcript_segments(transcript)
        windows = build_transcript_windows(transcript, 120, window_seconds=40, overlap_seconds=20)
        assert len(windows) >= 2  # otherwise the test proves nothing

        merged = merge_overlapping_windows(windows, segments)
        text = " ".join(w["text"] for w in merged)
        for i in range(len(segments)):
            assert text.split().count(f"s{i}") == 1, f"s{i} duplicated in {text!r}"

    def test_disjoint_windows_are_not_merged(self):
        segments = [(0.0, 10.0, "a"), (100.0, 110.0, "b")]
        windows = [
            {"id": "w1", "start": 0.0, "end": 10.0, "text": "a", "seg_from": 0, "seg_to": 0},
            {"id": "w2", "start": 100.0, "end": 110.0, "text": "b", "seg_from": 1, "seg_to": 1},
        ]
        assert len(merge_overlapping_windows(windows, segments)) == 2

    def test_output_is_chronological_whatever_the_input_order(self):
        # The shortlist arrives sorted by SCORE, so neighbours can be far apart.
        segments = [(float(i) * 10, float(i) * 10 + 10, f"s{i}") for i in range(6)]
        windows = [
            {"id": "w3", "start": 40.0, "end": 60.0, "text": "", "seg_from": 4, "seg_to": 5},
            {"id": "w1", "start": 0.0, "end": 20.0, "text": "", "seg_from": 0, "seg_to": 1},
        ]
        merged = merge_overlapping_windows(windows, segments)
        assert [w["start"] for w in merged] == [0.0, 40.0]

    def test_touching_windows_merge_into_one_span(self):
        segments = [(float(i) * 10, float(i) * 10 + 10, f"s{i}") for i in range(4)]
        windows = [
            {"id": "w1", "start": 0.0, "end": 20.0, "text": "", "seg_from": 0, "seg_to": 1},
            {"id": "w2", "start": 20.0, "end": 40.0, "text": "", "seg_from": 2, "seg_to": 3},
        ]
        merged = merge_overlapping_windows(windows, segments)
        assert len(merged) == 1
        assert (merged[0]["start"], merged[0]["end"]) == (0.0, 40.0)
        assert merged[0]["id"] == "w1"  # ids only exist for the model to echo back

    def test_does_not_mutate_the_caller_windows(self):
        segments = [(0.0, 10.0, "a"), (5.0, 15.0, "b")]
        windows = [
            {"id": "w1", "start": 0.0, "end": 10.0, "text": "a", "seg_from": 0, "seg_to": 0},
            {"id": "w2", "start": 5.0, "end": 15.0, "text": "b", "seg_from": 1, "seg_to": 1},
        ]
        merge_overlapping_windows(windows, segments)
        assert windows[0]["end"] == 10.0 and windows[0]["text"] == "a"

    def test_empty_input(self):
        assert merge_overlapping_windows([], []) == []


class TestWindowTextWithAnchors:
    def test_one_marker_per_segment(self):
        segments = [(12.34, 15.0, "hello"), (15.0, 20.0, "world")]
        window = {"seg_from": 0, "seg_to": 1, "text": "hello world"}
        assert window_text_with_anchors(window, segments) == "[12.340] hello [15.000] world"

    def test_markers_never_land_after_the_true_start(self):
        """A rounded marker lands INSIDE the first word of the sentence it
        marks — 30.56 emitted as [30.6] — and the model's `end` then reads as
        speech, so the clip keeps the word it meant to exclude. Truncating is
        what guarantees the marker is never late; more decimals only shrink the
        error.
        """
        segments = [(30.56, 31.2, "next"), (99.9999, 100.5, "later")]
        window = {"seg_from": 0, "seg_to": 1, "text": "next later"}
        rendered = window_text_with_anchors(window, segments)
        for marker, true_start in zip(re.findall(r"\[([\d.]+)\]", rendered),
                                      (30.56, 99.9999)):
            assert float(marker) <= true_start, f"{marker} is after {true_start}"

    def test_a_marker_is_read_as_a_gap_not_as_speech(self):
        """End to end: the marker this function emits, handed straight back by
        the model, must not extend the clip into the word it points at.
        """
        segments = [(30.56, 31.2, "next")]
        window = {"seg_from": 0, "seg_to": 0, "text": "next"}
        marker = float(re.findall(r"\[([\d.]+)\]", window_text_with_anchors(window, segments))[0])
        words = [_word("last", 28.0, 29.0), _word("next", 30.56, 31.2)]
        _start, end = snap_clip_to_words(10.0, marker, words, 100.0,
                                         min_duration=1.0, max_duration=60.0)
        assert end < 30.56, f"clip ran to {end}, swallowing the word at 30.56"

    def test_span_less_window_still_gets_one_legal_anchor(self):
        """The prompt forbids timestamps not taken from a marker, so the
        empty-transcript fallback must not arrive with zero markers.
        """
        window = {"seg_from": None, "seg_to": None, "start": 0.0,
                  "text": "whole transcript"}
        assert window_text_with_anchors(window, []) == "[0.000] whole transcript"


class TestDropOverlappingClips:
    def _clip(self, start, end, title="x"):
        return {"start": start, "end": end, "video_title_for_youtube_short": title}

    def test_identical_clips_collapse_to_the_best_ranked(self):
        # Input order is Gemini's ranking, best first.
        clips = [self._clip(10, 40, "best"), self._clip(10, 40, "dupe")]
        kept = drop_overlapping_clips(clips)
        assert len(kept) == 1
        assert kept[0]["video_title_for_youtube_short"] == "best"

    def test_padding_sized_overlap_is_kept(self):
        # snap_clip_to_words leads up to 0.35s in and trails up to 0.45s out, so
        # two genuinely consecutive clips can share ~0.8s of pure silence.
        clips = [self._clip(10, 40.4), self._clip(39.6, 70)]
        assert len(drop_overlapping_clips(clips)) == 2

    def test_real_shared_speech_is_dropped(self):
        clips = [self._clip(10, 40), self._clip(35, 65)]
        assert len(drop_overlapping_clips(clips)) == 1

    def test_disjoint_clips_all_survive_in_order(self):
        clips = [self._clip(60, 90, "a"), self._clip(10, 40, "b"), self._clip(120, 150, "c")]
        kept = drop_overlapping_clips(clips)
        assert [c["video_title_for_youtube_short"] for c in kept] == ["a", "b", "c"]

    def test_degenerate_entries_are_dropped(self):
        clips = [self._clip(10, 40), self._clip(50, 50), self._clip(80, 70),
                 {"start": None, "end": 30}]
        assert drop_overlapping_clips(clips) == [clips[0]]

    def test_empty_input(self):
        assert drop_overlapping_clips([]) == []
        assert drop_overlapping_clips(None) == []

    def test_collision_is_resolved_on_score_not_position(self):
        """The detail prompt asks for best-first order but cannot guarantee it,
        and the candidate payload it reads is chronological — so position is
        exactly the wrong thing to stake the choice on.
        """
        weak = {"start": 10, "end": 40, "predicted_score": 40, "id": "weak"}
        strong = {"start": 12, "end": 42, "predicted_score": 90, "id": "strong"}
        kept = drop_overlapping_clips([weak, strong])
        assert [c["id"] for c in kept] == ["strong"]

    def test_output_keeps_the_models_order(self):
        first = {"start": 100, "end": 130, "predicted_score": 50, "id": "first"}
        second = {"start": 10, "end": 40, "predicted_score": 90, "id": "second"}
        kept = drop_overlapping_clips([first, second])
        assert [c["id"] for c in kept] == ["first", "second"]

    def test_missing_score_does_not_crash_and_loses_to_a_scored_clip(self):
        unscored = {"start": 10, "end": 40, "id": "unscored"}
        scored = {"start": 12, "end": 42, "predicted_score": 10, "id": "scored"}
        assert [c["id"] for c in drop_overlapping_clips([unscored, scored])] == ["scored"]


class TestReconcileScores:
    """"Score EVERY window" is an instruction, not a guarantee: the response
    schema accepts any list length, so an omitted window would drop out of the
    ranking entirely — the same loss the batch cap used to cause.
    """

    def _windows(self, n=3):
        return [{"id": f"window_{i:03d}", "start": i * 10.0, "end": i * 10.0 + 10}
                for i in range(1, n + 1)]

    def test_omitted_windows_are_ranked_last_not_dropped(self):
        windows = self._windows()
        scored, missing = reconcile_scores([{"id": "window_002", "score": 70}], windows)
        assert missing == ["window_001", "window_003"]
        assert {e["id"] for e in scored} == {w["id"] for w in windows}
        assert [e["score"] for e in scored if e["id"] != "window_002"] == [0, 0]

    def test_nothing_missing_when_everything_is_scored(self):
        windows = self._windows()
        payload = [{"id": w["id"], "score": 50} for w in windows]
        scored, missing = reconcile_scores(payload, windows)
        assert missing == []
        assert scored == payload

    def test_hallucinated_ids_are_dropped(self):
        windows = self._windows(1)
        scored, missing = reconcile_scores(
            [{"id": "window_001", "score": 80}, {"id": "window_999", "score": 99}], windows)
        assert [e["id"] for e in scored] == ["window_001"]
        assert missing == []

    def test_duplicate_ids_keep_the_first(self):
        windows = self._windows(1)
        scored, _ = reconcile_scores(
            [{"id": "window_001", "score": 80}, {"id": "window_001", "score": 10}], windows)
        assert [e["score"] for e in scored] == [80]

    def test_empty_response_still_yields_every_window(self):
        windows = self._windows()
        scored, missing = reconcile_scores([], windows)
        assert len(scored) == 3 and len(missing) == 3


class TestShortlistSize:
    """No coverage existed for this. It matters now: with the scoring pass
    electing at most 3 windows per batch of 8, the cap and not the score was
    what reached the detail pass, so this ceiling was rarely the binding one.
    """

    def test_floor_of_three(self):
        # The floor wins over the window count on tiny inputs, so 1 window asks
        # for 3. Harmless: callers use the result as a slice bound.
        for n in (1, 2, 3):
            assert shortlist_size(n) == 3

    def test_never_exceeds_the_window_count_once_past_the_floor(self):
        for n in range(3, 40):
            assert shortlist_size(n) <= n

    def test_short_videos_get_the_flat_floor_of_ten(self):
        assert shortlist_size(13) == 10

    def test_long_videos_scale_with_the_material(self):
        # ~79 windows is a 2h source: 30% of it, capped at 24.
        assert shortlist_size(79) == 24
        assert shortlist_size(200) == 24

    def test_degenerate_input_does_not_crash(self):
        assert shortlist_size(0) >= 1
        assert shortlist_size(None) >= 1

    def test_env_override_for_ab_runs(self, monkeypatch):
        monkeypatch.setenv("CLIP_SHORTLIST_MAX", "5")
        assert shortlist_size(79) == 5

    def test_garbage_env_falls_back_to_computed(self, monkeypatch):
        monkeypatch.setenv("CLIP_SHORTLIST_MAX", "not-a-number")
        assert shortlist_size(79) == 24


class TestBuildTranscriptWindows:
    def test_windows_align_to_segment_boundaries(self):
        transcript = {"segments": [
            _seg(0, 40, "a"), _seg(40, 80, "b"), _seg(80, 100, "c"), _seg(100, 150, "d"),
        ]}
        windows = build_transcript_windows(transcript, 150, window_seconds=90, overlap_seconds=30)
        segment_edges = {0, 40, 80, 100, 150}
        for w in windows:
            assert w["start"] in segment_edges
            assert w["end"] in segment_edges

    def test_windows_overlap(self):
        transcript = {"segments": [_seg(i * 10, (i + 1) * 10, f"s{i}") for i in range(30)]}
        windows = build_transcript_windows(transcript, 300, window_seconds=90, overlap_seconds=30)
        assert len(windows) >= 3
        for prev, nxt in zip(windows, windows[1:]):
            # next window starts before the previous one ends (overlap)
            assert nxt["start"] < prev["end"]
        # full coverage to the end
        assert windows[-1]["end"] == 300

    def test_empty_transcript_falls_back_to_full_video(self):
        windows = build_transcript_windows({"segments": []}, 120)
        assert len(windows) == 1
        assert windows[0]["start"] == 0.0
        assert windows[0]["end"] == 120

    def test_always_progresses(self):
        # One giant segment must not loop forever
        transcript = {"segments": [_seg(0, 500, "long monolog")]}
        windows = build_transcript_windows(transcript, 500, window_seconds=90, overlap_seconds=30)
        assert len(windows) == 1


class TestSnapClipToWords:
    def _words(self):
        # words every ~2s with 0.4s gaps: [0,1.6], [2,3.6], [4,5.6], ...
        return [_word(f"w{i}", i * 2.0, i * 2.0 + 1.6) for i in range(40)]

    def test_start_snaps_into_silence_before_word(self):
        words = self._words()
        # Gemini proposes 10.3 — nearest word start is 10.0, gap before is 9.6->10.0
        start, end = snap_clip_to_words(10.3, 30.1, words, 80.0)
        assert 9.8 <= start <= 10.0  # word start minus half-gap lead
        # end 30.1 -> nearest word end 29.6 plus tail
        assert 29.6 <= end <= 30.05

    def test_no_words_nearby_keeps_original(self):
        words = [_word("far", 200.0, 201.0)]
        assert snap_clip_to_words(10.0, 40.0, words, 300.0) == (10.0, 40.0)

    def test_empty_words_keeps_original(self):
        assert snap_clip_to_words(5.0, 25.0, [], 100.0) == (5.0, 25.0)

    def test_duration_repaired_to_minimum(self):
        words = self._words()
        # snapping would yield ~14.4s; must be extended to >= 15s on a word end
        start, end = snap_clip_to_words(10.0, 24.5, words, 80.0)
        assert end - start >= 15.0

    def test_duration_capped_at_maximum(self):
        words = self._words()
        start, end = snap_clip_to_words(0.0, 59.9, words, 80.0)
        assert end - start <= 60.0

    def _words_with_gap(self, gap_start, gap_end, until=80):
        """One 0.8s word per second, except across [gap_start, gap_end)."""
        return [_word(f"w{i}", float(i), i + 0.8)
                for i in range(until) if not (gap_start <= i < gap_end)]

    def test_start_in_a_silence_walks_forward_to_speech(self):
        """The ±1.5s search window is empty exactly when the bound landed in a
        hole — so the old code kept Gemini's raw value precisely in the case
        that needed fixing, and the clip opened on dead air.
        """
        words = self._words_with_gap(10, 20, until=60)
        start, end = snap_clip_to_words(15.0, 45.0, words, 80.0)
        assert start == 19.65  # 20.0 minus the full lead, out of the silence
        assert end == 44.9

    def test_end_in_a_silence_walks_backward_to_speech(self):
        words = self._words_with_gap(30, 40)
        start, end = snap_clip_to_words(10.0, 35.0, words, 80.0)
        assert end == 30.25  # last word ends 29.8, plus the trail
        assert start == 9.9

    def test_silence_wider_than_the_cap_keeps_the_raw_bound(self):
        """Past max_silence_skip the timestamp is not slightly off inside a
        pause, it is wrong — and moving the clip that far would change what is
        in it, which is not the snapper's call to make.
        """
        words = self._words_with_gap(10, 60)
        assert snap_clip_to_words(15.0, 35.0, words, 80.0) == (15.0, 35.0)

    def test_bound_in_a_short_pause_still_walks_the_right_way(self):
        """Copilot's case: words at 10s and 12s, a start proposed at 10.9s. The
        nearest word start is 10.0, but 10.9 is in the pause after it, so
        opening there would replay the tail of the previous phrase.
        """
        words = [_word("a", 10.0, 10.8), _word("b", 12.0, 12.8),
                 _word("c", 30.0, 30.8), _word("d", 32.0, 32.8)]
        start, _end = snap_clip_to_words(10.9, 32.5, words, 100.0,
                                         min_duration=1.0, max_duration=60.0)
        assert start == 12.0 - 0.35  # walked forward, not back to 10.0

    def test_end_at_a_sentence_marker_does_not_swallow_the_next_word(self):
        """The detail prompt asks for `end` at the marker of the sentence AFTER
        the last one wanted, so the nearest word END is frequently the first
        word of a sentence the clip must not contain.
        """
        words = [_word("last", 28.0, 29.0), _word("next", 30.5, 31.2)]
        _start, end = snap_clip_to_words(10.0, 30.5, words, 100.0,
                                         min_duration=1.0, max_duration=60.0)
        assert end == 29.0 + 0.45  # the previous word's end, not 31.2

    def test_a_bound_inside_a_long_word_masked_by_a_short_one_is_speech(self):
        """Consulting only the last word starting before the bound calls this
        silence: at t=17 over [(10,20),(15,16)] that lookup lands on (15,16),
        while (10,20) is still being spoken, and the caller then walks away
        from speech that has not finished.
        """
        words = [_word("looong", 10.0, 20.0), _word("in", 15.0, 16.0),
                 _word("after", 40.0, 41.0)]
        _start, end = snap_clip_to_words(0.5, 19.0, words, 100.0,
                                         min_duration=1.0, max_duration=60.0)
        # Read as speech, the bound snaps to the long word's own end (20.0).
        # Read as silence it walks back to 16.0 and cuts mid-word.
        assert end > 20.0, f"cut at {end}, inside the word running to 20.0"

    def test_failed_repair_keeps_the_bound_that_did_snap(self):
        """The old code returned the raw input the moment the duration repair
        failed, throwing away a start that had snapped correctly because the
        END could not.
        """
        words = [_word(f"w{i}", float(i), i + 0.8)
                 for i in list(range(20)) + list(range(100, 160))]
        start, end = snap_clip_to_words(8.0, 25.0, words, 200.0)
        assert start == 7.9   # snapped, where the old code gave back 8.0
        assert end == 25.0    # unrepairable: 45s of silence follows
        assert 15.0 <= end - start <= 60.0


class TestPricing:
    def test_known_models(self):
        assert lookup_model_prices("gemini-2.5-flash") == (0.30, 2.50)
        assert lookup_model_prices("gemini-3-flash-preview") == (0.50, 3.00)

    def test_prefix_match_with_suffix(self):
        assert lookup_model_prices("gemini-2.5-flash-002") == (0.30, 2.50)

    def test_unknown_model_returns_none(self):
        assert lookup_model_prices("gpt-9-mega") is None
        assert lookup_model_prices(None) is None


class TestClipCountTargets:
    """The floor is the whole point: prod was delivering a single clip on the
    mode, and users who got 1-3 came back 0.4% of the time against 16% for 4-9.
    """

    def test_floor_clears_the_dead_zone_once_there_is_material(self):
        # 4+ shortlisted windows must not be allowed to return the 1-3 band.
        for n in (4, 5, 6, 8, 10):
            low, high = clip_count_targets(n)
            assert low >= 4, f"{n} windows asked for only {low}"
            assert high >= low

    def test_tiny_shortlists_stay_modest(self):
        assert clip_count_targets(1)[0] <= 2
        assert clip_count_targets(2)[0] <= 3

    def test_ceiling_is_capped_so_long_videos_do_not_explode(self):
        assert clip_count_targets(40) == clip_count_targets(12)
        assert clip_count_targets(40)[1] <= 12

    def test_low_never_exceeds_high(self):
        for n in range(1, 40):
            low, high = clip_count_targets(n)
            assert low <= high

    def test_degenerate_input_does_not_crash(self):
        assert clip_count_targets(0)[0] >= 1
        assert clip_count_targets(None)[0] >= 1

    def test_env_overrides_for_ab_runs(self, monkeypatch):
        monkeypatch.setenv("CLIP_TARGET_MIN", "1")
        monkeypatch.setenv("CLIP_TARGET_MAX", "2")
        assert clip_count_targets(5) == (1, 2)

    def test_garbage_env_falls_back_to_computed(self, monkeypatch):
        baseline = clip_count_targets(5)
        monkeypatch.setenv("CLIP_TARGET_MIN", "not-a-number")
        assert clip_count_targets(5) == baseline

    def test_override_min_above_max_still_orders(self, monkeypatch):
        monkeypatch.setenv("CLIP_TARGET_MIN", "9")
        monkeypatch.setenv("CLIP_TARGET_MAX", "3")
        low, high = clip_count_targets(5)
        assert low <= high


class TestDetailPromptCarriesTheCount:
    def test_template_formats_with_the_targets(self):
        gw = pytest.importorskip("gemini_worker")
        low, high = clip_count_targets(5)
        prompt = gw.DETAIL_PROMPT_TEMPLATE.format(
            video_duration=300, language="es", min_clips=low, max_clips=high,
            windows_json="[]")
        assert f"return {low} to {high} clips" in prompt
        # The JSON schema example legitimately keeps braces (they are {{ }} in
        # the template), so assert on unsubstituted placeholders specifically.
        assert re.findall(r"\{[a-z_]+\}", prompt) == []
