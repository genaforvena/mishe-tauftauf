import subprocess
from pathlib import Path

from mishe_tauftauf.plant_tmux import parse_list_windows
from mishe_tauftauf.replacement_readings import (
    RENDERER_SENSE_ID, TMUX_SENSE_ID,
    read_renderer_coverage, read_tmux_windows,
)


def _done(code=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=err)


def test_parse_rejects_malformed_line():
    assert parse_list_windows("a\tb\t2\n") == [{"session": "a", "window": "b", "panes": 2}]
    assert parse_list_windows("a\tb\n") is None
    assert parse_list_windows("a\tb\tx\n") is None
    assert parse_list_windows("") == []


def test_tmux_no_socket_or_probe_failure_is_unknown():
    assert read_tmux_windows(None)["state"] == "unknown"
    assert read_tmux_windows("/s", runner=lambda s, a: None)["state"] == "unknown"


def test_tmux_nonzero_exit_is_unknown_with_cause():
    out = read_tmux_windows("/s", runner=lambda s, a: _done(1, err="no server running on /s\n"))
    assert out["state"] == "unknown"
    assert "exit=1" in out["sample"] and "no server" in out["sample"]


def test_tmux_answered_empty_is_verified_zero_and_windows_counted():
    assert read_tmux_windows("/s", runner=lambda s, a: _done(0, ""))["state"] == "verified"
    got = read_tmux_windows("/s", runner=lambda s, a: _done(0, "mishe\tsenses\t1\nmishe\tgenome\t2\n"))
    assert got["state"] == "verified" and got["id"] == TMUX_SENSE_ID
    assert "windows=2 panes=3" in got["sample"]


def test_tmux_malformed_output_is_unknown():
    assert read_tmux_windows("/s", runner=lambda s, a: _done(0, "garbage\n"))["state"] == "unknown"


def test_renderer_unset_root_is_unknown(tmp_path: Path):
    assert read_renderer_coverage(None)["state"] == "unknown"
    assert read_renderer_coverage(tmp_path / "absent")["state"] == "unknown"


def test_renderer_complete_root_verified(tmp_path: Path):
    pkg = tmp_path / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "plant_view.py").write_text("x = 1\n")
    (pkg / "plant_view_boundary.py").write_text("y = 2\n")
    got = read_renderer_coverage(tmp_path)
    assert got["state"] == "verified" and got["id"] == RENDERER_SENSE_ID


def test_renderer_missing_or_broken_module_is_drift(tmp_path: Path):
    pkg = tmp_path / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "plant_view.py").write_text("def broken(:\n")
    got = read_renderer_coverage(tmp_path)
    assert got["state"] == "drift"
    assert "plant_view.py=unparseable" in got["sample"]
    assert "plant_view_boundary.py=missing" in got["sample"]
