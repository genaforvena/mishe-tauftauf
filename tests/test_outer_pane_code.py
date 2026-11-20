"""A long-lived outer pane must not run a view from a superseded release.

The check compares decoded values: the release named by the pane's cwd, the
view the registry says the window runs, and the script bytes at the pin.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "src"))
check = load(REPO / "checks" / "outer-pane-code.py", "outer_pane_code_under_test")
proc = load(REPO / "src" / "mishe_tauftauf" / "proc_table.py", "proc_table_under_test")

PIN = "a" * 40
OLD = "b" * 40
SCRIPT = "map_view.py"
WINDOW = {
    "session": "outer", "name": "senses-map", "kind": "view",
    "command": ["python3", f"tools/{SCRIPT}"],
}
ROWS = {"senses-map": [("0", "1234")]}


def write_scripts(tmp_path, pinned="pinned code", running="pinned code",
                  release=OLD, in_tree=False):
    (tmp_path / "releases" / PIN / "tools").mkdir(parents=True)
    (tmp_path / "releases" / PIN / "tools" / SCRIPT).write_text(pinned)
    target = (tmp_path / "repo" / "tools") if in_tree else (tmp_path / "releases" / release / "tools")
    target.mkdir(parents=True, exist_ok=True)
    (target / SCRIPT).write_text(running)
    return tmp_path


def run_check(tmp_path, cwds, entries=(WINDOW,), rows=ROWS):
    return check.evaluate(tmp_path, list(entries), [SCRIPT], rows, cwds, PIN)


def release_cwd(tmp_path, name):
    return str(tmp_path / "releases" / name)


def test_a_pane_with_different_script_bytes_is_red(tmp_path):
    write_scripts(tmp_path, running="old code")
    findings = run_check(tmp_path, {"1234": release_cwd(tmp_path, OLD)})
    assert [sev for sev, _ in findings] == [2]
    assert OLD in findings[0][1] and PIN in findings[0][1]
    assert "respawn-pane" in findings[0][1]


def test_identical_bytes_in_an_old_release_are_green(tmp_path):
    # a promotion that did not change the view is not a stale pane
    write_scripts(tmp_path)
    assert run_check(tmp_path, {"1234": release_cwd(tmp_path, OLD)}) == []


def test_a_working_tree_pane_is_not_judged_here(tmp_path):
    # delivery and wake-view are restored from the repo root by design; they
    # are not release-pinned, so this detector must not mint a false RED
    write_scripts(tmp_path, running="uncommitted code", in_tree=True)
    assert run_check(tmp_path, {"1234": str(tmp_path / "repo")}) == []

def test_unreadable_pane_cwd_is_unknown_not_green(tmp_path):
    write_scripts(tmp_path)
    assert [sev for sev, _ in run_check(tmp_path, {})] == [3]


def test_unreadable_pinned_script_is_unknown(tmp_path):
    write_scripts(tmp_path)
    (tmp_path / "releases" / PIN / "tools" / SCRIPT).unlink()
    assert [sev for sev, _ in run_check(tmp_path, {"1234": release_cwd(tmp_path, OLD)})] == [3]


def test_ambiguous_release_in_pane_cwd_is_unknown(tmp_path):
    write_scripts(tmp_path)
    cwd = str(tmp_path / "releases" / OLD) + " and " + str(tmp_path / "releases" / ("c" * 40))
    assert [sev for sev, _ in run_check(tmp_path, {"1234": cwd})] == [3]


def test_a_window_with_no_live_pane_is_not_judged_here(tmp_path):
    write_scripts(tmp_path, running="old code")
    assert run_check(tmp_path, {}, rows={}) == []


def test_a_window_without_a_known_view_script_is_skipped(tmp_path):
    write_scripts(tmp_path, running="old code")
    entry = {"session": "outer", "name": "budget", "command": ["sh", "budget_pane.sh"]}
    assert run_check(tmp_path, {"1234": release_cwd(tmp_path, OLD)}, entries=(entry,)) == []


def test_pane_cwd_boundary_reads_a_live_process_and_refuses_a_dead_one():
    assert proc.read_pane_cwd(str(os.getpid())) == os.readlink(f"/proc/{os.getpid()}/cwd")
    assert proc.read_pane_cwd("999999999") == ""


def site_for_main(tmp_path):
    """Minimal site with one registered outer view window and a pinned script."""
    (tmp_path / "operator").mkdir(parents=True)
    (tmp_path / "runtime").mkdir(parents=True)
    (tmp_path / "releases" / PIN / "tools").mkdir(parents=True)
    (tmp_path / "releases" / PIN / "tools" / SCRIPT).write_text("pinned code")
    (tmp_path / "runtime" / "current").symlink_to(tmp_path / "releases" / PIN)
    (tmp_path / "operator" / "window-registry.json").write_text(
        '{"windows": [' + json.dumps(WINDOW) + ']}')
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / SCRIPT).write_text("pinned code")
    return repo


def _tmux_run(stderr):
    return lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=stderr, stdout="")


def test_dead_tmux_server_is_unknown_not_green(tmp_path, monkeypatch, capsys):
    repo = site_for_main(tmp_path)
    monkeypatch.setattr(check.subprocess, "run", _tmux_run("no server running on /tmp/tmux-1000/default"))
    assert check.main(["--home", str(tmp_path), "--repo", str(repo)]) == 3
    err = capsys.readouterr().err
    assert "tmux server unreachable; cannot tell" in err
    assert "GREEN" not in err


def test_error_connecting_tmux_is_unknown(tmp_path, monkeypatch):
    repo = site_for_main(tmp_path)
    monkeypatch.setattr(check.subprocess, "run", _tmux_run("error connecting to /tmp/tmux-1000/default"))
    assert check.main(["--home", str(tmp_path), "--repo", str(repo)]) == 3


def test_real_missing_window_message_stays_a_quiet_pass(tmp_path, monkeypatch, capsys):
    repo = site_for_main(tmp_path)
    monkeypatch.setattr(check.subprocess, "run", _tmux_run("can't find window: senses-map"))
    assert check.main(["--home", str(tmp_path), "--repo", str(repo)]) == 0
    assert "GREEN" in capsys.readouterr().out


def test_unrecognised_tmux_failure_is_unknown_not_green(tmp_path, monkeypatch, capsys):
    repo = site_for_main(tmp_path)
    for stderr in ("permission denied", "", "can't find session: plants"):
        monkeypatch.setattr(check.subprocess, "run", _tmux_run(stderr))
        assert check.main(["--home", str(tmp_path), "--repo", str(repo)]) == 3, stderr
        out = capsys.readouterr()
        assert "GREEN" not in out.out + out.err, stderr
        assert "cannot tell" in out.err, stderr
