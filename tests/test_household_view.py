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


def test_rhythm_line_shows_the_night_date_from_the_summary(tmp_path, monkeypatch):
    import datetime, json, time
    activity = tmp_path / "activity.json"
    now = time.time()
    lo_iso = datetime.datetime.fromtimestamp(now - 10 * 86400, datetime.timezone.utc).isoformat(timespec="seconds")
    activity.write_text(json.dumps({"summary": {"lights_out_utc": "04:11Z", "lights_out_iso": lo_iso,
                                                "first_activity_after_lights_out_utc": None,
                                                "longest_quiet_block_living_room": None}}))
    monkeypatch.setattr(hv, "ACTIVITY_FILE", activity)
    lines = hv.rhythm_lines(now)
    assert "lights out 04:11Z on " + lo_iso[:10] + " (10d ago, not last night)" in lines[0]

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
    # a lagging discovery snapshot must not be drawn as phone silence: the row was
    # 5s old when read 10 min ago, even though the battery epoch now looks 605s old
    import os
    stream_row(row_age_s=5,
               values={"battery": {"level": 0.92, "charging": True, "epoch": now - 605}})
    os.utime(latest, (now - 600, now - 600))
    assert hv.redmi10_reading_freshness(now) == (5.0, 600.0)
    lines = hv.draw().splitlines()
    phones = [l for l in lines if l.startswith("  redmi10")]
    assert any("phone row 5s old at reading" in l and "reading 10 min old" in l for l in phones)


def test_redmi10_reading_freshness_fails_closed_without_a_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "LATEST", tmp_path / "absent.json")
    assert hv.redmi10_reading_freshness(1794342600.0) is None


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
    path.write_text("5\n[1]\n" + "\n".join(json.dumps(r) for r in rows) + "\n")
    assert hv.redmi10_stability(now, window_s=600) == (1, 3, 5)  # non-object rows are skipped
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
    path.write_text("5\n" + json.dumps(rows[1]) + "\n")
    assert hv.signal_stability("budget-claude", now, window_s=600) is None    # non-object line: malformed history
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


def test_budget_forecast_line_names_the_margin(tmp_path, monkeypatch):
    import json
    now = 1795129932.0
    row = {"state": "FRESH", "used": 0.25, "burn_h": 0.0067, "eta_s": 401265,
           "reset_at": now + 445668, "observed_at": now - 10}
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"accounts": {"claude": {
        "state": "FRESH", "windows": {"7d": row, "5h": {}}}}}))
    monkeypatch.setattr(hv, "BUDGET_FILE", path)
    line = hv.budget_forecast_line(now)
    assert "25% used" in line and "12h short of reset" in line
    assert "burn 0.67%/h" in line and "need <=0.61%/h" in line


def test_budget_forecast_line_when_it_reaches_reset(tmp_path, monkeypatch):
    import json
    now = 1795129932.0
    row = {"state": "FRESH", "used": 0.10, "burn_h": 0.004, "eta_s": 810000,
           "reset_at": now + 300000, "observed_at": now - 5}
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"accounts": {"claude": {
        "state": "FRESH", "windows": {"7d": row}}}}))
    monkeypatch.setattr(hv, "BUDGET_FILE", path)
    line = hv.budget_forecast_line(now)
    assert "reaches reset" in line and "spare" in line and "short" not in line


def test_budget_forecast_line_fails_closed(tmp_path, monkeypatch):
    import json
    now = 1795129932.0
    path = tmp_path / "budget.json"
    monkeypatch.setattr(hv, "BUDGET_FILE", path)
    assert hv.budget_forecast_line(now) is None                     # missing
    path.write_text("{broken\n")
    assert hv.budget_forecast_line(now) is None                     # malformed
    good = {"state": "FRESH", "used": 0.25, "burn_h": 0.0067, "eta_s": 401265,
            "reset_at": now + 445668, "observed_at": now - 10}
    def write(accounts):
        path.write_text(json.dumps({"accounts": accounts}))
    write({"claude": {"state": "FRESH", "windows": {"7d": good}}})
    assert hv.budget_forecast_line(now) is not None
    write({"claude": {"state": "FRESH", "windows": {"7d": dict(good, observed_at=now - 1000)}}})
    assert hv.budget_forecast_line(now) is None                     # stale row
    write({"claude": {"state": "FRESH", "windows": {"5h": good}}})
    assert hv.budget_forecast_line(now) is None                     # no 7d
    write({"claude": {"state": "STALE", "windows": {"7d": good}}})
    assert hv.budget_forecast_line(now) is None                     # non-fresh account
    write({"claude": {"state": "FRESH", "windows": {"7d": dict(good, burn_h="x")}}})
    assert hv.budget_forecast_line(now) is None                     # malformed field
    write(5)
    assert hv.budget_forecast_line(now) is None                     # non-object accounts


