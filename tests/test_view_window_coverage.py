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


def test_every_view_with_a_live_window_is_green():
    windows = [entry("health", "health_view.py"), entry("delivery", "delivery_view.py")]
    live = {"health": [False], "delivery": [False]}
    assert vwc.evaluate(["health_view.py", "delivery_view.py"], windows, live) == []


def test_loop_wrapper_command_still_counts_as_running_the_view():
    windows = [{"session": "outer", "name": "delivery",
                "command": ["sh", "-c", "while :; do python3 tools/delivery_view.py; sleep 60; done"]}]
    assert vwc.evaluate(["delivery_view.py"], windows, {"delivery": [False]}) == []


def test_view_without_registry_entry_is_red_and_names_the_fix():
    gaps = vwc.evaluate(["delivery_view.py"], [entry("health", "health_view.py")], {"health": [False]})
    assert len(gaps) == 1 and "no entry" in gaps[0] and "delivery_view.py" in gaps[0]


def test_registered_window_missing_or_dead_is_red():
    windows = [entry("delivery", "delivery_view.py")]
    assert "missing" in vwc.evaluate(["delivery_view.py"], windows, {})[0]
    assert "dead panes" in vwc.evaluate(["delivery_view.py"], windows, {"delivery": [True]})[0]


def test_inner_session_entries_do_not_count():
    reg = [{"session": "inner", "name": "x", "command": ["python3", "tools/delivery_view.py"]}]
    assert vwc.registry_windows(reg) == []


def run_main(tmp_path, monkeypatch, tmux, registry="ok"):
    base = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
    home, repo = base / "home", base / "repo"
    (home / "operator").mkdir(parents=True)
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "delivery_view.py").write_text("")
    if registry == "ok":
        (home / "operator" / "window-registry.json").write_text(json.dumps(
            {"windows": [entry("delivery", "delivery_view.py")]}))
    monkeypatch.setattr(vwc.subprocess, "run", tmux)
    return vwc.main(["--home", str(home), "--repo", str(repo)])


def completed(code, out="", err=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, code, out, err)


def test_controlled_failure_missing_window_is_red(tmp_path, monkeypatch):
    assert run_main(tmp_path, monkeypatch, completed(0, "other\t0\t0\n")) == 2


def test_green_when_window_live(tmp_path, monkeypatch):
    assert run_main(tmp_path, monkeypatch, completed(0, "delivery\t0\t0\n")) == 0


def test_unknown_when_tmux_unreachable_or_rows_malformed_or_registry_missing(tmp_path, monkeypatch):
    assert run_main(tmp_path, monkeypatch, completed(1, "", "no server running")) == 3
    assert run_main(tmp_path, monkeypatch, completed(0, "garbage\n")) == 3
    assert run_main(tmp_path, monkeypatch, completed(0, "delivery\t0\t0\n"), registry="none") == 3
