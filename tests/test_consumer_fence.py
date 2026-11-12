"""Transient mid-start UNKNOWN is retried inside the drain deadline; persistent UNKNOWN fails closed."""
import json
import time

import pytest

from mishe_tauftauf import consumer_fence as fence


STARTING = {'Id': 'mishe-x.service', 'Type': 'oneshot', 'KillMode': 'control-group', 'ActiveState': 'activating',
            'ControlGroup': '/mishe-missing.slice/mishe-x.service', 'ExecMainStartTimestampMonotonic': '0',
            'InvocationID': ''}
SETTLED = {'Id': 'mishe-x.service', 'Type': 'oneshot', 'KillMode': 'control-group', 'ActiveState': 'inactive',
           'ControlGroup': '', 'ExecMainStartTimestampMonotonic': '0', 'InvocationID': ''}


def transition(tmp_path, label='pin'):
    """A cutoff already in the past, so the consumer is 'old' only if it started before it."""
    record = dict(label=label, cutoff_us=1, before={}, after={}, at=time.time())
    path = tmp_path / ('consumer-transition-' + label + '-1.json')
    path.write_text(json.dumps(record))
    (tmp_path / ('consumer-transition-' + label + '-latest.json')).write_text(json.dumps(dict(path=str(path))))
    return record


def test_transient_unknown_retries_then_drains(tmp_path, monkeypatch):
    transition(tmp_path)
    rows = iter([{'mishe-x.service': STARTING}, {'mishe-x.service': SETTLED}])
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: next(rows))
    naps = []
    record = fence.drain(tmp_path, tmp_path, 'pin', timeout=60, sleep=naps.append)
    assert record['label'] == 'pin'
    assert naps == [fence.RETRY_INTERVAL_S]  # one retry, then the clean sample returned
    evidence = next(tmp_path.glob('consumer-drain-pin-*.json'))
    samples = json.loads(evidence.read_text())['samples']
    assert samples[0]['transient'].startswith('active cgroup evidence UNKNOWN: mishe-x.service')
    assert samples[-1]['pending'] == []


def test_persistent_unknown_fails_closed_at_deadline(tmp_path, monkeypatch):
    transition(tmp_path)
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-x.service': STARTING})
    # A zero deadline leaves no retry window: the last UNKNOWN must surface, not a clean account.
    with pytest.raises(ValueError, match='active cgroup evidence UNKNOWN: mishe-x.service'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=0, sleep=lambda s: None)


def test_caller_own_invocation_is_excluded_from_drain(tmp_path, monkeypatch):
    transition(tmp_path)
    monkeypatch.setenv('INVOCATION_ID', 'self-inv')
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {})
    monkeypatch.setattr(fence, 'pending_old', lambda record, rows: [dict(unit='mishe-wake@x.service', invocation='self-inv', start_us=2, pids=[1])])
    record = fence.drain(tmp_path, tmp_path, 'pin', timeout=0, sleep=lambda s: None)
    assert record['label'] == 'pin'
    samples = json.loads(next(tmp_path.glob('consumer-drain-pin-*.json')).read_text())['samples']
    assert samples[-1]['pending'][0]['invocation'] == 'self-inv'  # raw evidence is kept


def test_other_old_invocation_still_blocks_drain(tmp_path, monkeypatch):
    transition(tmp_path)
    monkeypatch.setenv('INVOCATION_ID', 'self-inv')
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {})
    other = dict(unit='mishe-wake@x.service', invocation='other-inv', start_us=2, pids=[2])
    monkeypatch.setattr(fence, 'pending_old', lambda record, rows: [dict(unit='mishe-wake@x.service', invocation='self-inv', start_us=2, pids=[1]), other])
    with pytest.raises(ValueError, match='old invocations remain'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=0, sleep=lambda s: None)


def test_without_invocation_id_nothing_is_excluded(tmp_path, monkeypatch):
    transition(tmp_path)
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {})
    monkeypatch.setattr(fence, 'pending_old', lambda record, rows: [dict(unit='mishe-wake@x.service', invocation='self-inv', start_us=2, pids=[1])])
    with pytest.raises(ValueError, match='old invocations remain'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=0, sleep=lambda s: None)


def test_transient_retry_is_bounded_by_deadline(tmp_path, monkeypatch):
    transition(tmp_path)
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-x.service': STARTING})
    clock = iter([0.0, 0.0, 0.5, 100.0, 100.0])  # the third retry check is past the 1 s deadline
    monkeypatch.setattr(fence.time, 'monotonic', lambda: next(clock, 200.0))
    with pytest.raises(ValueError, match='active cgroup evidence UNKNOWN'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=1, sleep=lambda s: None)


def test_failing_rows_are_saved_before_fail_closed(tmp_path, monkeypatch):
    """An unbounded consumer raises, but its row must reach the drain file (the 12 Nov failure saved zero samples)."""
    transition(tmp_path)
    bad = dict(SETTLED, Id='mishe-debounce.service', ActiveState='active', Type='simple',
               ControlGroup='', ExecMainStartTimestampMonotonic='5', InvocationID='inv-1')
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-debounce.service': bad})
    monkeypatch.setattr(fence, 'cgroup_pids', lambda group: [7])
    with pytest.raises(ValueError, match='unbounded pinned consumer: mishe-debounce.service'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=60, sleep=lambda s: None)
    samples = json.loads(next(tmp_path.glob('consumer-drain-pin-*.json')).read_text())['samples']
    assert samples[-1]['error'] == 'unbounded pinned consumer: mishe-debounce.service'
    assert samples[-1]['invocations']['mishe-debounce.service']['Type'] == 'simple'
