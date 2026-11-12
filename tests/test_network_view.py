"""The network window's reading of probe rows: a failed or missing row must never look calm.

These are the view's tests (tools/network_view.py, tools/network_boundary.py).
The probe that writes the rows is a separate responsibility with its own
contract and its own tests (tests/test_mishe_network_probe.py).
"""
import importlib.util
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
view = load(REPO / "tools" / "network_view.py", "network_view_under_test")


def test_view_verdict_down_when_no_secure_site_answers():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"at": now, "gateway": {"loss_pct": 0.0, "avg_ms": 0.4},
           "internet": {"gstatic": {"ok": False, "curl_exit": 28}, "cloudflare": {"ok": False, "curl_exit": 28}}}
    assert view.verdict(row, 0).startswith("DOWN: router answers")


def test_view_verdict_unknown_for_old_format_and_stale_rows():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old = {"at": now, "gateway": {"loss_pct": 0.0}, "cloudflare": {"loss_pct": 0.0}}
    assert view.verdict(old, 0).startswith("UNKNOWN")
    fresh = {"at": now, "gateway": {"loss_pct": 0.0}, "internet": {"gstatic": {"ok": True, "total_ms": 300.0}}}
    assert view.verdict(fresh, 3600).startswith("UNKNOWN")  # stale probe is never a calm network
    assert view.verdict(fresh, 0) == "OK"


def test_wifi_decode_counts_clients_per_band_and_drops_macs():
    text = ("BAND 2.472\n7C:C3:A1:A5:20:D7  -28 dBm / unknown (SNR -28)  0 ms ago\n"
            "3A:2D:71:4B:CC:FC  -80 dBm / unknown  0 ms ago\nBAND 5.200\n")
    bands = view.nb.decode_wifi(text)
    assert bands == {"2.472": [-28, -80], "5.200": []}
    assert "7C:C3" not in repr(bands)


def test_wifi_decode_rejects_empty_listing():
    import pytest
    with pytest.raises(ValueError):
        view.nb.decode_wifi("")


def _probe(at, down):
    internet = {"gstatic": {"ok": not down, "total_ms": None if down else 300.0},
                "cloudflare": {"ok": not down, "total_ms": None if down else 280.0}}
    return {"at": at, "gateway": {"loss_pct": 0.0, "avg_ms": 0.4}, "internet": internet, "dns_ms": 50.0}


def test_outage_episodes_list_start_and_end_and_still_down():
    data = [_probe("2026-11-10T10:00:00Z", False), _probe("2026-11-10T10:01:00Z", True),
            _probe("2026-11-10T10:02:00Z", True), _probe("2026-11-10T10:05:00Z", False),
            _probe("2026-11-10T10:06:00Z", True)]
    assert view.outage_episodes(data) == [("2026-11-10T10:01:00Z", "2026-11-10T10:05:00Z"),
                                          ("2026-11-10T10:06:00Z", None)]
    lines = view.outage_lines(data)
    assert lines[0] == "  DOWN 10:01Z to 10:05Z (4 min)"
    assert lines[1] == "  DOWN since 10:06Z, still down at the last probe"


def test_outage_lines_say_none_when_all_probes_answer_and_unknown_without_rows():
    data = [_probe("2026-11-10T10:00:00Z", False), _probe("2026-11-10T10:01:00Z", False)]
    assert view.outage_lines(data)[0].startswith("  none over 2 probes")
    assert view.outage_lines([])[0].startswith("  UNKNOWN")


def test_outage_skips_rows_from_before_the_secure_web_check():
    old = {"at": "2026-11-10T09:00:00Z", "gateway": {"loss_pct": 0.0, "avg_ms": 0.4}}
    assert view.outage_episodes([old, _probe("2026-11-10T09:01:00Z", False)]) == []


def test_recorded_clients_line_unknown_without_log(tmp_path, monkeypatch):
    monkeypatch.setattr(view, "ROUTER_LOG", tmp_path / "router.jsonl")
    assert view.recorded_clients_line().startswith("UNKNOWN")


def test_recorded_clients_line_shows_count_weakest_and_age(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    now = 1_000_000.0
    clients = [{"band": "2.4", "dbm": -30, "name": "iPad", "id": "a", "rx": 1, "tx": 1},
               {"band": "5", "dbm": -36, "name": "Redmi-10", "id": "b", "rx": 1, "tx": 1}]
    log.write_text('{"ts": %s, "ok": true, "clients": %s}\n' % (now - 600, __import__("json").dumps(clients)))
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.recorded_clients_line(now)
    assert "2 clients" in line and "weakest Redmi-10 -36 dBm" in line and "read 600 s ago" in line
    assert "STALE" not in line


def test_recorded_clients_line_reports_failed_read_not_calm(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000000.0, "ok": false, "error": "ssh timeout"}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.recorded_clients_line(1000000.0 + 60)
    assert line.startswith("UNKNOWN") and "ssh timeout" in line


def test_recorded_clients_line_marks_stale(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000000.0, "ok": true, "clients": []}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert "STALE" in view.recorded_clients_line(1000000.0 + 3600)


def test_gateway_line_shows_jitter_and_unknown_when_missing():
    rows = [{"gateway": {"loss_pct": 0.0, "avg_ms": 0.47, "jitter_ms": 0.027}}]
    line = view.gateway_line(rows)
    assert "jitter 0.03 ms" in line
    old = [{"gateway": {"loss_pct": 0.0, "avg_ms": 0.47}}]
    assert "jitter UNKNOWN" in view.gateway_line(old)
    assert "jitter UNKNOWN" in view.gateway_line([{"gateway": None}])
    assert "jitter UNKNOWN" in view.gateway_line([])
