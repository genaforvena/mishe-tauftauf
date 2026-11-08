"""revive: restores only inside the guard's gates, never loops, never hides a failed action."""
import json

import pytest

from mishe_tauftauf import revive
from mishe_tauftauf.revive import STATE, GUARD

NOW = 2_000_000.0
LATCH = {'at': NOW - 1200, 'reason': 'cgroup OOM'}
READY = {'stability_satisfied': True, 'continuous_healthy_seconds': 500.0, 'detail': 'ok'}
UNITS = ('mishe-wake-health.path loaded inactive dead Mishe wake triggers\n'
         'mishe-wake-genome.path loaded active waiting Mishe wake triggers\n'
         'mishe-producer-dispatch.service loaded failed failed Mishe producer dispatch\n'
         'mishe-wake@genome.service loaded failed failed Mishe wake genome\n'
         'run-r1.scope loaded failed failed /x/python -m mishe_tauftauf.plant_boot inner\n')


class Fake(revive.Host):
    def __init__(self, tmp_path, *, latch=LATCH, emergency=False, ready=READY, inside=False, restores=(),
                 units=UNITS, views=False, io=0, restore_works=True, hold=False, life=None,
                 inventory=(), archives=(), start_fails=(), enabled=True):
        super().__init__(tmp_path / 'site', tmp_path / 'repo')
        self.t = NOW
        self.files, self.calls, self.inside, self.restores = {}, [], inside, list(restores)
        self.archives, self.start_fails, self.enabled, self.started = list(archives), set(start_fails), enabled, set()
        if inventory:
            self.files[str(revive.INVENTORY)] = json.dumps({'units': list(inventory)})
        self.latched = latch is not None
        if latch is not None:
            self.files[str(STATE / 'tripped')] = json.dumps(latch)
        if emergency:
            self.files[str(STATE / 'emergency')] = '{}'
        if hold:
            self.files[str(self.home / 'hold')] = 'maintenance'
        self.ready, self.units, self.views, self.io, self.restore_works, self.life = ready, units, views, io, restore_works, life
        self.normal = not self.latched
        self.healthy = 'NORMAL'

    def now(self): return self.t
    def sleep(self, seconds): self.t += seconds
    def read(self, path): return self.files.get(str(path))
    def exists(self, path): return str(path) in self.files
    def write(self, path, text): self.files[str(path)] = text
    def append(self, path, text): self.files[str(path)] = self.files.get(str(path), '') + text
    def archived_restores(self): return self.restores
    def latch_archive_times(self): return list(self.archives)
    def inside_mishe(self): return self.inside

    def run(self, argv, timeout=60, env=None):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ['python3', GUARD] and argv[2] == 'status':
            state = (self.healthy if self.normal else 'TRIPPED')
            return 0, json.dumps({'status': {'state': state, 'life_support': self.life}}), ''
        if argv[:2] == ['python3', GUARD] and argv[2] == 'io-probe':
            return self.io, '', ''
        if argv[-1] == '--json':
            return 0, json.dumps(self.ready) if self.ready is not None else 'garbage', ''
        if argv[:3] == ['sudo', '-n', 'python3']:
            self.verb = argv[-1]
            if self.restore_works:
                self.files.pop(str(STATE / 'tripped'), None)
                self.normal = True
                return 0, '', ''
            return 1, '', 'restore evidence must name exactly the latches it clears'
        if argv[:3] == ['systemctl', '--user', 'list-units']:
            names = argv[argv.index('--no-legend') + 1:]
            rows = ''.join(f'{u} loaded active running x\n' for u in sorted(self.started) if not names or u in names)
            return 0, self.units + rows, ''
        if argv[:3] == ['systemctl', '--user', 'start']:
            if argv[3] in self.start_fails:
                return 1, '', 'Failed to start'
            self.started.add(argv[3])
            return 0, '', ''
        if argv[:3] == ['systemctl', '--user', 'is-enabled']:
            return 0, ('enabled' if self.enabled else 'disabled'), ''
        if argv[0] == 'tmux':
            return (0, 'mishe\n', '') if self.views else (1, '', 'no server')
        return 0, '', ''


def acted(h):
    return [c for c in h.calls if c[:2] == ['systemctl', '--user'] and c[2] in ('start', 'reset-failed', 'restart')]


