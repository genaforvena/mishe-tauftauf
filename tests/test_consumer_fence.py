"""Transient mid-start UNKNOWN is retried inside the drain deadline; persistent UNKNOWN fails closed."""
import json
import time
from pathlib import Path

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
               ControlGroup='/', ExecMainStartTimestampMonotonic='5', InvocationID='inv-1')
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-debounce.service': bad})
    monkeypatch.setattr(fence, 'cgroup_pids', lambda group: [7])
    with pytest.raises(ValueError, match='unbounded pinned consumer: mishe-debounce.service'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=0, sleep=lambda s: None)
    samples = json.loads(next(tmp_path.glob('consumer-drain-pin-*.json')).read_text())['samples']
    assert samples[-1]['error'] == 'unbounded pinned consumer: mishe-debounce.service'
    assert samples[-1]['invocations']['mishe-debounce.service']['Type'] == 'simple'


def test_blank_properties_while_starting_are_retried_not_unbounded(tmp_path, monkeypatch):
    """A queued start can read blank Type/KillMode (the 17:41Z delivery); that is a transient read and drains once settled."""
    transition(tmp_path)
    blank = dict(STARTING, Type='', KillMode='', ControlGroup='/')
    rows = iter([{'mishe-x.service': blank}, {'mishe-x.service': SETTLED}])
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: next(rows))
    monkeypatch.setattr(fence, 'cgroup_pids', lambda group: [])
    naps = []
    record = fence.drain(tmp_path, tmp_path, 'pin', timeout=5, sleep=naps.append)
    assert record['label'] == 'pin' and naps
    samples = json.loads(next(tmp_path.glob('consumer-drain-pin-*.json')).read_text())['samples']
    assert samples[0]['transient'] == 'active unit properties UNKNOWN: mishe-x.service'


def test_persistent_blank_properties_fail_closed_at_deadline(tmp_path, monkeypatch):
    transition(tmp_path)
    blank = dict(STARTING, Type='', KillMode='', ControlGroup='/')
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-x.service': blank})
    monkeypatch.setattr(fence, 'cgroup_pids', lambda group: [7])
    clock = iter([0.0, 0.0, 0.5, 100.0, 100.0])
    monkeypatch.setattr(fence.time, 'monotonic', lambda: next(clock, 200.0))
    with pytest.raises(ValueError, match='active unit properties UNKNOWN: mishe-x.service'):
        fence.drain(tmp_path, tmp_path, 'pin', timeout=1, sleep=lambda s: None)


def test_default_drain_window_covers_longest_pinned_producer_run():
    """A 300 s engine-timeout producer (TimeoutStartSec 330 s) started before the cutoff must still drain."""
    import inspect
    assert fence.CONSUMER_MAX_RUN_S >= 300 + 30
    assert inspect.signature(fence.drain).parameters['timeout'].default == fence.DRAIN_TIMEOUT_S
    assert fence.DRAIN_TIMEOUT_S >= fence.CONSUMER_MAX_RUN_S + fence.DRAIN_MARGIN_S


def test_old_invocation_running_300s_drains_under_default_window(tmp_path, monkeypatch):
    transition(tmp_path)
    clock = {'now': 0.0}
    monkeypatch.setattr(fence.time, 'monotonic', lambda: clock['now'])
    running = dict(STARTING, ActiveState='active', ControlGroup='/', ExecMainStartTimestampMonotonic='1',
                   InvocationID='old-invocation', Type='oneshot', KillMode='control-group')
    def fake_inventory(home, previous=None):
        return {'mishe-x.service': running if clock['now'] < 300 else SETTLED}
    monkeypatch.setattr(fence, 'inventory', fake_inventory)
    monkeypatch.setattr(fence, 'cgroup_pids', lambda group: [4242] if clock['now'] < 300 else [])
    def sleep(seconds):
        clock['now'] += seconds
    record = fence.drain(tmp_path, tmp_path, 'pin', sleep=sleep)
    assert record['label'] == 'pin'
    assert clock['now'] >= 300


