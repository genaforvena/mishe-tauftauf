"""checks/pin-lag.py: the newest run log's stage and failure are the run's own outcome, not the wrapper's legend.

A promote that is still running has no record in its (empty, until return) wrapper log, so the
check must read the live ``work/releases`` record it has already written rather than report
"unknown" while a delivery is actively working.
"""
import importlib.util
import subprocess
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pin_lag', REPO / 'checks/pin-lag.py')
lag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lag)

PROMOTE_LEGEND = ('[promote exit 3: legend — 0 delivered, 2 could not start, '
                  '3 not delivered (check failed or rolled back; see work/releases), 6 inside a wake]')
DELIVER_LEGEND = ('[deliver.sh exit 2: 0 verified-or-nothing-to-do, 2 retarget/check failed, '
                  '3 review not clear, 4 dispatch lock busy, 5 apply/verify did not deliver, 6 inside a wake]')


def record(phase, sha='b0cec3c75de7ade970827bb19ef671e0cd7257cc'):
    return json.dumps({'version': 1, 'phase': phase, 'sha': sha, 'activated': False}) + '\n'


def write_live_record(home, phase, *, created_at, sha='180073379418e235ecede094a69f34a8df4c6a66'):
    root = home / 'work' / 'releases'
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{sha}-{int(created_at)}.json').write_text(json.dumps(
        {'version': 1, 'id': f'{sha}-{int(created_at)}', 'phase': phase, 'sha': sha, 'created_at': created_at}))
    return root / f'{sha}-{int(created_at)}.json'


def log_at(path, text, *, mtime):
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def test_a_check_failed_promote_reports_the_record_phase_not_the_legend(tmp_path):
    log = tmp_path / 'deliver-run-1.log'
    log.write_text(record('check-failed') + PROMOTE_LEGEND + '\n')
    assert lag.last_stage_and_failure(tmp_path, log) == ('check', 'check failed (b0cec3c7)')


def test_a_delivered_promote_has_no_failure(tmp_path):
    log = tmp_path / 'deliver-run-2.log'
    log.write_text(record('delivered') + '[promote exit 0: legend — 0 delivered, 6 inside a wake]\n')
    assert lag.last_stage_and_failure(tmp_path, log) == ('verify', '')


def test_a_rolled_back_promote_names_the_rollback(tmp_path):
    log = tmp_path / 'deliver-run-3.log'
    log.write_text(record('rolled-back', sha='5d608cd9779fadd1f425852eca74fd45588d1f11') + PROMOTE_LEGEND + '\n')
    assert lag.last_stage_and_failure(tmp_path, log) == ('verify', 'observation failed, rolled back (5d608cd9)')


def test_a_still_running_promote_reports_the_live_record_phase(tmp_path):
    log = log_at(tmp_path / 'deliver-run-4.log', '', mtime=1_000.0)
    write_live_record(tmp_path, 'waiting-for-wakes', created_at=1_010.0)
    assert lag.last_stage_and_failure(tmp_path, log) == ('activate', '')


def test_a_still_running_promote_with_no_record_is_unknown(tmp_path):
    log = log_at(tmp_path / 'deliver-run-4b.log', '', mtime=1_000.0)
    assert lag.last_stage_and_failure(tmp_path, log) == ('unknown', '')


def test_an_older_record_is_not_attributed_to_the_current_run(tmp_path):
    log = log_at(tmp_path / 'deliver-run-4c.log', '', mtime=2_000.0)
    write_live_record(tmp_path, 'check-failed', created_at=1_000.0)
    assert lag.last_stage_and_failure(tmp_path, log) == ('unknown', '')


def test_the_legend_alone_is_never_a_failure(tmp_path):
    log = tmp_path / 'deliver-run-5.log'
    log.write_text(DELIVER_LEGEND + '\n')
    assert lag.last_stage_and_failure(tmp_path, log) == ('unknown', '')


def test_a_legacy_log_still_scans_its_own_lines(tmp_path):
    log = tmp_path / 'deliver-run-6.log'
    log.write_text('check started\nverify exit 5')  # no JSON record: the retired deliver.sh path
    assert lag.last_stage_and_failure(tmp_path, log) == ('verify', 'verify exit 5')


