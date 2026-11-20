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


def test_guess_line_names_label_room_and_calls_weight_unvalidated():
    fresh = mt.frame(_model(_state(FRESH_ILYA)), NOW)
    assert "guess bedroom" not in fresh  # fresh Ilya is a fresh label, not a guess

    markers = {
        "ilya": {"room": "bedroom", "confidence": "UNKNOWN", "age_h": 203.8,
                 "guess": {"room": "bedroom", "confidence": 0.4}},
        "rozalia": {"room": "kitchen_stall", "confidence": "UNKNOWN", "age_h": 203.7,
                    "guess": {"room": "bedroom", "confidence": 0.4}},
    }
    text = mt.frame(_model(_state(markers)), NOW)
    assert "I Ilya: guess bedroom (weight 40%, not a probability); last label bedroom 203.8h ago" in text
    assert "R Rozalia: guess bedroom (weight 40%, not a probability); last label kitchen_stall 203.7h ago" in text
    assert "(label 203.7 h old)" not in text  # the age no longer floats unlabelled
    assert "best guess bedroom" not in text  # the old per-person wording is gone (map header may still say "best guess")


def test_guess_line_fails_closed_on_missing_or_malformed_label():
    markers = [
        {"room": None, "confidence": "UNKNOWN", "age_h": 203.7,
         "guess": {"room": "bedroom", "confidence": 0.4}},
        {"room": "bedroom", "confidence": "UNKNOWN", "age_h": None,
         "guess": {"room": "bedroom", "confidence": 0.4}},
        {"room": "bedroom", "confidence": "UNKNOWN", "age_h": "old",
         "guess": {"room": "bedroom", "confidence": 0.4}},
    ]
    for marker in markers:
        text = mt.frame(_model(_state({"ilya": marker})), NOW)
        assert "I Ilya: guess bedroom (weight 40%, not a probability); last label UNKNOWN" in text
        assert "last label bedroom" not in text


def test_ghost_line_dates_the_last_label_instead_of_opaque_hours():
    markers = {
        "ilya": {"room": "bedroom", "confidence": "UNKNOWN", "age_h": 208.9,
                 "label_utc": "2026-11-11T13:39Z",
                 "guess": {"room": "bedroom", "confidence": 0.15}},
    }
    text = mt.frame(_model(_state(markers)), NOW)
    assert "? Ilya: UNKNOWN, last known bedroom (label on 2026-11-11, 8.7d ago)" in text
    assert "208.9 h old" not in text


def test_guess_line_dates_the_last_label_beside_the_guess_room():
    markers = {
        "ilya": {"room": "bedroom", "confidence": "UNKNOWN", "age_h": 203.8,
                 "label_utc": "2026-11-11T18:33Z",
                 "guess": {"room": "bedroom", "confidence": 0.4}},
    }
    text = mt.frame(_model(_state(markers)), NOW)
    assert "last label bedroom on 2026-11-11, 8.5d ago" in text
    assert all(len(line) <= mt.MAX_WIDTH for line in text.splitlines())


def test_legend_fails_closed_on_missing_or_malformed_label_date():
    for lutc in (None, "garbage", "2026-99-99T13:39Z", 123):
        marker = {"room": "bedroom", "confidence": "UNKNOWN", "age_h": 208.9,
                  "guess": {"room": "bedroom", "confidence": 0.15}}
        if lutc is not None:
            marker["label_utc"] = lutc
        text = mt.frame(_model(_state({"ilya": marker})), NOW)
        ilya_line = [line for line in text.splitlines() if "Ilya:" in line][0]
        assert "(label 208.9 h old)" in ilya_line
        assert "2026-" not in ilya_line


def test_undrawn_room_names_the_recorded_place_instead_of_no_place():
    markers = {
        "rozalia": {"room": "kitchen_stall", "confidence": "UNKNOWN", "age_h": 208.8,
                    "label_utc": "2026-11-05T07:02Z",
                    "guess": {"room": None, "confidence": 0.0}},
    }
    model = _model(_state(markers))
    text = mt.frame(model, NOW)
    assert ("? Rozalia: UNKNOWN, last known kitchen stall "
            "(not drawn on this map; label on 2026-11-05, 8.7d ago)") in text
    assert "Rozalia: no place recorded" not in text
    assert not any(p == "rozalia" for _, _, _, p in model["sprites"])
    assert all(len(line) <= mt.MAX_WIDTH for line in text.splitlines())


def test_undrawn_room_fresh_label_keeps_its_freshness():
    marker = {"room": "kitchen_stall", "confidence": "medium", "age_h": 0.25,
              "label_utc": "2026-11-13T23:35Z", "guess": {"room": None, "confidence": 0.0}}
    line = [line for line in mt.frame(_model(_state({"rozalia": marker})), NOW).splitlines()
            if "Rozalia:" in line][0]
    assert line == "Rozalia: fresh label in kitchen stall (not drawn on this map)"


