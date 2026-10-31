"""Durable site-local model replacement, activation, status and exact reversion.

This service consumes typed boundary values. It does not infer shell semantics
or replay interrupted work. Fresh starts are explicit abandonment, not success.
"""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

from . import model_boundary as boundary
from . import dispositions
from . import seed
from .activity import roles
from .feed import Feed
from .observations import executable, validate_home, validate_home_binding, validate_slug


def now():
    return datetime.now(timezone.utc).timestamp()


def site_home(home):
    home = Path(home).resolve()
    validate_home_binding(home)
    validate_home(home)
    if (home / 'model-changes').is_symlink():
        raise ValueError('model change store cannot be a symlink')
    return home


class LockBusy(RuntimeError):
    """A bounded seed-lock acquisition gave up while another holder kept it."""


@contextmanager
def _seed_lock(home, wait=None):
    """Hold the plant-wide seed lock, optionally giving up at a deadline.

    ``wait=None`` reuses the shared blocking primitive, which is what the
    interactive commands want. A finite ``wait`` polls instead and raises
    ``LockBusy``. Every resident supervisor holds this lock across slow steps
    (wall.tick scans, captures a pane, renders and reads the chat tape under
    it), so the supervised watcher cannot afford an unbounded wait: it would
    outlive its own watchdog, be killed mid-cycle and publish nothing. The
    bound lives here rather than in seed._lock because the live watcher runs
    from a snapshot tree whose seed.py is the pinned release's copy.
    """
    if wait is None:
        with seed._lock(home):
            yield
        return
    if not (wait >= 0):
        raise ValueError('lock wait must be nonnegative')
    validate_home(home)
    with (home / '.seed.lock').open('a+') as handle:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(f'seed lock held elsewhere for {wait:g}s')
                time.sleep(0.2)
        yield


def inventory(home, selected=None):
    home = site_home(home)
    monitored = roles(home)
    if selected:
        for role in selected:
            validate_slug(role)
            if role not in monitored:
                raise ValueError(f'unregistered mind {role}')
    result = {}
    for role in sorted(selected or monitored):
        path = home / 'minds' / role
        if not path.exists() and not selected:
            continue
        try:
            if path.is_symlink() or not executable(path):
                raise ValueError('mind must be a regular executable launcher')
            result[role] = boundary.launcher(path.read_text())
        except (OSError, ValueError, UnicodeError) as exc:
            result[role] = str(exc)
    return result


def effort_for(config, available, policy):
    old = config.effort
    if policy == 'highest':
        return next((x for x in reversed(boundary.EFFORTS) if x in available), 'off')
    if old is None or old in available or old == 'off':
        return old
    if policy == 'preserve':
        raise ValueError(f'effort {old} unsupported; use --effort compatible or highest')
    allowed = boundary.EFFORTS[:boundary.EFFORTS.index(old) + 1]
    value = next((x for x in reversed(allowed) if x in available), None)
    if value is None:
        raise ValueError('no compatible effort available')
    return value


def _existing(home, source, target, selected, effort, fresh):
    root = home / 'model-changes'
    if not root.exists():
        return None
    paths = sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        if not path.is_dir():
            continue
        try:
            _, receipt = boundary.receipt(home, path.name)
        except (OSError, ValueError, KeyError, TypeError):
            # Retain incomplete/damaged attempts for watch_once to expose and
            # route. They cannot authorize writes or block unrelated work.
            continue
        if (receipt['source'] == source and receipt['target'] == target
                and receipt.get('selected') == sorted(selected or [])
                and receipt.get('effort_policy') == effort and receipt.get('fresh_start') == fresh
                and not receipt.get('reverting')):
            return receipt['id']
    return None


