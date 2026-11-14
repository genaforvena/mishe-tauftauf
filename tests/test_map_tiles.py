"""Terminal tile map: one model from floorplan.json and map-state.json.

Covers the honesty rules: assumed geometry dashed, an unknown door as '?', fresh
versus ghost people, UNKNOWN for missing or stale files, and the 100-column limit.
"""
import datetime
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import map_tiles as mt  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 13, 23, 50, tzinfo=UTC)

FLOOR = {
    "rooms": [
        {"id": "living_room", "label": "living room",
         "area_m2": {"value": 30, "provenance": "assumed"},
         "width_m": {"value": 6.0, "provenance": "assumed"},
         "depth_m": {"value": 5.0, "provenance": "assumed"},
         "position_m": {"x": 0.0, "y": 0.0, "provenance": "assumed"}},
        {"id": "bedroom", "label": "bedroom",
         "area_m2": {"value": 25, "provenance": "assumed"},
         "width_m": {"value": 5.0, "provenance": "assumed"},
         "depth_m": {"value": 5.0, "provenance": "assumed"},
         "position_m": {"x": 6.0, "y": 0.0, "provenance": "assumed"}},
    ],
    "doors": [{"id": "door_living_bedroom", "between": ["living_room", "bedroom"],
               "position_m": {"value": None, "provenance": "unknown"}}],
    "windows": [{"id": "bedroom_window", "room": "bedroom"}],
}


def _state(markers, generated="2026-11-13T23:49:00Z", rooms=None):
    return {"generated_utc": generated, "markers": markers,
            "room_activity": rooms or {
                "living_room": {"last_usable_utc": "2026-11-13T23:48:00+00:00", "activity": 0.01},
                "bedroom": {"last_usable_utc": "2026-11-13T20:00:00+00:00", "activity": 0.02}},
            "rssi": {"text": "rssi (Redmi10): UNKNOWN, no redmi10 rssi"}}


FRESH_ILYA = {
    "ilya": {"room": "bedroom", "confidence": "medium", "why": "label: bedroom_at_mac",
             "age_h": 0.5, "guess": {"room": "bedroom", "confidence": 0.8, "evidence": "fresh"}},
    "rozalia": {"room": "living_room", "confidence": "UNKNOWN", "why": "ghost",
                "age_h": 58.0, "guess": {"room": "living_room", "confidence": 0.1, "evidence": "ghost"}},
    "bruno": {"room": None, "confidence": "UNKNOWN", "why": "no room label ever",
              "guess": {"room": None, "confidence": 0.0, "evidence": "none"}},
}


def _model(state=None, floor=FLOOR, reason=None, phone=None, now=NOW):
    return mt.build_model(floor, reason, state, None if state is not None else "missing",
                          phone, now)


def _rows(model, color=False):
    return mt.map_rows(model, color=color)


def test_golden_frame_from_fixture_plain():
    model = _model(_state(FRESH_ILYA), phone={"age_s": 5.0, "status": "ok",
                                              "collector_t": NOW.timestamp() - 10})
    text = mt.frame(model, NOW, color=False)
    golden = (ROOT / "tests" / "fixtures" / "map_tiles_golden.txt").read_text(encoding="utf-8")
    assert text == golden


def test_assumed_geometry_is_dashed_and_has_no_solid_wall():
    rows = _rows(_model(_state(FRESH_ILYA)))
    walls = "".join(rows)
    assert ":" in walls                      # dashed plain-mode edges
    assert "-" not in walls and "|" not in walls


def test_unknown_door_is_a_question_tile_not_a_gap_or_solid_wall():
    model = _model(_state(FRESH_ILYA))
    assert model["doors"], "the door between the two rooms is placed on the shared wall"
    x0, y0, _, _ = mt._grid_bounds(model)
    rows = _rows(model)
    for cx, cy in model["doors"]:
        ch = rows[cy - y0][cx - x0]
        assert ch == "?"
    # the shared wall is otherwise dashed, never a solid gap
    cx = model["doors"][0][0]
    column = [rows[r][cx - x0] for r in range(len(rows)) if len(rows[r]) > cx - x0]
    assert " " not in [c for c in column[1:-1]]


def test_fresh_person_is_bright_letter_and_ghost_is_dim_question_mark():
    model = _model(_state(FRESH_ILYA))
    colour = "".join(_rows(model, color=True))
    assert "\x1b[1;92mI\x1b[0m" in colour     # fresh Ilya, bright solid
    assert "\x1b[2;90m?\x1b[0m" in colour     # ghost Rozalia, dim hollow
    assert "I Ilya: fresh label" in mt.frame(model, NOW)


