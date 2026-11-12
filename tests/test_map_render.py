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


def test_rssi_line_without_vectors_is_unknown_not_a_room(tmp_path):
    # no label-rssi-vectors.json or rssi-table.json: the fingerprint cannot run, so the line is UNKNOWN
    site = _site(tmp_path, _activity({}))
    state, svg = _render(site)
    assert state["rssi"]["state"] == "UNKNOWN"
    assert "UNKNOWN" in state["rssi"]["text"]
    assert "not a room claim" not in state["rssi"]["text"]
    assert state["markers"]["ilya"]["room"] is None or state["markers"]["ilya"]["confidence"] == "UNKNOWN"