def test_undrawn_room_fails_closed_on_missing_or_malformed_marker():
    for marker in (
        {"room": None, "confidence": "UNKNOWN", "age_h": 208.8},
        {"room": 123, "confidence": "UNKNOWN", "age_h": 208.8},
    ):
        text = mt.frame(_model(_state({"rozalia": marker})), NOW)
        assert "Rozalia: no place recorded (UNKNOWN)" in text

    marker = {"room": "kitchen_stall", "confidence": "UNKNOWN", "age_h": 208.8,
              "label_utc": "garbage"}
    line = [line for line in mt.frame(_model(_state({"rozalia": marker})), NOW).splitlines()
            if "Rozalia:" in line][0]
    assert "not drawn on this map; label 208.8 h old" in line
    assert "2026-" not in line


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
    lines = [s for s in model["status"] if s.startswith("activity living")]
    assert lines and "2 min" in lines[0] and "fresh" in lines[0]


def test_camera_age_field_adds_time_since_render():
    # age_s was measured when the state was rendered 10 min before NOW; the producer's bar
    # scale is 30 min, so an 11 min old bucket is still current, not stale.
    rooms = {"living_room": {"last_usable_utc": "2026-11-13T23:48:00+00:00",
                             "activity": 0.01, "age_s": 60.0},
             "bedroom": {"last_usable_utc": "2026-11-13T20:00:00+00:00",
                         "activity": 0.02, "age_s": None}}
    state = _state(FRESH_ILYA, generated="2026-11-13T23:40:00Z", rooms=rooms)
    model = _model(state)
    living = [s for s in model["status"] if s.startswith("activity living")][0]
    assert "11 min" in living and "(fresh)" in living and "(stale)" not in living
    assert mt._camera_age(rooms["living_room"], NOW, None) is None   # no render time: UNKNOWN
    assert mt._camera_age(rooms["living_room"], NOW, "2026-11-13T23:55:00Z") == 60.0  # clamped
    assert model["sprites"], "sprites stay until the map state is 30 min old"

def test_activity_lines_explain_bucket_clock_with_a_separate_frame_line():
    act = {"_newest_frame_utc": "2026-11-13T23:49:50+00:00",
           "living_room": {"last_usable_utc": "2026-11-13T23:48:00+00:00", "activity": 0.01},
           "bedroom": {"last_usable_utc": "2026-11-13T20:00:00+00:00", "activity": 0.02}}
    state = {"generated_utc": "2026-11-13T23:49:00Z", "markers": FRESH_ILYA,
             "room_activity": act, "rssi": {"text": "UNKNOWN"}}
    status = _model(state)["status"]
    rooms = [s for s in status if s.startswith(("activity living", "activity bedroom"))]
    assert len(rooms) == 2
    assert all("camera" not in s for s in rooms)
    notes = [s for s in status if s.startswith("activity age = ")]
    assert len(notes) == 1 and "15-min bucket" in notes[0] and "not camera health" in notes[0]
    frames = [s for s in status if s.startswith("camera frame ")]
    assert len(frames) == 1 and "10 s old" in frames[0]


def test_producer_state_is_authoritative_over_the_bar_scale():
    # The previous 300/900 s thresholds called this bucket stale while the producer said current.
    now = datetime.datetime(2026, 11, 20, 17, 18, 23, tzinfo=UTC)
    rooms = {"living_room": {"last_usable_utc": "2026-11-20T17:00:00+00:00",
                             "activity": 0.01, "age_s": 1103.2, "state": "current"},
             "bedroom": {"last_usable_utc": "2026-11-20T17:16:00+00:00",
                         "activity": 0.02, "age_s": 143.0, "state": "historical"}}
    state = _state(FRESH_ILYA, generated="2026-11-20T17:18:23Z", rooms=rooms)
    status = _model(state, now=now)["status"]
    living = [s for s in status if s.startswith("activity living")][0]
    bedroom = [s for s in status if s.startswith("activity bedroom")][0]
    assert "18 min" in living and "(fresh)" in living
    assert "2 min" in bedroom and "(stale)" in bedroom


def test_camera_frame_age_fails_closed_on_missing_or_malformed_stamp():
    base = _state(FRESH_ILYA)
    base["room_activity"]["_newest_frame_utc"] = "not-a-date"
    status = _model(base)["status"]
    assert "camera frame UNKNOWN (no dated frame in activity.json)" in status
    base["room_activity"]["_newest_frame_utc"] = "2026-11-13T23:49:50"
    assert any(s.startswith("camera frame UNKNOWN") for s in _model(base)["status"])
    assert mt._frame_age(None, NOW) is None
    assert mt._frame_age({}, NOW) is None


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

