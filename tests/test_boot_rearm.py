import copy

import pytest

from mishe_tauftauf.boot_rearm import eligible, consume


def request():
    return dict(id='authorized-reboot', old_boot='old', expires=1000,
                guard_hash='guard', config_hash='config')


def reading():
    return dict(boot='new', now=500, monotonic=200, guard_hash='guard', config_hash='config',
                emergency=False, restoring=False, invocation='first', restarts=0,
                latch={'reason': 'sentinel started/restarted: explicit rearm required', 'at': 300},
                status={'at': 499, 'state': 'TRIPPED', 'clear_since': 70,
                        'pressure_warning': False, 'observation': {
                            'limits_ok': True, 'ssh_available': True, 'host_io_available': True,
                            'memory': 100, 'maximum': 1000, 'tasks': 1, 'task_max': 100,
                            'available': 8 * 2**30, 'disk_free': 30 * 2**30,
                            'memory_pressure': 0, 'io_pressure': 0, 'oom_kill': 0}})


def test_only_explicit_next_healthy_boot_is_eligible():
    assert eligible(request(), reading(), invocation='first', oom=0) is None


@pytest.mark.parametrize('key,value', [('boot', 'old'), ('now', 1001), ('emergency', True),
    ('restoring', True), ('guard_hash', 'changed'), ('config_hash', 'changed'),
    ('invocation', 'restarted'), ('restarts', 1), ('status', None), ('latch', None)])
def test_fail_closed(key, value):
    sample = reading(); sample[key] = value
    assert eligible(request(), sample, invocation='first', oom=0)


@pytest.mark.parametrize('change', ['stale', 'malformed', 'short', 'wrong-reason', 'oom', 'pressure', 'io-failed'])
def test_failure_observations_are_never_boot_authorization(change):
    sample = reading()
    if change == 'stale': sample['status']['at'] = 400
    if change == 'malformed': sample['status']['observation'] = {}
    if change == 'short': sample['status']['clear_since'] = 100
    if change == 'wrong-reason': sample['latch']['reason'] = 'cgroup OOM'
    if change == 'oom': sample['status']['observation']['oom_kill'] = 1
    if change == 'pressure': sample['status']['observation']['memory_pressure'] = 3
    if change == 'io-failed': sample['status']['observation']['host_io_available'] = False
    assert eligible(request(), sample, invocation='first', oom=0)


def test_consumption_prevents_a_second_rearm(tmp_path):
    import json
    auth = tmp_path / 'authorized.json'
    auth.write_text(json.dumps(request()))
    saved = consume(auth, request())
    assert not auth.exists() and saved.exists()
    with pytest.raises(FileNotFoundError): consume(auth, request())


def test_executable_boot_gate_reads_kernel_oom_even_when_status_says_zero(tmp_path, monkeypatch):
    import json, time, hashlib, sys
    from mishe_tauftauf import boot_rearm as gate
    state, run, cgroup = tmp_path / 'state', tmp_path / 'run', tmp_path / 'cgroup'
    for path in (state, run, cgroup): path.mkdir()
    (cgroup / 'memory.events').write_text('oom 7\noom_kill 1\n')
    config = tmp_path / 'config.json'; config.write_text(json.dumps({'cgroup': str(cgroup)}))
    guard = tmp_path / 'guard.py'; guard.write_text('must not run')
    auth = tmp_path / 'authorization.json'
    value = request(); value.update(expires=time.time()+300,
        config_hash=hashlib.sha256(config.read_bytes()).hexdigest(),
        guard_hash=hashlib.sha256(guard.read_bytes()).hexdigest())
    auth.write_text(json.dumps(value))
    sample = reading(); sample['status'].update(at=time.time(), clear_since=time.monotonic()-130)
    (state / 'tripped').write_text(json.dumps(sample['latch']))
    (run / 'status.json').write_text(json.dumps(sample['status']))
    monkeypatch.setattr(sys, 'argv', ['gate', '--request', str(auth), '--state', str(state),
                                    '--run', str(run), '--config', str(config), '--guard', str(guard)])
    monkeypatch.setattr(gate.subprocess, 'check_output', lambda *a, **kw: 'InvocationID=first\nNRestarts=0\n')
    def forbidden(*a, **kw): raise AssertionError('unsafe rearm invoked')
    monkeypatch.setattr(gate.subprocess, 'run', forbidden)
    assert gate.main() == 3
    assert auth.exists()
