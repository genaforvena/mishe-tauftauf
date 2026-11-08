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


def _grid(mean, sd):
    # a 48-cell grid with exactly this mean and population stdev
    return [round(mean - sd, 4), round(mean + sd, 4)] * 24


def _run(t0, n, mean, sd, step=50.0):
    """n consecutive rows of one room, as the collector would store them (oldest first)."""
    rows = []
    for i in range(n):
        g = _grid(mean, sd)
        rows.append({"room": "living room", "status": "ok", "captured_at_epoch_s": t0 + i * step,
                     "mean_luma": mean, "grid": g, "dark_flat": hc.dark_flat(g, mean)})
    return rows


def test_covered_lens_single_frame_is_not_yet_blind():
    # one near-black flat frame is only a candidate; blindness needs the run
    grid = [1] * 48
    assert hc.decide_blind([], 1000.0, grid, 1.0) == (False, "")
    assert hc.dark_flat(grid, 1.0)


def test_covered_lens_persisting_is_blind():
    # a lens covered by a door: near-black, no structure, for well over five minutes
    hist = _run(1000.0, 6, 1.0, 0.0)  # 6 prior samples, 50 s apart: 250 s of history
    blind, reason = hc.decide_blind(hist, 1000.0 + 6 * 50.0, [1] * 48, 1.0)
    assert blind and "near-black and flat" in reason


def test_dusk_living_room_is_never_blind():
    # the 14:24-14:33Z rows of the defect report: luma 15-16, stdev 0.4-1.5 flapping frame to frame
    sds = [0.47, 0.99, 0.82, 0.63, 0.52, 1.44, 0.62, 0.76, 0.56, 1.08, 0.87, 0.76, 0.56]
    hist, t = [], 2000.0
    for i in range(60):
        sd = sds[i % len(sds)]
        g = _grid(15.4 + 0.1 * (i % 5), sd)
        blind, _ = hc.decide_blind(hist, t, g, 15.4 + 0.1 * (i % 5))
        assert not blind, f"dusk frame {i} went blind"
        hist.append({"room": "living room", "captured_at_epoch_s": t, "mean_luma": 15.4,
                     "dark_flat": hc.dark_flat(g, 15.4 + 0.1 * (i % 5))})
        t += 50.0


def test_dim_room_with_structure_is_not_dark_flat():
    grid = [5, 12, 2, 9, 14, 3, 7, 11] * 6
    assert hc.dark_flat(grid, 8.0) is False
    assert hc.decide_blind(_run(1000.0, 8, 8.0, 4.5), 1400.0, grid, 8.0) == (False, "")


def test_gap_in_observation_restarts_the_run():
    # six dark-flat frames, then a two-hour gap: the new frame is not yet persistent
    hist = _run(1000.0, 6, 1.0, 0.0)
    blind, _ = hc.decide_blind(hist, 1000.0 + 6 * 50.0 + 7200.0, [1] * 48, 1.0)
    assert blind is False


def test_rows_without_dark_flat_break_the_run():
    # rows written before this rule carry no dark_flat field: they cannot prove persistence
    hist = [dict(r, dark_flat=None) for r in _run(1000.0, 6, 1.0, 0.0)]
    assert hc.decide_blind(hist, 1300.0, [1] * 48, 1.0)[0] is False


def test_lit_room_with_structure_is_not_blind():
    grid = [20, 79, 94, 123, 143, 132, 184, 167, 20, 78, 90, 46, 35, 31, 157, 154, 15, 83,
            80, 35, 25, 27, 117, 84, 15, 49, 61, 38, 39, 38, 40, 49, 17, 39, 47, 29, 39, 30,
            30, 39, 16, 26, 47, 48, 30, 29, 23, 20]
    assert hc.decide_blind([], 0.0, grid, 59.5) == (False, "")