# --- evaluate(): a promotion in flight is progress, not a lagging pin -------------------------
#
# Measured promotion durations (duties.json note): 20.9, 28.4, 35.8, 47.9 and 58.9 min. The oldest
# unreleased commit therefore passes --green-age (60 min) while a perfectly healthy promote is
# still checking, so the age alone cannot decide. The check must see the live run instead.

NOW = 1_794_900_000.0


def make_repo(tmp_path, commit_times):
    """A real clone under tmp_path with one commit per timestamp; returns (repo, home, shas)."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    base = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def run(*args, env=None):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True,
                              env=env if env is not None else base)
    run('init', '-q', '-b', 'main')
    run('config', 'user.name', 'Pin Lag Test')
    run('config', 'user.email', 'pin-lag@example.invalid')
    shas = []
    for i, ts in enumerate(commit_times):
        (repo / f'file{i}').write_text(str(i))
        run('add', '-A')
        stamp = f'{int(ts)} +0000'
        run('commit', '-m', f'commit {i}', env=dict(base, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp))
        shas.append(run('rev-parse', 'HEAD').stdout.strip())
    home = repo / '.mishe-tauftauf'
    (home / 'runtime').mkdir(parents=True)
    return repo, home, shas


def pin_to(home, sha):
    link = home / 'runtime' / 'current'
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(Path('..', '..', 'releases', sha))


def test_a_promotion_in_flight_reads_green_and_names_its_stage(tmp_path):
    _, home, shas = make_repo(tmp_path, [NOW - 3000, NOW - 1800])  # 30 min lag: past green, inside red
    pin_to(home, shas[0])
    write_live_record(home, 'checking', created_at=NOW - 60, sha=shas[1])
    code, detail = lag.evaluate(home, NOW, green_age=600, red_age=10800)
    assert code == 0, detail
    assert any(ln.startswith(f'delivery in flight: check for {shas[1][:8]}') for ln in detail), detail


def test_a_lag_with_no_promotion_in_flight_is_amber(tmp_path):
    _, home, shas = make_repo(tmp_path, [NOW - 3000, NOW - 1800])
    pin_to(home, shas[0])
    code, detail = lag.evaluate(home, NOW, green_age=600, red_age=10800)
    assert code == 1, detail
    assert any('delivery in flight' in ln for ln in detail) is False, detail


def test_a_promotion_in_flight_past_red_age_still_reads_red(tmp_path):
    _, home, shas = make_repo(tmp_path, [NOW - 20000, NOW - 19000])  # 5.3 h lag
    pin_to(home, shas[0])
    write_live_record(home, 'applying', created_at=NOW - 60, sha=shas[1])
    code, detail = lag.evaluate(home, NOW, green_age=600, red_age=10800)
    assert code == 2, detail


def test_a_stale_live_record_does_not_mask_the_lag(tmp_path):
    _, home, shas = make_repo(tmp_path, [NOW - 3000, NOW - 1800])
    pin_to(home, shas[0])
    write_live_record(home, 'checking', created_at=NOW - 40000, sha=shas[1])
    code, detail = lag.evaluate(home, NOW, green_age=600, red_age=10800)
    assert code == 1, detail


def test_a_record_naming_a_commit_outside_this_clone_is_not_in_flight(tmp_path):
    _, home, shas = make_repo(tmp_path, [NOW - 3000, NOW - 1800])
    pin_to(home, shas[0])
    write_live_record(home, 'checking', created_at=NOW - 60, sha='f' * 40)
    code, detail = lag.evaluate(home, NOW, green_age=600, red_age=10800)
    assert code == 1, detail


def test_in_flight_ignores_a_terminal_phase(tmp_path):
    write_live_record(tmp_path, 'delivered', created_at=1000.0)
    assert lag.in_flight(tmp_path, 1100.0) is None


def test_in_flight_names_the_stage_of_a_live_record(tmp_path):
    write_live_record(tmp_path, 'waiting-for-wakes', created_at=1000.0, sha='a' * 40)
    assert lag.in_flight(tmp_path, 1100.0) == ('activate', 'a' * 40)
