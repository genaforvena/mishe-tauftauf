"""checks/pin-lag.py: the newest run log's stage and failure are the run's own outcome, not the wrapper's legend."""
import importlib.util
import json
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


def test_a_check_failed_promote_reports_the_record_phase_not_the_legend(tmp_path):
    log = tmp_path / 'deliver-run-1.log'
    log.write_text(record('check-failed') + PROMOTE_LEGEND + '\n')
    assert lag.last_stage_and_failure(log) == ('check', 'check failed (b0cec3c7)')


def test_a_delivered_promote_has_no_failure(tmp_path):
    log = tmp_path / 'deliver-run-2.log'
    log.write_text(record('delivered') + '[promote exit 0: legend — 0 delivered, 6 inside a wake]\n')
    assert lag.last_stage_and_failure(log) == ('verify', '')


def test_a_rolled_back_promote_names_the_rollback(tmp_path):
    log = tmp_path / 'deliver-run-3.log'
    log.write_text(record('rolled-back', sha='5d608cd9779fadd1f425852eca74fd45588d1f11') + PROMOTE_LEGEND + '\n')
    assert lag.last_stage_and_failure(log) == ('verify', 'observation failed, rolled back (5d608cd9)')


def test_a_still_running_promote_is_unknown(tmp_path):
    log = tmp_path / 'deliver-run-4.log'
    log.write_text('')
    assert lag.last_stage_and_failure(log) == ('unknown', '')


def test_the_legend_alone_is_never_a_failure(tmp_path):
    log = tmp_path / 'deliver-run-5.log'
    log.write_text(DELIVER_LEGEND + '\n')
    assert lag.last_stage_and_failure(log) == ('unknown', '')


def test_a_legacy_log_still_scans_its_own_lines(tmp_path):
    log = tmp_path / 'deliver-run-6.log'
    log.write_text('check started\nverify exit 5')  # no JSON record: the retired deliver.sh path
    assert lag.last_stage_and_failure(log) == ('verify', 'verify exit 5')