def test_budget_forecast_line_reads_codex_without_a_claude_row(tmp_path, monkeypatch):
    import json
    now = 1795129932.0
    row = {"state": "FRESH", "used": 0.48, "burn_h": 0.0114, "eta_s": 167109,
           "reset_at": now + 456610, "observed_at": now - 10}
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"accounts": {"codex-a": {
        "state": "FRESH", "windows": {"7d": row}}}}))
    monkeypatch.setattr(hv, "BUDGET_FILE", path)
    line = hv.budget_forecast_line(now, "codex-a", "codex")
    assert "budget-codex 7d: 48% used" in line and "short of reset" in line
    assert hv.budget_forecast_line(now) is None


def test_map_evidence_line_reports_the_weak_room_test(tmp_path, monkeypatch):
    import json
    import time
    fp = tmp_path / "rssi-fingerprint.json"
    state = tmp_path / "map-state.json"
    fp.write_text(json.dumps({"loo": {"n": 3, "correct": 0,
                                      "majority_baseline_accuracy": 0.67}}))
    state.write_text(json.dumps({"markers": {
        "ilya": {"age_h": 202.6}, "rozalia": {"age_h": 202.5}, "bruno": {"age_h": 205.4}}}))
    now = time.time()
    monkeypatch.setattr(hv, "RSSI_FP_FILE", fp)
    monkeypatch.setattr(hv, "MAP_STATE_FILE", state)
    line = hv.map_evidence_line(now)
    assert "0/3 correct" in line and "67% by always guessing" in line
    assert "3 labels, newest label 202 h old" in line
    assert "no better than a guess" in line


def test_map_evidence_line_when_the_room_test_beats_the_baseline(tmp_path, monkeypatch):
    import json
    import time
    fp = tmp_path / "rssi-fingerprint.json"
    fp.write_text(json.dumps({"loo": {"n": 3, "correct": 2,
                                      "majority_baseline_accuracy": 0.5}}))
    monkeypatch.setattr(hv, "RSSI_FP_FILE", fp)
    monkeypatch.setattr(hv, "MAP_STATE_FILE", tmp_path / "absent-state.json")
    # no state file -> label ages UNKNOWN, but the room test is still reported
    assert hv.map_evidence_line(time.time()) is None  # state is required
    state = tmp_path / "map-state.json"
    state.write_text(json.dumps({"markers": {"ilya": {"age_h": "x"}}}))
    monkeypatch.setattr(hv, "MAP_STATE_FILE", state)
    line = hv.map_evidence_line(time.time())
    assert "2/3 correct vs 50% by always guessing" in line
    assert "label ages UNKNOWN" in line and "beats the majority baseline" in line
    assert "no better" not in line


def test_map_evidence_line_fails_closed(tmp_path, monkeypatch):
    import json
    import os
    import time
    fp = tmp_path / "rssi-fingerprint.json"
    state = tmp_path / "map-state.json"
    monkeypatch.setattr(hv, "RSSI_FP_FILE", fp)
    monkeypatch.setattr(hv, "MAP_STATE_FILE", state)
    now = time.time()
    assert hv.map_evidence_line(now) is None                        # missing
    fp.write_text("{broken\n")
    state.write_text("{}")
    assert hv.map_evidence_line(now) is None                        # malformed fingerprint
    good = {"loo": {"n": 3, "correct": 0, "majority_baseline_accuracy": 0.67}}
    fp.write_text(json.dumps(good))
    assert hv.map_evidence_line(now) is not None                    # baseline case
    fp.write_text(json.dumps({}))
    assert hv.map_evidence_line(now) is None                        # no loo
    fp.write_text(json.dumps({"loo": {"n": 0, "correct": 0, "majority_baseline_accuracy": 0.67}}))
    assert hv.map_evidence_line(now) is None                        # no labelled moments
    fp.write_text(json.dumps({"loo": {"n": 3, "correct": 4, "majority_baseline_accuracy": 0.67}}))
    assert hv.map_evidence_line(now) is None                        # impossible score
    fp.write_text(json.dumps({"loo": {"n": 3, "correct": 0, "majority_baseline_accuracy": "x"}}))
    assert hv.map_evidence_line(now) is None                        # malformed field
    fp.write_text(json.dumps(good))
    state.write_text("{broken\n")
    assert hv.map_evidence_line(now) is None                        # malformed map state
    state.write_text("{}")
    os.utime(state, (now - hv.MAP_EVIDENCE_MAX_AGE_S - 120,) * 2)
    assert hv.map_evidence_line(now) is None                        # stale renderer