def test_phone_stability_counts_drops_and_fresh_rows():
    t = NOW.timestamp()
    rows = [{"t": t - 600 + i * 100, "redmi10": {"status": s}}
            for i, s in enumerate(["ok", "ok", "stale", "ok", "unknown", "ok"])]
    assert mt.phone_stability(rows, NOW) == (2, 4, 6)
    assert mt.phone_stability(rows, NOW, window_s=250) == (0, 1, 2)


def test_phone_stability_fails_closed():
    assert mt.phone_stability([], NOW) is None
    old = [{"t": NOW.timestamp() - 1e7, "redmi10": {"status": "ok"}}]
    assert mt.phone_stability(old, NOW) is None
    bad_status = [{"t": NOW.timestamp(), "redmi10": {"status": 3}}]
    assert mt.phone_stability(bad_status, NOW) is None
    bad_time = [{"t": "yesterday", "redmi10": {"status": "ok"}}]
    assert mt.phone_stability(bad_time, NOW) is None


def test_model_from_site_adds_phone_history_line(tmp_path):
    path = tmp_path / "phones.jsonl"
    rows = [{"t": NOW.timestamp() - 300 + i * 100, "redmi10": {"status": s, "age_s": 4.0}}
            for i, s in enumerate(["ok", "stale", "ok"])]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    model = mt.model_from_site(str(tmp_path / "absent"), NOW, phone_path=str(path))
    assert any("phone history: 1 drop onsets in 7d, fresh in 66% of 3 samples" in s
               for s in model["status"])
    assert any("phone Redmi10" in s and "newest row" in s for s in model["status"])


def test_model_from_site_phone_history_unknown_when_missing(tmp_path):
    model = mt.model_from_site(str(tmp_path / "absent"), NOW,
                               phone_path=str(tmp_path / "absent.jsonl"))
    assert any(s.startswith("phone history UNKNOWN") for s in model["status"])

def _night_curves():
    daylight = {"rooms": {"living room": {"hours": {
        "21": {"median_luma": 114.0, "samples": 477},
        "03": {"median_luma": 16.0, "samples": 523},
        "11": {"median_luma": 128.5, "samples": 402},
    }}}}
    activity = {"rooms": {"living room": {"buckets": [
        {"utc": "21:00Z", "activity": 0.005},
        {"utc": "03:00Z", "activity": 0.002},
        {"utc": "11:00Z", "activity": 0.010},
    ]}}}
    return daylight, activity


def _night_state(evidence="night prior 21-05 UTC (assumed local night); last label: ghost"):
    markers = _state(FRESH_ILYA)["markers"]
    markers["ilya"]["guess"]["evidence"] = evidence
    return {"markers": markers}


def test_night_prior_line_reports_measured_mismatch():
    daylight, activity = _night_curves()
    line = mt._night_prior_text(daylight, 60, activity, 60, _night_state())
    assert "night prior 21-05Z is unvalidated" in line
    assert "at 21Z living-room luma 114" in line
    assert "darkest 03Z 16" in line
    assert "camera activity 0.005 (quietest 03Z 0.002)" in line


def test_night_prior_line_names_a_window_that_holds():
    daylight, activity = _night_curves()
    daylight["rooms"]["living room"]["hours"]["21"]["median_luma"] = 12.0
    daylight["rooms"]["living room"]["hours"]["03"]["median_luma"] = 100.0
    activity["rooms"]["living room"]["buckets"][0]["activity"] = 0.001
    activity["rooms"]["living room"]["buckets"][1]["activity"] = 0.009
    line = mt._night_prior_text(daylight, 60, activity, 60, None)
    assert "night prior 21-05Z matches the darkest and quietest hour (21Z" in line


def test_night_prior_line_fails_closed():
    daylight, activity = _night_curves()
    assert "timing UNKNOWN" in mt._night_prior_text(daylight, 3600, activity, 60, None)
    assert "timing UNKNOWN" in mt._night_prior_text(None, 60, activity, 60, None)
    assert "timing UNKNOWN" in mt._night_prior_text(daylight, 60, {}, 60, None)
    assert "timing UNKNOWN" in mt._night_prior_text({"rooms": {}}, 60, activity, 60, None)
    bad = _night_state("night prior 22-06 UTC (assumed local night)")
    assert "different window" in mt._night_prior_text(daylight, 60, activity, 60, bad)


def test_missing_site_adds_unknown_night_prior_line(tmp_path):
    model = mt.model_from_site(str(tmp_path / "absent"), NOW)
    assert any(s.startswith("night prior 21-05Z:") and "UNKNOWN" in s for s in model["status"])
    assert "night prior 21-05Z:" in mt.frame(model, NOW)


