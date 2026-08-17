"""The template must document every key the preset loader accepts.

`style.example.json` is the only place a self-hoster discovers what a preset
can carry — `style_preset.SUPPORTED_KEYS` is a tuple in a module nobody reads,
and the dashboard's "Default style" panel exposes a subset. A key that is
supported but absent from the template is therefore invisible, and the cost is
not theoretical: `variants` shipped supported and undocumented, so every batch,
CLI and MCP submission silently produced a single render while the dashboard's
pre-ticked box made it look like the feature was on.

The mirror assertion matters just as much. A key advertised in the template but
missing from `SUPPORTED_KEYS` is dropped without a word by `style_env`, so the
operator edits a value, sees no change, and has nothing to grep for.
"""
import json
import os

import pytest

import style_preset

EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "style.example.json",
)


@pytest.fixture
def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def documented(preset):
    """Keys the template actually advertises; `_`-prefixed ones are prose."""
    return {k for k in preset if not k.startswith("_")}


def test_template_is_valid_json_and_an_object(example):
    assert isinstance(example, dict)


def test_every_supported_key_is_documented(example):
    missing = sorted(set(style_preset.SUPPORTED_KEYS) - documented(example))
    assert not missing, (
        "supported but absent from style.example.json, so undiscoverable: "
        + ", ".join(missing)
    )


def test_template_advertises_nothing_the_loader_drops(example):
    unknown = sorted(documented(example) - set(style_preset.SUPPORTED_KEYS))
    assert not unknown, (
        "documented in style.example.json but dropped by style_env, so editing "
        "it changes nothing: " + ", ".join(unknown)
    )


def test_template_survives_the_loader_intact(example, tmp_path, monkeypatch):
    """Copied to style.json verbatim, it must load rather than fall back.

    `load_style` answers `{}` for anything it dislikes, and that silence is
    deliberate everywhere else — here it would mean the shipped template is
    itself unusable.
    """
    path = tmp_path / "style.json"
    path.write_text(json.dumps(example), encoding="utf-8")
    monkeypatch.setenv(style_preset.STYLE_FILE_ENV, str(path))

    loaded = style_preset.load_style()

    assert documented(loaded) == documented(example)


def test_documented_keys_reach_the_renderer(example):
    """`style_env` is the seam to the job; a key that dies there is inert."""
    payload = style_preset.style_env(example)["OPENSHORTS_STYLE"]

    assert documented(json.loads(payload)) == documented(example)