def test_pinned_wake_started_before_cutoff_and_still_running_fails_the_drain(tmp_path, monkeypatch):
    """Reproduces the a0e0dbe8 rollback: a mishe-wake@ oneshot (a 35-minute wake) that started before the
    cutoff keeps old bytes running past DRAIN_TIMEOUT_S, so the drain must time out and fail closed."""
    transition(tmp_path)
    clock = {'now': 0.0}
    monkeypatch.setattr(fence.time, 'monotonic', lambda: clock['now'])
    wake = dict(STARTING, Id='mishe-wake@genome.service', Type='oneshot', KillMode='control-group',
                ActiveState='activating', ControlGroup='/', ExecMainStartTimestampMonotonic='1',
                InvocationID='wake-genome-inv')
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-wake@genome.service': wake})
    monkeypatch.setattr(fence, 'cgroup_pids', lambda group: [4343])
    def sleep(seconds):
        clock['now'] += seconds
    with pytest.raises(ValueError, match='old invocations remain'):
        fence.drain(tmp_path, tmp_path, 'pin', sleep=sleep)
    assert clock['now'] >= fence.DRAIN_TIMEOUT_S


def test_queued_wake_held_by_promotion_lock_drains_to_deadline_then_fails_closed(tmp_path, monkeypatch):
    """Pins the current drain response to a MODELLED state: a mishe-wake@ unit reported 'activating' with an empty
    InvocationID and no cgroup. This mock is not observed evidence. The installed ExecStart launches Python first,
    and a wake waiting on the shared dispatch lock is already old-code code that can continue after promotion, so
    'no invocation' does not hold for a real lock waiter. Whether a truly never-executed systemd job should be
    excluded needs a separate discriminator and post-promotion code-selection proof. This test does not endorse
    excluding it, and it does not explain the a0e0dbe8 producer-unit incident."""
    transition(tmp_path)
    clock = {'now': 0.0}
    monkeypatch.setattr(fence.time, 'monotonic', lambda: clock['now'])
    queued = dict(STARTING, Id='mishe-wake@genome.service', Type='oneshot', KillMode='control-group',
                  ActiveState='activating', ControlGroup='/mishe-wake.slice/mishe-wake@genome.service',
                  ExecMainStartTimestampMonotonic='0', InvocationID='')
    monkeypatch.setattr(fence, 'inventory', lambda home, previous=None: {'mishe-wake@genome.service': queued})
    def sleep(seconds):
        clock['now'] += seconds
    with pytest.raises(ValueError, match='active cgroup evidence UNKNOWN: mishe-wake@genome.service'):
        fence.drain(tmp_path, tmp_path, 'pin', sleep=sleep)
    assert clock['now'] >= fence.DRAIN_TIMEOUT_S


def _exec(argv):
    """The shape ``systemctl show -p ExecStart`` returns for a unit."""
    return "{ path=/usr/bin/python ; argv[]=/usr/bin/python " + argv + " ; ignore_errors=no ; }"


def test_release_review_is_not_a_pin_consumer():
    """A review run names the release under test; the pin names the release in service (2026-11-17)."""
    review = _exec("-m mishe_tauftauf --home /site release review --id 2e2d080d-1")
    consumer = _exec("-m mishe_tauftauf --home /site produce arm")
    assert fence.pin_independent(dict(ExecStart=review))
    assert not fence.pin_independent(dict(ExecStart=consumer))
    assert not fence.pin_independent(dict(ExecStart=""))


