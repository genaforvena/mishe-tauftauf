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


def _redmi_log(tmp_path, rows):
    # rows: (epoch, text) pairs in the stream format "<seq> <epoch> <kind> <fields>"
    f = tmp_path / "redmi10.observations.log"
    f.write_text("".join(f"{i} {int(e)} {text}\n" for i, (e, text) in enumerate(rows, 1)))
    return str(f)


def test_stale_redmi_stream_row_is_not_ok(tmp_path):
    # the newest phone row is 1032 s old: the live log must not read as a GREEN Redmi10
    now = time.time()
    log = _redmi_log(tmp_path, [(now - 1032, "light lux=4.0 accuracy=3")])
    out = hc.redmi_summary(str(tmp_path), now, log_path=log)
    assert out["status"] == "stale"


def test_fresh_redmi_stream_row_with_values_is_ok(tmp_path):
    now = time.time()
    log = _redmi_log(tmp_path, [(now - 4, "battery level=0.84 charging=false"),
                                (now - 3, "light lux=4.0 accuracy=3")])
    out = hc.redmi_summary(str(tmp_path), now, log_path=log)
    assert out["status"] == "ok" and out["lux"] == 4.0 and out["battery_raw"] == 0.84


def test_fresh_redmi_heartbeat_without_values_is_unknown(tmp_path):
    now = time.time()
    log = _redmi_log(tmp_path, [(now - 2, "ble_status source=androidbody_bluetooth_api api=33")])
    out = hc.redmi_summary(str(tmp_path), now, log_path=log)
    assert out["status"] == "unknown" and "no current" in out["reason"]


def test_redmi_snapshot_lag_no_longer_hides_fresh_samples(tmp_path):
    # a discovery snapshot written 5 min ago must not matter: the live row is 3 s old
    now = time.time()
    (tmp_path / "discovery").mkdir()
    (tmp_path / "discovery/latest.json").write_text(json.dumps({"observations": [
        {"id": "sense.body.redmi10.stream", "last_row_epoch": now - 300, "values": None}]}))
    log = _redmi_log(tmp_path, [(now - 3, "light lux=7.0 accuracy=3")])
    out = hc.redmi_summary(str(tmp_path), now, log_path=log)
    assert out["status"] == "ok" and out["lux"] == 7.0


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


class _FakeMacCamsig:
    def __init__(self, row):
        self.row = row

    def run(self, source, target, device, extra):
        return {"captured_at_epoch_s": 1.0, "source": source, **self.row}


def _mac_exit(monkeypatch, tmp_path, row):
    (tmp_path / "spatial/work/household").mkdir(parents=True, exist_ok=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text(
        json.dumps([{"name": "bedroom", "source": "mac", "target": "user@host"}]))
    monkeypatch.setattr(hc, "load_camsig", lambda: _FakeMacCamsig(row))
    return hc.main(["camera", "--room", "bedroom", "--source", "mac", "--site", str(tmp_path)])


UNREACHABLE = {"status": "unavailable", "reason_code": "mac_unreachable",
               "reason": "CalledProcessError: Command '['scp', ...]' returned non-zero exit status 255."}


def test_unreachable_mac_is_unknown_not_a_crash(tmp_path, monkeypatch):
    # The reason_code the camera emits for a failed ssh/scp is missing input: exit 4 (declared unknown code).
    assert _mac_exit(monkeypatch, tmp_path, UNREACHABLE) == 4


def test_mac_timeout_with_the_code_is_unknown(tmp_path, monkeypatch):
    row = {**UNREACHABLE, "reason": "TimeoutExpired: ssh timed out after 180 seconds"}
    assert _mac_exit(monkeypatch, tmp_path, row) == 4


def test_mac_failure_without_the_code_stays_a_crash(tmp_path, monkeypatch):
    # A message that merely looks like an scp failure, with no reason_code, is not trusted: exit 1.
    row = {"status": "unavailable", "reason": UNREACHABLE["reason"]}
    assert _mac_exit(monkeypatch, tmp_path, row) == 1


def test_missing_ffmpeg_on_mac_stays_a_crash(tmp_path, monkeypatch):
    assert _mac_exit(monkeypatch, tmp_path, {"status": "unavailable", "reason": "ffmpeg missing"}) == 1


def test_mac_config_error_stays_a_crash(tmp_path, monkeypatch):
    assert _mac_exit(monkeypatch, tmp_path, {"status": "unavailable", "reason": "mac needs USER@HOST"}) == 1


def test_captured_unreachable_mac_record_exits_unknown(tmp_path, monkeypatch):
    # Real path: camera_signature.run with scp failing as in the site's captured record
    # (signatures.jsonl, scp rc 255 to the bedroom Mac). Nothing faked above the subprocess layer.
    import shutil
    import subprocess as sp
    camsig = hc.load_camsig()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def scp_refused(args, check=False, **kwargs):
        if check:  # the first scp is check=True; ssh's rm cleanup is not and returns quietly
            raise sp.CalledProcessError(255, args)
        return sp.CompletedProcess(args, 255, "", "")

    monkeypatch.setattr(camsig.subprocess, "run", scp_refused)
    monkeypatch.setattr(hc, "load_camsig", lambda: camsig)
    (tmp_path / "spatial/work/household").mkdir(parents=True, exist_ok=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text(
        json.dumps([{"name": "bedroom", "source": "mac", "target": "ilya@192.168.8.214"}]))
    assert hc.main(["camera", "--room", "bedroom", "--source", "mac", "--site", str(tmp_path)]) == 4
    row = json.loads((tmp_path / "spatial/work/household/signatures.jsonl").read_text().splitlines()[-1])
    assert row["reason_code"] == "mac_unreachable" and "grid" not in row


def test_webcam_held_on_every_attempt_exits_unknown_not_a_crash(tmp_path, monkeypatch):
    # Real path: camera_signature.run with fswebcam reporting the V4L2 input busy on every attempt,
    # as in the 2026-11-09 00:14 livingroom failure. Nothing faked above the subprocess layer.
    import shutil
    import subprocess as sp
    camsig = hc.load_camsig()
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(camsig.time, "sleep", lambda s: None)

    def held(args, **kwargs):
        return sp.CompletedProcess(args, 0, stdout="", stderr="VIDIOC_S_INPUT: Device or resource busy")

    monkeypatch.setattr(camsig.subprocess, "run", held)
    monkeypatch.setattr(hc, "load_camsig", lambda: camsig)
    (tmp_path / "spatial/work/household").mkdir(parents=True, exist_ok=True)
    (tmp_path / "spatial/work/household/rooms.json").write_text(
        json.dumps([{"name": "living room", "source": "webcam"}]))
    assert hc.main(["camera", "--room", "living room", "--source", "webcam", "--site", str(tmp_path)]) == 4
    row = json.loads((tmp_path / "spatial/work/household/signatures.jsonl").read_text().splitlines()[-1])
    assert row["reason_code"] == "device_busy" and row["status"] == "unavailable" and "grid" not in row
