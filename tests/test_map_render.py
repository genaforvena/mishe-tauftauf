"""Map renderer: activity and phone evidence are dated; stale, undated, future or missing is never current."""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("map_render", ROOT / "tools/map_render.py")
mr = importlib.util.module_from_spec(spec)
sys.modules["map_render"] = mr
spec.loader.exec_module(mr)

UTC = datetime.timezone.utc


def _now():
    return datetime.datetime.now(UTC)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _site(tmp_path, activity=None, phones=None):
    site = tmp_path / "site"
    hh = site / "spatial/work/household"
    hh.mkdir(parents=True)
    (hh / "labels.tsv").write_text("")
    if activity is not None:
        (hh / "activity.json").write_text(json.dumps(activity))
    if phones is not None:
        (hh / "phones.jsonl").write_text("".join(json.dumps(r) + "\n" for r in phones))
    return site


def _activity(room_buckets, generated=None):
    rooms = {}
    for room, buckets in room_buckets.items():
        rooms[room] = {"buckets": buckets, "usable_buckets": sum(b.get("activity") is not None for b in buckets)}
    return {"generated_utc": generated or _iso(_now()), "rooms": rooms}


def _bucket(when, activity=0.05, luma=70.0, with_iso=True):
    b = {"utc": when.strftime("%H:%MZ"), "activity": activity, "mean_luma": luma}
    if with_iso:
        b["utc_iso"] = _iso(when)
    return b


def _render(site):
    assert mr.main(["--site", str(site)]) == 0
    state = json.loads((site / "senses/map/map-state.json").read_text())
    svg = (site / "senses/map/apartment-map.svg").read_text()
    return state, svg


def test_fresh_bucket_is_current_and_cited(tmp_path):
    now = _now()
    site = _site(tmp_path, _activity({"bedroom": [_bucket(now - datetime.timedelta(minutes=20), 0.09)]}))
    state, svg = _render(site)
    act = state["room_activity"]["bedroom"]
    assert act["state"] == "current"
    assert act["activity"] == 0.09
    assert "activity 0.090" in svg
    assert "camera busiest room bedroom" in json.dumps(state["markers"])


def test_previous_day_bucket_is_historical_not_busiest_now(tmp_path):
    now = _now()
    yesterday = now - datetime.timedelta(days=1)
    site = _site(tmp_path, _activity({"bedroom": [_bucket(yesterday, 0.2)]}))
    state, svg = _render(site)
    act = state["room_activity"]["bedroom"]
    assert act["state"] == "historical"
    assert "historical 0.200" in svg and "not current" in svg
    assert "activity 0.200" not in svg
    evidence = json.dumps(state["markers"])
    assert "camera busiest room" not in evidence
    assert "historical only" in evidence

def test_historical_room_names_camera_unreachable_when_summary_says_so(tmp_path):
    now = _now()
    yesterday = now - datetime.timedelta(days=1)
    activity = _activity({"bedroom": [_bucket(yesterday, 0.2)]})
    activity["summary"] = {"unknown_intervals": {"bedroom": [
        {"from_utc": "20:50Z", "minutes": 551,
         "reason": "no usable frame for over 300 s: camera unreachable or blind, not dark",
         "to_utc": "06:02Z"}]}}
    site = _site(tmp_path, activity)
    state, svg = _render(site)
    act = state["room_activity"]["bedroom"]
    assert act["state"] == "historical"
    assert "camera unreachable or blind" in act["reason"]
    assert "camera unreachable or blind" in svg


def test_historical_room_without_unknown_intervals_keeps_age_reason(tmp_path):
    now = _now()
    yesterday = now - datetime.timedelta(days=1)
    site = _site(tmp_path, _activity({"bedroom": [_bucket(yesterday, 0.2)]}))
    state, svg = _render(site)
    act = state["room_activity"]["bedroom"]
    assert act["state"] == "historical"
    assert "camera unreachable or blind" not in act["reason"]
    assert "newest usable bucket" in act["reason"]
    assert "camera unreachable or blind" not in svg