def replace(home, source, target, *, selected=None, effort='preserve', dry_run=False, fresh_start=False):
    home = site_home(home)
    boundary.identity(source)
    boundary.identity(target)
    if source == target:
        raise ValueError('source and target models are identical')
    if effort not in ('preserve', 'compatible', 'highest'):
        raise ValueError('invalid effort policy')
    if not dry_run and (previous := _existing(home, source, target, selected, effort, fresh_start)):
        return advance(home, previous)
    configs = inventory(home, selected)
    matched = {role: config for role, config in configs.items() if not isinstance(config, str) and config.model == source}
    unknown = {role: config for role, config in configs.items() if isinstance(config, str)}
    if not matched:
        raise ValueError(f'No registered minds match exact source {source}; UNKNOWN roles: {unknown}')
    planned, payloads = {}, {}
    catalog = {}
    for role, config in matched.items():
        key = boundary.digest(config.text.encode())
        # Validate using each effective environment/config, without credentials
        # copied into operation metadata. Identical scripts can share a probe.
        if key not in catalog:
            catalog[key] = boundary.preflight(config, target)
        chosen = effort_for(config, catalog[key]['thinking'], effort)
        changed = boundary.rewrite(config, target, chosen).encode()
        original = config.text.encode()
        mode = stat.S_IMODE((home / 'minds' / role).stat().st_mode)
        planned[role] = {'before': boundary.digest(original), 'after': boundary.digest(changed),
                         'mode': mode, 'effort': chosen, 'previous_effort': config.effort,
                         'state': 'selected', 'kind': config.kind}
        payloads[role] = original, changed
    if dry_run:
        return {'state': 'preview', 'source': source, 'target': target, 'roles': planned, 'unknown': unknown,
                'note': 'Catalog only; provider/auth probe and process activation occur on apply.'}
    probes = {}
    for role, config in matched.items():
        key = boundary.digest(config.text.encode())
        if key not in probes:
            probes[key] = boundary.probe(config, target, planned[role]['effort'])
        planned[role]['probe'] = probes[key]
    with seed._lock(home):
        # Another command may have finished preflight in parallel.
        if previous := _existing(home, source, target, selected, effort, fresh_start):
            return _status(home, previous)
        for role, row in planned.items():
            path = home / 'minds' / role
            if path.is_symlink() or boundary.digest(path.read_bytes()) != row['before']:
                raise ValueError(f'peer edited {role} during preflight; nothing changed')
        change = uuid.uuid4().hex
        root = home / 'model-changes' / change
        root.mkdir(parents=True)
        receipt = {'version': 1, 'id': change, 'home': str(home), 'source': source, 'target': target,
                   'selected': sorted(selected or []), 'effort_policy': effort, 'fresh_start': fresh_start,
                   'created': now(), 'roles': planned, 'unknown': unknown}
        for role, (before, after) in payloads.items():
            directory = root / role
            directory.mkdir()
            boundary.atomic(directory / 'before', before, 0o400)
            boundary.atomic(directory / 'after', after, 0o400)
        # Persist complete intent before the first launcher mutation.
        boundary.save(root / 'receipt.json', receipt)
        for role, row in planned.items():
            try:
                boundary.atomic(home / 'minds' / role, payloads[role][1], row['mode'])
                row['state'] = 'queued'
                boundary.save(root / 'receipt.json', receipt)
            except OSError:
                row.update(state='unknown', reason='launcher write interrupted; inspect retained intent')
                boundary.save(root / 'receipt.json', receipt)
                raise
    return advance(home, change)


