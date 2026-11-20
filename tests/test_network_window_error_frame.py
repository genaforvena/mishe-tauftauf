"""A pane that draws the view's WINDOW ERROR frame must not leave network-window GREEN.

The view keeps the window alive on a draw failure and says nothing in it is a reading; the
five-minute duty previously judged only the pane's process and code copy, so that loud UNKNOWN
was invisible to the signal meant to wake the resolver.
"""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


care = load(REPO / "checks" / "network-care.py", "network_care_error_frame_under_test")
view = load(REPO / "tools" / "network_view.py", "network_view_error_frame_under_test")

HEALTHY = "MISHE NETWORK  03:00:00Z\n  HOME INTERNET: OK\n"
ERROR_FRAME = ("MISHE NETWORK  03:00:00Z   the window cannot draw\n\n"
               "  WINDOW ERROR  ValueError: boom at line 42\n\n"
               "  Nothing in this pane is a network reading until this clears.\n")


def pane_runner(frame, capture_failure=None, capture_code=0):
    """tmux answers list-panes with a live view, and capture-pane with the supplied visible frame."""
    def run(argv, **kwargs):
        if argv[1] == "list-panes":
            return types.SimpleNamespace(returncode=0, stdout="0 1234 python3 tools/network_view.py",
                                         stderr="")
        if argv[1] == "capture-pane":
            if capture_failure is not None:
                raise capture_failure
            return types.SimpleNamespace(returncode=capture_code, stdout=frame, stderr="")
        raise AssertionError(f"unexpected command: {argv}")
    return run


def duty_verdict(monkeypatch, frame, **kwargs):
    monkeypatch.setattr(care.subprocess, "run", pane_runner(frame, **kwargs))
    monkeypatch.setattr(care, "window_process", lambda home, pid, cmd: 0)
    monkeypatch.setattr(care, "window_copy_stale", lambda home, repo: 0)
    return care.window("plants", Path("/tmp"), REPO)


def test_error_frame_is_red(monkeypatch, capsys):
    assert duty_verdict(monkeypatch, ERROR_FRAME) == 2
    assert "pane shows WINDOW ERROR" in capsys.readouterr().err


def test_healthy_frame_is_green(monkeypatch, capsys):
    assert duty_verdict(monkeypatch, HEALTHY) == 0
    assert capsys.readouterr().err == ""


def test_unreadable_pane_text_is_unknown_not_green(monkeypatch):
    assert duty_verdict(monkeypatch, ERROR_FRAME, capture_failure=OSError("tmux gone")) == 3
    assert duty_verdict(monkeypatch, ERROR_FRAME, capture_code=1) == 3


def test_empty_pane_is_unknown_not_green(monkeypatch):
    assert duty_verdict(monkeypatch, "") == 3


def test_view_error_frame_carries_the_duty_marker(monkeypatch):
    def broken_draw():
        raise ValueError("boom")
    monkeypatch.setattr(view, "draw", broken_draw)
    assert care.WINDOW_ERROR_MARKER in view.render_frame()
