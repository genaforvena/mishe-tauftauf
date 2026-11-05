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


def _wake_unit(unit_root: Path, pythonpath: str) -> None:
    unit_root.mkdir(parents=True, exist_ok=True)
    (unit_root / "mishe-wake@.service").write_text(
        "[Service]\n"
        f"Environment=PYTHONPATH={pythonpath}\n"
        "Environment=MISHE_SEED_HOME=/site\n")


def test_pinned_runtime_root_follows_the_installed_unit(tmp_path):
    from mishe_tauftauf import discovery

    unit_root = tmp_path / "units"
    _wake_unit(unit_root, "/rel/old/src")
    assert discovery._pinned_runtime_root(unit_root) == Path("/rel/old")
    # A re-pin of the unit changes the reading: the installed bytes decide, not a stale file.
    _wake_unit(unit_root, "/rel/new/src")
    assert discovery._pinned_runtime_root(unit_root) == Path("/rel/new")


def test_pinned_runtime_root_ignores_coordination_mode(tmp_path):
    import json
    from mishe_tauftauf import discovery

    unit_root = tmp_path / "units"
    _wake_unit(unit_root, "/rel/new/src")
    (tmp_path / "coordination-mode.json").write_text(json.dumps({"runtime": "/stale/snapshot"}))
    assert discovery._pinned_runtime_root(unit_root) == Path("/rel/new")


def test_pinned_runtime_root_unreadable_or_unpinned_is_none(tmp_path):
    from mishe_tauftauf import discovery

    unit_root = tmp_path / "units"
    assert discovery._pinned_runtime_root(unit_root) is None
    unit_root.mkdir()
    (unit_root / "mishe-wake@.service").write_text("[Service]\nExecStart=/bin/true\n")
    assert discovery._pinned_runtime_root(unit_root) is None
    _wake_unit(unit_root, "/rel/x/lib")  # PYTHONPATH that is not a src root
    assert discovery._pinned_runtime_root(unit_root) is None
