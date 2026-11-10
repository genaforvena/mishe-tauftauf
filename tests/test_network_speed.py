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


def test_check_mode_writes_nothing(tmp_path, capsys):
    rc = ns.main(["--home", str(tmp_path), "--check"])
    assert rc == 0
    assert not (tmp_path / "network" / "speed.jsonl").exists()