def _activate(home, change, root, receipt, role, row):
    reverting = bool(receipt.get('reverting'))
    marker = change + ('-revert' if reverting else '')
    desired = receipt['source'] if reverting else receipt['target']
    snapshot = 'before' if reverting else 'after'
    directory = root / role / 'revert-run' if reverting else root / role
    directory.mkdir(exist_ok=True)
    session, before = boundary.process(home, role)
    if before['marker'] == marker:
        # Recover only the bookkeeping after a proven respawn; never issue
        # another restart when a prior activation has already taken effect.
        if row.get('state') == 'activating':
            row.update(state='activated', after_process=before)
            _, pending, settled, cleared, *_ = seed._state(home, role)
            abandoned = row.get('abandoned_wake')
            if abandoned and pending is None and settled == abandoned and cleared != abandoned:
                Feed(home).append('seed', f'seed clear {role} after={abandoned}\nModel change {change} reconciled observed activation; no wake replay.')
        return
    if before['model'] == desired and row.get('after_process'):
        return
    _, pending, settled, cleared, *_ = seed._state(home, role)
    if row.get('state') == 'activating':
        raise ValueError('interrupted activation requires inspection; no automatic repeated restart')
    if not receipt['fresh_start']:
        if pending or (home / 'journals' / f'{role}-native.busy').exists() or not boundary.idle(home, session, role):
            row.update(state='queued', reason='waiting for canonical idle; --fresh-start explicitly abandons current wake')
            return
    # Never kill the calling agent through its own parent pane.
    if boundary.is_ancestor(before['pid']):
        row.update(state='queued', reason='calling mind cannot restart its own ancestor; watcher will activate')
        return
    target = f'{session}:{role}.1'
    socket = boundary.owned_socket(home, session)
    captured = seed._tmux('capture-pane', '-p', '-S', '-5000', '-t', target, socket=socket, check=False)
    if captured.returncode:
        raise ValueError('cannot preserve prior pane evidence')
    boundary.atomic(directory / 'prior-pane.txt', captured.stdout, 0o400)
    # A dedicated launch/session archive binds provider completions to this
    # operation, rather than confusing another resident's successful request.
    config = boundary.launcher((root / role / snapshot).read_text())
    launch = boundary.instrument(config, marker, directory)
    boundary.atomic(directory / 'launch', launch.encode(), 0o700)
    row.update(state='activating', before_process=before, abandoned_wake=pending,
               previous_settled=settled, activation_at=now())
    boundary.save(root / 'receipt.json', receipt)
    _, current_process = boundary.process(home, role)
    if (current_process['pid'], current_process['start']) != (before['pid'], before['start']):
        row.update(state='conflict', reason='process changed during activation planning')
        raise ValueError('process changed before activation; no restart performed')
    # Record abandonment before the restart: a crash at any later instruction
    # cannot leave the old wake pending for the supervisor to redeliver.
    if pending:
        Feed(home).append('seed', f'seed yield {role} wake={pending} continue=1\n'
            f'Turn settled (abandoned); explicit model fresh start {change}. '
            f'Prior effects remain UNKNOWN. Original history and pane retained at {directory}. '
            'Inspect durable state before repeating any external action.')
        Feed(home).append('model-change', f'[dm] to={role}\n'
            f'Model fresh start {change} abandoned wake {pending}; old wake must not be replayed. '
            f'Prior external effects are UNKNOWN. Inspect durable state and {directory}/prior-pane.txt before repeating actions.')
    else:
        Feed(home).append('model-change', f'[dm] to={role}\nModel selection changed by {change}; inspect current durable state and your dashboard on the next wake.')
    argv = list(seed._mind_launch_argv(home, role))
    argv[-1] = str(directory / 'launch')
    seed._tmux('respawn-pane', '-k', '-t', target, *argv, socket=socket)
    # One bounded process-start wait; do not repeat an uncertain respawn.
    for _ in range(30):
        try:
            _, after = boundary.process(home, role)
            if after['pid'] != before['pid'] and after['marker'] == marker:
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    else:
        raise ValueError('activation identity unavailable; inspect before retry')
    row.update(state='activated', after_process=after)
    if pending or (settled and settled != cleared):
        Feed(home).append('seed', f'seed clear {role} after={pending or settled}\nModel change {change} rotated this mind; original evidence retained.')
    row['reason'] = 'new process observed; waiting for resident provider completion'


def advance(home, change, lock_wait=None):
    home = site_home(home)
    with _seed_lock(home, wait=lock_wait):
        root, receipt = boundary.receipt(home, change)
        for role, row in receipt['roles'].items():
            try:
                path = home / 'minds' / role
                current = boundary.digest(path.read_bytes())
                if path.is_symlink() or current not in (row['before'], row['after']):
                    row.update(state='conflict', reason='launcher changed after this operation')
                    boundary.save(root / 'receipt.json', receipt)
                    continue
                if row['state'] == 'conflict':
                    continue
                wanted = 'before' if receipt.get('reverting') else 'after'
                if current != row[wanted]:
                    if row['state'] != 'selected':
                        row.update(state='conflict', reason='configured model changed after selection')
                        boundary.save(root / 'receipt.json', receipt)
                        continue
                    boundary.atomic(path, (root / role / wanted).read_bytes(), row['mode'])
                    row['state'] = 'queued'
                    boundary.save(root / 'receipt.json', receipt)
                _activate(home, change, root, receipt, role, row)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                row.update(reason=str(exc))
                if row['state'] != 'activating':
                    row['state'] = 'queued' if not seed.recorded_session(home) else 'unknown'
            boundary.save(root / 'receipt.json', receipt)
    return status(home, change)


