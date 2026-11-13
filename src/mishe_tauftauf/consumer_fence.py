"""Bounded observation of owned in-memory consumers across a pin transition.

Local systemd/cgroup adapter. No service is stopped or restarted here.
A oneshot caught mid-start (activating, cgroup or invocation not yet visible)
is transient evidence: drain retries it until the same deadline and fails
closed with the last error if it is still UNKNOWN then.
"""
import json
import os
from pathlib import Path
import subprocess
import time

PROPERTIES = 'Id,Type,ActiveState,SubState,MainPID,ControlGroup,InvocationID,ExecMainStartTimestampMonotonic,Environment,FragmentPath,ExecStart,KillMode'
TRANSIENT_UNKNOWN = ('active cgroup evidence UNKNOWN', 'invocation evidence UNKNOWN', 'active unit properties UNKNOWN')
RETRY_INTERVAL_S = 2
# A pinned oneshot consumer started just before the cutoff may run to its own TimeoutStartSec, which is the
# producer's engine --timeout + 30 (producers.load): readings 300 s -> 330 s. The drain window must cover that
# plus a margin; 150 s cut a legitimately running 300 s consumer (2026-11-13 moving-pin ca3952a6 revert).
CONSUMER_MAX_RUN_S = 330
DRAIN_MARGIN_S = 90
DRAIN_TIMEOUT_S = CONSUMER_MAX_RUN_S + DRAIN_MARGIN_S


def command(argv, timeout=10):
    return subprocess.check_output(argv, text=True, timeout=timeout)


def properties(unit):
    text = command(['systemctl', '--user', 'show', unit, '-p', PROPERTIES])
    result = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    if result.get('Id') != unit: raise ValueError('unit identity missing: ' + unit)
    return result


def inventory(home, previous=None):
    from mishe_tauftauf import release_promotion as rp
    previous = previous or {}
    owned = {str(p) for p in rp.config_paths(home) if p.suffix == '.service'}
    rows = json.loads(command(['systemctl', '--user', 'list-units', 'mishe-*.service', '--all', '--output=json', '--no-pager']))
    timers = json.loads(command(['systemctl', '--user', 'list-units', 'mishe-*.timer', '--state=active', '--output=json', '--no-pager']))
    pending = set()
    for timer in timers:
        pending.update(command(['systemctl', '--user', 'show', timer['unit'], '-p', 'Triggers', '--value']).split())
    names = {row['unit'] for row in rows} | pending | set(previous)
    result = {}
    for name in sorted(names):
        row = properties(name)
        explicit_site = '--home ' + str(home) + ' ' in row.get('ExecStart', '')
        if row.get('FragmentPath') not in owned and not explicit_site and name not in previous: continue
        if 'PYTHONPATH=' not in row.get('Environment', '') and name not in previous: continue
        row['pending_timer'] = name in pending
        result[name] = row
    return result


def cgroup_pids(group):
    if not group: return []
    path = Path('/sys/fs/cgroup') / group.lstrip('/')
    if not path.resolve().is_relative_to(Path('/sys/fs/cgroup')): raise ValueError('cgroup path escapes')
    if not path.exists(): return []  # kernel removes a cgroup only once it has no processes
    files = list(path.rglob('cgroup.procs'))
    if not files: raise ValueError('cgroup enumeration missing: ' + group)
    pids = []
    for file in files:
        try: pids.extend(int(v) for v in file.read_text().split())
        except FileNotFoundError: pass  # a removed child group has no remaining processes
    return sorted(set(pids))


def record_transition(home, directory, label, before):
    record = dict(label=label, cutoff_us=time.monotonic_ns() // 1000, before=before,
                  at=time.time(), after=inventory(home, before))
    path = directory / ('consumer-transition-' + label + '-' + str(time.time_ns()) + '.json')
    path.write_text(json.dumps(record, sort_keys=True))
    (directory / ('consumer-transition-' + label + '-latest.json')).write_text(json.dumps(dict(path=str(path))))
    return record


