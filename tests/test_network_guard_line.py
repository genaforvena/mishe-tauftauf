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
