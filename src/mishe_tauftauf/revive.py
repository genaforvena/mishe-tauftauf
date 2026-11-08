"""Bring a dead or tripped plant back to life, inside the host guard's own gates.

Two stages, both safe to run repeatedly (a healthy plant is left alone):

1. If the host guard is TRIPPED, clear the latch with the guard's own `rearm` (or `restore` while
   life support is configured), but only
   when every gate holds: no emergency latch, the guard's readiness gate (120 s of healthy
   readings) is satisfied, the trip is at least ten minutes old, at most 24 restores in 24 h,
   and the same trip reason was not restored in the last 3 h (a repeating fault is reported
   to the human, never restored in a loop). The guard refuses any caller inside
   /mishe.slice, so this must run outside it; that is checked, not assumed.
2. Once the guard is NORMAL, restart what the trip stopped: failed or dead wake paths and
   timers, failed units, and the tmux views. A `hold` file is respected and never removed.

Nothing here edits guard files, limits or credentials. Each restore leaves live evidence
under the site's artifacts directory and a receipt in operator/guard-actions.jsonl.
Exit status: 0 alive, revived or still inside the cooling-off, 2 refused by a gate (reported to the
resolver), 3 undecidable
or an action did not take effect.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import revive_boundary as b

STATE = Path('/var/lib/mishe-containment')
GUARD = '/usr/local/lib/mishe-containment/guard.py'
COOLING_SECONDS = 600
SAME_REASON_SECONDS = 3 * 3600
DAY = 24 * 3600
MAX_RESTORES_PER_DAY = 24
# Failed units revive may reset: those that stop the plant running at all. A producer or detector that fails on
# purpose (its RED exit code is a systemd failure) is evidence and is throttled by the host start limit; resetting
# it would erase that evidence every few minutes and its rate counter with it.
RESETTABLE = ('wake-path', 'wake-service', 'timer', 'plant-scope')
HEALTHY_STATES = ('NORMAL', 'READY')   # READY is the armed state of a guard configured without life support
# The guard stops every unit in its inventory on a trip (`stop` in host_containment.py) and records nothing about which
# were running. Revive therefore keeps its own sample: the inventory units active on each healthy pass, and the set
# owed a restart once a trip has been seen (latch present) or its rearm archived since the last healthy pass.
INVENTORY = Path('/etc/mishe-containment.json')
RUNNING = 'operator/running-units.json'      # under the site home; never committed
MAX_OWED_ATTEMPTS = 12                        # 12 passes of the 5-minute timer = one hour; then UNKNOWN, not silent retry


class Host:
    """Everything that touches the machine; tests substitute a fake."""

    def __init__(self, home: Path, repo: Path):
        self.home, self.repo = Path(home), Path(repo)
        self.inner_socket = f'/tmp/tmux-{os.getuid()}/mishe-tauftauf/tmux-{os.getuid()}/default'

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def run(self, argv, timeout=60, env=None):
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                                  env=dict(os.environ, **(env or {})))
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, '', str(exc)
        return done.returncode, done.stdout, done.stderr

    def read(self, path):
        try:
            return Path(path).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return None

    def exists(self, path) -> bool:
        return Path(path).exists()

    def write(self, path, text: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding='utf-8')

    def append(self, path, text: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open('a', encoding='utf-8') as handle:
            handle.write(text)

    def archived_restores(self) -> list:
        found = [b.restore_archive(p.name, self.read(p)) for p in sorted(STATE.glob('tripped-*.json'))]
        return [item for item in found if item is not None]

    def latch_archive_times(self) -> list:
        """Rearm times (seconds) from the archive names; the archive is written when the latch is cleared."""
        times = []
        for p in STATE.glob('tripped-*.json'):
            stem = p.name[len('tripped-'):-len('.json')]
            if stem.isdigit():
                times.append(int(stem) / 1e9)
        return times

    def inside_mishe(self) -> bool:
        pid = os.getpid()
        for _ in range(64):
            cgroup, status = self.read(f'/proc/{pid}/cgroup'), self.read(f'/proc/{pid}/status')
            if cgroup is None or status is None:
                return True                       # unobservable ancestry is treated as inside
            if b.in_mishe_slice(cgroup):
                return True
            parent = b.parent_pid(status)
            if parent is None:
                return True
            if parent <= 1:
                return False
            pid = parent
        return True


@dataclass
class Probe:
    state: str | None = None
    latch: dict | None = None
    emergency: bool = False
    life_support: dict | None = None
    hold: bool = False
    needs_start: list = field(default_factory=list)      # wake paths / timers that are not active
    failed: list = field(default_factory=list)           # failed units (name, kind)
    views: bool = False                                  # the inner tmux session answers
    inventory: list = field(default_factory=list)        # the guard's stop inventory (units it stops on a trip)
    active_inventory: list = field(default_factory=list) # the inventory units active now


@dataclass
class Result:
    code: int = 0
    lines: list = field(default_factory=list)
    actions: list = field(default_factory=list)

    def say(self, text: str) -> None:
        self.lines.append(text)


def probe(h: Host) -> Probe:
    p = Probe()
    code, out, _ = h.run(['python3', GUARD, 'status'])
    status = b.decode_json(out)
    inner = status.get('status') if isinstance(status, dict) else None
    if code == 0 and isinstance(inner, dict):
        p.state, p.life_support = inner.get('state'), inner.get('life_support')
    p.latch = b.decode_json(h.read(STATE / 'tripped'))
    if p.latch is None and h.exists(STATE / 'tripped'):
        p.latch = {'at': None, 'reason': 'unreadable latch'}
    p.emergency = h.exists(STATE / 'emergency')
    p.hold = h.exists(h.home / 'hold')
    _, listing, _ = h.run(['systemctl', '--user', 'list-units', '--all', '--plain', '--no-legend',
                           'mishe-*', 'run-*.scope'])
    for name, active, _sub, description in b.unit_rows(listing):
        kind = b.unit_kind(name, description)
        if kind in ('wake-path', 'timer') and active != 'active':
            p.needs_start.append(name)
        if active == 'failed' and kind in RESETTABLE:
            p.failed.append((name, kind))
    _, sessions, _ = h.run(['tmux', '-S', h.inner_socket, 'list-sessions', '-F', '#{session_name}'])
    p.views = bool(b.session_names(sessions))
    p.inventory = inventory_units(h)
    p.active_inventory = active_units(h, p.inventory)
    return p


def inventory_units(h: Host) -> list:
    config = b.decode_json(h.read(INVENTORY))
    units = config.get('units') if isinstance(config, dict) else None
    return [u for u in units if isinstance(u, str)] if isinstance(units, list) else []


def active_units(h: Host, names) -> list:
    """Which of these user units are active now; systemd prints nothing for a name it does not know."""
    if not names:
        return []
    _, listing, _ = h.run(['systemctl', '--user', 'list-units', '--all', '--plain', '--no-legend', *names])
    return sorted(name for name, active, _sub, _desc in b.unit_rows(listing) if active == 'active' and name in names)


def load_running(h: Host) -> dict:
    doc = b.decode_json(h.read(h.home / RUNNING))
    return doc if isinstance(doc, dict) else {}


def save_running(h: Host, doc: dict) -> None:
    h.write(h.home / RUNNING, b.dumps(doc) + '\n')


def owe(doc: dict, names) -> None:
    """Add units to the owed set. A unit newly owed starts with a fresh retry budget and leaves any unknown record."""
    names = set(names)
    fresh = names - set(doc.get('owed', []))
    doc['owed'] = sorted(set(doc.get('owed', [])) | names)
    doc['attempts'] = {k: v for k, v in dict(doc.get('attempts', {})).items() if k not in fresh}
    if isinstance(doc.get('unknown'), dict):
        doc['unknown'] = {k: v for k, v in doc['unknown'].items() if k not in names}


def note_trip(h: Host, dry_run: bool) -> None:
    """A latch is present: the units active before the trip are owed a restart once the guard is rearmed."""
    if dry_run:
        return
    doc = load_running(h)
    owe(doc, doc.get('active', []))
    save_running(h, doc)


def note_healthy(h: Host, p: Probe, r: Result, dry_run: bool) -> None:
    """Healthy guard: owe the pre-trip set if a rearm happened since the last sample; then sample the running set."""
    doc = load_running(h)
    last = doc.get('checked')
    if last is not None and any(t > last for t in h.latch_archive_times()):
        owe(doc, doc.get('active', []))
        r.say(f'a trip was rearmed since the last sample; owed restarts: {", ".join(doc["owed"]) or "none"}')
    doc['active'], doc['checked'] = p.active_inventory, h.now()
    if not dry_run:
        save_running(h, doc)


def _report_unknown(r: Result, unknown: dict) -> None:
    """Every pass reports the given-up units until they are back: a silent give-up is the failure this prevents."""
    if unknown:
        r.code = max(r.code, 3)
        r.say('UNKNOWN: not back after the bounded attempts, not retried (clear the record once fixed): '
              + '; '.join(f'{name} gave up after {count} attempts' for name, count in sorted(unknown.items())))


def restart_owed(h: Host, p: Probe, r: Result, *, dry_run: bool) -> None:
    """Start the owed units that are still down. Each one that stays down is retried, bounded, and reported UNKNOWN."""
    doc = load_running(h)
    owed = [u for u in doc.get('owed', []) if u not in p.active_inventory]
    came_back = [u for u in doc.get('owed', []) if u in p.active_inventory]   # already running: owed is settled
    unknown = dict(doc['unknown']) if isinstance(doc.get('unknown'), dict) else {}
    if not owed and not unknown:
        if came_back and not dry_run:
            doc['owed'] = [u for u in doc['owed'] if u not in came_back]
            save_running(h, doc)
        return
    back_up = sorted(set(active_units(h, sorted(unknown))))
    for name in back_up:
        unknown.pop(name)
        r.say(f'{name} is back up after giving up; its UNKNOWN record is cleared')
    if p.hold and owed:
        r.say(f'the plant is HELD: {len(owed)} owed unit(s) wait for `mishe hold off`: {", ".join(owed)}')
        owed_to_start = []
    else:
        owed_to_start = owed
    attempts = dict(doc.get('attempts', {}))
    for name in owed_to_start:
        if dry_run:
            r.say(f'dry run: would systemctl --user start {name}')
            continue
        attempts[name] = attempts.get(name, 0) + 1
        code, _, err = h.run(['systemctl', '--user', 'start', name], timeout=90)
        r.actions.append(f'start {name}')
        _, enabled, _ = h.run(['systemctl', '--user', 'is-enabled', name])
        state = b.first_line(enabled)
        if state != 'enabled':
            r.say(f'{name} is not enabled ({state or "unknown"}): it will not come back after a reboot; its owner enables it')
        if code != 0:
            r.say(f'start {name} failed: {b.first_line(err)}')
    if dry_run:
        _report_unknown(r, unknown)
        return
    back = set(active_units(h, owed_to_start))
    still = [u for u in owed_to_start if u not in back]
    done = {u for u in owed_to_start if u in back} | set(came_back)
    give_up = [u for u in still if attempts.get(u, 0) >= MAX_OWED_ATTEMPTS]
    for name in give_up:
        unknown[name] = attempts[name]
    doc['unknown'] = unknown
    doc['attempts'] = {k: v for k, v in attempts.items() if k in still and k not in give_up}
    doc['owed'] = sorted((set(doc.get('owed', [])) - done) - set(give_up))
    save_running(h, doc)
    _report_unknown(r, unknown)
    retrying = [u for u in still if u not in give_up]
    if retrying:
        r.code = max(r.code, 3)
        r.say(f'UNKNOWN: {len(retrying)} owed unit(s) still down after the rearm: {", ".join(retrying)}')
    if done:
        r.say(f'restarted after rearm: {", ".join(sorted(done))}')


def describe(p: Probe) -> str:
    bits = [f'guard {p.state or "UNKNOWN"}', 'latched' if p.latch else 'no latch',
            f'{len(p.needs_start)} wake path/timer not active', f'{len(p.failed)} failed unit(s)',
            'views up' if p.views else 'views DOWN', 'HELD' if p.hold else 'not held']
    return '; '.join(bits)


def _refuse(r: Result, why: str) -> Result:
    r.code = 2
    r.say(f'REFUSED: {why}')
    return r


def restore_latch(h: Host, p: Probe, r: Result, *, auto: bool, dry_run: bool, allow_repeat: bool,
                  diagnosis: str) -> bool:
    """Clear the guard latch if every gate holds. True when the latch is gone afterwards."""
    now = h.now()
    latch = p.latch or {}
    reason = latch.get('reason')
    if p.emergency:
        _refuse(r, 'an emergency latch is present: it needs explicit host-side disposition')
        return False
    if h.inside_mishe():
        _refuse(r, 'this process runs inside /mishe.slice and the guard refuses such callers; run it from outside')
        return False
    if p.life_support is not None and p.life_support.get('state') != 'PANES_LIVE':
        _refuse(r, f'the guard requires life support and it is {p.life_support.get("state")}')
        return False
    at = latch.get('at')
    if not isinstance(at, (int, float)):
        r.code = 3
        r.say('UNDECIDABLE: the latch has no readable trip time')
        return False
    if auto and now - at < COOLING_SECONDS:
        r.say(f'waiting: the trip is {int(now - at)} s old; unattended restore starts at {COOLING_SECONDS} s')
        return False
    code, out, _ = h.run([sys.executable, str(h.repo / 'checks/restore-readiness.py'), '--json'], timeout=90)
    readiness = b.decode_json(out)
    if not isinstance(readiness, dict) or not readiness.get('stability_satisfied'):
        detail = readiness.get('detail') if isinstance(readiness, dict) else 'readiness unreadable'
        _refuse(r, f'the guard readiness gate is not satisfied: {detail}')
        return False
    restores = h.archived_restores()
    if len([t for t, _ in restores if now - t < DAY]) >= MAX_RESTORES_PER_DAY:
        _refuse(r, f'{MAX_RESTORES_PER_DAY} restores in 24 h: a repeating fault is reported to the human')
        return False
    repeats = [t for t, why in restores if why == reason and now - t < SAME_REASON_SECONDS]
    if repeats and not allow_repeat:
        _refuse(r, f'the same trip reason ("{reason}") was restored {int((now - max(repeats)) // 60)} min ago: '
                   'a repeating fault is reported to the human, not restored in a loop')
        return False
    icode, _, _ = h.run(['python3', GUARD, 'io-probe'], timeout=30)
    if icode != 0:
        r.code = 3
        r.say(f'UNDECIDABLE: the host filesystem probe failed (exit {icode}); not restoring')
        return False
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))
    evidence = h.home / 'artifacts' / f'restore-verification-{stamp}-revive.json'
    r.say(f'gates hold: trip "{reason}" {int(now - at)} s old, healthy {readiness.get("continuous_healthy_seconds")} s, '
          f'{len(restores)} earlier restore(s) on record')
    if dry_run:
        r.say(f'dry run: would clear the latch with sudo guard '
              f'{"rearm" if p.life_support is None else "restore"} (evidence {evidence.name})')
        return False
    def check(what, expected, observed, ok):
        return {'check': what, 'expected': expected, 'observed': observed, 'result': 'match' if ok else 'MISMATCH'}
    document = {
        'kind': 'restore-verification-revive', 'observed_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
        'role': 'revive', 'clears': {'tripped': {'at': at, 'reason': reason}},
        'diagnosis': {'trip_cause': f'Sentinel latched with reason: {reason}',
                      'known_cause': diagnosis or 'not established by this artifact',
                      'limits_of_this_artifact': 'Restoration evidence, not causal closure; recurrence is reported to the human.'},
        'repair': {'action': 'none by this tool; it restores only after the guard\'s own readiness gate passes'},
        'verification': [
            check('latch identity', {'at': at, 'reason': reason}, {'at': at, 'reason': reason}, True),
            check('host filesystem probe passes now', 'rc=0', f'rc={icode}', icode == 0),
            check('restore-readiness stability >= 120 s', '>=120', readiness.get('continuous_healthy_seconds'), True),
            check('no emergency latch', False, p.emergency, not p.emergency),
            check('caller outside /mishe.slice', True, True, True),
            check('same reason not restored in 3 h', 0, len(repeats), not repeats or allow_repeat)],
    }
    h.write(evidence, b.dumps(document) + '\n')
    # The installed guard clears a latch by `restore --evidence` only while life support is configured;
    # without it (the configuration since 2026-11-04) its supported path is `rearm`, and `restore`
    # raises on the missing life-support record. The gates above apply to either.
    verb = 'rearm' if p.life_support is None else 'restore'
    command = ['sudo', '-n', 'python3', GUARD] + ([] if verb == 'rearm' else ['--evidence', str(evidence)]) + [verb]
    rc, out, err = h.run(command, timeout=300)
    r.actions.append(f'guard {verb}')
    cleared = not h.exists(STATE / 'tripped')
    r.say(f'guard {verb} exit {rc}: {b.first_line(err) or b.first_line(out) or "no output"}; '
          f'latch {"cleared" if cleared else "STILL PRESENT"}')
    h.append(h.home / 'operator/guard-actions.jsonl', b.dumps(
        {'at': now, 'action': 'revive-' + verb, 'exit': 0 if cleared else 1, 'by': 'revive',
         'reason': reason, 'evidence': str(evidence)}) + '\n')
    if not cleared:
        r.code = 3
    return cleared


def wait_normal(h: Host, r: Result, seconds=300) -> bool:
    deadline = h.now() + seconds
    while True:
        _, out, _ = h.run(['python3', GUARD, 'status'])
        status = b.decode_json(out)
        state = status.get('status', {}).get('state') if isinstance(status, dict) else None
        if state in HEALTHY_STATES:
            return True
        if h.now() >= deadline:
            r.code = 3
            r.say(f'UNDECIDABLE: the guard is {state or "unreadable"} {seconds} s after the restore')
            return False
        h.sleep(5)


def revive_units(h: Host, p: Probe, r: Result, *, dry_run: bool) -> None:
    if p.hold:
        r.say('the plant is HELD (hold file present): wakes stay blocked; `mishe hold off` releases it')
    todo = [('reset-failed', name) for name, _ in p.failed] + [('start', name) for name in p.needs_start]
    for verb, name in todo:
        if dry_run:
            r.say(f'dry run: would systemctl --user {verb} {name}')
            continue
        code, _, err = h.run(['systemctl', '--user', verb, name])
        r.actions.append(f'{verb} {name}')
        if code != 0:
            r.code = max(r.code, 3)
            r.say(f'{verb} {name} failed: {b.first_line(err)}')
    if todo and not dry_run:
        r.say(f'{len(todo)} unit action(s) done; failed states were recorded in the receipt before reset')
    if not p.views:
        if dry_run:
            r.say('dry run: would restart plant-mishe-ui.service to rebuild the tmux views')
        else:
            code, _, err = h.run(['systemctl', '--user', 'restart', 'plant-mishe-ui.service'], timeout=90)
            r.actions.append('restart plant-mishe-ui.service')
            if code != 0:
                r.code = max(r.code, 3)
                r.say(f'rebuilding the views failed: {b.first_line(err)}')


def run(h: Host, *, auto=False, dry_run=False, allow_repeat=False, diagnosis='') -> Result:
    r = Result()
    p = probe(h)
    r.say(f'probe: {describe(p)}')
    if p.state is None:
        r.code = 3
        r.say('UNDECIDABLE: the guard status is unreadable; nothing changed')
        return r
    before = {'state': p.state, 'failed': [name for name, _ in p.failed], 'not_active': list(p.needs_start),
              'views': p.views}
    if p.latch is not None or p.state == 'TRIPPED':
        if p.latch is None:
            r.code = 3
            r.say('UNDECIDABLE: the guard reports TRIPPED but no latch file is readable')
            return r
        note_trip(h, dry_run)                 # before any restore attempt: the pre-trip set must survive a failed one
        if not restore_latch(h, p, r, auto=auto, dry_run=dry_run, allow_repeat=allow_repeat, diagnosis=diagnosis):
            return r
        if not wait_normal(h, r):
            return r
        p = probe(h)
    elif p.state not in HEALTHY_STATES:
        r.say(f'the guard is {p.state}; waiting for it to settle before changing anything')
        return r
    note_healthy(h, p, r, dry_run)
    restart_owed(h, p, r, dry_run=dry_run)
    if not (p.needs_start or p.failed or not p.views):
        r.say('alive: nothing to revive')
        return r
    revive_units(h, p, r, dry_run=dry_run)
    if r.actions and not dry_run:
        h.append(h.home / 'operator/guard-actions.jsonl', b.dumps(
            {'at': h.now(), 'action': 'revive-units', 'by': 'revive', 'before': before, 'did': r.actions}) + '\n')
    return r


def main(argv=None) -> int:
    args = b.arguments(argv)
    result = run(Host(args.home, args.repo), auto=args.auto, dry_run=args.dry_run,
                 allow_repeat=args.allow_repeat, diagnosis=args.diagnosis)
    print('\n'.join(result.lines))
    return result.code


if __name__ == '__main__':
    sys.exit(main())
