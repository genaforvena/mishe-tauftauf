"""The household window reads change from the log; pairs must be adjacent frames."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("household_view", ROOT / "tools/household_view.py")
hv = importlib.util.module_from_spec(spec)
sys.modules["household_view"] = hv
spec.loader.exec_module(hv)


def test_few_rows_pair_adjacent_frames(tmp_path, monkeypatch):
    log = tmp_path / "signatures.jsonl"
    rows = [{"room": "living room", "status": "ok", "grid": [g] * 48, "captured_at_epoch_s": i,
             "mean_luma": g} for i, g in enumerate([10, 10, 50, 50, 90])]
    log.write_text("\n".join(__import__("json").dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(hv, "LOG", log)
    cam = {"reading": None, "hist": [], "dist": []}
    hv.refresh("living room", cam)
    # adjacent differences: 0, 40, 0, 40 (not all zeros as with self-pairing)
    assert cam["dist"] == [0.0, 40.0, 0.0, 40.0]


def test_single_row_has_no_change(tmp_path, monkeypatch):
    log = tmp_path / "signatures.jsonl"
    log.write_text('{"room": "bedroom", "status": "ok", "grid": [5,5,5,5], "captured_at_epoch_s": 1}\n')
    monkeypatch.setattr(hv, "LOG", log)
    cam = {"reading": None, "hist": [], "dist": []}
    hv.refresh("bedroom", cam)
    assert cam["dist"] == []


def test_daylight_and_rhythm_are_unknown_without_their_files(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "DAYLIGHT_FILE", tmp_path / "daylight.json")
    monkeypatch.setattr(hv, "ACTIVITY_FILE", tmp_path / "activity.json")
    assert hv.daylight_lines(1e9) == ["  DAYLIGHT: UNKNOWN (no daylight curve yet)"]
    assert hv.rhythm_lines(1e9) == ["  RHYTHM: UNKNOWN (no activity reading yet)"]


def test_daylight_row_shows_each_room_with_missing_hours_as_dots(tmp_path, monkeypatch):
    import json
    daylight = tmp_path / "daylight.json"
    hours = {f"{h:02d}": {"samples": 0, "median_luma": None, "lamp_steps": 0} for h in range(24)}
    hours["05"] = {"samples": 9, "median_luma": 60.4, "lamp_steps": 0}
    hours["07"] = {"samples": 9, "median_luma": 68.6, "lamp_steps": 0}
    daylight.write_text(json.dumps({"rooms": {"living room": {"hours": hours}}}))
    monkeypatch.setattr(hv, "DAYLIGHT_FILE", daylight)
    lines = hv.daylight_lines(daylight.stat().st_mtime + 60)
    row = next(line for line in lines if "DAYLIGHT living room" in line)
    assert row.count("·") == 22 and "latest 07:00Z luma 68.6" in row
    assert not any("STALE" in line for line in lines)


def test_old_daylight_file_says_stale_not_current(tmp_path, monkeypatch):
    import json
    daylight = tmp_path / "daylight.json"
    daylight.write_text(json.dumps({"rooms": {"living room": {"hours": {}}}}))
    monkeypatch.setattr(hv, "DAYLIGHT_FILE", daylight)
    lines = hv.daylight_lines(daylight.stat().st_mtime + hv.FEED_MAX_AGE_S + 120)
    assert any("STALE" in line for line in lines)


def test_rhythm_line_comes_from_the_activity_summary(tmp_path, monkeypatch):
    import json
    activity = tmp_path / "activity.json"
    activity.write_text(json.dumps({"summary": {"lights_out_utc": "23:58Z",
                                               "first_activity_after_lights_out_utc": None,
                                               "longest_quiet_block_living_room": None},
                                    "sound_by_hour": {"livingroom": {"07": {"samples": 3, "median_rms": 410.0}}}}))
    monkeypatch.setattr(hv, "ACTIVITY_FILE", activity)
    lines = hv.rhythm_lines(activity.stat().st_mtime + 10)
    assert "RHYTHM (provisional): lights out 23:58Z; first living-room activity after that UNKNOWN" in lines[0]
    assert lines[1].startswith("  SOUND by UTC hour, living room")

def test_once_writes_the_view_without_a_none_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hv, "LOG", tmp_path / "signatures.jsonl")
    monkeypatch.setattr(hv, "CAMERAS", {})
    monkeypatch.setattr(sys, "argv", ["household_view.py", "--once"])
    hv.main()
    out = capsys.readouterr().out
    assert "MISHE SENSES" in out
    assert "None" not in out.splitlines()

def test_phones_render_note3_sampler_and_redmi10_history(tmp_path, monkeypatch):
    import json
    now = 1794342600.0
    household = tmp_path / "spatial" / "work" / "household"
    household.mkdir(parents=True)
    monkeypatch.setattr(hv, "SITE", tmp_path)
    samples = household / "note3_samples_run1.jsonl"
    fresh = {"battery": {"level_pct": 100, "usb_powered": True, "ac_powered": False}, "wall_ts": now - 44}
    stale = {"battery": {"level_pct": 42, "usb_powered": False, "ac_powered": False}, "wall_ts": now - 900}
    samples.write_text(json.dumps(stale) + "\n" + json.dumps(fresh) + "\n")

    def fake_time():
        return now
    # Monkeypatched, not assigned: `hv.time` is the global time module, so a raw
    # assignment leaks the frozen clock to every later test in the session.
    monkeypatch.setattr(hv.time, "time", fake_time)

    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({"observations": [{"id": "sense.body.redmi10.stream",
        "history": {"battery": {"level": 1.0, "charging": False, "epoch": now - 23000}}}]}) + "\n")
    monkeypatch.setattr(hv, "LATEST", latest)
    bat, age = hv.note3_battery()
    assert bat["level_pct"] == 100 and 40 <= age <= 50
    hist = hv.redmi10_history()
    assert hist is not None and hist[0]["level"] == 1.0 and now - hist[1] == 23000

    # stale sampler row reads UNKNOWN, fresh reads battery
    old = {"battery": fresh["battery"], "wall_ts": now - 400}
    samples.write_text(json.dumps(old) + "\n")
    bat, age = hv.note3_battery()
    assert age > 300

    # no stream history row -> None
    latest.write_text(json.dumps({"observations": []}) + "\n")
    assert hv.redmi10_history() is None
