"""Speed reading: gating, failure rows and the window line. A failed or missing reading is never a pass."""
import importlib.util
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import network_speed as ns  # noqa: E402


def _load_view():
    spec = importlib.util.spec_from_file_location("network_view_speed_test", REPO / "tools" / "network_view.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


view = _load_view()
NOW = 1_800_000_000.0


def test_due_only_off_peak():
    assert ns.due(NOW, None, 3) == (True, "due")
    ok, why = ns.due(NOW, None, 14)
    assert not ok and "off-peak" in why


def test_due_respects_minimum_interval():
    assert ns.due(NOW, NOW - 11 * 3600, 3)[0] is False
    assert ns.due(NOW, NOW - 13 * 3600, 3)[0] is True


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_measure_good_download_computes_mbps():
    payload = b"x" * ns.EXPECT_BYTES
    row = ns.measure(opener=lambda url, timeout: _Resp(payload))
    assert row["ok"] is True
    assert row["bytes"] == ns.EXPECT_BYTES
    assert row["down_mbps"] > 0
    assert "error" not in row


def test_measure_short_read_is_a_failed_row():
    row = ns.measure(opener=lambda url, timeout: _Resp(b"x" * 1000))
    assert row["ok"] is False
    assert "short read" in row["error"]


def test_measure_exception_is_a_failed_row_not_a_raise():
    def boom(url, timeout):
        raise OSError("network unreachable")

    row = ns.measure(opener=boom)
    assert row["ok"] is False
    assert row["error"].startswith("OSError")


def test_read_rows_skips_bad_lines(tmp_path):
    p = tmp_path / "speed.jsonl"
    p.write_text('{"ts": 1, "ok": true, "down_mbps": 50}\nnot json\n{"ts": 2, "ok": false, "error": "x"}\n')
    rows = ns.read_rows(p)
    assert [r["ts"] for r in rows] == [1, 2]
    assert ns.read_rows(tmp_path / "missing.jsonl") == []


def test_speed_line_unknown_when_never_measured():
    assert view.speed_line([], NOW).startswith("UNKNOWN (no speed reading yet")


def test_speed_line_shows_last_good_reading_with_age():
    rows = [{"ts": NOW - 7200, "ok": True, "down_mbps": 213.4}]
    line = view.speed_line(rows, NOW)
    assert line.startswith("DOWN 213.4 Mbps, measured 2.0 h ago")


def test_speed_line_failed_last_try_is_unknown_not_the_old_number():
    rows = [
        {"ts": NOW - 7200, "ok": True, "down_mbps": 213.4},
        {"ts": NOW - 600, "ok": False, "error": "timeout"},
    ]
    line = view.speed_line(rows, NOW)
    assert line.startswith("UNKNOWN (last try 10 min ago failed: timeout")


def test_speed_line_stale_good_reading_is_unknown():
    rows = [{"ts": NOW - 40 * 3600, "ok": True, "down_mbps": 90}]
    assert view.speed_line(rows, NOW).startswith("UNKNOWN (last reading is 40 h old)")


def test_speed_up_line_unknown_when_never_measured():
    assert view.speed_up_line([], NOW).startswith("UNKNOWN (no upload reading yet")


def test_speed_up_line_shows_last_good_with_age():
    rows = [{"ts": NOW - 7200, "ok": True, "up_mbps": 18.6}]
    assert view.speed_up_line(rows, NOW).startswith("UP 18.6 Mbps, measured 2.0 h ago")


def test_speed_up_line_failed_last_try_is_unknown_not_the_old_number():
    rows = [
        {"ts": NOW - 7200, "ok": True, "up_mbps": 18.6},
        {"ts": NOW - 600, "ok": False, "error": "timeout: too slow"},
    ]
    assert view.speed_up_line(rows, NOW).startswith("UNKNOWN (last upload 10 min ago failed: timeout")


def test_speed_up_line_stale_good_is_unknown():
    rows = [{"ts": NOW - 40 * 3600, "ok": True, "up_mbps": 18.6}]
    assert view.speed_up_line(rows, NOW).startswith("UNKNOWN (last upload is 40 h old)")


def test_check_mode_writes_nothing(tmp_path, capsys):
    rc = ns.main(["--home", str(tmp_path), "--check"])
    assert rc == 0
    assert not (tmp_path / "network" / "speed.jsonl").exists()


def _write_rows(home, rows):
    import json
    path = home / "network" / ns.LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _pin_outside_offpeak(monkeypatch):
    """The clock decides off-peak; these tests need 12:00 local so they never reach a live download."""
    real_due = ns.due
    monkeypatch.setattr(ns, "due", lambda now, last, hour: real_due(now, last, 12))
    monkeypatch.setattr(ns, "clock_hour", lambda now: 12)


def test_not_due_with_no_reading_is_unknown_not_green(tmp_path, capsys, monkeypatch):
    # Outside off-peak and never measured: the duty must not report a calm GREEN exit.
    _pin_outside_offpeak(monkeypatch)
    rc = ns.main(["--home", str(tmp_path)])
    assert rc == 3
    assert "UNKNOWN" in capsys.readouterr().out


def test_not_due_with_recent_good_reading_is_green(tmp_path, capsys, monkeypatch):
    import time
    _pin_outside_offpeak(monkeypatch)
    _write_rows(tmp_path, [{"ts": time.time() - 3600, "ok": True, "down_mbps": 80.0}])
    assert ns.main(["--home", str(tmp_path)]) == 0


def test_not_due_with_stale_good_reading_is_unknown(tmp_path, capsys, monkeypatch):
    import time
    _pin_outside_offpeak(monkeypatch)
    _write_rows(tmp_path, [{"ts": time.time() - 40 * 3600, "ok": True, "down_mbps": 80.0}])
    assert ns.main(["--home", str(tmp_path)]) == 3


def test_stale_download_with_failed_upload_names_both_causes(tmp_path, capsys, monkeypatch):
    # Both readings untrustworthy: the detail must record both, and the duty must stay UNKNOWN.
    import time
    _pin_outside_offpeak(monkeypatch)
    _write_rows(tmp_path, [{"ts": time.time() - 40 * 3600, "ok": True, "down_mbps": 80.0}])
    _write_up_rows(tmp_path, [{"ts": time.time() - 300, "ok": False, "error": "timeout: uplink gone"}])
    assert ns.main(["--home", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "no good reading in the last 36 h, so UNKNOWN" in out
    assert "upload UNKNOWN: last upload 5 min ago failed: timeout: uplink gone" in out


def test_measure_sends_named_user_agent_not_urllib_default():
    # Cloudflare returns 403 to the default Python-urllib agent; the reading must name its client.
    seen = {}

    def opener(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        return _Resp(b"x" * ns.EXPECT_BYTES)

    row = ns.measure(opener=opener)
    assert row["ok"] is True
    assert seen["ua"] == ns.USER_AGENT
    assert "Python-urllib" not in seen["ua"]


def test_measure_up_posts_bounded_payload_with_named_agent():
    seen = {}

    def opener(req, timeout):
        seen["method"] = req.get_method()
        seen["ua"] = req.get_header("User-agent")
        seen["length"] = len(req.data)
        seen["timeout"] = timeout
        return _Resp(b"")

    row = ns.measure_up(opener=opener)
    assert row["ok"] is True
    assert row["up_mbps"] > 0
    assert "error" not in row
    assert seen == {
        "method": "POST",
        "ua": ns.USER_AGENT,
        "length": ns.UP_EXPECT_BYTES,
        "timeout": ns.UP_TIMEOUT_S,
    }


def test_measure_up_exception_is_failed_row_not_a_raise():
    def boom(req, timeout):
        raise OSError("uplink gone")

    row = ns.measure_up(opener=boom)
    assert row["ok"] is False
    assert row["error"].startswith("OSError")


def test_failed_try_is_retried_in_window_after_30_min_not_next_day(tmp_path, capsys, monkeypatch):
    # A 403 at 02:01 must not block the rest of the night: failures retry after 30 min, successes wait 12 h.
    import time
    real_due = ns.due
    monkeypatch.setattr(ns, "due", lambda now, last, hour: real_due(now, last, 3))
    _write_rows(tmp_path, [{"ts": time.time() - 60, "ok": False, "error": "HTTPError: 403"}])
    assert ns.main(["--home", str(tmp_path), "--check"]) == 0
    assert "retry not before" in capsys.readouterr().out
    _write_rows(tmp_path, [{"ts": time.time() - 31 * 60, "ok": False, "error": "HTTPError: 403"}])
    assert ns.main(["--home", str(tmp_path), "--check"]) == 0
    assert capsys.readouterr().out.startswith("due:")


def test_good_reading_throttle_ignores_later_failures(tmp_path, capsys, monkeypatch):
    import time
    real_due = ns.due
    monkeypatch.setattr(ns, "due", lambda now, last, hour: real_due(now, last, 3))
    _write_rows(tmp_path, [
        {"ts": time.time() - 2 * 3600, "ok": True, "down_mbps": 80.0},
        {"ts": time.time() - 40 * 60, "ok": False, "error": "x"},
    ])
    assert ns.main(["--home", str(tmp_path), "--check"]) == 0
    assert capsys.readouterr().out.startswith("not due: measured recently")


def test_main_writes_both_logs_when_due(tmp_path, capsys, monkeypatch):
    import time
    real_due = ns.due
    monkeypatch.setattr(ns, "due", lambda now, last, hour: real_due(now, last, 3))
    monkeypatch.setattr(ns, "measure", lambda: {"ts": time.time(), "ok": True, "down_mbps": 90.0})
    monkeypatch.setattr(ns, "measure_up", lambda: {"ts": time.time(), "ok": True, "up_mbps": 20.0})
    assert ns.main(["--home", str(tmp_path)]) == 0
    assert (tmp_path / "network" / "speed.jsonl").exists()
    assert (tmp_path / "network" / "speed-up.jsonl").exists()
    assert '"up_mbps": 20.0' in capsys.readouterr().out


def test_main_retries_failed_upload_within_window_while_download_fresh(tmp_path, capsys, monkeypatch):
    # A failed upload at 02:01 retries after 30 min even though the download's 12 h throttle holds.
    import json
    import time
    real_due = ns.due
    monkeypatch.setattr(ns, "due", lambda now, last, hour: real_due(now, last, 3))
    _write_rows(tmp_path, [{"ts": time.time() - 60, "ok": True, "down_mbps": 90.0}])
    up_path = tmp_path / "network" / "speed-up.jsonl"
    up_path.parent.mkdir(parents=True, exist_ok=True)
    up_path.write_text(json.dumps({"ts": time.time() - 31 * 60, "ok": False, "error": "timeout"}) + "\n")
    ran = []

    def no_download():
        ran.append("down")
        return {"ts": time.time(), "ok": True, "down_mbps": 90.0}

    def do_upload():
        ran.append("up")
        return {"ts": time.time(), "ok": True, "up_mbps": 20.0}

    monkeypatch.setattr(ns, "measure", no_download)
    monkeypatch.setattr(ns, "measure_up", do_upload)
    assert ns.main(["--home", str(tmp_path)]) == 0
    assert ran == ["up"]
    assert "not due: measured recently" in capsys.readouterr().out


def _write_up_rows(home, rows):
    import json
    path = home / "network" / ns.UP_LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_upload_state_missing_log_is_honest_only_outside_the_window():
    assert ns.upload_state([], NOW, 12) == (True, "no upload reading yet")
    assert ns.upload_state([], NOW, 3) == (False, "no upload attempt recorded in the off-peak window")


def test_upload_state_failed_attempt_is_unknown_not_the_old_figure():
    rows = [
        {"ts": NOW - 7200, "ok": True, "up_mbps": 18.6},
        {"ts": NOW - 600, "ok": False, "error": "timeout: uplink gone"},
    ]
    ok, why = ns.upload_state(rows, NOW, 12)
    assert ok is False
    assert why == "last upload 10 min ago failed: timeout: uplink gone"


def test_upload_state_good_row_is_green_with_its_age():
    assert ns.upload_state([{"ts": NOW - 7200, "ok": True, "up_mbps": 18.6}], NOW, 12) == (
        True, "upload 18.6 Mbps, 2.0 h ago")


def test_upload_state_stale_good_row_is_unknown():
    ok, why = ns.upload_state([{"ts": NOW - 40 * 3600, "ok": True, "up_mbps": 18.6}], NOW, 12)
    assert ok is False and why == "last upload is 40 h old"


def test_upload_state_unreadable_success_row_is_unknown():
    ok, why = ns.upload_state([{"ts": NOW - 60, "ok": True}], NOW, 12)
    assert ok is False and why == "last upload row is unreadable"


def test_failed_upload_makes_the_duty_unknown_not_green(tmp_path, capsys, monkeypatch):
    # A night of failing uploads must wake me: the download is fine, but the upload line is not.
    import time
    _pin_outside_offpeak(monkeypatch)
    _write_rows(tmp_path, [{"ts": time.time() - 3600, "ok": True, "down_mbps": 80.0}])
    _write_up_rows(tmp_path, [{"ts": time.time() - 300, "ok": False, "error": "timeout: uplink gone"}])
    assert ns.main(["--home", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "upload UNKNOWN: last upload 5 min ago failed: timeout: uplink gone" in out


def test_good_upload_keeps_the_duty_green(tmp_path, capsys, monkeypatch):
    import time
    _pin_outside_offpeak(monkeypatch)
    _write_rows(tmp_path, [{"ts": time.time() - 3600, "ok": True, "down_mbps": 80.0}])
    _write_up_rows(tmp_path, [{"ts": time.time() - 7200, "ok": True, "up_mbps": 20.0}])
    assert ns.main(["--home", str(tmp_path)]) == 0
    assert "upload: upload 20 Mbps, 2.0 h ago" in capsys.readouterr().out


def test_no_upload_reading_yet_is_not_a_failure_outside_the_window(tmp_path, capsys, monkeypatch):
    import time
    _pin_outside_offpeak(monkeypatch)
    _write_rows(tmp_path, [{"ts": time.time() - 3600, "ok": True, "down_mbps": 80.0}])
    assert ns.main(["--home", str(tmp_path)]) == 0
    assert "upload: no upload reading yet" in capsys.readouterr().out
