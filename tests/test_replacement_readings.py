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


from mishe_tauftauf.replacement_readings import (  # noqa: E402
    SESSION_SENSE_ID, judge_session_stability, probe_session_stability,
)


def test_session_stability_unknown_when_declared_or_probe_unreadable():
    assert judge_session_stability(None, ["plant"], {"1": "-900"})["state"] == "unknown"
    assert judge_session_stability("plant", None, {"1": "-900"})["state"] == "unknown"
    assert judge_session_stability("plant", ["plant"], None)["state"] == "unknown"
    got = judge_session_stability("plant", ["plant"], {"1": None})
    assert got["state"] == "unknown" and "pids=1" in got["sample"]


def test_session_stability_drift_when_declared_session_missing():
    got = judge_session_stability("plant", ["other"], {"2": "-900"})
    assert got["state"] == "drift" and got["id"] == SESSION_SENSE_ID
    assert "session-missing=plant" in got["sample"]


def test_session_stability_drift_when_server_lost_shield():
    got = judge_session_stability("plant", ["plant"], {"3": "0", "4": "-900"})
    assert got["state"] == "drift" and got["unshielded"] == ["3"]


def test_session_stability_verified_when_present_and_shielded():
    got = judge_session_stability("plant", ["plant"], {"3": "-900"})
    assert got["state"] == "verified" and got["unshielded"] == []


SOCK = "/tmp/tmux-1000/mishe-tauftauf/tmux-1000/default"


def test_probe_broken_tmux_is_unknown_not_calm():
    got = probe_session_stability("plant", SOCK, runner=lambda socket, args: None,
                                  shield_reader=lambda pid: "-900")
    assert got["state"] == "unknown"


def test_probe_no_server_running_is_drift_with_declared_session():
    def runner(socket, args):
        return _done(1, err="no server running on /tmp/tmux-1000/default\n")
    got = probe_session_stability("plant", SOCK, runner=runner, shield_reader=lambda pid: "-900")
    assert got["state"] == "drift" and "session-missing=plant" in got["sample"]


def test_probe_other_tmux_error_is_unknown():
    def runner(socket, args):
        return _done(1, err="error connecting to /tmp/tmux-1000/default (Permission denied)\n")
    assert probe_session_stability("plant", SOCK, runner=runner,
                                   shield_reader=lambda pid: "-900")["state"] == "unknown"


def test_probe_reads_shields_only_for_listed_session_servers():
    read = []

    def runner(socket, args):
        if args[-1] == "#{pid}":
            return _done(0, "11\n12\n")
        return _done(0, "plant\n")

    def shield(pid):
        read.append(pid)
        return "-900" if pid == "11" else "0"

    got = probe_session_stability("plant", SOCK, runner=runner, shield_reader=shield)
    assert read == ["11", "12"]
    assert got["state"] == "drift" and got["unshielded"] == ["12"]


def test_probe_without_plant_socket_is_unknown_never_default_server():
    def runner(socket, args):
        raise AssertionError("must not probe any server without the plant's socket")
    assert probe_session_stability("plant", None, runner=runner)["state"] == "unknown"


def test_probe_passes_plant_socket_to_every_tmux_call():
    seen = []

    def runner(socket, args):
        seen.append(socket)
        return _done(0, "plant\n") if args[-1] != "#{pid}" else _done(0, "")

    probe_session_stability("plant", SOCK, runner=runner, shield_reader=lambda pid: "-900")
    assert seen == [SOCK, SOCK]


def test_session_stability_unshielded_is_drift_even_without_declared_session():
    assert judge_session_stability(None, ["plant"], {"5": "200"})["state"] == "drift"
    assert judge_session_stability(None, ["plant"], {"5": "-900"})["state"] == "unknown"
