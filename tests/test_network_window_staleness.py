"""A network window that runs an old copy of the view must not look healthy.

The view records the start of its running copy in SITE/network/window.json and re-executes itself
when its own files change; the window detector (checks/network-care.py) turns a copy started before the
last commit touching the view into RED with the exact respawn command.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
care = load(REPO / "checks" / "network-care.py", "network_care_under_test")
view = load(REPO / "tools" / "network_view.py", "network_view_under_test")


def write_state(home, fingerprint):
    (home / "network").mkdir(parents=True, exist_ok=True)
    (home / "network" / "window.json").write_text(json.dumps({"pid": 1, "started": time.time(), "code": "x",
                                                              "fingerprint": fingerprint}))


def test_missing_state_is_stale(tmp_path, capsys):
    assert care.window_copy_stale(tmp_path, REPO) == 2
    assert "window.json missing" in capsys.readouterr().err


def test_copy_with_other_code_is_stale(tmp_path, capsys):
    write_state(tmp_path, "0" * 64)
    assert care.window_copy_stale(tmp_path, REPO) == 2
    assert "differs from the files on disk" in capsys.readouterr().err


def test_copy_with_the_code_on_disk_is_current(tmp_path):
    write_state(tmp_path, view.code_fingerprint(care_paths()))
    assert care.window_copy_stale(tmp_path, REPO) == 0


def test_committing_unchanged_code_is_not_stale(tmp_path):
    # the fingerprint is of content, not of commit time: a commit that changes nothing leaves the copy current
    write_state(tmp_path, view.code_fingerprint(care_paths()))
    assert care.window_copy_stale(tmp_path, REPO) == 0


def care_paths():
    return [REPO / "tools" / "network_view.py", REPO / "tools" / "network_boundary.py"]


def test_window_state_is_written_by_the_view(tmp_path, monkeypatch):
    monkeypatch.setattr(view, "WINDOW_STATE", tmp_path / "network" / "window.json")
    view.report_window_state()
    state = json.loads((tmp_path / "network" / "window.json").read_text())
    assert state["pid"] > 0 and state["started"] <= time.time()
    assert state["fingerprint"] == view.code_fingerprint()


def test_unchanged_own_files_do_not_reexec(monkeypatch):
    calls = []
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    stamp = view.own_stamp()
    assert view.maybe_reexec(stamp) == stamp
    assert calls == []


def test_changed_file_that_does_not_compile_is_not_run(tmp_path, monkeypatch):
    calls = []
    fake = tmp_path / "network_view.py"
    fake.write_text("def broken(:\n")
    monkeypatch.setattr(view, "OWN_FILES", (fake, tmp_path / "boundary.py"))
    monkeypatch.setattr(view.os, "execv", lambda *a: calls.append(a))
    old = (0, 0)
    assert view.maybe_reexec(old) == old  # keeps the old stamp, retried later
    assert calls == []
