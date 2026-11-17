"""checks/review-backlog.py: a delivered release whose review has not merged is visible, and stranded differs from waiting.

Measured 2026-11-17: thirty delivered releases awaited review on a spent weekly quota and every
dashboard signal read GREEN, because the retry ladder (which keeps the outage survivable) had no
published state. These tests pin the verdict boundaries: a live rung is AMBER, an exhausted or
rungless review is RED, a review still inside its delivery grace is not RED, and no records is
UNKNOWN rather than GREEN.
"""
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_backlog', REPO / 'checks/review-backlog.py')
backlog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backlog)

NOW = 1_800_000_000.0
SHA = 'c0ffee1234567890abcdef'


def record(home, *, sha=SHA, phase='delivered', pending=True, due=None, state=None,
           delivered_at=None, error=None, created_at=None):
    root = home / 'work' / 'releases'
    root.mkdir(parents=True, exist_ok=True)
    body = {'version': 1, 'id': f'{sha}-1', 'sha': sha, 'phase': phase,
            'review_pending': pending,
            'delivered_at': NOW if delivered_at is None else delivered_at,
            'created_at': NOW if created_at is None else created_at}
    if due is not None:
        body['review_retry_due'] = due
    if state is not None:
        body['review_retry_state'] = state
    if error is not None:
        body['review_error'] = error
    (root / f'{sha}-1.json').write_text(json.dumps(body))
    return home


def test_no_records_is_unknown_not_green(tmp_path):
    code, lines = backlog.evaluate(tmp_path, NOW)
    assert code == 3
    assert 'no work/releases' in lines[0]


def test_no_delivered_record_is_unknown(tmp_path):
    record(tmp_path, phase='check-failed')
    code, lines = backlog.evaluate(tmp_path, NOW)
    assert code == 3
    assert 'nothing to judge' in lines[0]


def test_merged_review_is_green(tmp_path):
    record(tmp_path, pending=False)
    code, lines = backlog.evaluate(tmp_path, NOW)
    assert code == 0
    assert '0 of 1' in lines[0]


def test_live_rung_is_amber_and_names_the_reason_and_next_rung(tmp_path):
    record(tmp_path, due=NOW + 3600, state='scheduled in 3600s',
           error="Command '['python', '-m', 'mishe_tauftauf.wall_review']' returned non-zero exit "
                 "status 1. stderr tail: 'File \"wall_review.py\", line 191\\nRuntimeError: patch "
                 "reader failed: reviewer exited 1; stdout tail \"You\\'ve hit your weekly limit\"'")
    code, lines = backlog.evaluate(tmp_path, NOW)
    text = '\n'.join(lines)
    assert code == 1
    assert '1 of 1' in text
    # The failure sentence, not the traceback that precedes it.
    assert "You've hit your weekly limit" in text
    assert 'line 191' not in text
    assert 'next rung' in text


def test_review_inside_the_delivery_grace_is_amber_not_red(tmp_path):
    record(tmp_path, due=None, delivered_at=NOW - 60)
    code, _ = backlog.evaluate(tmp_path, NOW, grace=1800)
    assert code == 1


def test_rungless_review_past_the_grace_is_red(tmp_path):
    record(tmp_path, due=None, delivered_at=NOW - 7200)
    code, lines = backlog.evaluate(tmp_path, NOW, grace=1800)
    assert code == 2
    assert 'stranded' in '\n'.join(lines)


def test_rung_that_never_fired_is_red_once_overdue(tmp_path):
    record(tmp_path, due=NOW - 3600, state='scheduled in 3600s')
    code, lines = backlog.evaluate(tmp_path, NOW, grace=1800)
    assert code == 2
    assert 'overdue' in '\n'.join(lines)


def test_exhausted_ladder_is_red(tmp_path):
    record(tmp_path, state='exhausted')
    code, lines = backlog.evaluate(tmp_path, NOW)
    assert code == 2
    assert 'exhausted' in '\n'.join(lines)


def test_unreadable_record_is_skipped_not_fatal(tmp_path):
    record(tmp_path, pending=False)
    (tmp_path / 'work' / 'releases' / 'broken.json').write_text('{not json')
    code, _ = backlog.evaluate(tmp_path, NOW)
    assert code == 0