def test_dim_room_with_structure_is_not_blind():
    # night, lamps off: low luma but real structure must stay a valid reading
    grid = [5, 12, 2, 9, 14, 3, 7, 11] * 6
    assert hc.decide_blind([], 0.0, grid, 8.0)[0] is False


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
    def __init__(self, grid, mean_luma, t=1.0):
        self.row = {"status": "ok", "grid": grid, "mean_luma": mean_luma, "captured_at_epoch_s": t}

    def run(self, source, target, device, extra):
        return dict(self.row)


def _camera_exit(monkeypatch, tmp_path, grid, mean_luma, t=1.0):
    (tmp_path / "spatial/work/household").mkdir(parents=True, exist_ok=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text(
        json.dumps([{"name": "living room", "source": "webcam"}]))
    monkeypatch.setattr(hc, "load_camsig", lambda: _FakeCamsig(grid, mean_luma, t))
    return hc.main(["camera", "--room", "living room", "--source", "webcam", "--site", str(tmp_path)])


def test_blind_camera_is_recorded_red_not_a_crash(tmp_path, monkeypatch):
    # A covered lens is an observed state: once persistent, the row is written as blind
    # and the producer must see code 2 (RED observation), not 1 (cannot tell).
    sig = tmp_path / "spatial/work/household/signatures.jsonl"
    sig.parent.mkdir(parents=True)
    sig.write_text("".join(json.dumps(r) + "\n" for r in _run(1000.0, 6, 1.0, 0.0)))
    assert _camera_exit(monkeypatch, tmp_path, [1] * 48, 1.0, t=1300.0) == 2
    row = json.loads(sig.read_text().splitlines()[-1])
    assert row["status"] == "blind" and row["dark_flat"] is True


def test_first_dark_frame_exits_green_not_red(tmp_path, monkeypatch):
    assert _camera_exit(monkeypatch, tmp_path, [1] * 48, 1.0, t=1000.0) == 0


def test_lit_camera_is_green(tmp_path, monkeypatch):
    grid = [10, 200] * 24
    assert _camera_exit(monkeypatch, tmp_path, grid, 120.0) == 0


def test_unavailable_room_is_cannot_tell(tmp_path, monkeypatch):
    (tmp_path / "spatial/work/household").mkdir(parents=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text("[]")
    assert hc.main(["camera", "--room", "living room", "--source", "webcam", "--site", str(tmp_path)]) == 1


class _UnreachableMacCamsig:
    def __init__(self, reason):
        self.reason = reason

    def run(self, source, target, device, extra):
        return {"captured_at_epoch_s": 1.0, "source": source, "status": "unavailable", "reason": self.reason}


def _mac_exit(monkeypatch, tmp_path, reason):
    (tmp_path / "spatial/work/household").mkdir(parents=True, exist_ok=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text(
        json.dumps([{"name": "bedroom", "source": "mac", "target": "user@host"}]))
    monkeypatch.setattr(hc, "load_camsig", lambda: _UnreachableMacCamsig(reason))
    return hc.main(["camera", "--room", "bedroom", "--source", "mac", "--site", str(tmp_path)])


def test_unreachable_mac_is_unknown_not_a_crash(tmp_path, monkeypatch):
    # ssh/scp failing to reach the Mac is missing input: exit 4 (declared unknown code), not 1.
    reason = "CalledProcessError: Command '['scp', ...]' returned non-zero exit status 255."
    assert _mac_exit(monkeypatch, tmp_path, reason) == 4


def test_mac_capture_timeout_is_unknown(tmp_path, monkeypatch):
    assert _mac_exit(monkeypatch, tmp_path, "TimeoutExpired: ssh timed out after 180 seconds") == 4


def test_missing_ffmpeg_on_mac_stays_a_crash(tmp_path, monkeypatch):
    assert _mac_exit(monkeypatch, tmp_path, "ffmpeg missing") == 1


def test_mac_config_error_stays_a_crash(tmp_path, monkeypatch):
    assert _mac_exit(monkeypatch, tmp_path, "mac needs USER@HOST") == 1