def pending_old(record, rows):
    pending = []
    for name, row in rows.items():
        group = row.get('ControlGroup') or record['before'].get(name, {}).get('ControlGroup', '')
        if row.get('ActiveState') in ('activating', 'active', 'deactivating', 'reloading') and (not group or not (Path('/sys/fs/cgroup') / group.lstrip('/')).exists()):
            raise ValueError('active cgroup evidence UNKNOWN: ' + name)
        pids = cgroup_pids(group)
        if not pids and row.get('ActiveState') not in ('activating', 'active', 'deactivating', 'reloading'): continue
        # A non-atomic systemctl read during a queued start can return blank properties: that is UNKNOWN (retried), not unbounded.
        if row.get('ActiveState') in ('activating', 'reloading') and not (row.get('Type') and row.get('KillMode')):
            raise ValueError('active unit properties UNKNOWN: ' + name)
        if row.get('Type') != 'oneshot' or row.get('KillMode') != 'control-group':
            raise ValueError('unbounded pinned consumer: ' + name)
        stamp = row.get('ExecMainStartTimestampMonotonic', '')
        if not stamp.isdecimal() or int(stamp) <= 0 or not row.get('InvocationID'):
            raise ValueError('invocation evidence UNKNOWN: ' + name)
        if int(stamp) <= record['cutoff_us']:
            pending.append(dict(unit=name, invocation=row['InvocationID'], start_us=int(stamp), pids=pids))
    return pending


def drain(home, directory, label, timeout=DRAIN_TIMEOUT_S, sleep=time.sleep):
    pointer = json.loads((directory / ('consumer-transition-' + label + '-latest.json')).read_text())
    evidence = Path(pointer['path']).resolve()
    if not evidence.is_relative_to(directory.resolve()): raise ValueError('transition evidence escapes owned directory')
    record = json.loads(evidence.read_text())
    if (record.get('label') != label or type(record.get('cutoff_us')) is not int
            or not 0 < record['cutoff_us'] <= time.monotonic_ns() // 1000
            or not isinstance(record.get('before'), dict) or not isinstance(record.get('after'), dict)):
        raise ValueError('transition observation UNKNOWN')
    until = time.monotonic() + timeout
    self_id = os.environ.get('INVOCATION_ID', '')
    samples = []
    try:
        while True:
            rows = inventory(home, {**record['before'], **record['after']})
            try:
                pending = pending_old(record, rows)
            except ValueError as exc:
                # Transient mid-start evidence: retry until the same deadline, then fail closed with the last error.
                if str(exc).split(':')[0] in TRANSIENT_UNKNOWN and time.monotonic() < until:
                    samples.append(dict(at=time.time(), transient=str(exc), invocations=rows))
                    sleep(RETRY_INTERVAL_S)
                    continue
                # Fail closed, but keep the rows that failed: the drain file is written in `finally`.
                samples.append(dict(at=time.time(), error=str(exc), invocations=rows))
                raise
            samples.append(dict(at=time.time(), pending=pending, invocations=rows))
            # The caller drains from inside its own invocation; that invocation cannot exit until drain returns.
            pending = [p for p in pending if not (self_id and p['invocation'] == self_id)]
            if not pending: return record
            if time.monotonic() >= until: raise ValueError('old invocations remain: ' + repr(pending))
            sleep(RETRY_INTERVAL_S)
    finally:
        (directory / ('consumer-drain-' + label + '-' + str(time.time_ns()) + '.json')).write_text(json.dumps(dict(transition=pointer, samples=samples), sort_keys=True))


def verify_future(home, expected, previous):
    from mishe_tauftauf.work_boundary import pythonpath_entries
    rows = inventory(home, previous)
    checked = []
    for name, row in rows.items():
        if not row.get('pending_timer') and row.get('ActiveState') not in ('activating', 'active', 'deactivating', 'reloading'): continue
        pins = pythonpath_entries(row.get('Environment', ''))
        if not pins or any(Path(pin).resolve() != Path(expected).resolve() for pin in pins):
            raise ValueError('active or queued consumer has wrong pin: ' + name)
        checked.append(dict(unit=name, invocation=row.get('InvocationID'), pending_timer=row.get('pending_timer'), pins=pins))
    return checked
