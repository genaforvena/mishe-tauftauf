"""Installing the guard is not activating it: the running sentinel must have loaded it.

The guard is a long-running process.  Replacing the file on disk leaves the
running boundary executing the code it loaded at start, so an installed fix
can sit unexercised while the plant is latched off by the old behaviour.
These tests pin the check that tells the two states apart.
"""
import datetime
import importlib.util
import pathlib

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "guard-activation" / "check-activation.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("check_activation", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_sentinel_that_started_before_the_bytes_was_written_is_not_activated(tmp_path, monkeypatch):
    """The case that latched the plant: the file was replaced, the process was not restarted."""
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started",
                        lambda pid: datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60))
    row = tool.report()
    assert row["activated"] is not True
    assert "before" in row["reason"]


def test_a_sentinel_that_started_after_the_bytes_was_written_is_activated(tmp_path, monkeypatch):
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started",
                        lambda pid: datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=60))
    row = tool.report()
    assert row["activated"] is True


def test_a_missing_sentinel_is_not_activated(tmp_path, monkeypatch):
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: None)
    row = tool.report()
    assert row["activated"] is not True
    assert "no running main process" in row["reason"]


def test_a_missing_installed_file_is_not_activated(tmp_path, monkeypatch):
    tool = load_tool()
    monkeypatch.setattr(tool, "GUARD_PATH", tmp_path / "absent.py")
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    row = tool.report()
    assert row["activated"] is not True
    assert "missing or unreadable" in row["reason"]


def test_an_unreadable_start_time_is_not_activated(tmp_path, monkeypatch):
    """An unknown start time must not be read as an activation."""
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started", lambda pid: None)
    row = tool.report()
    assert row["activated"] is not True
    assert "start time is unreadable" in row["reason"]


def test_the_exit_code_is_zero_only_when_activated(tmp_path, monkeypatch, capsys):
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started",
                        lambda pid: datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60))
    assert tool.main([]) == 1
    assert "activated" in capsys.readouterr().out


def test_the_apply_tool_reports_activation_after_a_write(tmp_path, monkeypatch):
    """An install must not be readable as a live change on its own."""
    apply_tool = pathlib.Path(__file__).resolve().parents[1] / "tools" / "guard-activation" / "apply.py"
    source = apply_tool.read_text(encoding="utf-8")
    assert "check-activation.py" in source
    assert "the running sentinel has not loaded these bytes" in source
