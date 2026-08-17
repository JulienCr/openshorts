"""The measurement harness itself.

A metric that is wrong in the flattering direction is worse than no metric: it
licenses a change that made things worse. These pin the two definitions the
camera work is about to be judged on.
"""
import camera
import camera_replay


def _trace(detections, frames=60, scenes=None, width=1920, fps=30.0,
           strategies=None):
    return {
        "width": width, "height": 1080, "fps": fps, "frames": frames,
        "detect_stride": 1,
        "scenes": scenes or [[0, frames]],
        "strategies": strategies or ["TRACK"],
        "detections": detections,
    }


def _cam(width=1920):
    return camera.SmoothedCameraman(width, 1080, width, 1080, aspect_ratio=9 / 16)


class TestSceneMetrics:
    def test_a_still_camera_scores_zero(self):
        xs = [500] * 60
        m = camera_replay.scene_metrics(xs, [(0, 60)], 30.0)
        assert m[0]["travel_per_s"] == 0.0
        assert m[0]["reversals_per_s"] == 0.0

    def test_a_steady_pan_travels_but_never_reverses(self):
        xs = list(range(500, 560))
        m = camera_replay.scene_metrics(xs, [(0, 60)], 30.0)
        assert m[0]["reversals_per_s"] == 0.0
        assert m[0]["travel_per_s"] > 0

    def test_hunting_is_counted_as_reversals(self):
        # The exact symptom: the crop oscillating around a point.
        xs = []
        for i in range(60):
            xs.append(500 + (5 if i % 2 else 0))
        m = camera_replay.scene_metrics(xs, [(0, 60)], 30.0)
        assert m[0]["reversals_per_s"] > 20

    def test_the_scene_boundary_snap_is_not_scored(self):
        """A cut is SUPPOSED to reframe. Scoring it measures the feature."""
        # Frame 0 snaps from 200 to 900, then the camera holds perfectly still.
        xs = [200] + [900] * 59
        m = camera_replay.scene_metrics(xs, [(0, 60)], 30.0)
        assert m[0]["travel_per_s"] == 0.0, "the snap must not count as travel"
        assert m[0]["reversals_per_s"] == 0.0

    def test_scenes_are_scored_independently(self):
        xs = [500] * 30 + [900] * 30
        m = camera_replay.scene_metrics(xs, [(0, 30), (30, 60)], 30.0)
        assert len(m) == 2
        assert all(s["travel_per_s"] == 0.0 for s in m)

    def test_general_frames_are_skipped(self):
        # GENERAL/SPLIT scenes have no trajectory; None must not be arithmetic.
        xs = [None] * 60
        assert camera_replay.scene_metrics(xs, [(0, 60)], 30.0) == []


class TestSummarise:
    def test_weights_scenes_by_length(self):
        # A 1s busy scene and a 9s still one must not average to "half busy".
        xs = [500] * 300
        for i in range(2, 30):
            xs[i] = 500 + (10 if i % 2 else 0)
        summary = camera_replay.summarise(xs, [(0, 30), (30, 300)], 30.0)
        still = camera_replay.summarise(xs, [(30, 300)], 30.0)
        assert still["travel_per_s"] == 0.0
        # The short busy scene is 10% of the footage, so it must contribute
        # about a tenth of its own rate, not half of it.
        assert 0 < summary["travel_per_s"] < 0.2 * (
            camera_replay.summarise(xs, [(0, 30)], 30.0)["travel_per_s"])

    def test_empty_input_is_not_a_crash(self):
        s = camera_replay.summarise([], [], 30.0)
        assert s["scenes"] == 0 and s["travel_per_s"] == 0.0


class TestCompare:
    def _summary(self, travels):
        return {"per_scene": [
            {"start_frame": i * 100, "seconds": 1.0,
             "travel_per_s": t, "reversals_per_s": 0.0}
            for i, t in enumerate(travels)]}

    def test_reports_scenes_that_got_worse_not_just_the_mean(self):
        """bdd9e5d improved the mean while making 7 of 84 scenes busier.

        A harness that only reported means would have called that a clean win.
        """
        before = self._summary([100.0, 100.0, 100.0])
        after = self._summary([10.0, 10.0, 300.0])
        c = camera_replay.compare(before, after)
        assert c["calmer"] == 2
        assert c["busier"] == 1
        assert c["worst_regression"]["after"] == 300.0

    def test_identical_runs_are_all_unchanged(self):
        s = self._summary([50.0, 60.0])
        c = camera_replay.compare(s, s)
        assert c == {"calmer": 0, "busier": 0, "unchanged": 2,
                     "worst_regression": None}


class TestReplayLoop:
    """The replay must reproduce the shipping decision loop, not approximate it."""

    def test_a_static_subject_produces_a_static_crop(self):
        det = {str(f): [{"box": [900, 100, 120, 120], "score": 14400}]
               for f in range(0, 60)}
        xs = camera_replay.replay(_trace(det), _cam(), camera.SpeakerTracker(),
                                  detect_stride=1)
        assert len(set(xs)) == 1, "a motionless subject must not move the frame"

    def test_general_scenes_get_no_trajectory(self):
        xs = camera_replay.replay(
            _trace({}, strategies=["GENERAL"]), _cam(), camera.SpeakerTracker(),
            detect_stride=1)
        assert all(x is None for x in xs)

    def test_scene_start_is_a_detection_frame_only_when_phased(self):
        """The stale-snap bug, isolated.

        DETECT_STRIDE=4 and a scene starting at frame 6: 6 % 4 != 0, so today
        the first detection inside the new shot lands at frame 8 — two frames
        AFTER the force_snap at frame 6 has already committed the camera to
        whatever the previous shot's target was. Three scene starts in four
        land this way. Phasing the stride on the scene start makes frame 6
        itself a detection frame, so the snap has something current to snap to.

        The move here is deliberately inside the safe zone: a bigger one would
        need jump_confirm_frames consecutive detections and would prove nothing
        about phasing.
        """
        det = {"6": [{"box": [1000, 100, 100, 100], "score": 10000}]}
        trace = _trace(det, frames=20, scenes=[[0, 6], [6, 20]],
                       strategies=["TRACK", "TRACK"])

        unphased = camera_replay.replay(trace, _cam(), camera.SpeakerTracker(),
                                        detect_stride=4, phase_on_scene=False)
        phased = camera_replay.replay(trace, _cam(), camera.SpeakerTracker(),
                                      detect_stride=4, phase_on_scene=True)
        # Unphased snaps to the inherited centre; phased snaps to the subject.
        assert unphased[6] == 960 - 607 // 2 - 1   # frame centre, nothing seen
        assert phased[6] > unphased[6], "phased must snap onto the new subject"