def _bias_slots(hour, count):
    """Unique past timestamps in one UTC hour, spreading across days (4 per day)."""
    return [datetime.datetime(2026, 11, 13 - i // 4, hour, (i % 4) * 15, tzinfo=UTC)
            for i in range(count)]


def _bias_activity(bed_win_hours, living_win_hours):
    """Shared 15-min buckets; each (hour, count) names the room that wins there."""
    bed, liv = [], []
    for room, wins in (("bed", bed_win_hours), ("liv", living_win_hours)):
        for hour, count in wins:
            for when in _bias_slots(hour, count):
                iso = when.isoformat(timespec="seconds")
                bed.append({"utc_iso": iso, "activity": 0.09 if room == "bed" else 0.03})
                liv.append({"utc_iso": iso, "activity": 0.03 if room == "bed" else 0.09})
    return {"generated_utc": "2026-11-13T23:49:00Z",
            "rooms": {"bedroom": {"buckets": bed}, "living room": {"buckets": liv}}}


def _bias_site(tmp_path, activity):
    hh = tmp_path / "site" / "spatial" / "work" / "household"
    hh.mkdir(parents=True, exist_ok=True)
    (hh / "activity.json").write_text(json.dumps(activity))
    return str(tmp_path / "site")


def test_camera_bias_reports_near_constant_bedroom(tmp_path):
    site = _bias_site(tmp_path, _bias_activity([(10, 20), (11, 10)], []))
    line = mt.camera_room_bias_line(site, NOW)
    assert "bedroom busier in 100% of 30 shared buckets" in line
    assert "in all 2 hours" in line
    assert "near-constant, not a person signal" in line


def test_camera_bias_names_the_flip_hour(tmp_path):
    site = _bias_site(tmp_path, _bias_activity([(10, 20)], [(11, 10)]))
    line = mt.camera_room_bias_line(site, NOW)
    assert "bedroom busier in 66% of 30 shared buckets" in line
    assert "living room leads at 11Z" in line


def test_camera_bias_fails_closed(tmp_path):
    assert "UNKNOWN" in mt.camera_room_bias_line(str(tmp_path / "absent"), NOW)
    thin = _bias_activity([(10, 3)], [])
    assert "only 3 shared activity buckets" in mt.camera_room_bias_line(_bias_site(tmp_path, thin), NOW)


def _audio_site(tmp_path, rows):
    hh = tmp_path / "site" / "spatial" / "work" / "household"
    hh.mkdir(parents=True, exist_ok=True)
    (hh / "audio.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(tmp_path / "site")


def test_audio_line_reports_speech_age_and_freshness(tmp_path):
    t = NOW.timestamp()
    rows = [{"t": t - 600, "room": "livingroom", "status": "NO_SPEECH"},
            {"t": t - 60, "room": "livingroom", "status": "HINT", "text": "never drawn"},
            {"t": t - 5, "room": "livingroom", "status": "NO_SPEECH"}]
    line = mt.audio_room_line(_audio_site(tmp_path, rows), NOW)
    assert line == "audio livingroom: speech 60 s ago; listener row 5 s old (not a person claim)"
    assert "never drawn" not in line


def test_audio_line_ignores_other_rooms_and_unconfirmed_status(tmp_path):
    t = NOW.timestamp()
    rows = [{"t": t - 30, "room": "bedroom", "status": "NO_HINT"},
            {"t": t - 20, "room": "livingroom", "status": "SPOT_UNCONFIRMED"},
            {"t": t - 5, "room": "livingroom", "status": "NO_SPEECH"}]
    line = mt.audio_room_line(_audio_site(tmp_path, rows), NOW)
    assert line.startswith("audio livingroom: no speech in last")
    assert "listener row 5 s old" in line


def test_audio_line_fails_closed(tmp_path):
    assert "UNKNOWN" in mt.audio_room_line(str(tmp_path / "absent"), NOW)
    stale = [{"t": NOW.timestamp() - 900, "room": "livingroom", "status": "NO_SPEECH"}]
    assert "no fresh listener row" in mt.audio_room_line(_audio_site(tmp_path, stale), NOW)
    bad = [{"t": "yesterday", "room": "livingroom", "status": "NO_HINT"}]
    assert "UNKNOWN" in mt.audio_room_line(_audio_site(tmp_path, bad), NOW)


def test_model_from_site_adds_audio_line(tmp_path):
    t = NOW.timestamp()
    site = _audio_site(tmp_path, [{"t": t - 5, "room": "livingroom", "status": "NO_HINT"}])
    model = mt.model_from_site(site, NOW)
    assert any(s.startswith("audio livingroom: speech") for s in model["status"])
