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


def test_transient_retry_is_bounded_by_deadline(tmp_path, monkeypatch):
    transition(tmp_path)
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-x.service': STARTING})
    clock = iter([0.0, 0.0, 0.5, 100.0, 100.0])  # the third retry check is past the 1 s deadline
    monkeypatch.setattr(fence.time, 'monotonic', lambda: next(clock, 200.0))
    with pytest.raises(ValueError, match='active cgroup evidence UNKNOWN'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=1, sleep=lambda s: None)
