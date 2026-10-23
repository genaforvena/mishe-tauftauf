"""Engine account inventory and quota dashboard: attribution, freshness and failure states."""
import json
import os
from pathlib import Path
import sqlite3
import stat

import pytest

from mishe_tauftauf import engine_boundary as boundary
from mishe_tauftauf import engine_usage as usage

NOW = 1_792_712_432_436
FRESH = NOW - 10_000
RESET = NOW + 3_600_000


def limit(limit_id='rolling-5h', window_id='5h', remaining=1.0, resets=RESET, unit='percent', status='ok'):
    return {
        'id': limit_id, 'label': 'x', 'status': status,
        'scope': {'provider': 'p', 'windowId': window_id, 'shared': True},
        'window': {'id': window_id, 'label': 'x', 'resetsAt': resets},
        'amount': {'used': round((1 - remaining) * 100), 'usedFraction': 1 - remaining,
                   'remainingFraction': remaining, 'unit': unit},
    }


def report(provider, fetched=FRESH, limits=None):
    return {'provider': provider, 'fetchedAt': fetched, 'limits': [limit()] if limits is None else limits, 'metadata': {}}


def usage_doc(*reports, **extra):
    doc = {'generatedAt': NOW, 'reports': list(reports), 'accountsWithoutUsage': [], 'disabledCredentials': [],
           'capacity': {}}
    doc.update(extra)
    return doc


