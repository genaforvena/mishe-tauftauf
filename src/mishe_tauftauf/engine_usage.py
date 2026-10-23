"""Read-only engine account inventory and quota dashboard.

Milestone 1 reports which subscription pools exist, what OMP last measured for
each account-bound window, and what is UNKNOWN. It makes no routing decision and
changes no launcher, credential or runtime. Protocol parsing lives in
engine_boundary; this module consumes decoded values only.

Each account reads quota through its own OMP credential store. OMP usage reports
carry no credential identity, so a report is attributed to a configured account
only when that account's store holds exactly one configured account for the
provider and yields exactly one report for it. Any other shape is UNATTRIBUTED
rather than guessed. Two accounts whose quota windows reset at identical instants
are treated as one entitlement under two credentials; both stay UNATTRIBUTED.

Last-good retention: a successful FRESH or STALE sample is kept in
engine/last-good.json. When a later read fails the row is UNKNOWN and carries that
sample as `last_good`, labelled with its own age; it is never presented as current.
"""
from __future__ import annotations

from datetime import datetime, timezone
import contextlib
import json
import os
import tempfile
import time

from . import engine_boundary as boundary

REPORT_TTL_MS = 180_000
FUTURE_SKEW_MS = 5_000
ERROR_LOG = 'usage-errors.jsonl'
LAST_GOOD = 'last-good.json'
RANK = {'UNKNOWN': 3, 'UNATTRIBUTED': 2, 'STALE': 1, 'FRESH': 0}


def _iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _pct(remaining):
    return f"{remaining * 100:.0f}%" if remaining is not None else 'n/a'


def _window_verdict(window, fetched, now):
    if window['problem']:
        return 'UNKNOWN', window['problem']
    if fetched - now > FUTURE_SKEW_MS:
        return 'UNKNOWN', 'provider fetch timestamp is in the future'
    if window['reset_ms'] <= fetched:
        return 'UNKNOWN', 'reset precedes provider observation'
    if now - fetched > REPORT_TTL_MS:
        return 'STALE', 'provider reading older than report TTL'
    return 'FRESH', None


def _auth(entry, creds, blocks, store_error, now):
    if entry['state'] != 'configured':
        return {'state': 'not-configured', 'reason': None, 'blocked_until': None}
    if store_error:
        return {'state': 'unknown', 'reason': store_error, 'blocked_until': None}
    row = creds.get(entry['credential_id'])
    if row is None:
        return {'state': 'missing', 'reason': 'credential reference absent from store', 'blocked_until': None}
    if row['provider'] != entry['provider']:
        return {'state': 'mismatch', 'reason': 'credential provider differs from registry', 'blocked_until': None}
    if row['disabled']:
        return {'state': 'disabled', 'reason': 'credential disabled by OMP', 'blocked_until': None}
    active = [b['blocked_until_ms'] for b in blocks.get(entry['credential_id'], [])
              if type(b['blocked_until_ms']) in (int, float) and b['blocked_until_ms'] > now]
    if active:
        return {'state': 'blocked', 'reason': 'OMP credential block active', 'blocked_until': _iso(max(active))}
    return {'state': 'ok', 'reason': None, 'blocked_until': None}


def _registry_view(alias, entry, auth):
    return {
        'alias': alias,
        'provider': entry['provider'],
        'store': entry['store'],
        'registry_state': entry['state'],
        'entitlement': entry['entitlement'],
        'credential_id': entry['credential_id'],
        'evidence': entry['evidence'],
        'evidence_at': entry['evidence_at'],
        'auth': auth,
    }


def _credential_stores(home, registry):
    """Credential metadata for each store a configured account reads: {store: (creds, blocks, error)}."""
    stores = {}
    for entry in registry.values():
        if entry['state'] != 'configured' or entry['store'] in stores:
            continue
        try:
            creds, blocks = boundary.credential_store(home / entry['store'])
            stores[entry['store']] = (creds, blocks, None)
        except ValueError as exc:
            stores[entry['store']] = ({}, {}, str(exc))
    return stores


def _unregistered(registry, store, creds):
    referenced = {e['credential_id'] for e in registry.values() if e['state'] == 'configured' and e['store'] == store}
    counts = {}
    for cid, row in creds.items():
        if cid not in referenced:
            counts[row['provider']] = counts.get(row['provider'], 0) + 1
    return counts


def _unregistered_by_store(registry, stores):
    found = {}
    for store, (creds, _, error) in stores.items():
        counts = {} if error else _unregistered(registry, store, creds)
        if counts:
            found[store] = counts
    return found


