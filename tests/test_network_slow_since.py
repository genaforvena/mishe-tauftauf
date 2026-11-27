"""slow_since_line: since when the secure handshake has stayed slow, in whole UTC hours."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("network_view_slow_since", REPO / "tools" / "network_view.py")
view = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(REPO / "tools"))
spec.loader.exec_module(view)


def row(at, handshake):
    host = {"connect_ms": 70.0, "tls_ms": handshake + 70.0, "total_ms": handshake + 200.0, "ok": True}
    return {"at": at, "internet": {"gstatic": dict(host), "cloudflare": dict(host)}}


def hour_rows(hour, handshake, n=4):
    return [row(f"2026-11-27T{hour:02d}:{m:02d}:00Z", handshake) for m in range(0, n * 10, 10)]


def test_slow_run_starts_at_first_slow_hour():
    data = hour_rows(7, 280) + hour_rows(8, 700) + hour_rows(9, 720)
    text = view.slow_since_line(data)
    assert "since 2026-11-27 08:00Z" in text
    assert "2 hours" in text


def test_newest_hour_not_slow_says_so():
    data = hour_rows(8, 700) + hour_rows(9, 280)
    assert view.slow_since_line(data).startswith("not slow in the newest hour")


def test_thin_hour_is_unknown_and_marks_start_as_at_least():
    data = hour_rows(7, 700, n=1) + hour_rows(8, 700) + hour_rows(9, 720)
    assert "since at least 2026-11-27 08:00Z" in view.slow_since_line(data)


def test_no_rows_is_unknown():
    assert view.slow_since_line([]).startswith("UNKNOWN")
