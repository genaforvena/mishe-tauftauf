"""The window detector must judge the process running in the pane now, not the command the pane started with.

A controlled process whose command line names network_view.py stands in for the view; the detector
returns GREEN only when that process is the pid the view recorded, and RED for any other process.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


care = load(REPO / "checks" / "network-care.py", "network_care_identity_under_test")


def fake_view(tmp_path):
    """A sleeping process whose argv names network_view.py, like the real window."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "network_view.py"])
    for _ in range(100):
        if care.process_cmdline(proc.pid) and "network_view.py" in care.process_cmdline(proc.pid):
            break
        time.sleep(0.02)
    return proc


def write_state(home, pid):
    (home / "network").mkdir(parents=True, exist_ok=True)
    (home / "network" / "window.json").write_text(json.dumps({"pid": pid, "started": time.time(),
                                                              "code": "x", "fingerprint": "0" * 64}))


def test_recorded_view_process_is_green(tmp_path):
    proc = fake_view(tmp_path)
    try:
        write_state(tmp_path, proc.pid)
        assert care.window_process(tmp_path, str(proc.pid), "python3 network_view.py") == 0
    finally:
        proc.kill()


def test_pane_running_another_process_is_red(tmp_path, capsys):
    proc = fake_view(tmp_path)
    try:
        # the pane's start command still names the view, but the process now in it is a different one
        write_state(tmp_path, proc.pid)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "other.py"])
        try:
            assert care.window_process(tmp_path, str(other.pid), "python3 network_view.py") == 2
            assert "not the network view" in capsys.readouterr().err
        finally:
            other.kill()
    finally:
        proc.kill()


def test_state_naming_another_pid_is_red(tmp_path, capsys):
    proc = fake_view(tmp_path)
    try:
        write_state(tmp_path, os.getpid())  # fingerprint of some other process
        assert care.window_process(tmp_path, str(proc.pid), "python3 network_view.py") == 2
        assert "describes another process" in capsys.readouterr().err
    finally:
        proc.kill()


def test_unreadable_pane_process_is_unknown(tmp_path):
    assert care.window_process(tmp_path, "999999999", "python3 network_view.py") == 3
    assert care.window_process(tmp_path, "", "python3 network_view.py") == 3