def test_best_guess_with_number_is_normal_and_numbered_in_legend():
    model = _model(_state(FRESH_ILYA))
    text = mt.frame(model, NOW)
    assert "best guess bedroom, 80%" not in text  # fresh Ilya is a fresh label, not a guess
    model2 = _model(_state({"ilya": {"room": None, "confidence": "UNKNOWN",
                                     "guess": {"room": "bedroom", "confidence": 0.4}}}))
    assert "best guess bedroom, 40%" in mt.frame(model2, NOW)


def test_missing_floorplan_is_unknown_and_draws_no_rooms():
    model = _model(_state(FRESH_ILYA), floor=None, reason="missing")
    text = mt.frame(model, NOW)
    assert "Map UNKNOWN: floorplan.json missing" in text
    assert "+" not in "".join(_rows(model))


def test_missing_map_state_is_unknown_and_hides_sprites():
    model = _model(None)
    text = mt.frame(model, NOW)
    assert "Best guess UNKNOWN: map-state.json missing" in text
    assert model["sprites"] == []


def test_stale_state_is_flagged_and_sprites_are_hidden():
    model = _model(_state(FRESH_ILYA, generated="2026-11-13T22:00:00Z"))
    text = mt.frame(model, NOW)
    assert "Best guess STALE" in text
    assert model["sprites"] == []


def test_camera_age_is_derived_from_last_usable_bucket_when_no_age_field():
    model = _model(_state(FRESH_ILYA))
    lines = [s for s in model["status"] if s.startswith("camera living")]
    assert lines and "2 min" in lines[0] and "fresh" in lines[0]


def test_camera_age_field_adds_time_since_render():
    # age_s was measured when the state was rendered 10 min before NOW; the living camera
    # limit is 5 min, so the bucket is 11 min old now and must be tagged stale, not fresh.
    rooms = {"living_room": {"last_usable_utc": "2026-11-13T23:48:00+00:00",
                             "activity": 0.01, "age_s": 60.0},
             "bedroom": {"last_usable_utc": "2026-11-13T20:00:00+00:00",
                         "activity": 0.02, "age_s": None}}
    state = _state(FRESH_ILYA, generated="2026-11-13T23:40:00Z", rooms=rooms)
    model = _model(state)
    living = [s for s in model["status"] if s.startswith("camera living")][0]
    assert "11 min" in living and "(stale)" in living and "(fresh)" not in living
    assert mt._camera_age(rooms["living_room"], NOW, None) is None   # no render time: UNKNOWN
    assert mt._camera_age(rooms["living_room"], NOW, "2026-11-13T23:55:00Z") == 60.0  # clamped
    assert model["sprites"], "sprites stay until the map state is 30 min old"


def test_every_frame_line_fits_one_hundred_columns():
    long_state = _state({"ilya": {"room": "bedroom", "confidence": "medium", "why": "x" * 400,
                                  "age_h": 0.1, "guess": {}}})
    text = mt.frame(_model(long_state), NOW, color=False)
    assert all(len(line) <= mt.MAX_WIDTH for line in text.splitlines())
    rows = _rows(_model(long_state))
    assert all(len(r) <= mt.MAX_WIDTH for r in rows)


def test_no_color_env_and_non_tty_are_plain(monkeypatch):
    spec = importlib.util.spec_from_file_location("map_view", ROOT / "tools" / "map_view.py")
    mv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mv)

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    assert mv.colour_wanted(False, Tty()) is False
    monkeypatch.delenv("NO_COLOR")
    assert mv.colour_wanted(True, Tty()) is False
    assert mv.colour_wanted(False, Tty()) is True


def test_site_files_load_and_missing_site_is_unknown(tmp_path):
    model = mt.model_from_site(str(tmp_path / "absent"), NOW)
    assert model["sprites"] == []
    text = mt.frame(model, NOW)
    assert "Map UNKNOWN" in text and "Best guess UNKNOWN" in text


def test_phone_row_age_counts_from_collector_time(tmp_path):
    path = tmp_path / "phones.jsonl"
    path.write_text(json.dumps({"t": NOW.timestamp() - 100,
                                "redmi10": {"status": "ok", "age_s": 4.0}}) + "\n")
    phone = mt.read_phone_row(str(path))
    assert phone["age_s"] == 4.0 and phone["collector_t"] == NOW.timestamp() - 100
    model = _model(_state(FRESH_ILYA), phone=phone)
    assert any("phone Redmi10" in s and "104 s" in s for s in model["status"])