def test_audio_listener_line_reports_process_state(tmp_path, monkeypatch):
    import json
    import os
    now = 1795194160.0
    path = tmp_path / "audio-listener-status.json"
    path.write_text(json.dumps({"state": "LISTENING", "coverage_state": "WARMING"}))
    os.utime(path, (now - 5, now - 5))
    monkeypatch.setattr(hv, "AUDIO_STATUS", path)
    line = hv.audio_listener_line(now)
    assert "LISTENING" in line and "WARMING" in line and "heartbeat 5s old" in line


def test_audio_listener_line_fails_closed(tmp_path, monkeypatch):
    import json
    import os
    now = 1795194160.0
    path = tmp_path / "audio-listener-status.json"
    monkeypatch.setattr(hv, "AUDIO_STATUS", path)
    assert hv.audio_listener_line(now) == "  LISTENER: UNKNOWN (no readable heartbeat)"
    path.write_text(json.dumps({"state": "LISTENING", "coverage_state": "GREEN"}))
    os.utime(path, (now - 900, now - 900))
    line = hv.audio_listener_line(now)
    assert "UNKNOWN" in line and "not reporting" in line
    path.write_text("{broken\n")
    os.utime(path, (now, now))
    assert "UNKNOWN" in hv.audio_listener_line(now)


def test_sound_lines_show_listener_state_even_without_a_log(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "AUDIO", tmp_path / "absent.jsonl")
    monkeypatch.setattr(hv, "AUDIO_STATUS", tmp_path / "absent.json")
    lines = hv.sound_lines(1795194160.0)
    assert lines[0].startswith("  LISTENER: UNKNOWN")
    assert "no audio log" in lines[1]


def test_phones_signal_lines_name_the_stale_phone_and_its_reason(tmp_path, monkeypatch):
    import json
    detail = tmp_path / "household-phones.detail"
    monkeypatch.setattr(hv, "PHONES_DETAIL", detail)
    detail.write_text(json.dumps({
        "note3": {"status": "ok", "age_s": 30},
        "redmi10": {"status": "stale", "age_s": 212444.0,
                    "reason": "Redmi10 stream row 212444s old (reader requires <=120s)"}}))
    lines = hv.phones_signal_lines()
    assert len(lines) == 1
    assert lines[0].startswith("  signal   redmi10 stale: Redmi10 stream row 212444s old")
    assert "note3" not in "".join(lines)


def test_phones_signal_lines_all_ok_adds_nothing(tmp_path, monkeypatch):
    import json
    detail = tmp_path / "household-phones.detail"
    monkeypatch.setattr(hv, "PHONES_DETAIL", detail)
    detail.write_text(json.dumps({"note3": {"status": "ok"}, "redmi10": {"status": "ok"}}))
    assert hv.phones_signal_lines() == []


def test_phones_signal_lines_fail_closed(tmp_path, monkeypatch):
    import json
    detail = tmp_path / "household-phones.detail"
    monkeypatch.setattr(hv, "PHONES_DETAIL", detail)
    assert hv.phones_signal_lines() == [
        "  signal   household-phones detail: UNKNOWN (no readable detail)"]
    detail.write_text("{broken")
    assert "UNKNOWN" in hv.phones_signal_lines()[0]
    detail.write_text(json.dumps({"note3": {"status": "ok"}}))
    assert hv.phones_signal_lines() == [
        "  signal   redmi10 UNKNOWN (no entry in detail)"]