def make_site(tmp_path, accounts, credentials=(), blocks=()):
    home = tmp_path / 'site'
    (home / 'engine').mkdir(parents=True)
    (home / 'engine' / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': accounts}))
    store = home / 'omp-recovery' / 'agent'
    store.mkdir(parents=True)
    con = sqlite3.connect(store / 'agent.db')
    con.execute('create table auth_schema_version (id integer primary key, version integer)')
    con.execute('insert into auth_schema_version values (1, 8)')
    con.execute('create table auth_credentials (id integer primary key, provider text, credential_type text, '
                'data text, disabled_cause text, identity_key text, created_at integer, updated_at integer)')
    for cid, provider, disabled in credentials:
        con.execute('insert into auth_credentials (id, provider, credential_type, data, disabled_cause) values (?,?,?,?,?)',
                    (cid, provider, 'api_key', 'SECRET-VALUE-NOT-READ', disabled))
    con.execute('create table auth_credential_blocks (credential_id integer, provider_key text, block_scope text, '
                'blocked_until_ms integer, updated_at integer)')
    for cid, scope, until in blocks:
        con.execute('insert into auth_credential_blocks values (?,?,?,?,0)', (cid, 'p', scope, until))
    con.commit()
    con.close()
    return home


def go(alias, cid):
    return {'provider': 'opencode-go', 'state': 'configured', 'credential_id': cid, 'entitlement': alias}


def fake_omp(tmp_path, payload=None, exit_code=0, sleep=0):
    body = tmp_path / 'payload.txt'
    body.write_text(payload if payload is not None else '')
    script = tmp_path / 'omp'
    script.write_text(f'#!/bin/sh\n[ "{sleep}" = 0 ] || sleep {sleep}\ncat "{body}"\nexit {exit_code}\n')
    script.chmod(0o755)
    return str(script)


def dashboard(home, omp, now=NOW):
    return usage.status(home, now_ms=now, omp=omp)


def test_two_go_entitlements_are_never_merged_into_one_report(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-b': go('b', 9)}, [(8, 'opencode-go', None), (9, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    result = dashboard(home, omp)
    assert result['accounts']['go-a']['state'] == 'UNATTRIBUTED'
    assert result['accounts']['go-b']['state'] == 'UNATTRIBUTED'
    assert result['accounts']['go-a']['windows'] == []
    assert result['state'] == 'UNKNOWN'


def test_duplicate_credential_reference_is_one_pool_and_rejected_as_two(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-dup': go('dup', 8)}, [(8, 'opencode-go', None)])
    with pytest.raises(ValueError, match='unique credential'):
        boundary.load_registry(home)


def test_missing_provider_report_is_unknown_not_healthy(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc()))
    row = dashboard(home, omp)['accounts']['go-a']
    assert (row['state'], row['reason']) == ('UNKNOWN', 'no provider report')


def test_cached_old_provider_sample_stays_stale_under_fresh_aggregate(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    old = NOW - 400_000
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go', fetched=old))))
    result = dashboard(home, omp)
    assert result['omp_generated_at'] is not None
    assert result['accounts']['go-a']['state'] == 'STALE'
    assert result['accounts']['go-a']['age_seconds'] == 400.0
    assert result['state'] != 'FRESH'


def test_partial_window_is_unknown_while_sibling_windows_remain_reported(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    broken = {'id': 'weekly', 'status': 'ok', 'scope': {}, 'window': {'id': '7d', 'resetsAt': RESET}}
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go', limits=[limit(), broken]))))
    row = dashboard(home, omp)['accounts']['go-a']
    states = {w['limit']: w['state'] for w in row['windows']}
    assert states == {'rolling-5h': 'FRESH', 'weekly': 'UNKNOWN'}
    assert row['state'] == 'UNKNOWN'


def test_unsupported_unit_is_not_merged_into_percent(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go', limits=[limit(unit='tokens')]))))
    window = dashboard(home, omp)['accounts']['go-a']['windows'][0]
    assert (window['state'], window['reason']) == ('UNKNOWN', 'unsupported unit')
    assert window['remaining_fraction'] is None


def test_future_fetch_timestamp_is_unknown(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go', fetched=NOW + 3_600_000))))
    row = dashboard(home, omp)['accounts']['go-a']
    assert row['state'] == 'UNKNOWN'
    assert 'future' in row['windows'][0]['reason'] or 'future' in row['reason']


def test_malformed_usage_json_fails_closed_and_is_logged_privately(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, '{not json')
    result = dashboard(home, omp)
    assert result['coverage']['usage_error'] == 'malformed-json'
    assert result['accounts']['go-a']['state'] == 'UNKNOWN'
    log = home / 'engine' / usage.ERROR_LOG
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_provider_exit_failure_is_unknown_and_never_fresh(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, '', exit_code=3)
    result = dashboard(home, omp)
    assert result['coverage']['usage_error'] == 'exit-3'
    assert result['state'] == 'UNKNOWN'


def test_fetch_timeout_is_bounded_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, 'USAGE_TIMEOUT', 0.5)
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))), sleep=5)
    result = dashboard(home, omp)
    assert result['coverage']['usage_error'] == 'timeout'
    assert result['accounts']['go-a']['state'] == 'UNKNOWN'


def test_disabled_credential_is_quarantined_not_quota_checked(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', 'invalid auth')])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    row = dashboard(home, omp)['accounts']['go-a']
    assert row['auth']['state'] == 'disabled'
    assert row['state'] == 'UNKNOWN'
    assert row['windows'] == []


def test_missing_credential_reference_is_unknown(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    assert dashboard(home, omp)['accounts']['go-a']['auth']['state'] == 'missing'


def test_exhausted_window_with_zero_remaining_is_reported_not_unknown(tmp_path):
    accounts = {'minimax-a': {'provider': 'minimax-code', 'state': 'configured', 'credential_id': 12}}
    home = make_site(tmp_path, accounts, [(12, 'minimax-code', None)], blocks=[(12, '5h', NOW + 60_000)])
    doc = usage_doc(report('minimax-code', limits=[limit(remaining=0.0, status='exhausted')]))
    omp = fake_omp(tmp_path, json.dumps(doc))
    row = dashboard(home, omp)['accounts']['minimax-a']
    assert row['auth']['state'] == 'blocked'
    assert row['windows'][0]['remaining_fraction'] == 0.0
    assert row['windows'][0]['state'] == 'FRESH'
    assert row['state'] == 'FRESH'


def test_not_configured_alias_is_listed_with_evidence_and_no_quota(tmp_path):
    accounts = {'go-b': {'provider': 'opencode-go', 'state': 'validated_but_not_configured', 'credential_id': None,
                         'evidence': 'candidate-go-check-2.json', 'evidence_at': '2026-10-22T20:41:59Z'}}
    home = make_site(tmp_path, accounts)
    omp = fake_omp(tmp_path, json.dumps(usage_doc()))
    row = dashboard(home, omp)['accounts']['go-b']
    assert row['state'] == 'NOT_CONFIGURED'
    assert row['evidence'] == 'candidate-go-check-2.json'
    assert row['windows'] == []


def test_unregistered_credential_rows_are_counted_as_coverage_gap(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None), (9, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    result = dashboard(home, omp)
    assert result['coverage']['unregistered_credentials'] == {'opencode-go': 1}
    assert result['state'] != 'FRESH'


def test_schema_version_mismatch_refuses_credential_metadata(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    con = sqlite3.connect(home / 'omp-recovery' / 'agent' / 'agent.db')
    con.execute('update auth_schema_version set version = 99')
    con.commit()
    con.close()
    with pytest.raises(ValueError, match='unsupported credential store schema'):
        boundary.credential_store(home / 'omp-recovery' / 'agent')


def test_registry_rejects_unknown_provider_and_escaping_store(tmp_path):
    home = make_site(tmp_path, {'x': {'provider': 'anthropic', 'state': 'configured', 'credential_id': 1}})
    with pytest.raises(ValueError):
        boundary.load_registry(home)
    (home / 'engine' / 'accounts.json').write_text(json.dumps({'version': 1, 'store': '../elsewhere', 'accounts': {}}))
    with pytest.raises(ValueError, match='store path'):
        boundary.load_registry(home)


def test_accounts_view_reads_no_provider_quota(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    view = usage.accounts(home, now_ms=NOW)
    assert view['accounts']['go-a']['auth']['state'] == 'ok'
    assert 'windows' not in view['accounts']['go-a']
    assert view['state'] == 'FRESH'
