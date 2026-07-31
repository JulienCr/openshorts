"""Ask Gemini which layout a video needs, once per video.

The four previous attempts at this problem asked the model (or a pixel
heuristic) to MEASURE something — edge density, MSER text density, what fraction
of the duration had content on screen, what fraction of the width it spanned —
and let thresholds turn that number into a routing decision. All four failed the
same way: they could not separate a spreadsheet from a corner scoreboard.

This asks for the decision itself. Measured over the 48-clip corpus against
hand-checked labels (`labels-layout.json` in the reframe-testing skill), three
runs:

    run 1   45/48  94%   18/20 content found   1 false positive of 28
    run 2   44/48  92%   17/20                 1 of 28
    run 3   46/48  96%   18/20                 0 of 28

Two clips out of 48 changed answer between runs. That matters because the
earlier note in this repo said Gemini was too non-deterministic to build on —
the same video scored 1% and 97% coverage on consecutive runs. The variance was
in asking for a continuous measurement, not in the model: a categorical choice
between closed options is stable.

Off by default (``AUTO_LAYOUT=1``). A caller that already switched layouts on
by hand wins: this only ever ADDS, so an explicit choice is never overridden.
"""
import json
import os
import time

ENABLED = os.environ.get("AUTO_LAYOUT", "0") == "1"

# What each decision turns on. Keys match the layout names in the prompt.
DECISION_FLAGS = {
    "none": [],
    "screencast": ["screencast_layout"],
    "split": ["split_layout", "active_speaker"],
}

VALID = set(DECISION_FLAGS)


def _module_flags(decision):
    """Modules to enable for a decision, ignoring anything unrecognised."""
    return DECISION_FLAGS.get(str(decision or "none").strip().lower(), [])


def apply(decision):
    """Switch on the modules a decision needs. Returns the modules touched.

    Deliberately additive: an operator who set SPLIT_LAYOUT=1 for a job wants
    stacking regardless of what the model thinks, and a model that says "none"
    must not quietly undo that.
    """
    import active_speaker
    import screencast_layout
    import split_layout

    modules = {"split_layout": split_layout,
               "screencast_layout": screencast_layout,
               "active_speaker": active_speaker}

    touched = []
    for name in _module_flags(decision):
        module = modules.get(name)
        if module is not None and not getattr(module, "ENABLED", False):
            module.ENABLED = True
            touched.append(name)
    return touched


def pick(video_path, video_duration):
    """The layout Gemini picks for this video, or "none" on any failure.

    Never raises: a missing answer has to degrade to today's routing rather
    than break the job.
    """
    if not ENABLED:
        return "none"
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "none"

    model_name = os.environ.get("GEMINI_MODEL") or 'gemini-3.1-flash-lite'
    print("🎛️  Choosing a layout for this video…")
    try:
        # Inside the try on purpose: the contract above is that this never
        # raises, and an unimportable SDK is just one more reason to fall back.
        from google import genai
        from google.genai import types as genai_types
        import gemini_worker

        client = genai.Client(api_key=api_key)
        upload = client.files.upload(file=video_path)
        deadline = time.time() + 180
        while True:
            info = client.files.get(name=upload.name)
            state = str(getattr(getattr(info, "state", info), "name", "")).upper()
            if state == "ACTIVE":
                break
            if state == "FAILED" or time.time() > deadline:
                print("   ⚠️ Upload not usable — keeping the default layout.")
                return "none"
            time.sleep(2)

        response = client.models.generate_content(
            model=model_name,
            contents=[upload, gemini_worker.LAYOUT_CHOICE_PROMPT],
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=gemini_worker.LayoutChoice,
            ))
        gemini_worker.raise_if_blocked(response)
        answer = json.loads(response.text) or {}
    except Exception as e:
        print(f"   ⚠️ Layout choice failed ({e}) — keeping the default layout.")
        return "none"

    decision = str(answer.get("layout", "none")).strip().lower()
    if decision not in VALID:
        print(f"   ⚠️ Unknown layout '{decision}' — keeping the default layout.")
        return "none"

    why = str(answer.get("why", ""))[:80]
    confidence = answer.get("confidence")
    print(f"   🎬 Layout: {decision} (confianza {confidence}) — {why}")
    return decision


def pick_and_apply(video_path, video_duration):
    """Convenience for the pipeline: decide, switch on, report what changed."""
    decision = pick(video_path, video_duration)
    touched = apply(decision)
    if touched:
        print(f"   ✅ Enabled: {', '.join(touched)}")
    return decision
