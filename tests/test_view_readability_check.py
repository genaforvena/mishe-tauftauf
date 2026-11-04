"""checks/view-readability.py: RED on machine syntax in a pane, UNKNOWN when it cannot judge."""
import importlib.util
import subprocess
import sys
from pathlib import Path

from mishe_tauftauf import plant_view
from mishe_tauftauf.core.dispatch_record import valid_role
from mishe_tauftauf.plant_view_boundary import machine_syntax

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('view_readability', REPO / 'checks/view-readability.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def home_with_walls(tmp_path, *roles):
    (tmp_path / 'walls').mkdir()
    for role in roles:
        (tmp_path / 'walls' / f'{role}.md').write_text(f'# {role}\nplain prose\n')
    return tmp_path


def run(home, render):
    return check.evaluate(home, 100.0, render, machine_syntax, valid_role)


def test_real_panes_of_a_fresh_site_are_green(tmp_path):
    code, lines = run(home_with_walls(tmp_path, 'genome', 'health'), plant_view.render)
    assert code == 0 and '2 roles x 2 panes' in lines[0]


def test_controlled_failure_a_pane_that_dumps_json_is_red_and_names_the_line(tmp_path):
    def leaky(home, role, *, now, output):
        return 'fine\n{"ttft_ms": 3701,\n"type": "result"}' if role == 'health' and output else 'fine'
    code, lines = run(home_with_walls(tmp_path, 'genome', 'health'), leaky)
    assert code == 2 and any('health bottom' in line and 'ttft_ms' in line for line in lines)


def test_unrenderable_view_and_missing_walls_never_read_green(tmp_path):
    def broken(home, role, *, now, output):
        raise ValueError('boom')
    assert run(home_with_walls(tmp_path, 'genome'), broken)[0] == 3
    assert run(tmp_path / 'nowhere', plant_view.render)[0] == 3


def test_machine_syntax_beats_an_unrenderable_sibling(tmp_path):
    def mixed(home, role, *, now, output):
        if role == 'docs':
            raise OSError('gone')
        return '"a": 1'
    assert run(home_with_walls(tmp_path, 'docs', 'genome'), mixed)[0] == 2


def test_cli_exit_codes(tmp_path):
    home = home_with_walls(tmp_path, 'genome')
    ok = subprocess.run([sys.executable, str(REPO / 'checks/view-readability.py'), '--home', str(home),
                         '--repo', str(REPO)], capture_output=True, text=True)
    assert ok.returncode == 0 and 'human-readable' in ok.stdout
    empty = subprocess.run([sys.executable, str(REPO / 'checks/view-readability.py'), '--home', str(tmp_path / 'none'),
                            '--repo', str(REPO)], capture_output=True, text=True)
    assert empty.returncode == 3
