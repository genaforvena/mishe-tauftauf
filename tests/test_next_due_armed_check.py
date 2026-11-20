"""checks/next-due-armed.py: RED when the due-time timer is not armed; UNKNOWN when it cannot tell."""
import importlib.util
import json
import subprocess
import sys
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('next_due_armed', REPO / 'checks/next-due-armed.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NOW = 1_794_123_000.0


def home(tmp_path, at, marker=True):
    if at is not None:
        (tmp_path / 'next-due.json').write_text(json.dumps({'at': at}))
    if marker:
        (tmp_path / 'units-installed').write_text('x')
    return tmp_path


def test_armed_and_fresh_is_green(tmp_path):
    code, _ = check.evaluate(home(tmp_path, NOW + 600), NOW, ['mishe-next-due-abc.timer'])
    assert code == 0


def test_frozen_due_time_with_no_timer_is_red(tmp_path):
    code, lines = check.evaluate(home(tmp_path, NOW - 3600, marker=False), NOW, [])
    assert code == 2
    assert any('never armed' in line for line in lines)
    assert any('units-installed' in line for line in lines)


def test_overdue_due_with_armed_retry_timer_is_green(tmp_path):
    # arm.py re-arms an overdue target on a 120 s backoff; the armed retry timer is the evidence, not a RED.
    code, lines = check.evaluate(home(tmp_path, NOW - 3600), NOW, ['mishe-next-due-abc.timer'])
    assert code == 0, lines


def test_future_due_without_timer_is_red(tmp_path):
    code, lines = check.evaluate(home(tmp_path, NOW + 600), NOW, [])
    assert code == 2 and 'no mishe-next-due timer' in lines[0]


def test_unreadable_next_due_is_unknown(tmp_path):
    code, _ = check.evaluate(home(tmp_path, None), NOW, ['mishe-next-due-abc.timer'])
    assert code == 3


def test_timer_listing_unavailable_is_unknown_not_green(tmp_path):
    code, _ = check.evaluate(home(tmp_path, NOW + 600), NOW, None)
    assert code == 3


def test_cli_exit_code_when_home_missing(tmp_path):
    done = subprocess.run([sys.executable, str(REPO / 'checks/next-due-armed.py'), '--home', str(tmp_path / 'nope')],
                          capture_output=True, text=True)
    assert done.returncode in (2, 3)


def timer_name(path):
    tag = sha256(str(path.resolve()).encode()).hexdigest()[:8]
    return f"mishe-next-due-{tag}-123456789abc.timer"


@pytest.mark.parametrize(("listing", "manager_status", "expected"), [
    ("neighbour-only", 0, 2),
    ("local", 0, 0),
    ("neighbour-only", 1, 3),
])
def test_cli_checks_arming_for_this_home(tmp_path, monkeypatch, listing, manager_status, expected):
    local_home = home(tmp_path, NOW + 600)
    timers = [timer_name(tmp_path.with_name(tmp_path.name + "-other"))]
    if listing == "local":
        timers.append(timer_name(local_home))

    def list_timers(argv, **kwargs):
        rows = [
            f"Fri 2026-11-20 02:48:29 UTC 1min 25s - - {name} {name.removesuffix('.timer')}.service"
            for name in timers
            if argv[-1] == "--plain" or fnmatchcase(name, argv[-1])
        ]
        return subprocess.CompletedProcess(argv, manager_status, "\n".join(rows), "")

    monkeypatch.setattr(check.time, "time", lambda: NOW)
    monkeypatch.setattr(check.subprocess, "run", list_timers)
    assert check.main(["--home", str(local_home)]) == expected


def test_timer_listing_waits_for_scheduler_replacement_lock(tmp_path, monkeypatch):
    # The scheduler stops and starts the timer under next-due.lock; sampling outside it sees a false "never armed".
    locked = []
    real_flock = check.fcntl.flock

    def recording_flock(fd, op):
        locked.append(op)
        return real_flock(fd, op)

    timer = timer_name(tmp_path)

    class Done:
        returncode = 0
        stdout = f'Thu 2026 {timer} {timer.removesuffix(".timer")}.service\n'
        stderr = ''

    monkeypatch.setattr(check.fcntl, 'flock', recording_flock)
    monkeypatch.setattr(check.subprocess, 'run', lambda *a, **k: Done())
    code, timers = check.list_timers(tmp_path)
    assert code == 0
    assert timers == [timer]
    assert locked and locked[0] == check.fcntl.LOCK_EX
    assert (tmp_path / check.LOCK_NAME).exists()
