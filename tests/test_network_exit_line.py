"""The network window's EXIT NODE line reads the egress duty's file and is never a calm GREEN by accident.

A missing, unreadable or stale file is UNKNOWN; a RED or unknown client is named.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
view = load(REPO / "tools" / "network_view.py", "network_view_exit_under_test")


def write(tmp_path, results, age_s=0.0):
    path = tmp_path / "egress.json"
    path.write_text(json.dumps({"results": results}), encoding="utf-8")
    t = 1_000_000.0
    os.utime(path, (t, t))
    return path, t + age_s


def test_missing_file_is_unknown(tmp_path):
    line = view.exit_line(now=1.0, path=tmp_path / "absent.json")
    assert line.startswith("UNKNOWN")


def test_all_green_fresh_file(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "GREEN"}, {"client": "note3", "state": "GREEN"}], age_s=60)
    line = view.exit_line(now=now, path=path)
    assert line.startswith("all GREEN: mesh-home GREEN, note3 GREEN")
    assert "read 1 min ago" in line


def test_red_client_is_named(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "GREEN"}, {"client": "note3", "state": "RED"}])
    line = view.exit_line(now=now, path=path)
    assert line.startswith("1 of 2 not GREEN")
    assert "note3 RED" in line


def test_odd_state_is_unknown_not_green(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "BLUE"}])
    line = view.exit_line(now=now, path=path)
    assert "mesh-home UNKNOWN" in line
    assert "all GREEN" not in line


def test_stale_file_is_unknown(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "GREEN"}], age_s=0)
    line = view.exit_line(now=now + view.EGRESS_STALE_S + 60, path=path)
    assert line.startswith("UNKNOWN (last exit-node reading")


def test_corrupt_file_is_unknown(tmp_path):
    path = tmp_path / "egress.json"
    path.write_text("{not json", encoding="utf-8")
    assert view.exit_line(now=1.0, path=path).startswith("UNKNOWN (exit-node file could not be read")
