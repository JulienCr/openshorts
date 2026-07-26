"""Speaker switching hysteresis.

Regression cover for the camera swinging between subjects: the switch cooldown
used to fall through and switch anyway whenever the active speaker happened to
be missing from the current frame's candidates — a blink or one motion-blurred
frame was enough, which is exactly when the cooldown is needed. Measured on a
12s clip (25-jul-2026), 3 of 7 target switches jumped the cooldown this way.
"""
import pytest

main = pytest.importorskip("main")  # needs cv2/mediapipe, absent in minimal CI

WIDTH = 1280
COOLDOWN = 30


def _face(center_x, size=120, score=None):
    """A candidate box centred on ``center_x``."""
    return {"box": [center_x - size / 2, 100, size, size],
            "score": score if score is not None else size * size}


def _tracker():
    return main.SpeakerTracker(cooldown_frames=COOLDOWN)


def _lock_onto(tracker, x, frame=0):
    """Drive the tracker until it is locked on the subject at ``x``."""
    for f in range(frame, frame + 5):
        tracker.get_target([_face(x)], f, WIDTH)
    return tracker.active_speaker_id


class TestSwitchCooldown:
    def test_locks_onto_a_lone_speaker(self):
        t = _tracker()
        assert _lock_onto(t, 300) is not None

    def test_holds_when_the_active_speaker_blinks_out(self):
        # THE BUG: speaker A vanishes for one frame while B is on screen.
        # Inside the cooldown the tracker must hold, not hand the camera to B.
        t = _tracker()
        a = _lock_onto(t, 300)
        box = t.get_target([_face(1000)], 6, WIDTH)   # only B visible
        assert box is None, "must hold instead of switching mid-cooldown"
        assert t.active_speaker_id == a, "the active speaker must not change"

    def test_holds_on_the_active_speaker_when_both_are_visible(self):
        t = _tracker()
        a = _lock_onto(t, 300)
        box = t.get_target([_face(300), _face(1000)], 6, WIDTH)
        assert box is not None
        assert t.active_speaker_id == a

    def test_switches_once_the_cooldown_expires(self):
        # The hold is bounded: someone who really left the shot is released.
        t = _tracker()
        a = _lock_onto(t, 300)
        t.get_target([_face(1000)], 6, WIDTH)          # held
        assert t.active_speaker_id == a
        for f in range(COOLDOWN + 10, COOLDOWN + 20):  # past the window
            t.get_target([_face(1000)], f, WIDTH)
        assert t.active_speaker_id != a, "cooldown must not hold forever"

    def test_no_candidates_holds_rather_than_recentres(self):
        t = _tracker()
        _lock_onto(t, 300)
        assert t.get_target([], 6, WIDTH) is None

    def test_repeated_dropouts_do_not_accumulate_switches(self):
        # A flickering detection used to switch on every dropout. The lock is
        # taken at frame 0, so the cooldown window runs up to frame COOLDOWN.
        t = _tracker()
        a = _lock_onto(t, 300)
        for f in range(6, COOLDOWN):
            t.get_target([_face(1000)] if f % 2 else [_face(300), _face(1000)],
                         f, WIDTH)
        assert t.active_speaker_id == a