def accounts(home, now_ms=None):
    """Registry and credential-store view. Reads no provider quota."""
    now = now_ms if now_ms is not None else time.time() * 1000
    _, registry = boundary.load_registry(home)
    stores = _credential_stores(home, registry)
    rows = {}
    for alias, entry in registry.items():
        creds, blocks, store_error = stores.get(entry['store'], ({}, {}, None))
        rows[alias] = _registry_view(alias, entry, _auth(entry, creds, blocks, store_error, now))
    store_errors = {store: err for store, (_, _, err) in stores.items() if err}
    state = 'FRESH' if rows and all(r['registry_state'] == 'configured' and r['auth']['state'] == 'ok' for r in rows.values()) else 'PARTIAL'
    if store_errors:
        state = 'UNKNOWN'
    return {'state': state, 'observed_at': _iso(now), 'accounts': rows,
            'unregistered_credentials': _unregistered_by_store(registry, stores), 'store_errors': store_errors}


def _log_usage_error(home, now, store, usage):
    directory = home / 'engine'
    directory.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'observed_at': _iso(now), 'store': store, 'error': usage['error'],
                       'stderr': usage['stderr']}, sort_keys=True)
    path = directory / ERROR_LOG
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def _save_last_good(home, samples):
    directory = home / 'engine'
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.last-good-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'version': 1, 'accounts': samples}, handle, sort_keys=True)
        os.replace(tmp, directory / LAST_GOOD)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _attribute(entry, registry, store_reports, now):
    provider = entry['provider']
    peers = [a for a, e in registry.items()
             if e['state'] == 'configured' and e['store'] == entry['store'] and e['provider'] == provider]
    reports = [r for r in store_reports if r['provider'] == provider]
    empty = {'windows': [], 'fetched_at': None, 'fetched_ms': None, 'age_seconds': None}
    if not reports:
        return {'state': 'UNKNOWN', 'reason': 'no provider report', **empty}
    if len(peers) > 1:
        return {'state': 'UNATTRIBUTED',
                'reason': 'several accounts share this credential store and provider; OMP report carries no credential identity',
                **empty}
    if len(reports) > 1:
        return {'state': 'UNATTRIBUTED', 'reason': 'several reports for one configured account', **empty}
    report = reports[0]
    if report['problem']:
        return {'state': 'UNKNOWN', 'reason': report['problem'], **empty}
    verdicts, windows = [], []
    for window in report['windows']:
        verdict, reason = _window_verdict(window, report['fetched_ms'], now)
        verdicts.append((verdict, reason))
        windows.append({
            'limit': window['id'],
            'window': window['window_id'],
            'label': window['label'],
            'remaining_fraction': window['remaining_fraction'],
            'reset_at': _iso(window['reset_ms']),
            'duration_ms': window['duration_ms'],
            'provider_status': window['provider_status'],
            'state': verdict,
            'reason': reason,
        })
    if not windows:
        worst, reason = 'UNKNOWN', 'report carries no windows'
    else:
        worst, reason = max(verdicts, key=lambda pair: RANK[pair[0]], default=('UNKNOWN', None))
    return {
        'state': worst,
        'reason': reason,
        'windows': windows,
        'fetched_at': _iso(report['fetched_ms']),
        'fetched_ms': report['fetched_ms'],
        'age_seconds': round((now - report['fetched_ms']) / 1000, 1),
    }


def _shared_windows(rows, registry):
    """Mark configured accounts of one provider whose windows reset at identical instants."""
    signatures = {alias: {(w['limit'], w['window'], w['reset_at']) for w in row['windows'] if w['reset_at']}
                  for alias, row in rows.items()
                  if row['state'] in ('FRESH', 'STALE') and registry[alias]['state'] == 'configured'}
    flagged = set()
    aliases = sorted(signatures)
    for i, a in enumerate(aliases):
        for b in aliases[i + 1:]:
            if rows[a]['provider'] == rows[b]['provider'] and signatures[a] & signatures[b]:
                flagged |= {a, b}
    for alias in flagged:
        rows[alias].update(state='UNATTRIBUTED', windows=[], fetched_at=None, fetched_ms=None, age_seconds=None,
                           reason='window resets match another account; possibly one entitlement under two credentials')
    return flagged