def test_a_healthy_plant_is_left_alone(tmp_path):
    h = Fake(tmp_path, latch=None, units='mishe-wake-genome.path loaded active waiting x\n', views=True)
    r = revive.run(h)
    assert r.code == 0 and 'alive: nothing to revive' in r.lines[-1] and not acted(h)


def test_a_trip_is_restored_then_the_plant_is_revived(tmp_path):
    h = Fake(tmp_path)
    r = revive.run(h, diagnosis='tmux scrollback')
    assert r.code == 0 and 'latch cleared' in ' '.join(r.lines)
    assert h.verb == 'rearm' and not any('--evidence' in c for c in h.calls)   # no life support configured
    assert ['systemctl', '--user', 'start', 'mishe-wake-health.path'] in h.calls
    assert ['systemctl', '--user', 'reset-failed', 'mishe-wake@genome.service'] in h.calls
    assert ['systemctl', '--user', 'reset-failed', 'mishe-producer-dispatch.service'] not in h.calls   # detector failures stay
    assert ['systemctl', '--user', 'restart', 'plant-mishe-ui.service'] in h.calls
    evidence = next(v for k, v in h.files.items() if k.endswith('-revive.json'))
    doc = json.loads(evidence)
    assert doc['clears'] == {'tripped': LATCH} and doc['verification'] and 'tmux scrollback' in evidence
    log = h.files[str(h.home / 'operator/guard-actions.jsonl')]
    assert 'revive-rearm' in log and 'revive-units' in log and 'mishe-wake@genome.service' in log


@pytest.mark.parametrize('kw, why', [
    (dict(emergency=True), 'emergency latch'),
    (dict(inside=True), 'inside /mishe.slice'),
    (dict(ready=dict(READY, stability_satisfied=False, detail='only 30 s healthy')), 'readiness gate is not satisfied'),
    (dict(ready=None), 'readiness gate is not satisfied'),
    (dict(restores=[(NOW - 3600, 'cgroup OOM')]), 'repeating fault'),
    (dict(restores=[(NOW - 100 * i, f'r{i}') for i in range(24)]), '24 restores in 24 h'),
    (dict(life={'state': 'DEAD'}), 'life support'),
])
def test_every_gate_refuses_and_changes_nothing(tmp_path, kw, why):
    h = Fake(tmp_path, **kw)
    r = revive.run(h)
    assert r.code == 2 and why in ' '.join(r.lines)
    assert not any(c[:3] == ['sudo', '-n', 'python3'] for c in h.calls) and not acted(h)
    assert str(STATE / 'tripped') in h.files


def test_with_life_support_configured_the_evidence_restore_is_used(tmp_path):
    h = Fake(tmp_path, life={'state': 'PANES_LIVE'})
    assert revive.run(h).code == 0 and h.verb == 'restore'
    assert any(c[-3:-1] == ['--evidence', next(k for k in h.files if k.endswith('-revive.json'))] for c in h.calls)


def test_ready_is_a_healthy_guard_state_and_an_odd_state_is_left_alone(tmp_path):
    h = Fake(tmp_path)
    h.healthy = 'READY'
    assert revive.run(h).code == 0
    settling = Fake(tmp_path, latch=None)
    settling.healthy = 'RESTORING'
    r = revive.run(settling)
    assert 'waiting for it to settle' in ' '.join(r.lines) and not acted(settling)


def test_a_repeat_needs_the_explicit_human_override(tmp_path):
    h = Fake(tmp_path, restores=[(NOW - 3600, 'cgroup OOM')])
    assert revive.run(h, allow_repeat=True).code == 0


def test_unattended_use_waits_out_the_cooling_off(tmp_path):
    young = Fake(tmp_path, latch=dict(LATCH, at=NOW - 120))
    r = revive.run(young, auto=True)
    assert r.code == 0 and 'waiting' in ' '.join(r.lines) and not any(c[:1] == ['sudo'] for c in young.calls)
    assert revive.run(Fake(tmp_path, latch=dict(LATCH, at=NOW - 120)), auto=False).code == 0


def test_a_failed_restore_is_reported_not_hidden(tmp_path):
    h = Fake(tmp_path, restore_works=False)
    r = revive.run(h)
    assert r.code == 3 and 'STILL PRESENT' in ' '.join(r.lines) and not acted(h)
    assert '"exit": 1' in h.files[str(h.home / 'operator/guard-actions.jsonl')]


