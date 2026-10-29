"""The checks-lane reader for the attempts of a pending wake that no record names."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from mishe_tauftauf import native_recovery as recovery
from mishe_tauftauf.feed import Feed

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "native-unseen-attempt.py"

RATE_LIMITED = {'kind': 'status', 'status': 429, 'statusText': 'Too Many Requests'}


def pending(home, role='docs'):
    """Make one wake pending for the role; return the plant's number for it."""
    (home / 'journals').mkdir(parents=True, exist_ok=True)
    entry = Feed(home).append('seed', f'seed wake {role} observation=1\nOwed work.\n')
    return entry.sequence


def attempt(root, name, wake, *, settled=False, status='unknown'):
    """One attempt directory with the recorded bytes of a failed wake."""
    directory = root / name
    (directory / 'effects').mkdir(parents=True)
    for filename in ('intents', 'starts', 'outcomes'):
        (directory / 'effects' / f'{filename}.jsonl').write_text('')
    (directory / 'result.json').write_text(json.dumps({
        'wake': wake, 'settled': settled, 'status': status, 'turns': 1, 'calls': 0,
        'fetch_ledger': [{'allowance': 1, 'workClass': '', 'dispatches': 1,
                          'upstream_failure': RATE_LIMITED}]}))
    (directory / 'journal.jsonl').write_text(json.dumps(
        {'kind': 'model_failure', 'fetch_ledger': [{'upstream_failure': RATE_LIMITED}]}) + '\n')
    return directory


def gate(home, role, branch, verdict='OK'):
    """The plant's own readiness record for the role."""
    path = home / 'checks' / f'native-gate-{role}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'at': '2026-10-21T00:31:56.488816+00:00', 'branch': branch,
        'engine': 'python' if branch == 'delegation' else 'omp', 'verdict': verdict}))


def run(home, *extra):
    """Run the check against a plant home and return the completed process."""
    return subprocess.run([sys.executable, str(CHECK), '--home', str(home),
                           '--repo', str(REPO), *extra],
                          capture_output=True, text=True)


def test_the_check_reports_an_attempt_no_plant_record_names(tmp_path):
    """Neither marker nor hold: the attempt is there and nothing names it."""
    wake = pending(tmp_path)
    attempt(tmp_path / 'native' / 'docs' / 'wakes', str(wake), wake)
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    result = run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'UNSEEN' in result.stdout
    assert str(wake) in result.stdout
    assert 'FAIL' in result.stdout


def test_the_busy_marker_names_the_attempt_so_the_reading_is_clean(tmp_path):
    """The guard refuses on the marker, so the attempt is not unnamed."""
    wake = pending(tmp_path)
    attempt(tmp_path / 'native' / 'docs' / 'wakes', str(wake), wake)
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    marker = recovery.busy_path(tmp_path, 'docs')
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(wake))
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'UNSEEN' not in result.stdout


def test_the_hold_record_names_the_attempt(tmp_path):
    """A hold means the plant sees the role as held, so nothing is unnamed."""
    wake = pending(tmp_path)
    attempt(tmp_path / 'native' / 'docs' / 'wakes', str(wake), wake)
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    recovery.write_blocked_state(tmp_path, 'docs', wake, verdict=recovery.RECONCILE,
                                 reason='an earlier attempt dispatched an effect')
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'UNSEEN' not in result.stdout


def test_a_hold_that_cannot_be_decoded_reads_unknown_not_clean(tmp_path):
    """Broken monitoring is UNKNOWN; the plant's reading is not ready either."""
    wake = pending(tmp_path)
    attempt(tmp_path / 'native' / 'docs' / 'wakes', str(wake), wake)
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    path = recovery.blocked_state_path(tmp_path, 'docs')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{not json', encoding='utf-8')
    result = run(tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'UNKNOWN' in result.stdout


def test_a_settled_attempt_is_not_reported(tmp_path):
    wake = pending(tmp_path)
    attempt(tmp_path / 'native' / 'docs' / 'wakes', str(wake), wake, settled=True,
            status='complete')
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'UNSEEN' not in result.stdout


def test_the_reading_finds_the_canary_layout_of_the_attempts(tmp_path):
    """The attempts live under a canary directory; the parent names the role."""
    wake = pending(tmp_path)
    attempt(tmp_path / 'native-canary' / 'docs' / 'wakes', str(wake), wake)
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    result = run(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'native-canary/docs/wakes' in result.stdout


def test_a_directory_whose_parent_names_no_role_is_not_read(tmp_path):
    """A fixture's attempt directory is not a role's attempts."""
    wake = pending(tmp_path, 'witness')
    attempt(tmp_path / 'native-canary' / 'failure-site' / 'wakes', str(wake), wake)
    gate(tmp_path, 'witness', 'tui')
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'UNSEEN' not in result.stdout


def test_a_native_role_whose_attempts_cannot_be_located_reads_unknown(tmp_path):
    """A retained attempt cannot be ruled out, so the reading is not clean."""
    pending(tmp_path)
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    result = run(tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'UNKNOWN' in result.stdout


def test_a_tui_role_reads_clean_without_an_attempt_directory(tmp_path):
    """A TUI role writes no attempt directory, so there is nothing to find."""
    pending(tmp_path, 'witness')
    gate(tmp_path, 'witness', 'tui')
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'OK' in result.stdout


def test_a_plant_without_a_tape_reads_unknown_not_clean(tmp_path):
    """An absent tape is an absent observation, not a plant with nothing owed."""
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    result = run(tmp_path)
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'tape' in result.stdout


def test_the_reading_follows_the_tree_under_test(tmp_path):
    """The verdict comes from the reviewed tree, not from an ambient package."""
    wake = pending(tmp_path)
    attempt(tmp_path / 'native' / 'docs' / 'wakes', str(wake), wake)
    gate(tmp_path, 'docs', 'delegation', 'GREEN')
    reference = tmp_path / 'reference'
    (reference / 'src').mkdir(parents=True)
    shutil.copytree(REPO / 'src' / 'mishe_tauftauf', reference / 'src' / 'mishe_tauftauf')
    module = reference / 'src' / 'mishe_tauftauf' / 'native_recovery.py'
    module.write_text(module.read_text() + "\n\ndef unseen_attempts(home, role, attempts, wake):\n"
                      "    return []\n")
    result = run(tmp_path, '--reference-src', str(reference / 'src'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'UNSEEN' not in result.stdout
