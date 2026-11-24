import importlib.util
import json
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "view_window_coverage", Path(__file__).resolve().parent.parent / "checks" / "view-window-coverage.py")
vwc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vwc)


def entry(name, view):
    return {"session": "outer", "name": name, "command": ["python3", f"tools/{view}"]}


def running(*cmds):
    """Stub for pane_commands: every pid reports the given command lines."""
    return lambda pid: list(cmds)


def test_every_view_with_a_live_window_running_it_is_green():
    windows = [entry("health", "health_view.py"), entry("delivery", "delivery_view.py")]
    live = {"health": [(False, 10)], "delivery": [(False, 20)]}
    cmds = {10: ["python3 tools/health_view.py"], 20: ["sh -c while :; do python3 tools/delivery_view.py; sleep 60; done"]}
    assert vwc.evaluate(["health_view.py", "delivery_view.py"], windows, live,
                        lambda pid: cmds[pid]) == []


def test_loop_wrapper_command_still_counts_as_running_the_view():
    windows = [{"session": "outer", "name": "delivery",
                "command": ["sh", "-c", "while :; do python3 tools/delivery_view.py; sleep 60; done"]}]
    assert vwc.evaluate(["delivery_view.py"], windows, {"delivery": [(False, 1)]},
                        running("sh -c while :; do python3 tools/delivery_view.py; sleep 60; done")) == []


def test_live_window_running_something_else_is_red():
    # a stale shell in the window: pane is alive but no process names the view
    gaps = vwc.evaluate(["delivery_view.py"], [entry("delivery", "delivery_view.py")],
                        {"delivery": [(False, 1)]}, running("bash", "sleep 300"))
    assert len(gaps) == 1 and "no process in it runs tools/delivery_view.py" in gaps[0]


def test_identity_checks_every_live_pane_not_only_the_first():
    cmds = {1: ["bash"], 2: ["python3 tools/delivery_view.py"]}
    assert vwc.evaluate(["delivery_view.py"], [entry("delivery", "delivery_view.py")],
                        {"delivery": [(False, 1), (False, 2)]}, lambda pid: cmds[pid]) == []


def test_dead_pane_does_not_satisfy_identity():
    commands_for = []

    def commands(pid):
        commands_for.append(pid)
        return ["python3 tools/delivery_view.py"]

    gaps = vwc.evaluate(["delivery_view.py"], [entry("delivery", "delivery_view.py")],
                        {"delivery": [(True, 1)]}, commands)
    assert "dead panes" in gaps[0] and commands_for == []


def test_view_without_registry_entry_is_red_and_names_the_fix():
    gaps = vwc.evaluate(["delivery_view.py"], [entry("health", "health_view.py")],
                        {"health": [(False, 1)]}, running("python3 tools/health_view.py"))
    assert len(gaps) == 1 and "no entry" in gaps[0] and "delivery_view.py" in gaps[0]


def test_registered_window_missing_or_dead_is_red():
    windows = [entry("delivery", "delivery_view.py")]
    assert "missing" in vwc.evaluate(["delivery_view.py"], windows, {}, running())[0]
    assert "dead panes" in vwc.evaluate(["delivery_view.py"], windows, {"delivery": [(True, 1)]}, running())[0]


def test_inner_session_entries_do_not_count():
    reg = [{"session": "inner", "name": "x", "command": ["python3", "tools/delivery_view.py"]}]
    assert vwc.registry_windows(reg) == []


def test_pane_commands_reads_own_and_direct_child_command_lines(tmp_path, monkeypatch):
    # Fake /proc: pid 100 (the loop shell) has child 101 (the view) and an unrelated pid 102.
    proc = tmp_path / "proc"
    for pid, ppid, argv in [(100, 1, [b"sh", b"-c", b"loop"]), (101, 100, [b"python3", b"tools/x_view.py"]),
                            (102, 1, [b"other"])]:
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(b"\0".join(argv) + b"\0")
        (proc / str(pid) / "stat").write_text(f"{pid} (name) S {ppid} 0 0")
    real_path = vwc.Path
    monkeypatch.setattr(vwc, "Path", lambda p: proc if p == "/proc" else real_path(p))
    lines = vwc.pane_commands(100)
    assert lines[0] == "sh -c loop" and "python3 tools/x_view.py" in lines and "other" not in " ".join(lines)


def run_main(tmp_path, monkeypatch, tmux, registry="ok", commands=None):
    base = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
    home, repo = base / "home", base / "repo"
    (home / "operator").mkdir(parents=True)
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "delivery_view.py").write_text("")
    if registry == "ok":
        (home / "operator" / "window-registry.json").write_text(json.dumps(
            {"windows": [entry("delivery", "delivery_view.py")]}))
    monkeypatch.setattr(vwc.subprocess, "run", tmux)
    monkeypatch.setattr(vwc, "pane_commands", commands or running("python3 tools/delivery_view.py"))
    return vwc.main(["--home", str(home), "--repo", str(repo)])


def completed(code, out="", err=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, code, out, err)


def test_controlled_failure_missing_window_is_red(tmp_path, monkeypatch):
    assert run_main(tmp_path, monkeypatch, completed(0, "other\t0\t0\t1\n")) == 2


def test_controlled_failure_window_running_stale_shell_is_red(tmp_path, monkeypatch):
    assert run_main(tmp_path, monkeypatch, completed(0, "delivery\t0\t0\t1\n"),
                    commands=running("bash")) == 2


def test_green_when_window_live_and_running_the_view(tmp_path, monkeypatch):
    assert run_main(tmp_path, monkeypatch, completed(0, "delivery\t0\t0\t1\n")) == 0


def test_unknown_when_tmux_unreachable_or_rows_malformed_or_registry_missing(tmp_path, monkeypatch):
    assert run_main(tmp_path, monkeypatch, completed(1, "", "no server running")) == 3
    assert run_main(tmp_path, monkeypatch, completed(0, "garbage\n")) == 3
    assert run_main(tmp_path, monkeypatch, completed(0, "delivery\t0\t0\t1\n"), registry="none") == 3


def test_unknown_when_a_live_pane_identity_is_unreadable(tmp_path, monkeypatch):
    def gone(pid):
        raise OSError("no such process")
    assert run_main(tmp_path, monkeypatch, completed(0, "delivery\t0\t0\t1\n"), commands=gone) == 3