def test_unreadable_guard_or_filesystem_probe_is_undecidable(tmp_path):
    assert revive.run(Fake(tmp_path, io=1)).code == 3
    class Blind(Fake):
        def run(self, argv, timeout=60, env=None):
            return (1, '', 'boom') if argv[:2] == ['python3', GUARD] else super().run(argv, timeout, env)
    assert revive.run(Blind(tmp_path)).code == 3


def test_dry_run_changes_nothing_and_a_hold_is_respected(tmp_path):
    h = Fake(tmp_path)
    assert revive.run(h, dry_run=True).code == 0
    assert not any(c[:1] == ['sudo'] for c in h.calls) and not acted(h) and str(STATE / 'tripped') in h.files
    held = Fake(tmp_path, latch=None, hold=True)
    r = revive.run(held)
    assert 'HELD' in ' '.join(r.lines) and str(held.home / 'hold') in held.files


def test_boundary_decoders_never_trust_malformed_input():
    from mishe_tauftauf import revive_boundary as b
    assert b.decode_json('{bad') is None and b.decode_json(None) is None
    assert b.jsonl('{"a":1}\nnot json\n[1]\n') == [{'a': 1}]
    assert b.restore_archive('tripped-1500000000000000000.json', '{"reason": "x"}') == (1.5e9, 'x')
    assert b.restore_archive('tripped-latest.json', '{"reason": "x"}') is None
    assert b.unit_kind('mishe-wake-docs.path') == 'wake-path' and b.unit_kind('run-x.scope') == 'other'
    assert b.unit_kind('mishe-wake@docs.service') == 'wake-service' and b.unit_kind('mishe-producer-x.service') == 'service'
    assert b.unit_rows('a.service loaded failed failed Some thing\nshort\n') == [('a.service', 'failed', 'failed', 'Some thing')]


PHONE = ('light-cycle-monitor.service', 'android-body-rendezvous.service')
PHONE_UP = ''.join(f'{u} loaded active running x\n' for u in PHONE)


def test_a_healthy_pass_samples_the_running_inventory_and_starts_nothing(tmp_path):
    h = Fake(tmp_path, latch=None, inventory=PHONE, units=PHONE_UP, views=True)
    r = revive.run(h)
    doc = json.loads(h.files[str(h.home / revive.RUNNING)])
    assert doc['active'] == sorted(PHONE) and doc['checked'] == NOW and 'owed' not in doc
    assert not acted(h) and r.code == 0 and 'alive: nothing to revive' in r.lines[-1]


def test_units_running_before_a_trip_come_back_after_rearm(tmp_path):
    h = Fake(tmp_path, inventory=PHONE)           # latched: the guard stopped the phone units
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': list(PHONE), 'checked': NOW - 600})
    r = revive.run(h)
    assert r.code == 0 and h.verb == 'rearm'
    assert set(PHONE) <= h.started
    doc = json.loads(h.files[str(h.home / revive.RUNNING)])
    assert doc['owed'] == [] and doc['active'] == []     # the sample taken after the restart saw nothing active yet
    assert 'restarted after rearm' in ' '.join(r.lines)


def test_a_rearm_seen_only_in_the_archive_still_owes_the_pre_trip_set(tmp_path):
    h = Fake(tmp_path, latch=None, inventory=PHONE, archives=(NOW - 100,))   # trip and rearm between two passes
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': list(PHONE), 'checked': NOW - 600})
    r = revive.run(h)
    assert r.code == 0 and set(PHONE) <= h.started, r.lines


def test_a_unit_that_stays_down_is_unknown_and_retried_not_silent(tmp_path):
    h = Fake(tmp_path, inventory=PHONE, start_fails=('light-cycle-monitor.service',))
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': list(PHONE), 'checked': NOW - 600})
    r = revive.run(h)
    text = ' '.join(r.lines)
    assert r.code == 3 and 'UNKNOWN' in text and 'light-cycle-monitor.service' in text
    doc = json.loads(h.files[str(h.home / revive.RUNNING)])
    assert doc['owed'] == ['light-cycle-monitor.service'] and doc['attempts'] == {'light-cycle-monitor.service': 1}


