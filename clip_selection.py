"""
Pure helpers for the Gemini clip-selection pipeline.

Standard-library only so both main.py and gemini_worker.py can import it and
the logic stays unit-testable without the heavy video dependencies. That is not
a style preference: main.py imports cv2/torch/ultralytics/mediapipe at module
scope and CI installs none of them, so anything living there cannot be tested.
New selection logic belongs in this file for that reason.
"""

import bisect

# USD per 1M tokens (input, output incl. thinking), from ai.google.dev pricing.
MODEL_PRICES = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),  # deprecated (shut down 2026-06-01)
}


def lookup_model_prices(model_name):
    """Longest-prefix match against MODEL_PRICES; None if unknown."""
    name = str(model_name or "").lower()
    best_key = None
    for key in MODEL_PRICES:
        if name.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return MODEL_PRICES[best_key] if best_key else None


def clip_count_targets(n_windows):
    """How many clips to ask the detail pass for, given the shortlist size.

    Measured on prod 3-ago-2026: 408 of 429 jobs (95%) delivered 3 clips or
    fewer, the mode being ONE, while the prompt was free to return one per
    shortlisted window. Users who received 1-3 clips came back a second day
    0.4% of the time; those who received 4-9 came back 16.1% — so the clip
    count, not the clip quality, is what the retention curve hangs on.

    The old prompt biased hard the other way ("prefer one great clip per
    candidate window") and handed the model two unbounded licences to drop
    clips (the 2-second rule and STANDS ALONE both end in "or skip it"), with
    no floor to stop it collapsing to a single clip. This puts a floor and a
    realistic ceiling on it instead.

    ``CLIP_TARGET_MIN`` / ``CLIP_TARGET_MAX`` override both for A/B runs
    without a deploy (the reframe-testing harness drives them).
    """
    import os

    n = max(1, int(n_windows or 1))
    # Floor grows with the material: 3 windows -> 3, 5 -> 4, 10+ -> 6.
    low = max(2, min(6, n // 2 + 2))
    # Ceiling allows a rich window to yield more than one without inviting padding.
    high = min(12, max(4, n * 2))
    low = min(low, high)

    def _override(name, current):
        raw = os.environ.get(name)
        if not raw:
            return current
        try:
            return max(1, int(raw))
        except ValueError:
            return current

    low = _override("CLIP_TARGET_MIN", low)
    high = _override("CLIP_TARGET_MAX", high)
    return low, max(low, high)


def shortlist_size(n_windows):
    """How many scored windows reach the (expensive) detail pass.

    The ceiling used to be a flat 10 whatever the length, which quietly made the
    analysis worse the longer the source: a 15-minute video builds ~13 windows
    and had 10 of them examined, while a 2-hour live builds ~79 and still had
    10 — 13% of the material, the rest scored and then thrown away. The floor in
    clip_count_targets then had the model return its minimum out of that narrow
    slice, which is how a two-hour show came back with six clips.

    Taking a share of the windows rather than a share of the running time is
    what makes this track the actual material: windows are built from speech, so
    a live with a 20-minute "starting soon" card does not get credited for it.

    The ceiling stays bounded because the detail prompt carries each window's
    text, but the headroom is real — a 2-hour transcript is only ~23k tokens in
    full, so 24 windows costs a few thousand.

    ``CLIP_SHORTLIST_MAX`` overrides the ceiling for A/B runs without a deploy.
    """
    import os

    n = max(1, int(n_windows or 1))
    ceiling = max(10, min(24, round(n * 0.3)))
    raw = os.environ.get("CLIP_SHORTLIST_MAX")
    if raw:
        try:
            ceiling = max(1, int(raw))
        except ValueError:
            pass
    return max(3, min(ceiling, n))


def transcript_segments(transcript_result):
    """The transcript's non-empty segments as (start, end, text) tuples.

    Windows index into this list rather than carrying their prose around, which
    is what lets two overlapping windows be merged without their shared
    sentences appearing twice — see merge_overlapping_windows.
    """
    segments = []
    for segment in (transcript_result or {}).get("segments", []) or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        segments.append((float(segment.get("start", 0)), float(segment.get("end", 0)), text))
    return segments


def build_transcript_windows(transcript_result, video_duration,
                             window_seconds=90, overlap_seconds=30):
    """
    Build scoring windows aligned to Whisper segment boundaries, so a sentence
    (and usually a viral moment) is never cut in half mid-window. Windows grow
    segment by segment to roughly window_seconds (up to 1.25x for the closing
    segment) and the next window starts ~overlap_seconds before the previous
    end, also snapped to a segment start.

    Each window also carries ``seg_from``/``seg_to``: its span in
    ``transcript_segments(transcript_result)``, both ends inclusive. Consumers
    build their prompt payload from named keys, so these never reach the model.
    """
    segments = transcript_segments(transcript_result)

    windows = []
    window_index = 1
    i = 0
    n = len(segments)
    while i < n:
        w_start = segments[i][0]
        j = i
        # Extend while the NEXT segment still fits within a tolerant cap, so the
        # window closes on a segment boundary near window_seconds.
        while j + 1 < n and segments[j + 1][1] - w_start <= window_seconds * 1.25:
            j += 1
            if segments[j][1] - w_start >= window_seconds:
                break
        w_end = segments[j][1]
        windows.append({
            "id": f"window_{window_index:03d}",
            "start": round(w_start, 3),
            "end": round(w_end, 3),
            "text": " ".join(seg[2] for seg in segments[i:j + 1]),
            "seg_from": i,
            "seg_to": j,
        })
        window_index += 1

        if j >= n - 1:
            break
        # Next window starts at the first segment beginning after (end - overlap),
        # but always makes progress.
        target = w_end - overlap_seconds
        k = i + 1
        while k <= j and segments[k][0] < target:
            k += 1
        i = max(k, i + 1)

    if not windows:
        windows.append({
            "id": "window_001",
            "start": 0.0,
            "end": round(float(video_duration), 3),
            "text": str(transcript_result.get("text", "") or ""),
            # No segments to index into: the helpers below fall back to "text".
            "seg_from": None,
            "seg_to": None,
        })
    return windows


def merge_overlapping_windows(windows, segments):
    """Sort chronologically and fuse windows whose spans touch or overlap.

    build_transcript_windows deliberately overlaps consecutive windows by
    ~30s so no moment is cut in half. That is right for scoring, and wrong for
    the detail pass: two adjacent windows both surviving the shortlist hand the
    model the same sentences twice, under an instruction to work through every
    window. Duplicate clips are the predictable result, and the only thing
    standing against it was a DIVERSITY line in the prompt.

    The merge goes through segment indices, never through string surgery on the
    joined text: rebuilding from ``segments[seg_from:seg_to + 1]`` of the union
    is what guarantees shared prose appears exactly once.

    The surviving block keeps the FIRST window's id — ids only exist for the
    model to echo back in ``source_window_id``; nothing looks them up.
    """
    if not windows:
        return []

    ordered = sorted(windows, key=lambda w: (float(w["start"]), float(w["end"])))
    merged = []
    for window in ordered:
        window = dict(window)  # never mutate the caller's dicts
        previous = merged[-1] if merged else None
        if previous is None or float(window["start"]) > float(previous["end"]):
            merged.append(window)
            continue

        previous["end"] = round(max(float(previous["end"]), float(window["end"])), 3)
        spans = [w for w in (previous, window)
                 if w.get("seg_from") is not None and w.get("seg_to") is not None]
        if len(spans) == 2:
            previous["seg_from"] = min(s["seg_from"] for s in spans)
            previous["seg_to"] = max(s["seg_to"] for s in spans)
            previous["text"] = " ".join(
                seg[2] for seg in segments[previous["seg_from"]:previous["seg_to"] + 1])
        else:
            # A window with no segment span (the empty-transcript fallback).
            # Concatenating is the best available answer and cannot duplicate
            # anything, because that fallback is only ever the sole window.
            previous["text"] = " ".join(
                t for t in (previous.get("text"), window.get("text")) if t)
    return merged


def window_text_with_anchors(window, segments, precision=1):
    """The window's prose with an absolute ``[SECONDS]`` marker per segment.

    The detail pass has to answer in absolute seconds and used to receive prose
    plus the window's own start/end — so it interpolated a position inside 90s
    of text and was routinely wrong, which is what snap_clip_to_words spends
    its time repairing. One marker per Whisper segment gives it real anchors to
    choose between, at roughly one marker per 5-10s of speech.

    Per-WORD timings were the other option and cost ~40x more: a 24-window
    shortlist is ~5-6k words, so ~65k input tokens of coordinates burying the
    prose the model is supposed to be judging.
    """
    seg_from = window.get("seg_from")
    seg_to = window.get("seg_to")
    if seg_from is None or seg_to is None or seg_to < seg_from:
        return str(window.get("text", "") or "")
    return " ".join(
        f"[{start:.{precision}f}] {text}"
        for start, _end, text in segments[seg_from:seg_to + 1])


# Two clips that genuinely run back to back can still overlap a little:
# snap_clip_to_words leads into the silence before the first word (up to
# max_lead) and trails past the last one (up to max_tail), so ~0.8s of shared
# padding is normal and means nothing. Beyond that they share actual speech.
MAX_CLIP_OVERLAP_SECONDS = 1.0


def drop_overlapping_clips(shorts, max_overlap=MAX_CLIP_OVERLAP_SECONDS):
    """Remove clips that repeat a better-ranked clip's footage.

    Input order is Gemini's ranking (best first), so the first clip of a
    colliding pair is the one worth keeping. Order is preserved; nothing is
    re-sorted. Degenerate entries are dropped too — they would only reach
    ffmpeg and fail there.
    """
    kept = []
    for clip in shorts or []:
        try:
            start = float(clip.get("start"))
            end = float(clip.get("end"))
        except (TypeError, ValueError):
            continue
        if not end > start:
            continue
        collides = any(
            min(end, float(k["end"])) - max(start, float(k["start"])) > max_overlap
            for k in kept)
        if not collides:
            kept.append(clip)
    return kept


# How far the snapper may walk to reach speech when a bound lands in silence.
# Past this the model's timestamp is not "slightly inside a pause", it is
# simply wrong, and moving the bound that far would change which content the
# clip contains — an editor's decision, not the snapper's.
MAX_SILENCE_SKIP = 10.0


def _snap_start_to_speech(start, starts, search_window, max_silence_skip):
    """Word start the clip should open on, or None to leave the bound alone."""
    nearby = [s for s in starts if abs(s - start) <= search_window]
    if nearby:
        return min(nearby, key=lambda s: abs(s - start))
    # Nothing within reach means the bound landed in a silence — which is
    # exactly the case that needs repairing, and exactly the one the search
    # window used to give up on. Walk FORWARD: the nearest word overall may be
    # the tail of the previous sentence, on the wrong side of the gap, and
    # opening on dead air is what kills the first two seconds.
    index = bisect.bisect_left(starts, start)
    if index >= len(starts):
        return None
    word_start = starts[index]
    return word_start if word_start - start <= max_silence_skip else None


def _snap_end_to_speech(end, ends, search_window, max_silence_skip):
    """Word end the clip should close on, or None to leave the bound alone."""
    nearby = [e for e in ends if abs(e - end) <= search_window]
    if nearby:
        return min(nearby, key=lambda e: abs(e - end))
    # Mirror of the start: walk BACKWARD so the clip never trails off into
    # silence it was never meant to include.
    index = bisect.bisect_right(ends, end)
    if index == 0:
        return None
    word_end = ends[index - 1]
    return word_end if end - word_end <= max_silence_skip else None


def snap_clip_to_words(start, end, words, video_duration,
                       min_duration=15.0, max_duration=60.0,
                       search_window=1.5, max_lead=0.35, max_tail=0.45,
                       max_silence_skip=MAX_SILENCE_SKIP):
    """
    Snap Gemini-proposed clip boundaries onto real word boundaries plus a bit
    of the surrounding silence. LLMs are bad at millisecond arithmetic; the
    word-level timestamps are ground truth, so cuts land in pauses instead of
    mid-word.

    words: [{'w','s','e'}, ...] for the whole video, sorted by start.
    Returns (start, end), falling back to the input only when no arrangement of
    snapped and raw bounds satisfies the duration limits.
    """
    original = (round(float(start), 3), round(float(end), 3))
    if not words:
        return original

    starts = sorted(float(w.get("s", 0)) for w in words)
    ends = sorted(float(w.get("e", 0)) for w in words)

    # START: onto a word start, then lead back into the silence before it.
    new_start = float(start)
    word_start = _snap_start_to_speech(new_start, starts, search_window, max_silence_skip)
    if word_start is not None:
        prev_ends = [e for e in ends if e <= word_start]
        lead = min(max_lead, max(0.0, word_start - max(prev_ends)) / 2) if prev_ends else max_lead
        new_start = max(0.0, word_start - lead)

    # END: onto a word end, then trail into the silence after it.
    new_end = float(end)
    word_end = _snap_end_to_speech(new_end, ends, search_window, max_silence_skip)
    if word_end is not None:
        next_starts = [s for s in starts if s >= word_end]
        tail = min(max_tail, max(0.0, min(next_starts) - word_end) / 2) if next_starts else max_tail
        new_end = min(float(video_duration), word_end + tail)

    # Repair the duration while staying on word boundaries.
    repaired_end = new_end
    if repaired_end - new_start < min_duration:
        target = new_start + min_duration
        later = [e for e in ends if e >= target]
        if later and later[0] - new_start <= max_duration:
            repaired_end = min(float(video_duration), later[0] + 0.2)
    if repaired_end - new_start > max_duration:
        target = new_start + max_duration
        earlier = [e for e in ends if new_start < e <= target]
        repaired_end = (max(earlier) + 0.2) if earlier else target
        repaired_end = min(repaired_end, new_start + max_duration, float(video_duration))

    # Take the most-snapped pair that is actually valid. The old code returned
    # the raw input the moment the repair failed, which threw away a bound that
    # HAD snapped correctly because the other one could not — a clip with a
    # clean start came back raw on both sides.
    for low, high in ((new_start, repaired_end), (new_start, new_end),
                      (new_start, original[1]), (original[0], repaired_end),
                      (original[0], new_end), original):
        low, high = float(low), float(high)
        if low < 0 or high > float(video_duration) or high <= low:
            continue
        if min_duration <= high - low <= max_duration:
            return (round(low, 3), round(high, 3))
    return original