def test_inventory_skips_a_queued_release_review_retry(monkeypatch):
    """The retry timer queued behind the pin must not enter the consumer rows at all."""
    from mishe_tauftauf import release_promotion as rp
    review = "mishe-release-review-retry-x.service"
    services = [{'unit': 'mishe-arm.service'}, {'unit': review}]
    timers = [{'unit': 'mishe-release-review-retry-x.timer'}]
    rows = {
        'mishe-arm.service': dict(FragmentPath='/site/units/mishe-arm.service',
                                  ExecStart=_exec("-m mishe_tauftauf --home /site produce arm"),
                                  Environment='PYTHONPATH=/site/runtime/current/src'),
        review: dict(FragmentPath='/run/user/1000/systemd/transient/' + review,
                     ExecStart=_exec("-m mishe_tauftauf --home /site release review --id 2e2d080d-1"),
                     Environment='PYTHONPATH=/site/releases/2e2d080d/src'),
    }
    def command(args):
        if 'mishe-*.service' in args: return json.dumps(services)
        if 'mishe-*.timer' in args: return json.dumps(timers)
        return review
    monkeypatch.setattr(fence, 'command', command)
    monkeypatch.setattr(fence, 'properties', lambda name: dict(rows[name]))
    monkeypatch.setattr(rp, 'config_paths', lambda home: [Path('/site/units/mishe-arm.service')])
    assert set(fence.inventory(Path('/site'))) == {'mishe-arm.service'}


def test_inventory_keeps_a_queued_consumer_with_blank_environment(monkeypatch):
    """field-review 2026-11-19: a queued target with missing Environment evidence must fail closed, not vanish.

    The real arming path always sets PYTHONPATH (arm.py --setenv), so this is a synthetic evidence-loss
    case; verify_future already rejects a row with no pins once the row reaches it. The gap was that
    inventory dropped the row first.
    """
    from mishe_tauftauf import release_promotion as rp
    name = 'mishe-next-due-example.service'
    timer = 'mishe-next-due-example.timer'
    rows = {name: dict(FragmentPath='/run/user/1000/systemd/transient/' + name,
                       ExecStart=_exec("-m mishe_tauftauf --home /site due"),
                       Environment='', ActiveState='inactive')}

    def command(args):
        if 'mishe-*.service' in args: return json.dumps([{'unit': name}])
        if 'mishe-*.timer' in args: return json.dumps([{'unit': timer}])
        return name

    monkeypatch.setattr(fence, 'command', command)
    monkeypatch.setattr(fence, 'properties', lambda n: dict(rows[n]))
    monkeypatch.setattr(rp, 'config_paths', lambda home: [])
    inv = fence.inventory(Path('/site'))
    assert name in inv and inv[name]['pending_timer'] is True
    with pytest.raises(ValueError, match='wrong pin: ' + name):
        fence.verify_future(Path('/site'), Path('/site/releases/' + ('a' * 40) + '/src'), {})


def test_inventory_still_skips_an_active_unpinned_row(monkeypatch):
    """The new fail-closed gate is queued-only: an active row with blank Environment stays skipped."""
    from mishe_tauftauf import release_promotion as rp
    name = 'mishe-arm.service'
    rows = {name: dict(FragmentPath='/site/units/mishe-arm.service',
                       ExecStart=_exec("-m mishe_tauftauf --home /site produce arm"),
                       Environment='', ActiveState='active')}

    def command(args):
        if 'mishe-*.service' in args: return json.dumps([{'unit': name}])
        if 'mishe-*.timer' in args: return json.dumps([])
        return name

    monkeypatch.setattr(fence, 'command', command)
    monkeypatch.setattr(fence, 'properties', lambda n: dict(rows[n]))
    monkeypatch.setattr(rp, 'config_paths', lambda home: [Path('/site/units/mishe-arm.service')])
    assert fence.inventory(Path('/site')) == {}