def test_bare_hhmm_label_has_no_date_and_is_unknown(tmp_path):
    now = _now()
    site = _site(tmp_path, _activity({"living_room": [_bucket(now - datetime.timedelta(minutes=5), 0.3, with_iso=False)]}))
    state, svg = _render(site)
    act = state["room_activity"]["living_room"]
    assert act["state"] == "UNKNOWN"
    assert act["activity"] is None
    assert "without an absolute date" in act["reason"]
    assert "activity UNKNOWN" in svg
    assert "0.300" not in svg


def test_future_dated_bucket_is_rejected(tmp_path):
    now = _now()
    site = _site(tmp_path, _activity({"hallway": [_bucket(now + datetime.timedelta(hours=3), 0.5)]}))
    state, _ = _render(site)
    act = state["room_activity"]["hallway"]
    assert act["state"] == "UNKNOWN"
    assert "future-dated" in act["reason"]


def test_newest_usable_bucket_wins_and_unusable_is_skipped(tmp_path):
    now = _now()
    old = _bucket(now - datetime.timedelta(minutes=40), 0.1)
    new_unusable = _bucket(now - datetime.timedelta(minutes=5), None)
    fresh = _bucket(now - datetime.timedelta(minutes=10), 0.07)
    site = _site(tmp_path, _activity({"balcony": [old, new_unusable, fresh]}))
    state, _ = _render(site)
    assert state["room_activity"]["balcony"]["activity"] == 0.07


def test_newest_frame_utc_is_published_for_the_tile_view(tmp_path):
    now = _now()
    frame = _iso(now - datetime.timedelta(seconds=37))
    activity = _activity({"bedroom": [_bucket(now - datetime.timedelta(minutes=20), 0.09)]})
    activity["newest_frame_utc"] = frame
    site = _site(tmp_path, activity)
    state, _ = _render(site)
    assert state["room_activity"]["_newest_frame_utc"] == frame


def test_missing_newest_frame_fails_closed(tmp_path):
    now = _now()
    site = _site(tmp_path, _activity({"bedroom": [_bucket(now - datetime.timedelta(minutes=20), 0.09)]}))
    state, _ = _render(site)
    assert state["room_activity"]["_newest_frame_utc"] is None


def test_missing_activity_file_is_unknown(tmp_path):
    site = _site(tmp_path, activity=None)
    state, svg = _render(site)
    assert state["room_activity"]["_error"].startswith("activity.json missing")
    assert "camera room activity UNKNOWN" in json.dumps(state["markers"])
    assert "activity UNKNOWN" in svg


def _phone(t, age_s=6.0, status="ok"):
    return {"redmi10": {"age_s": age_s, "status": status}, "t": t}


def _phone_note(state):
    return state["markers"]["ilya"]["guess"]["evidence"]


def test_fresh_phone_row_is_home(tmp_path):
    now = _now().timestamp()
    site = _site(tmp_path, _activity({}), phones=[_phone(now - 10, age_s=6.0)])
    state, _ = _render(site)
    assert "phone home" in _phone_note(state)


def test_previous_day_phone_row_is_unknown_not_home(tmp_path):
    day = _now().timestamp() - 86400
    site = _site(tmp_path, _activity({}), phones=[_phone(day, age_s=6.0)])
    state, _ = _render(site)
    assert "phone home" not in _phone_note(state)
    assert "phone presence UNKNOWN" in _phone_note(state)
    assert "last phone row" in _phone_note(state)


def test_future_phone_row_is_unknown(tmp_path):
    later = _now().timestamp() + 3600
    site = _site(tmp_path, _activity({}), phones=[_phone(later, age_s=6.0)])
    state, _ = _render(site)
    assert "future-dated" in _phone_note(state)


def test_failed_phone_status_is_unknown(tmp_path):
    now = _now().timestamp()
    site = _site(tmp_path, _activity({}), phones=[_phone(now - 5, age_s=6.0, status="unavailable")])
    state, _ = _render(site)
    assert "phone home" not in _phone_note(state)
    assert "phone status unavailable" in _phone_note(state)


def test_row_without_collector_time_is_unknown(tmp_path):
    now = _now().timestamp()
    row = {"redmi10": {"age_s": 6.0, "status": "ok"}}
    site = _site(tmp_path, _activity({}), phones=[row])
    state, _ = _render(site)
    assert "no collector time" in _phone_note(state)


