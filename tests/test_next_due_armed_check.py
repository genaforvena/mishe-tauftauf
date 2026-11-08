"""checks/next-due-armed.py: RED when the due-time timer is not armed; UNKNOWN when it cannot tell."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

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