def role_reason(roles):
    """Name the per-role cause so a top-level UNKNOWN is never reported as unnamed."""
    named = [f"{role}: {row['reason']}" for role, row in sorted(roles.items()) if row.get('reason')]
    return '; '.join(named)[:200] or None


def _status(home, change):
    root, receipt = boundary.receipt(home, change)
    report = {'id': change, 'source': receipt['source'], 'target': receipt['target'],
              'created': receipt['created'], 'checked': now(), 'roles': {}, 'unknown': receipt['unknown']}
    for role, row in receipt['roles'].items():
        result = dict(row)
        path = home / 'minds' / role
        reverting = bool(receipt.get('reverting'))
        expected = row['before'] if reverting else row['after']
        if path.is_symlink() or boundary.digest(path.read_bytes()) != expected:
            result.update(state='conflict', reason='desired launcher differs from receipt')
        elif row['state'] not in ('activating', 'conflict'):
            observed = boundary.observe(home, role, change + ('-revert' if reverting else ''),
                receipt['source'] if reverting else receipt['target'],
                root / role / 'revert-run' if reverting else root / role)
            result.update(observed)
            if observed['state'] == 'adopted' and row.get('after_process'):
                # Delivery of the operation is historical after a normal
                # rotation. Keep it separate from the new process's provider
                # health rather than repeatedly restarting a healthy mind.
                previous = boundary.completion(
                    root / role / 'revert-run' if reverting else root / role,
                    receipt['source'] if reverting else receipt['target'],
                    row['after_process']['started'])
                if previous == 'verified':
                    result.update(state='reverted' if reverting else 'delivered',
                        reason='operation provider completion retained; current process selects intended model',
                        current_provider='unknown')
            if reverting and observed['state'] == 'verified':
                result['state'] = 'reverted'
            if row['state'] == 'queued' and observed['state'] in ('unknown', 'queued'):
                result['state'] = 'queued'
                result['reason'] = row.get('reason', observed.get('reason'))
        report['roles'][role] = result
    states = {r['state'] for r in report['roles'].values()}
    report['state'] = ('reverted' if states == {'reverted'} else 'verified' if states == {'verified'}
                       else 'delivered' if states <= {'verified', 'delivered'} and 'delivered' in states
                       else 'unknown' if states & {'unknown', 'conflict', 'activating', 'failed'}
                       else 'pending')
    if report['state'] not in ('verified', 'delivered', 'reverted'):
        report['reason'] = role_reason(report['roles']) or receipt.get('reason')
    return report


def status(home, change):
    home = site_home(home)
    try:
        return _status(home, change)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'id': change, 'state': 'unknown', 'checked': now(), 'reason': str(exc), 'roles': {}}


def revert(home, change):
    home = site_home(home)
    with seed._lock(home):
        root, receipt = boundary.receipt(home, change)
        if receipt.get('reverting'):
            return _status(home, change)
        # Reverse intent is durable BEFORE restoring any file. A crash can
        # never make the watcher resume the previous forward direction.
        receipt['reverting'] = now()
        for role, row in receipt['roles'].items():
            path = home / 'minds' / role
            if path.is_symlink() or boundary.digest(path.read_bytes()) not in (row['before'], row['after']):
                row.update(state='conflict', reason='peer edited launcher; revert refused')
                continue
            row.update(state='selected', reason='prior config requested; waiting for observed process rollback')
            row.pop('after_process', None)
        boundary.save(root / 'receipt.json', receipt)
    return advance(home, change)


LOCK_WAIT_SECONDS = 20