def test_saved_twin_shows_the_same_phone_history_as_the_live_pane(tmp_path):
    now = _now().timestamp()
    rows = [_phone(now - 300 + i * 100, age_s=4.0, status=s)
            for i, s in enumerate(["ok", "stale", "ok"])]
    site = _site(tmp_path, _activity({}), phones=rows)
    _render(site)
    twin = (Path(site) / "senses" / "map" / "apartment-map.txt").read_text()
    assert "phone history: 1 drop onsets in 7d, fresh in 66% of 3 samples" in twin
    assert "no usable collector rows" not in twin


def test_saved_twin_phone_history_unknown_when_collector_missing(tmp_path):
    site = _site(tmp_path, _activity({}))
    _render(site)
    twin = (Path(site) / "senses" / "map" / "apartment-map.txt").read_text()
    assert "phone history UNKNOWN" in twin


def test_saved_twin_carries_the_camera_room_bias(tmp_path):
    now = _now()
    bed, liv = [], []
    for i in range(30):
        when = now - datetime.timedelta(minutes=15 * (30 - i))
        bed.append(_bucket(when, 0.09))
        liv.append(_bucket(when, 0.03))
    site = _site(tmp_path, _activity({"bedroom": bed, "living room": liv}))
    _render(site)
    twin = (Path(site) / "senses" / "map" / "apartment-map.txt").read_text()
    assert "camera room bias: bedroom busier in 100% of 30 shared buckets" in twin


def test_saved_twin_camera_room_bias_unknown_without_activity(tmp_path):
    site = _site(tmp_path, _activity({}))
    _render(site)
    twin = (Path(site) / "senses" / "map" / "apartment-map.txt").read_text()
    assert "camera room bias UNKNOWN" in twin

def test_saved_twin_carries_night_prior_and_audio_lines(tmp_path):
    now = _now()
    site = _site(tmp_path, _activity({}))
    hh = Path(site) / "spatial" / "work" / "household"
    (hh / "daylight.json").write_text(json.dumps({"rooms": {"living room": {"hours": {
        "21": {"median_luma": 114.0, "samples": 477},
        "03": {"median_luma": 16.0, "samples": 523},
    }}}}))
    (hh / "activity.json").write_text(json.dumps({"rooms": {"living room": {"buckets": [
        {"utc": "21:00Z", "activity": 0.005},
        {"utc": "03:00Z", "activity": 0.002},
    ]}}}))
    (hh / "audio.jsonl").write_text(
        json.dumps({"room": "livingroom", "t": now.timestamp(), "status": "NO_HINT"}) + "\n")
    _render(site)
    twin = (Path(site) / "senses" / "map" / "apartment-map.txt").read_text()
    assert "night prior 21-05Z is unvalidated: at 21Z living-room luma 114" in twin
    assert "audio livingroom: speech " in twin and "not a person claim" in twin


def test_saved_twin_keeps_unknown_status_lines_when_sources_are_missing(tmp_path):
    site = _site(tmp_path, _activity({}))
    _render(site)
    twin = (Path(site) / "senses" / "map" / "apartment-map.txt").read_text()
    assert "night prior 21-05Z: timing UNKNOWN" in twin
    assert "audio livingroom UNKNOWN (no audio log)" in twin



def test_rssi_fresh_live_without_labels_is_unknown_not_a_tie(tmp_path):
    # fresh phone RSSI but no labelled moment: nothing to match, so no tie and no crash
    import json
    site = _site(tmp_path, _activity({}))
    mapdir = Path(site) / "senses" / "map"
    mapdir.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    (mapdir / "rssi-table.json").write_text(json.dumps(
        {"generated_at": stamp, "vantages": [{"vantage": "redmi10", "rssi": -60.0, "rssi_age_s": 1}]}))
    (mapdir / "label-rssi-vectors.json").write_text(json.dumps({"moments": []}))
    state, _ = _render(site)
    assert state["rssi"]["state"] == "UNKNOWN"
    assert "no labelled moments" in state["rssi"]["text"]
    assert "tie" not in state["rssi"]["text"]


def _rssi_inputs(site, moments, dbm=-44.0):
    mapdir = Path(site) / "senses" / "map"
    mapdir.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    (mapdir / "rssi-table.json").write_text(json.dumps(
        {"generated_at": stamp, "vantages": [{"vantage": "redmi10", "rssi": dbm, "rssi_age_s": 1}]}))
    (mapdir / "label-rssi-vectors.json").write_text(json.dumps({"moments": moments}))


