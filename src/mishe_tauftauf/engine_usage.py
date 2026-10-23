"""Read-only engine account inventory and quota dashboard.

Milestone 1 reports which subscription pools exist, what OMP last measured for
each account-bound window, and what is UNKNOWN. It makes no routing decision and
changes no launcher, credential or runtime. Protocol parsing lives in
engine_boundary; this module consumes decoded values only.

Attribution rule: OMP usage reports for opencode-go carry no credential identity,
so a report is attributed to a configured account only when that provider has
exactly one configured account and exactly one report. Any other shape is
UNATTRIBUTED rather than guessed.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import time

from . import engine_boundary as boundary

REPORT_TTL_MS = 180_000
FUTURE_SKEW_MS = 5_000
ERROR_LOG = 'usage-errors.jsonl'
RANK = {'UNKNOWN': 3, 'UNATTRIBUTED': 2, 'STALE': 1, 'FRESH': 0}


def _iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


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
        'registry_state': entry['state'],
        'entitlement': entry['entitlement'],
        'credential_id': entry['credential_id'],
        'evidence': entry['evidence'],
        'evidence_at': entry['evidence_at'],
        'auth': auth,
    }


def accounts(home, now_ms=None):
    """Registry and credential-store view. Reads no provider quota."""
    now = now_ms if now_ms is not None else time.time() * 1000
    store, registry = boundary.load_registry(home)
    store_error = None
    try:
        creds, blocks = boundary.credential_store(home / store)
    except ValueError as exc:
        creds, blocks, store_error = {}, {}, str(exc)
    rows = {alias: _registry_view(alias, entry, _auth(entry, creds, blocks, store_error, now))
            for alias, entry in registry.items()}
    unregistered = _unregistered(registry, creds)
    state = 'FRESH' if rows and all(r['registry_state'] == 'configured' and r['auth']['state'] == 'ok' for r in rows.values()) else 'PARTIAL'
    if store_error:
        state = 'UNKNOWN'
    return {'state': state, 'observed_at': _iso(now), 'accounts': rows,
            'unregistered_credentials': unregistered, 'store_error': store_error}


def _unregistered(registry, creds):
    referenced = {entry['credential_id'] for entry in registry.values() if entry['state'] == 'configured'}
    counts = {}
    for cid, row in creds.items():
        if cid not in referenced:
            counts[row['provider']] = counts.get(row['provider'], 0) + 1
    return counts


def _log_usage_error(home, now, usage):
    directory = home / 'engine'
    directory.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'observed_at': _iso(now), 'error': usage['error'], 'stderr': usage['stderr']}, sort_keys=True)
    path = directory / ERROR_LOG
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def status(home, now_ms=None, omp='omp'):
    """Full dashboard: registry, credential metadata, and OMP usage windows with freshness."""
    now = now_ms if now_ms is not None else time.time() * 1000
    store, registry = boundary.load_registry(home)
    store_error = None
    try:
        creds, blocks = boundary.credential_store(home / store)
    except ValueError as exc:
        creds, blocks, store_error = {}, {}, str(exc)
    usage = boundary.usage(home / store, omp)
    if usage['error']:
        _log_usage_error(home, now, usage)
    reports_by_provider = {}
    for report in usage['reports']:
        reports_by_provider.setdefault(report['provider'], []).append(report)
    rows = {}
    for alias, entry in registry.items():
        auth = _auth(entry, creds, blocks, store_error, now)
        row = _registry_view(alias, entry, auth)
        if entry['state'] != 'configured':
            row.update(state='NOT_CONFIGURED', reason='validated but no credential configured', windows=[],
                       fetched_at=None, age_seconds=None)
        elif usage['error']:
            row.update(state='UNKNOWN', reason=f"usage read failed: {usage['error']}", windows=[],
                       fetched_at=None, age_seconds=None)
        elif auth['state'] not in ('ok', 'blocked'):
            row.update(state='UNKNOWN', reason=auth['reason'], windows=[], fetched_at=None, age_seconds=None)
        else:
            row.update(_attribute(entry, alias, reports_by_provider, registry, now))
        rows[alias] = row
    gaps = {
        'usage_error': usage['error'],
        'store_error': store_error,
        'unregistered_credentials': _unregistered(registry, creds),
        'without_usage': usage['without_usage'],
        'disabled': usage['disabled'],
    }
    if all(r['state'] == 'FRESH' for r in rows.values()) and rows and not any(gaps[k] for k in gaps):
        state = 'FRESH'
    elif any(r['state'] == 'FRESH' for r in rows.values()):
        state = 'PARTIAL'
    else:
        state = 'UNKNOWN'
    return {'state': state, 'observed_at': _iso(now), 'ttl_seconds': REPORT_TTL_MS // 1000,
            'omp_generated_at': _iso(usage['generated_ms']), 'accounts': rows, 'coverage': gaps}


def _attribute(entry, alias, reports_by_provider, registry, now):
    provider = entry['provider']
    configured = [a for a, e in registry.items() if e['provider'] == provider and e['state'] == 'configured']
    reports = reports_by_provider.get(provider, [])
    if not reports:
        return {'state': 'UNKNOWN', 'reason': 'no provider report', 'windows': [], 'fetched_at': None, 'age_seconds': None}
    if len(configured) > 1:
        return {'state': 'UNATTRIBUTED', 'reason': 'several accounts share this provider; OMP report carries no credential identity',
                'windows': [], 'fetched_at': None, 'age_seconds': None}
    if len(reports) > 1:
        return {'state': 'UNATTRIBUTED', 'reason': 'several reports for one configured account', 'windows': [],
                'fetched_at': None, 'age_seconds': None}
    report = reports[0]
    if report['problem']:
        return {'state': 'UNKNOWN', 'reason': report['problem'], 'windows': [], 'fetched_at': None, 'age_seconds': None}
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
        'age_seconds': round((now - report['fetched_ms']) / 1000, 1),
    }


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
        if row.get('fetched_at'):
            lines.append(f"  fetched {row['fetched_at']} age {row['age_seconds']}s")
        for w in row.get('windows', []):
            remaining = w['remaining_fraction']
            pct = f"{remaining * 100:.0f}%" if remaining is not None else 'n/a'
            lines.append(f"  {w['limit']:<16} remaining {pct:<5} resets {w['reset_at']}  {w['state']}")
        if row.get('evidence'):
            lines.append(f"  evidence: {row['evidence']} ({row.get('evidence_at')})")
    return '\n'.join(lines)


def cmd_engine(args) -> int:
    if args.engine_command == 'accounts':
        report = accounts(args.home)
        ok = report['state'] == 'FRESH'
    else:
        report = status(args.home)
        ok = report['state'] == 'FRESH'
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report) if args.engine_command == 'status' else json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 3


def register(sub):
    engine = sub.add_parser('engine', help='Read-only subscription account inventory and quota dashboard').add_subparsers(dest='engine_command', required=True)
    p = engine.add_parser('accounts', help='Registered accounts and credential state (no provider reads)')
    p.add_argument('--json', action='store_true'); p.set_defaults(func=cmd_engine)
    p = engine.add_parser('status', help='Per-account quota windows with provider freshness')
    p.add_argument('--json', action='store_true'); p.set_defaults(func=cmd_engine)
