import panel_layout
from panel_layout import (
    analyze_scene,
    panel_filtergraph,
    panel_geometry,
    tile_grid,
    usable_faces,
    well_separated,
)


def face(cx, w=200, h=200, cy=400):
    box = [int(cx - w / 2), int(cy - h / 2), w, h]
    return {'box': box, 'score': w * h}


def panel(n, frame_w=1920):
    """n evenly spaced faces across the frame."""
    step = frame_w / (n + 1)
    return [face(step * (i + 1)) for i in range(n)]


class TestTileGrid:
    def test_three_people_stack(self):
        cols, rows, tw, th = tile_grid(3, 1080, 1920)
        assert (cols, rows) == (1, 3)
        assert tw == 1080 and th == 640

    def test_four_people_make_a_grid(self):
        cols, rows, tw, th = tile_grid(4, 1080, 1920)
        assert (cols, rows) == (2, 2)
        assert tw == 540 and th == 960

    def test_tiles_are_even(self):
        for n in (3, 4):
            _c, _r, tw, th = tile_grid(n, 1080, 1920)
            assert tw % 2 == 0 and th % 2 == 0

    def test_tiles_fill_the_frame(self):
        for n in (3, 4):
            cols, rows, tw, th = tile_grid(n, 1080, 1920)
            assert cols * tw == 1080
            assert abs(rows * th - 1920) <= 2


class TestUsableFaces:
    def test_tiny_faces_are_dropped(self):
        faces = panel(3) + [face(1800, w=40, h=40)]
        assert len(usable_faces(faces, 1920)) == 3

    def test_result_is_left_to_right(self):
        faces = list(reversed(panel(4)))
        xs = [c['box'][0] for c in usable_faces(faces, 1920)]
        assert xs == sorted(xs)


class TestWellSeparated:
    def test_evenly_spaced_panel_passes(self):
        assert well_separated(usable_faces(panel(4), 1920), 1920)

    def test_two_people_shoulder_to_shoulder_fail(self):
        faces = [face(900), face(980), face(1500)]
        assert not well_separated(faces, 1920)

    def test_people_stacked_vertically_count_as_separated(self):
        # Measured on a real recording: two webcams on the same side of the
        # frame, 18px apart horizontally and 545px vertically. A horizontal-gap
        # test called them one person.
        faces = [face(300, cy=400), face(1760, cy=210), face(1760, cy=755)]
        assert well_separated(faces, 1920)

    def test_faces_close_in_both_axes_still_fail(self):
        faces = [face(300, cy=400), face(1760, cy=400), face(1780, cy=420)]
        assert not well_separated(faces, 1920)


class TestAnalyzeScene:
    def test_steady_panel_of_three_is_found(self):
        centres = analyze_scene([panel(3)] * 10, 1920)
        assert centres is not None and len(centres) == 3

    def test_steady_panel_of_four_is_found(self):
        centres = analyze_scene([panel(4)] * 10, 1920)
        assert len(centres) == 4

    def test_two_people_are_left_to_split(self):
        # Two is SPLIT's job; this layout must not claim it.
        assert analyze_scene([panel(2)] * 10, 1920) is None

    def test_a_crowd_is_rejected(self):
        # Five tiles on a 1080-wide frame leaves nobody legible.
        assert analyze_scene([panel(6)] * 10, 1920) is None

    def test_shot_reverse_shot_is_rejected(self):
        # Faces never coexisting means a multi-camera cut; tiling would show the
        # same person more than once.
        frames = [[face(900)], [face(1000)], [face(800)]] * 4
        assert analyze_scene(frames, 1920) is None

    def test_the_dominant_headcount_wins(self):
        # One frame catching a fourth person must not turn a trio into a quad.
        frames = [panel(3)] * 9 + [panel(4)]
        assert len(analyze_scene(frames, 1920)) == 3

    def test_empty_input_is_rejected(self):
        assert analyze_scene([], 1920) is None

    def test_centres_come_back_left_to_right(self):
        centres = analyze_scene([panel(4)] * 10, 1920)
        assert [c[0] for c in centres] == sorted(c[0] for c in centres)


class TestPanelGeometry:
    def test_crop_stays_inside_the_source(self):
        _c, _r, tw, th = tile_grid(3, 1080, 1920)
        for cx in (100, 960, 1850):
            w, h, x, y = panel_geometry(1920, 1080, tw, th, (cx, 400, 200, 200))
            assert 0 <= x <= 1920 - w
            assert 0 <= y <= 1080 - h

    def test_crop_matches_the_tile_aspect(self):
        _c, _r, tw, th = tile_grid(4, 1080, 1920)
        w, h, _x, _y = panel_geometry(1920, 1080, tw, th, (900, 400, 200, 200))
        assert abs((w / h) - (tw / th)) < 0.02

    def test_dimensions_are_even(self):
        _c, _r, tw, th = tile_grid(3, 1080, 1920)
        w, h, x, y = panel_geometry(1920, 1080, tw, th, (777, 333, 201, 201))
        assert w % 2 == 0 and h % 2 == 0 and x % 2 == 0 and y % 2 == 0


class TestPanelFiltergraph:
    def test_three_stack_vertically(self):
        graph = panel_filtergraph(1920, 1080, 1080, 1920,
                                  [(300, 400, 200, 200), (900, 400, 200, 200),
                                   (1500, 400, 200, 200)])
        assert "split=3" in graph
        assert "vstack=inputs=3" in graph
        assert "hstack" not in graph
        assert graph.endswith("[v]")

    def test_four_make_a_grid(self):
        graph = panel_filtergraph(1920, 1080, 1080, 1920,
                                  [(200, 400, 180, 180), (700, 400, 180, 180),
                                   (1200, 400, 180, 180), (1700, 400, 180, 180)])
        assert "split=4" in graph
        assert graph.count("hstack=inputs=2") == 2
        assert "vstack=inputs=2" in graph

    def test_every_tile_is_the_same_size(self):
        graph = panel_filtergraph(1920, 1080, 1080, 1920,
                                  [(300, 400, 200, 200), (900, 400, 200, 200),
                                   (1500, 400, 200, 200)])
        assert graph.count("scale=1080:640") == 3

    def test_tiles_keep_source_order(self):
        graph = panel_filtergraph(1920, 1080, 1080, 1920,
                                  [(300, 400, 200, 200), (900, 400, 200, 200),
                                   (1500, 400, 200, 200)])
        xs = [int(seg.split("x=")[1].split(":")[0])
              for seg in graph.split("crop=")[1:]]
        assert xs == sorted(xs)


class TestGating:
    def test_disabled_module_touches_nothing(self, monkeypatch):
        monkeypatch.setattr(panel_layout, "ENABLED", False)
        assert panel_layout.detect_panel_scenes("x.mp4", [], []) == {}