def _moment(who, room, dbm):
    return {"who": who, "room": room, "label_utc": "2026-11-11T13:00Z",
            "phone_to_ap": {"rssi_median_dbm": dbm, "n": 120}}


def test_rssi_same_value_for_every_label_is_named_not_a_failed_test(tmp_path):
    # three labels in two rooms, all at one dBm: nearest-neighbour scoring cannot separate them
    site = _site(tmp_path, _activity({}))
    _rssi_inputs(site, [_moment("bruno", "bedroom", -60),
                        _moment("ilya", "bedroom", -60),
                        _moment("rozalia", "kitchen_stall", -60)])
    state, _ = _render(site)
    text = state["rssi"]["text"]
    assert state["rssi"]["state"] == "fresh"
    assert "all 3 labels -60 dBm, no room spread" in text
    assert "0/3 correct" in text
    assert "not a room claim" in text


def test_rssi_different_labels_do_not_claim_no_spread(tmp_path):
    site = _site(tmp_path, _activity({}))
    _rssi_inputs(site, [_moment("bruno", "bedroom", -60),
                        _moment("ilya", "bedroom", -50),
                        _moment("rozalia", "kitchen_stall", -70)])
    state, _ = _render(site)
    assert "no room spread" not in state["rssi"]["text"]


def test_rssi_one_label_does_not_claim_no_spread(tmp_path):
    site = _site(tmp_path, _activity({}))
    _rssi_inputs(site, [_moment("bruno", "bedroom", -60)])
    state, _ = _render(site)
    text = state["rssi"]["text"]
    assert "no room spread" not in text
    assert "nearest bedroom" in text


def test_rssi_line_without_vectors_is_unknown_not_a_room(tmp_path):
    # no label-rssi-vectors.json or rssi-table.json: the fingerprint cannot run, so the line is UNKNOWN
    site = _site(tmp_path, _activity({}))
    state, svg = _render(site)
    assert state["rssi"]["state"] == "UNKNOWN"
    assert "UNKNOWN" in state["rssi"]["text"]
    assert "not a room claim" not in state["rssi"]["text"]
    assert state["markers"]["ilya"]["room"] is None or state["markers"]["ilya"]["confidence"] == "UNKNOWN"


FLOORPLAN_FIXTURE = {
    "written_utc": "2026-11-12T19:50Z",
    "total_area_m2": {"value": 55, "provenance": "given"},
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
}


def _floorplan(site, doc=FLOORPLAN_FIXTURE):
    mapdir = Path(site) / "senses" / "map"
    mapdir.mkdir(parents=True, exist_ok=True)
    (mapdir / "floorplan.json").write_text(json.dumps(doc))