def test_owed_unit_gives_up_after_the_bounded_attempts(tmp_path):
    h = Fake(tmp_path, latch=None, inventory=PHONE, start_fails=PHONE)
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': [], 'owed': list(PHONE), 'checked': NOW,
                                                         'attempts': {u: revive.MAX_OWED_ATTEMPTS - 1 for u in PHONE}})
    r = revive.run(h)
    assert r.code == 3 and 'gave up' in ' '.join(r.lines)
    assert json.loads(h.files[str(h.home / revive.RUNNING)])['owed'] == []


def test_a_disabled_owed_unit_is_reported_and_not_enabled_by_revive(tmp_path):
    h = Fake(tmp_path, latch=None, inventory=PHONE, enabled=False)
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': [], 'owed': [PHONE[0]], 'checked': NOW})
    r = revive.run(h)
    assert 'is not enabled' in ' '.join(r.lines)
    assert not any(c[:3] == ['systemctl', '--user', 'enable'] for c in h.calls)


def test_dry_run_samples_nothing_and_starts_nothing(tmp_path):
    h = Fake(tmp_path, inventory=PHONE)
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': list(PHONE), 'checked': NOW - 600})
    before = h.files[str(h.home / revive.RUNNING)]
    revive.run(h, dry_run=True)
    assert h.files[str(h.home / revive.RUNNING)] == before and not h.started


def test_a_given_up_unit_stays_unknown_on_every_pass_until_it_is_back(tmp_path):
    h = Fake(tmp_path, latch=None, inventory=PHONE, start_fails=PHONE)
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': [], 'owed': list(PHONE), 'checked': NOW,
                                                         'attempts': {u: revive.MAX_OWED_ATTEMPTS - 1 for u in PHONE}})
    first = revive.run(h)
    assert first.code == 3 and 'gave up after 12 attempts' in ' '.join(first.lines)
    starts = lambda: [c[3] for c in h.calls if c[:3] == ['systemctl', '--user', 'start'] and c[3] in PHONE]
    starts_after_give_up = len(starts())
    second = revive.run(h)                       # no change in the world: the record must still say UNKNOWN
    assert second.code == 3 and all(u in ' '.join(second.lines) for u in PHONE)
    assert starts_after_give_up == 2 and len(starts()) == 2   # one attempt per unit on pass 1, none on pass 2
    doc = json.loads(h.files[str(h.home / revive.RUNNING)])
    assert doc['owed'] == [] and doc['unknown'] == {u: revive.MAX_OWED_ATTEMPTS for u in PHONE}


def test_a_given_up_unit_that_comes_back_clears_its_record(tmp_path):
    h = Fake(tmp_path, latch=None, inventory=PHONE, units=PHONE_UP)
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': [], 'owed': [], 'checked': NOW,
                                                         'unknown': {PHONE[0]: revive.MAX_OWED_ATTEMPTS}})
    r = revive.run(h)
    assert r.code == 0 and 'back up after giving up' in ' '.join(r.lines)
    assert json.loads(h.files[str(h.home / revive.RUNNING)])['unknown'] == {}


def test_a_new_trip_gives_a_newly_owed_unit_a_fresh_retry_budget(tmp_path):
    h = Fake(tmp_path, inventory=PHONE, start_fails=(PHONE[0],))     # latched: the guard stopped the units
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': [PHONE[0]], 'checked': NOW - 600,
                                                         'attempts': {PHONE[0]: revive.MAX_OWED_ATTEMPTS - 1}})
    r = revive.run(h)
    text = ' '.join(r.lines)
    doc = json.loads(h.files[str(h.home / revive.RUNNING)])
    assert 'gave up' not in text and 'still down after the rearm' in text
    assert doc['attempts'] == {PHONE[0]: 1} and doc.get('unknown', {}) == {}
    assert doc['owed'] == [PHONE[0]]


def test_a_dry_run_reports_a_persisting_unknown_record(tmp_path):
    h = Fake(tmp_path, latch=None, inventory=PHONE)
    h.files[str(h.home / revive.RUNNING)] = json.dumps({'active': [], 'checked': NOW,
                                                         'unknown': {PHONE[1]: revive.MAX_OWED_ATTEMPTS}})
    r = revive.run(h, dry_run=True)
    assert r.code == 3 and 'gave up after 12 attempts' in ' '.join(r.lines) and not acted(h)