def test_inventory_skips_a_queued_direct_script_service_without_a_pin(monkeypatch):
    """A timer-triggered direct-script check (human-inbound-relay) is legitimately unpinned (2026-11-19).

    It uses `python3 -I <script> --home ...`, has no PYTHONPATH, and must not be rejected as a wrong pin.
    """
    from mishe_tauftauf import release_promotion as rp
    name = 'mishe-human-inbound-relay.service'
    timer = 'mishe-human-inbound-relay.timer'
    raw_exec = ('{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I '
                '/home/mesh-home/checks/human-inbound-relayed.py --home /site --relay ; ignore_errors=no ; }')
    rows = {name: dict(FragmentPath='/home/mesh-home/.config/systemd/user/' + name,
                       ExecStart=raw_exec,
                       Environment='PATH=/usr/bin MISHE_SEED_HOME=/site', ActiveState='inactive')}

    def command(args):
        if 'mishe-*.service' in args: return json.dumps([{'unit': name}])
        if 'mishe-*.timer' in args: return json.dumps([{'unit': timer}])
        return name

    monkeypatch.setattr(fence, 'command', command)
    monkeypatch.setattr(fence, 'properties', lambda n: dict(rows[n]))
    monkeypatch.setattr(rp, 'config_paths', lambda home: [Path('/home/mesh-home/.config/systemd/user/' + name)])
    assert fence.inventory(Path('/site')) == {}


def test_verify_future_still_rejects_an_installed_consumer_on_old_bytes(tmp_path, monkeypatch):
    """The review exemption must not soften the check for a real site consumer."""
    home = tmp_path / 'site'
    (home / 'runtime' / 'current').mkdir(parents=True)
    old = home / 'releases' / ('a' * 40) / 'src'
    row = dict(ExecStart=_exec("-m mishe_tauftauf --home /site produce arm"), ActiveState='active',
               Environment='PYTHONPATH=' + str(old), Type='oneshot', KillMode='control-group')
    monkeypatch.setattr(fence, 'inventory', lambda h, previous=None: {'mishe-arm.service': row})
    with pytest.raises(ValueError, match='wrong pin: mishe-arm.service'):
        fence.verify_future(home, home / 'runtime' / 'current' / 'src', {})


def test_cgroup_removed_during_the_scan_reads_as_no_processes(tmp_path, monkeypatch):
    """A oneshot that exits mid-read has its group deleted in one step; that is not an enumeration failure.

    Backs the 2026-11-17 20:34Z rollback: mishe-producer-initiatives-rust-plan.service started at
    20:34:26 and finished at 20:34:27 while the promotion drain was reading it, and the probe answered
    `cgroup enumeration missing` for a release whose own checks had passed.
    """
    root = tmp_path / 'cgroup'
    group = root / 'mishe-plants.slice' / 'mishe-producer-x.service'
    group.mkdir(parents=True)
    real_rglob = Path.rglob
    def vanishing(self, pattern):
        if self == group: group.rmdir()  # the kernel removes the group between exists() and the scan
        return real_rglob(self, pattern)
    monkeypatch.setattr(Path, 'rglob', vanishing)
    assert fence.cgroup_pids('/mishe-plants.slice/mishe-producer-x.service', root=root) == []


def test_cgroup_present_without_procs_is_still_an_error(tmp_path):
    """A group that is still there and has no cgroup.procs is a real failure, not a disappearance."""
    root = tmp_path / 'cgroup'
    (root / 'mishe-x.service').mkdir(parents=True)
    with pytest.raises(ValueError, match='cgroup enumeration missing: /mishe-x.service'):
        fence.cgroup_pids('/mishe-x.service', root=root)


def test_cgroup_pids_reads_the_group_and_its_child_groups(tmp_path):
    root = tmp_path / 'cgroup'
    group = root / 'mishe-plants.slice' / 'mishe-x.service'
    child = group / 'child'
    child.mkdir(parents=True)
    (group / 'cgroup.procs').write_text('31\n32\n')
    (child / 'cgroup.procs').write_text('33\n')
    assert fence.cgroup_pids('/mishe-plants.slice/mishe-x.service', root=root) == [31, 32, 33]