def watch_once(home, lock_wait=LOCK_WAIT_SECONDS, notify=None):
    """Advance every recorded change once and publish one fresh observation.

    Each change's seed-lock acquisition is bounded. Resident supervisors hold
    that lock across slow steps, so an unbounded wait outlives this unit's
    watchdog, gets the watcher killed mid-cycle and leaves the freshness check
    reading a stale snapshot. A cycle that cannot observe says so and stays
    alive to retry.
    """
    home = site_home(home)
    reports = []
    root = home / 'model-changes'
    alerts_path = home / 'checks' / 'model-change-alerts.json'
    try:
        alerts = boundary.load(alerts_path)
    except (OSError, ValueError):
        alerts = {}  # unreadable memory re-alerts once; it never mutes
    for path in sorted(root.iterdir()) if root.exists() else []:
        if not path.is_dir():
            continue
        if notify:
            notify()
        try:
            report = advance(home, path.name, lock_wait=lock_wait)
        except LockBusy as exc:
            report = {'id': path.name, 'state': 'unknown', 'roles': {}, 'checked': now(),
                      'reason': f'observation deferred: {exc}'}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            report = {'id': path.name, 'state': 'unknown', 'reason': str(exc), 'roles': {}, 'checked': now()}
        reports.append(report)
        bad = report['state'] == 'unknown' or (report['state'] == 'pending' and now() - report['created'] > 120)
        if not bad:
            dispositions.resolve(alerts, path.name)
            continue
        reason = str(report.get('reason') or 'no named reason')[:200]
        held = dispositions.active(home, 'model-change:' + path.name, now=now())
        if held:
            # Recorded answer, still listed: the finding is not erased, only not re-sent.
            report['disposition'] = {k: held[k] for k in ('kind', 'owner', 'evidence', 'recheck_at')}
            dispositions.resolve(alerts, path.name)
            continue
        # Edge-triggered: one message per new finding or changed reason, then an
        # age ladder that widens the resolver. No time bucket in the text, so the
        # append-once guard dedupes repeats instead of being defeated by them.
        due = dispositions.edge(alerts, path.name, reason, now=now())
        if due is None:
            continue
        level, first = due
        if level == 0:
            to, who = 'health', 'Resolver=health'
            tail = f'Inspect model status and retained evidence; record the answer with `disposition set model-change:{path.name}` (expires, then returns) or fix the cause.'
        else:
            to, who = 'genome', 'Resolver=genome (escalated from health)'
            tail = f'Unresolved {(now() - first) / 3600:.1f}h after first sighting; health has not resolved or dispositioned it. Resolve, disposition with evidence, or defer to a named trigger.'
        Feed(home).append_runtime_once('model-change', f'[dm] to={to}\nModel change {path.name} {report["state"]}: {reason}; escalation={level}. {who}. {tail}')
    boundary.save(alerts_path, alerts)
    open_reports = [r for r in reports if r['state'] == 'unknown' or (r['state'] == 'pending' and now() - r['created'] > 120)]
    snapshot = {'checked': now(), 'reports': reports,
                'dispositioned': sorted(r['id'] for r in open_reports if 'disposition' in r),
                'state': 'unknown' if any('disposition' not in r for r in open_reports) else 'ok'}
    boundary.save(home / 'checks' / 'model-changes.json', snapshot)
    return snapshot


def check_watch(home):
    try:
        value = boundary.load(site_home(home) / 'checks' / 'model-changes.json')
        age = now() - value['checked']
        if not 0 <= age <= 90 or value['state'] != 'ok' or not isinstance(value['reports'], list):
            raise ValueError('model change monitoring stale or non-GREEN')
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'state': 'unknown', 'reason': str(exc)}


def _watchdog():
    if os.environ.get('NOTIFY_SOCKET'):
        subprocess.run(['systemd-notify', 'WATCHDOG=1'], check=True, timeout=5)


def watch(home, interval=30):
    if not math.isfinite(interval) or not 1 <= interval <= 60:
        raise ValueError('watch interval must be 1..60 seconds')
    while True:
        # The heartbeat brackets every bounded lock wait, so this unit's
        # liveness never depends on how long a supervisor keeps the plant lock.
        watch_once(home, notify=_watchdog)
        _watchdog()
        time.sleep(interval)
