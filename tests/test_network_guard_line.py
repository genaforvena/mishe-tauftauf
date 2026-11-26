"""The network window says whether the host guard's trip marker is present.

A trip makes the router units skip, so the readings go STALE; the window must name the cause, and an
unreadable marker must be UNKNOWN rather than a calm "clear".
"""
import importlib.util
import os
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
view = load(REPO / "tools" / "network_view.py", "network_view_guard_under_test")


def test_absent_marker_is_clear(tmp_path):
    line = view.guard_line(marker=str(tmp_path / "tripped"))
    assert line.startswith("clear")


def test_present_marker_names_trip_and_age(tmp_path):
    marker = tmp_path / "tripped"
    marker.write_text("")
    past = time.time() - 20 * 60
    os.utime(marker, (past, past))
    line = view.guard_line(now=time.time(), marker=str(marker))
    assert line.startswith("TRIPPED 20 min ago")
    assert "STALE" in line


def test_unreadable_marker_is_unknown(tmp_path):
    blocked = tmp_path / "locked"
    blocked.mkdir()
    marker = blocked / "tripped"
    marker.write_text("")
    blocked.chmod(0)
    try:
        line = view.guard_line(marker=str(marker))
    finally:
        blocked.chmod(0o755)
    if os.geteuid() == 0:  # root reads through mode 000; the UNKNOWN branch cannot be forced here
        return
    assert line.startswith("UNKNOWN")


def test_recent_trip_record_is_named_though_bare_marker_is_gone(tmp_path):
    # the guard now writes tripped-<ns>.json records; a fresh one must not read as a calm clear
    record = tmp_path / "tripped-1795567161284062546.json"
    record.write_text("{}")
    line = view.guard_line(now=time.time(), marker=str(tmp_path / "tripped"))
    assert line.startswith("TRIP RECORD")
    assert "latch not read here" in line


def test_old_trip_record_is_clear(tmp_path):
    record = tmp_path / "tripped-1793189424987208050.json"
    record.write_text("{}")
    old = time.time() - 48 * 3600
    os.utime(record, (old, old))
    line = view.guard_line(now=time.time(), marker=str(tmp_path / "tripped"))
    assert line.startswith("clear")
    assert "last trip record" in line


def test_unlistable_trip_records_are_unknown_not_clear(tmp_path, monkeypatch):
    # A directory that stat can reach but not list must not read as "no trip record" (a calm clear).
    # Forced by patching scandir, so it holds for root too (root can list a mode-000 directory).
    def denied(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(view.os, "scandir", denied)
    line = view.guard_line(marker=str(tmp_path / "tripped"))
    assert line.startswith("UNKNOWN"), line


def test_recent_trip_record_with_fresh_router_read_says_units_run_again(tmp_path):
    # the 2026-11-26 case: a trip 71 min ago, router read 28 s ago. The line must not say the guard
    # "may still hold back" the router units when their readings are fresh.
    record = tmp_path / "tripped-1795567161284062546.json"
    record.write_text("{}")
    past = time.time() - 71 * 60
    os.utime(record, (past, past))
    line = view.guard_line(now=time.time(), marker=str(tmp_path / "tripped"), router_read_at=time.time() - 28.0)
    assert line.startswith("TRIP RECORD 71 min ago")
    assert "router was read 28 s ago, after the record" in line
    assert "running again" in line
    assert "may still hold back" not in line


def test_recent_trip_record_with_stale_router_read_still_warns(tmp_path):
    record = tmp_path / "tripped-1795567161284062546.json"
    record.write_text("{}")
    line = view.guard_line(now=time.time(), marker=str(tmp_path / "tripped"), router_read_at=time.time() - 3600.0)
    assert "may still hold back" in line


def test_router_read_taken_before_the_record_does_not_clear_the_trip(tmp_path):
    # the 2026-11-26 review case: record 3 min ago, last router read 10 min ago. The read is older than
    # the record, so it says nothing about the units now; the line must keep the warning.
    record = tmp_path / "tripped-1795567161284062546.json"
    record.write_text("{}")
    now = time.time()
    os.utime(record, (now - 3 * 60, now - 3 * 60))
    line = view.guard_line(now=now, marker=str(tmp_path / "tripped"), router_read_at=now - 10 * 60)
    assert "may still hold back" in line
    assert "running again" not in line


def test_failed_router_run_newer_than_record_is_not_a_fresh_read(tmp_path, monkeypatch):
    """A failed router run written after the trip record must not read as the units running again."""
    now = time.time()
    rec = tmp_path / "tripped-1.json"
    rec.write_text("{}")
    os.utime(rec, (now - 3 * 60, now - 3 * 60))
    log = tmp_path / "router.jsonl"
    import json
    log.write_text(
        json.dumps({"ts": now - 600, "ok": True}) + "\n"
        + json.dumps({"ts": now - 60, "ok": False}) + "\n"
    )
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    read_at = view.router_read_at_s()
    assert read_at == now - 600  # the failed row at now-60 is not a read
    line = view.guard_line(now=now, marker=str(tmp_path / "tripped"), router_read_at=read_at)
    assert "running again" not in line
    assert "may still hold back" in line