def status(home, now_ms=None, omp='omp'):
    """Full dashboard: registry, credential metadata, per-store OMP usage windows with freshness and last-good samples."""
    now = now_ms if now_ms is not None else time.time() * 1000
    _, registry = boundary.load_registry(home)
    stores = _credential_stores(home, registry)
    # A store whose metadata cannot be read is not handed to OMP, which could migrate it.
    usages = {store: boundary.usage(home / store, omp) for store, (_, _, err) in stores.items() if not err}
    for store, usage in usages.items():
        if usage['error']:
            _log_usage_error(home, now, store, usage)
    rows = {}
    for alias, entry in registry.items():
        creds, blocks, store_error = stores.get(entry['store'], ({}, {}, None))
        auth = _auth(entry, creds, blocks, store_error, now)
        row = _registry_view(alias, entry, auth)
        if entry['state'] != 'configured':
            row.update(state='NOT_CONFIGURED', reason='validated but no credential configured', windows=[],
                       fetched_at=None, fetched_ms=None, age_seconds=None)
        elif auth['state'] not in ('ok', 'blocked'):
            row.update(state='UNKNOWN', reason=auth['reason'], windows=[], fetched_at=None, fetched_ms=None,
                       age_seconds=None)
        elif usages[entry['store']]['error']:
            row.update(state='UNKNOWN', reason=f"usage read failed: {usages[entry['store']]['error']}", windows=[],
                       fetched_at=None, fetched_ms=None, age_seconds=None)
        else:
            row.update(_attribute(entry, registry, usages[entry['store']]['reports'], now))
        rows[alias] = row
    flagged = _shared_windows(rows, registry)

    last_good, last_good_error = boundary.last_good_samples(home / 'engine' / LAST_GOOD)
    samples = {}
    for alias, row in rows.items():
        if row['state'] in ('FRESH', 'STALE') and row['fetched_ms'] is not None:
            samples[alias] = {'fetched_ms': row['fetched_ms'], 'windows': row['windows']}
        elif row['state'] == 'UNKNOWN' and alias in last_good:
            sample = last_good[alias]
            row['last_good'] = {'fetched_at': _iso(sample['fetched_ms']),
                                'age_seconds': round((now - sample['fetched_ms']) / 1000, 1),
                                'windows': sample['windows']}
    if samples and not last_good_error:
        retained = {alias: s for alias, s in last_good.items() if alias in registry}
        retained.update(samples)
        _save_last_good(home, retained)
    for row in rows.values():
        row.pop('fetched_ms', None)
        row.setdefault('last_good', None)

    gaps = {
        'usage_errors': {store: u['error'] for store, u in usages.items() if u['error']},
        'store_errors': {store: err for store, (_, _, err) in stores.items() if err},
        'unregistered_credentials': _unregistered_by_store(registry, stores),
        'without_usage': {store: u['without_usage'] for store, u in usages.items() if u['without_usage']},
        'disabled': {store: u['disabled'] for store, u in usages.items() if u['disabled']},
        'shared_windows': sorted(flagged),
        'last_good': last_good_error,
    }
    if rows and all(r['state'] == 'FRESH' for r in rows.values()) and not any(gaps.values()):
        state = 'FRESH'
    elif any(r['state'] == 'FRESH' for r in rows.values()):
        state = 'PARTIAL'
    else:
        state = 'UNKNOWN'
    return {'state': state, 'observed_at': _iso(now), 'ttl_seconds': REPORT_TTL_MS // 1000,
            'omp_generated_at': {store: _iso(u['generated_ms']) for store, u in usages.items() if u['generated_ms']},
            'accounts': rows, 'coverage': gaps}


def render(report):
    lines = [f"engine {report['state']}  observed {report['observed_at']}"]
    for alias, row in report['accounts'].items():
        auth = row['auth']
        head = f"{alias:<10} {row['provider']:<13} {row['state']:<14} auth={auth['state']}"
        if auth.get('blocked_until'):
            head += f" blocked_until={auth['blocked_until']}"
        if row.get('reason'):
            head += f"  ({row['reason']})"
        lines.append(head)
        lines.append(f"  store {row['store']}")
        if row.get('fetched_at'):
            lines.append(f"  fetched {row['fetched_at']} age {row['age_seconds']}s")
        for w in row.get('windows', []):
            lines.append(f"  {w['limit']:<16} remaining {_pct(w['remaining_fraction']):<5} resets {w['reset_at']}  {w['state']}")
        last = row.get('last_good')
        if last:
            lines.append(f"  last good {last['fetched_at']} age {last['age_seconds']}s (not current)")
            for w in last['windows']:
                lines.append(f"    {w['limit']:<16} remaining {_pct(w['remaining_fraction']):<5} resets {w['reset_at']}")
        if row.get('evidence'):
            lines.append(f"  evidence: {row['evidence']} ({row.get('evidence_at')})")
    gaps = {k: v for k, v in (report.get('coverage') or {}).items() if v}
    if gaps:
        lines.append('coverage gaps: ' + json.dumps(gaps, sort_keys=True))
    return '\n'.join(lines)


def cmd_engine(args) -> int:
    report = accounts(args.home) if args.engine_command == 'accounts' else status(args.home)
    if args.json or args.engine_command == 'accounts':
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0 if report['state'] == 'FRESH' else 3


def register(sub):
    engine = sub.add_parser('engine', help='Read-only subscription account inventory and quota dashboard').add_subparsers(dest='engine_command', required=True)
    p = engine.add_parser('accounts', help='Registered accounts and credential state (no provider reads)')
    p.add_argument('--json', action='store_true'); p.set_defaults(func=cmd_engine)
    p = engine.add_parser('status', help='Per-account quota windows with provider freshness')
    p.add_argument('--json', action='store_true'); p.set_defaults(func=cmd_engine)
