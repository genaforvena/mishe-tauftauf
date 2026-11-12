"""The window detector must judge the process running in the pane now, not the command the pane started with.

A controlled process stands in for the view: a real script file named network_view.py run by an
interpreter. The detector returns GREEN only when that process is the pid the view recorded, its argv is
that script (not code that merely mentions the name), and its start time matches the recorded start.
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

SLEEPER = "import time; time.sleep(60)"


def fake_view(tmp_path):
    """A sleeping process whose script is a file named network_view.py, like the real window."""
    script = tmp_path / "network_view.py"
    script.write_text(SLEEPER + "\n")
    proc = subprocess.Popen([sys.executable, str(script)])
    wait_argv(proc.pid, lambda argv: argv and argv[-1] == str(script))
    return proc


def wait_argv(pid, ok):
    for _ in range(100):
        if ok(care.process_argv(pid)):
            return
        time.sleep(0.02)
    raise AssertionError(f"pid {pid} never showed the expected argv")


def write_state(home, pid, started=None):
    (home / "network").mkdir(parents=True, exist_ok=True)
    (home / "network" / "window.json").write_text(json.dumps({
        "pid": pid, "started": time.time() if started is None else started,
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
        other = subprocess.Popen([sys.executable, "-c", SLEEPER, "other.py"])
        try:
            assert care.window_process(tmp_path, str(other.pid), "python3 network_view.py") == 2
            assert "not the network view" in capsys.readouterr().err
        finally:
            other.kill()
    finally:
        proc.kill()


def test_argv_mentioning_the_view_later_is_red(tmp_path, capsys):
    # the counterexample: an unrelated -c program with network_view.py as an extra argument
    write_state(tmp_path, os.getpid())
    stand_in = subprocess.Popen([sys.executable, "-c", SLEEPER, "network_view.py"])
    try:
        wait_argv(stand_in.pid, lambda argv: argv and "network_view.py" in argv)
        write_state(tmp_path, stand_in.pid)
        assert care.window_process(tmp_path, str(stand_in.pid), "python3 network_view.py") == 2
        assert "not the network view" in capsys.readouterr().err
    finally:
        stand_in.kill()


def test_state_naming_another_pid_is_red(tmp_path, capsys):
    proc = fake_view(tmp_path)
    try:
        write_state(tmp_path, os.getpid())  # fingerprint of some other process
        assert care.window_process(tmp_path, str(proc.pid), "python3 network_view.py") == 2
        assert "describes another process" in capsys.readouterr().err
    finally:
        proc.kill()


def test_reused_pid_with_old_start_time_is_red(tmp_path, capsys):
    # the recorded view started an hour before the process now holding its pid
    proc = fake_view(tmp_path)
    try:
        write_state(tmp_path, proc.pid, started=time.time() - 3600)
        assert care.window_process(tmp_path, str(proc.pid), "python3 network_view.py") == 2
        assert "pid was reused" in capsys.readouterr().err
    finally:
        proc.kill()


def test_unreadable_pane_process_is_unknown(tmp_path):
    assert care.window_process(tmp_path, "999999999", "python3 network_view.py") == 3
    assert care.window_process(tmp_path, "", "python3 network_view.py") == 3


def test_is_view_argv_rules():
    assert care.is_view_argv(["python3", "tools/network_view.py"])
    assert care.is_view_argv(["/usr/bin/python3", "-I", "/x/tools/network_view.py"])
    assert not care.is_view_argv(["python3", "-c", "print(1)", "network_view.py"])
    assert not care.is_view_argv(["python3", "-m", "network_view.py"])
    assert not care.is_view_argv(["python3", "other.py", "network_view.py"])
    assert not care.is_view_argv(["sleep", "network_view.py"])
    assert not care.is_view_argv([])



def test_exec_reload_keeps_the_recorded_start_matching_the_live_pid(tmp_path):
    """The view reloads itself with os.execv when its code changes. The pid and kernel start time survive
    the reload, so the recorded "started" must be the kernel start, not the moment the new image loaded."""
    tools = REPO / "tools"
    wrapper = tmp_path / "reload_wrapper.py"
    wrapper.write_text(
        "import os, sys, time, json\n"
        f"sys.path.insert(0, {str(tools)!r})\n"
        "import network_boundary as nb\n"
        "import network_view as v\n"
        "v.report_window_state()\n"
        "if len(sys.argv) == 1:\n"
        "    time.sleep(1.5)  # the new image loads well after the process started\n"
        "    os.execv(sys.executable, [sys.executable, __file__, 'reloaded'])\n"
        "state = json.loads(v.WINDOW_STATE.read_text())\n"
        "print(json.dumps({'pid': os.getpid(), 'recorded_pid': state['pid'], 'started': state['started'],\n"
        "                  'kernel_start': nb.process_start(os.getpid())}))\n"
    )
    env = dict(os.environ, MISHE_TAUFTAUF_HOME=str(tmp_path))
    out = subprocess.run([sys.executable, str(wrapper)], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    rec = json.loads(out.stdout.strip().splitlines()[-1])
    assert rec["recorded_pid"] == rec["pid"]
    assert rec["kernel_start"] is not None
    assert abs(rec["started"] - rec["kernel_start"]) < 0.5, rec
