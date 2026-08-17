"""Resolving a clip to its delivery variants on disk.

The whole A/B design rests on one property: the auto variant and the safe
variant live in disjoint filename namespaces, so neither can ever win the
other's slot. That is what these tests pin down — everything else about the
feature is recoverable, but a safe render served as the canonical clip would
silently replace what the user is about to publish.
"""
import os
import time

import pytest

app_module = pytest.importorskip("app")  # fastapi/boto3 etc., absent in minimal envs

_canonical_clip_file = app_module._canonical_clip_file
_clip_variant_entries = app_module._clip_variant_entries
_newest_derived = app_module._newest_derived


def _touch(d, name, age=0):
    """Create a file, optionally aged so mtime ordering is deterministic."""
    path = os.path.join(d, name)
    with open(path, "w") as f:
        f.write("x")
    if age:
        past = time.time() - age
        os.utime(path, (past, past))
    return path


class TestVariantIsolation:
    """A safe render must never be mistaken for the canonical clip."""

    def test_newer_safe_file_does_not_win_the_auto_slot(self, tmp_path):
        d = str(tmp_path)
        _touch(d, "Talk_clip_1.mp4", age=100)
        _touch(d, "subtitled_100_Talk_clip_1.mp4", age=50)
        _touch(d, "Talk_clip_1_safe.mp4", age=10)
        # Deliberately the most recent file in the directory.
        _touch(d, "subtitled_200_Talk_clip_1_safe.mp4")

        # _canonical_clip_file picks the NEWEST derived file, so if the safe
        # variant were inside its glob it would win here — and the dashboard,
        # the webhook and the R2 archive would all serve the wrong framing.
        assert _canonical_clip_file(d, "Talk", 0) == "subtitled_100_Talk_clip_1.mp4"

    def test_each_variant_resolves_to_its_own_derived_file(self, tmp_path):
        d = str(tmp_path)
        _touch(d, "Talk_clip_1.mp4", age=100)
        _touch(d, "subtitled_100_Talk_clip_1.mp4", age=50)
        _touch(d, "Talk_clip_1_safe.mp4", age=40)
        _touch(d, "subtitled_200_Talk_clip_1_safe.mp4", age=10)

        entries = _clip_variant_entries("job1", d, "Talk", 0)
        assert [e["id"] for e in entries] == ["auto", "safe"]
        assert entries[0]["video_url"] == "/videos/job1/subtitled_100_Talk_clip_1.mp4"
        assert entries[1]["video_url"] == "/videos/job1/subtitled_200_Talk_clip_1_safe.mp4"

    def test_uncaptioned_variants_resolve_to_the_clean_files(self, tmp_path):
        d = str(tmp_path)
        _touch(d, "Talk_clip_2.mp4")
        _touch(d, "Talk_clip_2_safe.mp4")

        entries = _clip_variant_entries("job1", d, "Talk", 1)
        assert [e["video_url"] for e in entries] == [
            "/videos/job1/Talk_clip_2.mp4",
            "/videos/job1/Talk_clip_2_safe.mp4",
        ]

    def test_clip_index_is_not_confused_with_a_neighbour(self, tmp_path):
        d = str(tmp_path)
        _touch(d, "Talk_clip_1.mp4")
        _touch(d, "Talk_clip_1_safe.mp4")
        _touch(d, "Talk_clip_11.mp4")

        entries = _clip_variant_entries("job1", d, "Talk", 0)
        assert [e["video_url"] for e in entries] == [
            "/videos/job1/Talk_clip_1.mp4",
            "/videos/job1/Talk_clip_1_safe.mp4",
        ]


class TestNoKeyOnOrdinaryJobs:
    """A job that never asked for a second render must not grow a new key.

    The webhook payload, the MCP summaries, the ZIP bundle and the local
    library all consume the clip dict directly. Adding a key unconditionally
    would change every one of those payloads for every existing user.
    """

    def test_single_variant_returns_empty(self, tmp_path):
        d = str(tmp_path)
        _touch(d, "Talk_clip_1.mp4")
        _touch(d, "subtitled_100_Talk_clip_1.mp4")

        assert _clip_variant_entries("job1", d, "Talk", 0) == []

    def test_safe_without_auto_is_not_a_toggle(self, tmp_path):
        # One entry is not a choice; and the auto file missing means the clip
        # itself failed, which is not something a variant list should paper over.
        d = str(tmp_path)
        _touch(d, "Talk_clip_1_safe.mp4")

        assert _clip_variant_entries("job1", d, "Talk", 0) == []

    def test_nothing_on_disk_returns_empty(self, tmp_path):
        assert _clip_variant_entries("job1", str(tmp_path), "Talk", 0) == []


class TestNewestDerived:
    def test_returns_the_clean_name_when_nothing_derived_exists(self, tmp_path):
        # This is why _clip_variant_entries tests existence on the pristine
        # name: _newest_derived can never answer "never rendered".
        assert _newest_derived(str(tmp_path), "Talk_clip_1.mp4") == "Talk_clip_1.mp4"

    def test_picks_the_most_recent_styling(self, tmp_path):
        d = str(tmp_path)
        _touch(d, "subtitled_100_Talk_clip_1.mp4", age=100)
        _touch(d, "subtitled_200_Talk_clip_1.mp4", age=10)

        assert _newest_derived(d, "Talk_clip_1.mp4") == "subtitled_200_Talk_clip_1.mp4"


class TestResolveVariants:
    def test_default_is_auto_only(self):
        for raw in (None, "", [], "   "):
            assert app_module.resolve_variants(raw) == ["auto"]

    def test_safe_is_additive_never_a_replacement(self):
        assert app_module.resolve_variants("safe") == ["auto", "safe"]
        assert app_module.resolve_variants(["safe", "auto"]) == ["auto", "safe"]

    def test_unknown_names_are_ignored(self):
        assert app_module.resolve_variants("safe,bogus") == ["auto", "safe"]

    def test_horizontal_collapses_to_auto(self):
        # Nothing is reframed on the horizontal path, so the second render
        # would be a byte-identical duplicate of the first.
        assert app_module.resolve_variants("safe", "horizontal") == ["auto"]

    def test_vertical_and_square_keep_the_second_render(self):
        assert app_module.resolve_variants("safe", "vertical") == ["auto", "safe"]
        assert app_module.resolve_variants("safe", "square") == ["auto", "safe"]
