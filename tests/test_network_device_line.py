"""The window's DEVICE PING line: a silent or stale device reading is never a calm line.

Rows come from a temporary site; the line shows each device's average, its loss and the row's age.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
view = load(REPO / "tools" / "network_view.py", "network_view_device_line_under_test")
care = load(REPO / "checks" / "network-care.py", "network_care_device_line_under_test")


def point_at(monkeypatch, tmp_path, rows):
    log = tmp_path / "devices.jsonl"
    if rows is not None:
        log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(view, "DEVICES_LOG", log)


def test_missing_log_is_unknown(monkeypatch, tmp_path):
    point_at(monkeypatch, tmp_path, None)
    assert view.devices_line(now=1000.0).startswith("UNKNOWN")


def test_fresh_row_shows_average_loss_and_age(monkeypatch, tmp_path):
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 3600, "devices": {"iMac-Rozalia": {"ok": True, "avg_ms": 900.0, "loss_pct": 0.0}}},
        {"ts": now - 120, "devices": {"iMac-Rozalia": {"ok": True, "avg_ms": 63.9, "loss_pct": 0.0}}},
    ])
    line = view.devices_line(now=now)
    assert "iMac-Rozalia 64 ms avg, 0% loss" in line
    assert "read 2 min ago" in line
    assert "STALE" not in line


def test_silent_device_is_named_not_shown_as_fast(monkeypatch, tmp_path):
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 60, "devices": {"iPad-Rozalia": {"ok": False, "why": "timeout"}}},
    ])
    line = view.devices_line(now=now)
    assert line.startswith("UNKNOWN")
    assert "iPad-Rozalia no reply (timeout)" in line
    assert "ms avg" not in line


def test_old_row_is_marked_stale(monkeypatch, tmp_path):
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 40 * 60, "devices": {"iMac-Rozalia": {"ok": True, "avg_ms": 5.0, "loss_pct": 0.0}}},
    ])
    line = view.devices_line(now=now)
    assert line.startswith("UNKNOWN")
    assert "40 min old (STALE)" in line
    assert "ms avg" not in line


def test_malformed_lines_are_skipped(monkeypatch, tmp_path):
    now = 10_000.0
    log = tmp_path / "devices.jsonl"
    log.write_text("not json\n" + json.dumps({"ts": now - 30, "devices": {"iMac-Rozalia": {"ok": True, "avg_ms": 4.0, "loss_pct": 0.0}}}) + "\n")
    monkeypatch.setattr(view, "DEVICES_LOG", log)
    assert "iMac-Rozalia 4 ms avg" in view.devices_line(now=now)


def test_future_row_is_unknown_in_view_and_detector(monkeypatch, tmp_path):
    now = 10_000.0
    rows = [{"ts": now + 60, "devices": {"Example": {"ok": True, "avg_ms": 4.0, "loss_pct": 0.0}}}]
    point_at(monkeypatch, tmp_path, rows)
    assert care.devices_state(rows, now)[0] == 3
    line = view.devices_line(now=now)
    assert line.startswith("UNKNOWN")
    assert "from the future" in line
    assert "ms avg" not in line


@pytest.mark.parametrize(
    "reply",
    [
        {"ok": True, "avg_ms": 4.0},
        {"ok": True, "avg_ms": 4.0, "loss_pct": "broken"},
        {"ok": True, "avg_ms": float("nan"), "loss_pct": 0.0},
        {"ok": True, "avg_ms": 4.0, "loss_pct": 100.0},
    ],
)
def test_invalid_reply_is_unknown_in_view_and_detector(monkeypatch, tmp_path, reply):
    now = 10_000.0
    rows = [{"ts": now - 30, "devices": {"Example": reply}}]
    point_at(monkeypatch, tmp_path, rows)
    line = view.devices_line(now=now)
    assert line.startswith("UNKNOWN")
    assert "Example" in line
    assert "ms avg" not in line

    code, message = care.devices_state(rows, now)
    assert code == 3
    assert "Example" in message


def test_newer_empty_row_does_not_hide_behind_an_older_reply(monkeypatch, tmp_path):
    now = 10_000.0
    rows = [
        {"ts": now - 90, "devices": {"Example": {"ok": True, "avg_ms": 4.0, "loss_pct": 0.0}}},
        {"ts": now - 30, "devices": {}},
    ]
    point_at(monkeypatch, tmp_path, rows)
    assert view.devices_line(now=now).startswith("UNKNOWN")
    assert care.devices_state(rows, now)[0] == 3


@pytest.mark.parametrize("broken_row", [b'{"ts":', b'{"ts": null}', b'\xff'])
def test_unreadable_newest_row_is_unknown_in_view_and_detector(monkeypatch, tmp_path, capsys, broken_row):
    now = 10_000.0
    network = tmp_path / "network"
    network.mkdir()
    log = network / "devices.jsonl"
    good_row = {"ts": now - 60, "devices": {"Example": {"ok": True, "avg_ms": 4.0, "loss_pct": 0.0}}}
    log.write_bytes(json.dumps(good_row).encode() + b"\n" + broken_row + b"\n")
    monkeypatch.setattr(view, "DEVICES_LOG", log)
    monkeypatch.setattr(care.time, "time", lambda: now)

    assert view.devices_line(now=now).startswith("UNKNOWN")
    assert care.devices(tmp_path) == 3
    assert "unreadable" in capsys.readouterr().err


def test_arp_silent_device_says_not_answering_the_lan(monkeypatch, tmp_path):
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 60, "devices": {"iMac-Rozalia": {"ok": False, "why": "no reply", "neigh": "FAILED"}}},
    ])
    line = view.devices_line(now=now)
    assert line.startswith("UNKNOWN")
    assert "iMac-Rozalia not answering the LAN (no ARP answer)" in line
    assert "no reply (no reply)" not in line


def test_arp_probing_device_is_not_called_no_reply_twice(monkeypatch, tmp_path):
    """The live iMac state 2026-11-26: ARP still PROBE, associated. Say it plainly, once."""
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 60, "devices": {"iMac-Rozalia": {"ok": False, "why": "no reply",
                                                       "neigh": "PROBE", "wifi": "ASSOCIATED"}}},
    ])
    line = view.devices_line(now=now)
    assert "iMac-Rozalia not answering the LAN yet (ARP still probing), still on Wi-Fi" in line
    assert "no reply (no reply)" not in line


def test_non_object_device_value_is_unreadable_not_a_crash(monkeypatch, tmp_path):
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 60, "devices": {"iPad-Rozalia": "garbage"}},
    ])
    line = view.devices_line(now=now)
    assert line.startswith("UNKNOWN")
    assert "iPad-Rozalia unreadable ping result" in line


def test_associated_silent_device_with_frozen_counters_is_linked(monkeypatch, tmp_path):
    """Associated on Wi-Fi but not answering, and the router's counters for it did not move: say so."""
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 60, "devices": {"iMac-Rozalia": {"ok": False, "why": "no reply",
                                                       "neigh": "INCOMPLETE", "wifi": "ASSOCIATED"}}},
    ])
    reads = [{"ok": True, "ts": now - 600 + i, "clients": [
        {"name": "iMac-Rozalia", "rx": 144.0, "tx": 130.0, "dbm": -28},
        {"name": "iPhone", "rx": 100.0 + i, "tx": 50.0 + i, "dbm": -27},
    ]} for i in range(view.FROZEN_READS)]
    monkeypatch.setattr(view, "router_rows", lambda: reads)
    line = view.devices_line(now=now)
    assert "iMac-Rozalia not answering the LAN (no ARP answer), still on Wi-Fi" in line
    assert "counters unchanged over the last 6 reads" in line
    assert line.startswith("UNKNOWN")


def test_associated_silent_device_without_frozen_counters_is_not_linked(monkeypatch, tmp_path):
    now = 10_000.0
    point_at(monkeypatch, tmp_path, [
        {"ts": now - 60, "devices": {"iMac-Rozalia": {"ok": False, "why": "no reply",
                                                       "neigh": "INCOMPLETE", "wifi": "ASSOCIATED"}}},
    ])
    monkeypatch.setattr(view, "router_rows", lambda: [])
    line = view.devices_line(now=now)
    assert "still on Wi-Fi (a sleeping device looks like this; not a network fault)" in line
    assert "counters unchanged" not in line