def test_plan_draws_floorplan_rooms_with_area_and_assumed_dashes(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    state, svg = _render(site)
    assert state["floorplan"]["state"] == "ok"
    assert "30 m²" in svg and "25 m²" in svg
    assert "6.0 × 5.0 m [assumed]" in svg
    assert 'stroke-dasharray="8 5"' in svg  # assumed geometry is dashed, not solid


def test_unknown_token_is_hollow_and_halo_shows_uncertainty(tmp_path, monkeypatch):
    # the UNKNOWN ghost is only drawn inside the assumed night prior (in_night); pin it so the
    # assertion does not depend on the wall-clock hour the suite runs at
    monkeypatch.setattr(mr, "in_night", lambda hour: True)
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    state, svg = _render(site)
    # no label ever recorded for these people: UNKNOWN, so the token must be hollow
    assert state["markers"]["ilya"]["confidence"] == "UNKNOWN"
    assert 'fill="none" stroke="#f2c14e"' in svg
    assert "UNKNOWN" in svg


def test_room_with_list_or_object_id_is_skipped_and_render_completes(tmp_path):
    # an unhashable room id used to raise TypeError in svg() and stop main() before map-state.json
    doc = json.loads(json.dumps(FLOORPLAN_FIXTURE))
    doc["rooms"] = list(doc["rooms"]) + [
        dict(doc["rooms"][0], id=["kitchen"]),
        dict(doc["rooms"][0], id={"name": "hall"}),
    ]
    site = _site(tmp_path, _activity({}))
    _floorplan(site, doc)
    state, svg = _render(site)
    assert state["floorplan"]["state"] == "ok"
    assert "30 m²" in svg  # the valid rooms still draw


def test_missing_floorplan_is_unknown_and_draws_no_rooms(tmp_path):
    site = _site(tmp_path, _activity({}))
    state, svg = _render(site)
    assert state["floorplan"]["state"] == "UNKNOWN"
    assert "NO FLOORPLAN" in svg
    assert "30 m²" not in svg


def test_footer_states_leave_one_out_and_camera_coverage(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    _, svg = _render(site)
    assert "Leave-one-out" in svg
    assert "Camera coverage: not drawn" in svg
    assert "Blind spot:" in svg


def test_footer_states_the_render_time_so_a_stale_map_is_visible(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    _, svg = _render(site)
    assert "Rendered 20" in svg
    assert "has not refreshed" in svg


def _history(site, text):
    path = site / "senses/map/rssi-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_history_non_object_lines_are_skipped_and_counted(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    good = json.dumps({"at": _iso(_now())})
    _history(site, "\n".join([good, "[]", "3", '"x"', '{"at": ', good]) + "\n")
    state, svg = _render(site)
    assert state["generated_utc"]
    assert "RSSI history: 2 rows" in svg
    assert "4 unreadable rows" in svg


def test_history_only_bad_lines_says_zero_readable_rows(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    _history(site, "[]\n3\n")
    _, svg = _render(site)
    assert "RSSI history: 0 rows, 2 unreadable rows" in svg


def test_empty_history_file_is_zero_rows(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    _history(site, "")
    _, svg = _render(site)
    assert "RSSI history: 0 rows" in svg
    assert "unreadable rows" not in svg


def test_history_row_without_time_has_unknown_age(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    _history(site, json.dumps({"rssi": -50}) + "\n")
    _, svg = _render(site)
    assert "RSSI history: 1 rows, newest UNKNOWN old" in svg


def test_fingerprint_that_is_a_list_is_unknown_and_render_completes(tmp_path):
    site = _site(tmp_path, _activity({}))
    _floorplan(site)
    fp = site / "senses/map/rssi-fingerprint.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("[1, 2, 3]")
    mr.configure(str(site))
    assert mr.fingerprint_footer() == "Leave-one-out: UNKNOWN (rssi-fingerprint.json is not an object)"
    _, svg = _render(site)
    assert "Leave-one-out: UNKNOWN" in svg


def test_night_prior_uses_learned_hour_only_when_adopted_and_sampled(tmp_path):
    site = Path(_site(tmp_path, _activity({})))
    mr.configure(str(site))
    assert mr.night_bedroom_prior(23) == (mr.NIGHT_BEDROOM, "hand-set prior %.1f" % mr.NIGHT_BEDROOM)
    priors = site / "senses/map/priors.json"
    priors.parent.mkdir(parents=True, exist_ok=True)
    cell = {"p_active": 0.9, "n": 5}
    doc = {"night_bedroom": {"adopted": False}, "rooms": {"bedroom": {"by_hour_utc": {"23": cell}}}}
    priors.write_text(json.dumps(doc))
    assert mr.night_bedroom_prior(23)[0] == mr.NIGHT_BEDROOM  # not adopted: hand-set kept
    doc["night_bedroom"]["adopted"] = True
    priors.write_text(json.dumps(doc))
    assert mr.night_bedroom_prior(23)[0] == 0.9
    cell["n"] = 2
    priors.write_text(json.dumps(doc))
    assert mr.night_bedroom_prior(23)[0] == mr.NIGHT_BEDROOM  # thin hour: hand-set kept
    priors.write_text("not json")
    assert mr.night_bedroom_prior(23)[0] == mr.NIGHT_BEDROOM  # unreadable: hand-set kept
    for non_object in ("[]", "null", "3", '{"night_bedroom": {"adopted": true}, "rooms": []}'):
        priors.write_text(non_object)
        assert mr.night_bedroom_prior(23)[0] == mr.NIGHT_BEDROOM  # valid JSON, not a usable object: hand-set kept
