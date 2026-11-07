"""Blind-camera classification and phone freshness for tools/household_collector.py."""
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("household_collector", ROOT / "tools/household_collector.py")
hc = importlib.util.module_from_spec(spec)
sys.modules["household_collector"] = hc
spec.loader.exec_module(hc)


def test_covered_lens_is_blind():
    # a lens covered by a door: every cell near zero, no structure
    grid = [1] * 48
    blind, reason = hc.classify_frame(grid, 1.0)
    assert blind and "flat" in reason


def test_dark_room_without_structure_is_blind():
    grid = [1, 5, 1, 5, 2, 4, 1, 5] * 6
    blind, reason = hc.classify_frame(grid, 2.9)
    assert blind and "near-black" in reason


def test_lit_room_with_structure_is_not_blind():
    grid = [20, 79, 94, 123, 143, 132, 184, 167, 20, 78, 90, 46, 35, 31, 157, 154, 15, 83,
            80, 35, 25, 27, 117, 84, 15, 49, 61, 38, 39, 38, 40, 49, 17, 39, 47, 29, 39, 30,
            30, 39, 16, 26, 47, 48, 30, 29, 23, 20]
    assert hc.classify_frame(grid, 59.5) == (False, "")


def test_dim_room_with_structure_is_not_blind():
    # night, lamps off: low luma but real structure must stay a valid reading
    grid = [5, 12, 2, 9, 14, 3, 7, 11] * 6
    assert hc.classify_frame(grid, 8.0)[0] is False


def test_stale_note3_is_unknown(tmp_path):
    site = tmp_path
    f = site / "spatial/work/household/note3_samples_run1.jsonl"
    f.parent.mkdir(parents=True)
    now = time.time()
    f.write_text(json.dumps({"wall_ts": now - 3600, "sensors": {}, "battery": {"level_pct": 50}}) + "\n")
    out = hc.note3_summary(str(site), now)
    assert out["status"] == "stale" and "sampler stopped" in out["reason"]


def test_missing_note3_is_unavailable(tmp_path):
    assert hc.note3_summary(str(tmp_path), time.time())["status"] == "unavailable"


def test_fresh_adb_failure_row_is_not_ok(tmp_path):
    # the sampler writes an explicit unknown row when adb fails; its wall_ts is fresh,
    # so age alone must not report the phone as GREEN
    f = tmp_path / "spatial/work/household/note3_samples_run1.jsonl"
    f.parent.mkdir(parents=True)
    now = time.time()
    f.write_text(json.dumps({"wall_ts": now - 2, "status": "unknown", "reason": "adb: rc=1"}) + "\n")
    out = hc.note3_summary(str(tmp_path), now)
    assert out["status"] == "unknown" and "adb: rc=1" in out["reason"]


def test_fresh_note3_with_inactive_light_is_not_ok(tmp_path):
    f = tmp_path / "spatial/work/household/note3_samples_run1.jsonl"
    f.parent.mkdir(parents=True)
    now = time.time()
    row = {"wall_ts": now - 5, "battery": {"level_pct": 48},
           "sensors": {"light": {"status": "unknown", "reason": "not active (no listener)"},
                       "accelerometer": {"status": "ok", "values": [-0.1, 0.1, 10.2]}}}
    f.write_text(json.dumps(row) + "\n")
    out = hc.note3_summary(str(tmp_path), now)
    assert out["status"] == "unknown" and "light" in out["reason"]


def test_fresh_note3_reports_raw_light(tmp_path):
    f = tmp_path / "spatial/work/household/note3_samples_run1.jsonl"
    f.parent.mkdir(parents=True)
    now = time.time()
    row = {"wall_ts": now - 5, "battery": {"level_pct": 48},
           "sensors": {"light": {"status": "ok", "values": [0.0, 2300.0, 0.0]},
                       "accelerometer": {"status": "ok", "values": [-0.1, 0.1, 10.2]}}}
    f.write_text(json.dumps(row) + "\n")
    out = hc.note3_summary(str(tmp_path), now)
    assert out["status"] == "ok" and out["light_raw"] == [0.0, 2300.0, 0.0]
    assert "not verified" in out["light_note"]


def _redmi_site(tmp_path, row):
    f = tmp_path / "discovery/latest.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"observations": [dict(id="sense.body.redmi10.stream", **row)]}))
    return str(tmp_path)


def test_stale_redmi_stream_row_is_not_ok(tmp_path):
    # the snapshot file is fresh (scan just ran) but the phone's newest row is 1032 s old,
    # with no values: this must not read as a GREEN Redmi10
    now = time.time()
    site = _redmi_site(tmp_path, {"last_row_epoch": now - 1032, "values": None})
    out = hc.redmi_summary(site, now)
    assert out["status"] == "stale"


def test_fresh_redmi_stream_row_with_values_is_ok(tmp_path):
    now = time.time()
    site = _redmi_site(tmp_path, {"last_row_epoch": now - 3,
                                  "values": {"lux": {"value": 4.0}, "battery": {"level": 0.84}}})
    out = hc.redmi_summary(site, now)
    assert out["status"] == "ok" and out["lux"] == 4.0 and out["battery_raw"] == 0.84


def test_fresh_redmi_heartbeat_without_values_is_unknown(tmp_path):
    now = time.time()
    site = _redmi_site(tmp_path, {"last_row_epoch": now - 2, "values": None})
    out = hc.redmi_summary(site, now)
    assert out["status"] == "unknown" and "no sensor values" in out["reason"]


class _FakeCamsig:
    def __init__(self, grid, mean_luma):
        self.row = {"status": "ok", "grid": grid, "mean_luma": mean_luma, "captured_at_epoch_s": 1.0}

    def run(self, source, target, device, extra):
        return dict(self.row)


def _camera_exit(monkeypatch, tmp_path, grid, mean_luma):
    (tmp_path / "spatial/work/household").mkdir(parents=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text(
        json.dumps([{"name": "living room", "source": "webcam"}]))
    monkeypatch.setattr(hc, "load_camsig", lambda: _FakeCamsig(grid, mean_luma))
    return hc.main(["camera", "--room", "living room", "--source", "webcam", "--site", str(tmp_path)])


def test_blind_camera_is_recorded_red_not_a_crash(tmp_path, monkeypatch, capsys):
    # A covered or dark lens is an observed state: the row is written as blind and the
    # producer must see code 2 (RED observation), not 1 (cannot tell, a failed unit).
    assert _camera_exit(monkeypatch, tmp_path, [10] * 48, 1.0) == 2
    row = json.loads((tmp_path / "spatial/work/household/signatures.jsonl").read_text().splitlines()[-1])
    assert row["status"] == "blind"


def test_lit_camera_is_green(tmp_path, monkeypatch):
    grid = [10, 200] * 24
    assert _camera_exit(monkeypatch, tmp_path, grid, 120.0) == 0


def test_unavailable_room_is_cannot_tell(tmp_path, monkeypatch):
    (tmp_path / "spatial/work/household").mkdir(parents=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text("[]")
    assert hc.main(["camera", "--room", "living room", "--source", "webcam", "--site", str(tmp_path)]) == 1
