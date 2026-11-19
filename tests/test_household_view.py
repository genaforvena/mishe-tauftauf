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

def test_phones_render_note3_sampler_and_redmi10_battery(tmp_path, monkeypatch):
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
    monkeypatch.setattr(hv, "LATEST", latest)
    phones_log = tmp_path / "phones.jsonl"
    phones_log.write_text("\n".join(json.dumps(
        {"t": now - 60, "redmi10": {"status": "ok"}}) for _ in range(3)) + "\n")
    monkeypatch.setattr(hv, "PHONES_FILE", phones_log)

    def stream_row(**body):
        latest.write_text(json.dumps({"observations": [
            {"id": "sense.body.redmi10.stream", **body}]}) + "\n")

    # a streaming phone carries a live reading in `values`; fraction -> percent
    stream_row(values={"battery": {"level": 0.92, "charging": True, "epoch": now - 5}})
    bat, age = hv.note3_battery()
    assert bat["level_pct"] == 100 and 40 <= age <= 50
    cur = hv.redmi10_battery()
    assert cur is not None and cur[0]["level"] == 0.92 and cur[2] is True
    assert now - cur[1] == 5

    # a stale stream keeps only labelled history, never current
    stream_row(history={"battery": {"level": 0.4, "charging": False, "epoch": now - 23000}})
    hist = hv.redmi10_battery()
    assert hist is not None and hist[0]["level"] == 0.4 and hist[2] is False
    assert now - hist[1] == 23000

    # stale sampler row reads UNKNOWN, fresh reads battery
    old = {"battery": fresh["battery"], "wall_ts": now - 400}
    samples.write_text(json.dumps(old) + "\n")
    bat, age = hv.note3_battery()
    assert age > 300

    # no stream row -> None
    latest.write_text(json.dumps({"observations": []}) + "\n")
    assert hv.redmi10_battery() is None

    # the drawn PHONES block: a streaming phone shows a live percent reading
    stream_row(values={"battery": {"level": 0.92, "charging": True, "epoch": now - 5}})
    lines = hv.draw().splitlines()
    phones = [l for l in lines if l.startswith("  redmi10")]
    assert "  redmi10  battery=92%  charging=yes  (row 5s old)" in phones
    assert "  redmi10  history: 0 drop onsets in 7d, fresh in 100% of 3 samples" in phones


def test_redmi10_stability_counts_onsets_and_fails_closed(tmp_path, monkeypatch):
    import json
    now = 1795126500.0
    path = tmp_path / "phones.jsonl"
    rows = [
        {"t": now - 900, "redmi10": {"status": "ok"}},       # outside window
        {"t": now - 500, "redmi10": {"status": "ok"}},
        {"t": now - 400, "redmi10": {"status": "unknown"}},  # onset
        {"t": now - 300, "redmi10": {"status": "stale"}},
        {"t": now - 200, "redmi10": {"status": "ok"}},       # recovery
        {"t": now - 100, "redmi10": {"status": "ok"}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(hv, "PHONES_FILE", path)
    # 1 ok->unknown onset, 3/5 fresh rows inside the 600 s window
    assert hv.redmi10_stability(now, window_s=600) == (1, 3, 5)
    assert hv.redmi10_stability(now, window_s=50) is None      # empty window
    path.write_text("{broken json\n")
    assert hv.redmi10_stability(now, window_s=600) is None     # malformed
    monkeypatch.setattr(hv, "PHONES_FILE", tmp_path / "absent.jsonl")
    assert hv.redmi10_stability(now, window_s=600) is None     # missing


def test_signal_stability_counts_flips_and_fails_closed(tmp_path, monkeypatch):
    import json
    now = 1795127354.0
    path = tmp_path / "edges.jsonl"
    rows = [
        {"at": now - 900, "name": "budget-claude", "value": "RED"},      # outside window
        {"at": now - 500, "name": "budget-claude", "value": "GREEN"},
        {"at": now - 400, "name": "budget-claude", "value": None},       # UNKNOWN is a flip
        {"at": now - 300, "name": "budget-claude", "value": None},       # no flip
        {"at": now - 200, "name": "budget-claude", "value": "RED"},      # flip
        {"at": now - 100, "name": "budget-claude", "value": "GREEN"},    # flip
        {"at": now - 150, "name": "another-signal", "value": "RED"},     # ignored
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(hv, "SIGNALS_EDGES", path)
    assert hv.signal_stability("budget-claude", now, window_s=600) == (3, 2, 5)
    assert hv.signal_stability("budget-claude", now, window_s=50) is None     # empty window
    path.write_text("{broken json\n")
    assert hv.signal_stability("budget-claude", now, window_s=600) is None    # malformed
    monkeypatch.setattr(hv, "SIGNALS_EDGES", tmp_path / "absent.jsonl")
    assert hv.signal_stability("budget-claude", now, window_s=600) is None    # missing


def test_redmi10_daily_shape_shows_hourly_rhythm(tmp_path, monkeypatch):
    import calendar, json
    base = calendar.timegm((2026, 11, 19, 0, 0, 0, 0, 0, 0))  # 00Z
    rows = []
    # hour 2 all stale (quiet), hour 14 all ok (busy), hour 5 half fresh
    for hour, statuses in ((2, ["stale", "stale"]), (14, ["ok", "ok"]), (5, ["ok", "stale"])):
        for i, st in enumerate(statuses):
            rows.append({"t": base + hour * 3600 + i * 60, "redmi10": {"status": st}})
    path = tmp_path / "phones.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(hv, "PHONES_FILE", path)

    profile = hv.redmi10_hourly()
    assert profile[2] == 0.0
    assert profile[14] == 1.0
    assert profile[5] == 0.5
    assert profile[3] is None            # no rows that hour -> dot, never a guess

    lines = hv.redmi10_daily_lines(base + 2 * 3600)
    assert "quietest 02Z" in lines[1] and "busiest 14Z" in lines[1]
    assert "now 02Z" in lines[1]
    assert "▁" in lines[0] and "█" in lines[0]

    # one read per draw: passing rows avoids a second file read, keeps the 7d stats
    assert hv.redmi10_stability(base + 14 * 3600 + 120, window_s=600, rows=rows) == (0, 2, 2)


def test_redmi10_daily_shape_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "PHONES_FILE", tmp_path / "absent.jsonl")
    assert hv.redmi10_hourly() is None
    assert hv.redmi10_daily_lines(0)[0].endswith("UNKNOWN (no collector rows)")
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{broken json\n")
    monkeypatch.setattr(hv, "PHONES_FILE", bad)
    assert hv.redmi10_hourly() is None
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n")
    monkeypatch.setattr(hv, "PHONES_FILE", empty)
    assert hv.redmi10_hourly() is None
