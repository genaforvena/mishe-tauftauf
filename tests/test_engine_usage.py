"""Engine account inventory and quota dashboard: attribution, freshness and failure states."""
import json
import sqlite3
import stat

import pytest

from mishe_tauftauf import engine_boundary as boundary
from mishe_tauftauf import engine_usage as usage

NOW = 1_792_712_432_436
FRESH = NOW - 10_000
RESET = NOW + 3_600_000
RESET_B = RESET + 86_400_000
RESIDENT = 'omp-recovery/agent'
GO_B = 'engine/go-b'


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


def _write_store(store, credentials, blocks):
    con = sqlite3.connect(store / 'agent.db')
    con.execute('create table auth_schema_version (id integer primary key, version integer)')
    con.execute('insert into auth_schema_version values (1, 8)')
    con.execute('create table auth_credentials (id integer primary key, provider text, credential_type text, '
                'data text, disabled_cause text, identity_key text, created_at integer, updated_at integer)')
    _add_store_rows(con, credentials, blocks)
    con.commit()
    con.close()


def _add_store_rows(con, credentials, blocks):
    """Add credential rows to an existing store, as a later login does."""
    for cid, provider, disabled in credentials:
        con.execute('insert into auth_credentials (id, provider, credential_type, data, disabled_cause) values (?,?,?,?,?)',
                    (cid, provider, 'api_key', 'SECRET-VALUE-NOT-READ', disabled))
    con.execute('create table if not exists auth_credential_blocks (credential_id integer, provider_key text, '
                'block_scope text, blocked_until_ms integer, updated_at integer)')
    for cid, scope, until in blocks:
        con.execute('insert into auth_credential_blocks values (?,?,?,?,0)', (cid, 'p', scope, until))


def make_site(tmp_path, accounts, credentials=(), blocks=()):
    home = tmp_path / 'site'
    (home / 'engine').mkdir(parents=True)
    (home / 'engine' / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': accounts}))
    store = home / RESIDENT
    store.mkdir(parents=True)
    _write_store(store, credentials, blocks)
    return home


def add_store(home, relative, credentials=(), blocks=()):
    store = home / relative
    store.mkdir(parents=True)
    _write_store(store, credentials, blocks)


def go(alias, cid):
    return {'provider': 'opencode-go', 'state': 'configured', 'credential_id': cid, 'entitlement': alias}


def fake_omp(tmp_path, payload=None, exit_code=0, sleep=0, per_store=None):
    """Fake `omp usage`. per_store maps a store-path suffix to (payload, exit_code), chosen by PI_CODING_AGENT_DIR."""
    body = tmp_path / 'payload.txt'
    body.write_text(payload if payload is not None else '')
    branches = ''
    for n, (suffix, (text, code)) in enumerate((per_store or {}).items()):
        path = tmp_path / f'payload-{n}.txt'
        path.write_text(text)
        branches += f'case "$PI_CODING_AGENT_DIR" in *"{suffix}") cat "{path}"; exit {code};; esac\n'
    script = tmp_path / 'omp'
    script.write_text(f'#!/bin/sh\n[ "{sleep}" = 0 ] || sleep {sleep}\n{branches}cat "{body}"\nexit {exit_code}\n')
    script.chmod(0o755)
    return str(script)


def dashboard(home, omp, now=NOW):
    return usage.status(home, now_ms=now, omp=omp)


def test_two_go_entitlements_in_one_store_are_never_merged_into_one_report(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-b': go('b', 9)}, [(8, 'opencode-go', None), (9, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    result = dashboard(home, omp)
    assert result['accounts']['go-a']['state'] == 'UNATTRIBUTED'
    assert result['accounts']['go-b']['state'] == 'UNATTRIBUTED'
    assert result['accounts']['go-a']['windows'] == []
    assert result['state'] == 'UNKNOWN'


def test_go_entitlements_in_separate_stores_are_each_attributed_to_their_own_pool(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-b': {**go('b', 9), 'store': GO_B}}, [(8, 'opencode-go', None)])
    add_store(home, GO_B, [(9, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go', limits=[limit(remaining=0.98)]))),
                   per_store={GO_B: (json.dumps(usage_doc(report('opencode-go', limits=[limit(remaining=0.79, resets=RESET_B)]))), 0)})
    result = dashboard(home, omp)
    assert result['accounts']['go-a']['state'] == 'FRESH'
    assert result['accounts']['go-b']['state'] == 'FRESH'
    assert result['accounts']['go-a']['windows'][0]['remaining_fraction'] == 0.98
    assert result['accounts']['go-b']['windows'][0]['remaining_fraction'] == 0.79
    assert result['accounts']['go-b']['store'] == GO_B
    assert result['state'] == 'FRESH'


def test_identical_window_resets_across_stores_mark_both_as_one_entitlement(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-b': {**go('b', 9), 'store': GO_B}}, [(8, 'opencode-go', None)])
    add_store(home, GO_B, [(9, 'opencode-go', None)])
    doc = json.dumps(usage_doc(report('opencode-go')))
    omp = fake_omp(tmp_path, doc, per_store={GO_B: (doc, 0)})
    result = dashboard(home, omp)
    assert result['accounts']['go-a']['state'] == 'UNATTRIBUTED'
    assert result['accounts']['go-b']['state'] == 'UNATTRIBUTED'
    assert result['coverage']['shared_windows'] == ['go-a', 'go-b']
    assert result['state'] != 'FRESH'


def test_failing_store_is_unknown_while_the_other_store_stays_fresh(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-b': {**go('b', 9), 'store': GO_B}}, [(8, 'opencode-go', None)])
    add_store(home, GO_B, [(9, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))), per_store={GO_B: ('', 3)})
    result = dashboard(home, omp)
    assert result['accounts']['go-a']['state'] == 'FRESH'
    assert result['accounts']['go-b']['state'] == 'UNKNOWN'
    assert result['coverage']['usage_errors'] == {GO_B: 'exit-3'}
    assert result['state'] == 'PARTIAL'


def test_per_account_store_outside_home_is_refused(tmp_path):
    home = make_site(tmp_path, {'go-a': {**go('a', 8), 'store': '../elsewhere'}})
    with pytest.raises(ValueError, match='store path'):
        boundary.load_registry(home)


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
    assert result['omp_generated_at'][RESIDENT] is not None
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
    assert result['coverage']['usage_errors'] == {RESIDENT: 'malformed-json'}
    assert result['accounts']['go-a']['state'] == 'UNKNOWN'
    log = home / 'engine' / usage.ERROR_LOG
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_provider_exit_failure_is_unknown_and_never_fresh(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, '', exit_code=3)
    result = dashboard(home, omp)
    assert result['coverage']['usage_errors'] == {RESIDENT: 'exit-3'}
    assert result['state'] == 'UNKNOWN'


def test_fetch_timeout_is_bounded_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, 'USAGE_TIMEOUT', 0.5)
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))), sleep=5)
    result = dashboard(home, omp)
    assert result['coverage']['usage_errors'] == {RESIDENT: 'timeout'}
    assert result['accounts']['go-a']['state'] == 'UNKNOWN'


def test_failed_read_keeps_last_good_sample_labelled_with_its_own_age(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    good = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go', limits=[limit(remaining=0.5)]))))
    assert dashboard(home, good)['accounts']['go-a']['state'] == 'FRESH'
    bad = fake_omp(tmp_path, '', exit_code=3)
    row = dashboard(home, bad, now=NOW + 60_000)['accounts']['go-a']
    assert row['state'] == 'UNKNOWN'
    assert row['windows'] == []
    assert row['last_good']['age_seconds'] == 70.0
    assert row['last_good']['windows'][0]['remaining_fraction'] == 0.5


def test_malformed_last_good_file_is_reported_and_not_overwritten(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    (home / 'engine' / usage.LAST_GOOD).write_text('{not json')
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    result = dashboard(home, omp)
    assert result['coverage']['last_good'] == 'last-good file malformed'
    assert (home / 'engine' / usage.LAST_GOOD).read_text() == '{not json'


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


def test_unregistered_credential_rows_are_counted_per_store_as_coverage_gap(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None), (9, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    result = dashboard(home, omp)
    assert result['coverage']['unregistered_credentials'] == {RESIDENT: {'opencode-go': 1}}
    assert result['state'] != 'FRESH'


def test_schema_version_mismatch_refuses_credential_metadata(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    con = sqlite3.connect(home / RESIDENT / 'agent.db')
    con.execute('update auth_schema_version set version = 99')
    con.commit()
    con.close()
    with pytest.raises(ValueError, match='unsupported credential store schema'):
        boundary.credential_store(home / RESIDENT)


def test_mismatched_store_refuses_usage_read_and_is_unknown(tmp_path):
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    con = sqlite3.connect(home / RESIDENT / 'agent.db')
    con.execute('update auth_schema_version set version = 99')
    con.commit()
    con.close()
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    result = dashboard(home, omp)
    assert result['coverage']['store_errors'] == {RESIDENT: 'unsupported credential store schema'}
    assert result['coverage']['usage_errors'] == {}
    assert result['accounts']['go-a']['state'] == 'UNKNOWN'


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


def test_account_without_pinned_credential_follows_the_stores_live_one(tmp_path):
    """An account that names no credential reads whichever one the store holds."""
    accounts = {'go-a': {'provider': 'opencode-go', 'state': 'configured'}}
    home = make_site(tmp_path, accounts, [(15, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    row = dashboard(home, omp)['accounts']['go-a']
    assert row['auth']['state'] == 'ok'
    assert row['credential_id'] == 15


def test_relogin_writes_a_new_credential_id_and_the_account_follows_it(tmp_path):
    """A login that replaces the credential is picked up, not reported as missing."""
    accounts = {'go-a': {'provider': 'opencode-go', 'state': 'configured'}}
    home = make_site(tmp_path, accounts, [(15, 'opencode-go', 'deleted by user')])
    con = sqlite3.connect(home / RESIDENT / 'agent.db')
    _add_store_rows(con, [(16, 'opencode-go', None)], [])
    con.commit()
    con.close()
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    row = dashboard(home, omp)['accounts']['go-a']
    assert row['auth']['state'] == 'ok'
    assert row['credential_id'] == 16


def test_logout_then_relogin_is_followed_across_both_row_ids(tmp_path):
    """A pinned id that dies is followed to the replacement, in either direction."""
    accounts = {'go-a': go('a', 15)}
    home = make_site(tmp_path, accounts, [(15, 'opencode-go', 'deleted by user')])
    con = sqlite3.connect(home / RESIDENT / 'agent.db')
    _add_store_rows(con, [(16, 'opencode-go', None)], [])
    con.commit()
    con.close()
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    row = dashboard(home, omp)['accounts']['go-a']
    assert row['auth']['state'] == 'ok'
    assert row['credential_id'] == 16


def test_two_active_credentials_for_one_provider_are_not_attributed(tmp_path):
    """Two live credentials cannot be told apart, so neither is claimed."""
    accounts = {'go-a': {'provider': 'opencode-go', 'state': 'configured'}}
    home = make_site(tmp_path, accounts, [(15, 'opencode-go', None), (16, 'opencode-go', None)])
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    row = dashboard(home, omp)['accounts']['go-a']
    assert row['auth']['state'] == 'missing'


def test_blocked_account_reports_the_credential_it_actually_reads(tmp_path):
    """A block on the live credential must not be shown against the stale pinned id."""
    accounts = {'go-a': go('a', 9)}
    home = make_site(tmp_path, accounts, [(9, 'opencode-go', 'deleted by user')])
    con = sqlite3.connect(home / RESIDENT / 'agent.db')
    _add_store_rows(con, [(13, 'opencode-go', None)], [])
    con.commit()
    con.close()
    omp = fake_omp(tmp_path, json.dumps(usage_doc(report('opencode-go'))))
    row = dashboard(home, omp)['accounts']['go-a']
    assert row['credential_id'] == 13
    assert row['auth']['state'] == 'ok'


def test_shared_store_symlink_is_read_as_the_same_credential_store(tmp_path):
    """A store whose agent.db links to the shared client store is readable."""
    home = make_site(tmp_path, {'go-a': go('a', 8)}, [(8, 'opencode-go', None)])
    shared = tmp_path / 'shared-agent.db'
    shared.write_bytes((home / RESIDENT / 'agent.db').read_bytes())
    (home / RESIDENT / 'agent.db').unlink()
    (home / RESIDENT / 'agent.db').symlink_to(shared)
    creds, _blocks = boundary.credential_store(home / RESIDENT)
    assert creds[8]['provider'] == 'opencode-go'


# ---------------------------------------------------------------------------
# cmd_explain: policy.json vs registry fallback (shadow, no launcher change)
# ---------------------------------------------------------------------------

def _fresh_shadow_status():
    """Minimal FRESH inventory for shadow explain: no quota windows to gate."""
    accounts = {alias: {'alias': alias, 'provider': 'opencode-go', 'state': 'FRESH', 'windows': []}
                for alias in ('go-a', 'go-b')}
    return {'state': 'FRESH', 'accounts': accounts}


def _explain_json(home, role, monkeypatch, capsys):
    import types
    monkeypatch.setattr(usage, 'status', lambda *a, **k: _fresh_shadow_status())
    code = usage.cmd_explain(types.SimpleNamespace(home=str(home), role=role, json=True))
    return code, json.loads(capsys.readouterr().out)


def test_valid_policy_file_wins_over_registry_fallback(tmp_path, monkeypatch, capsys):
    """A valid engine/policy.json routes the role even when other accounts are configured."""
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-b': go('b', 9)},
                     [(8, 'opencode-go', None), (9, 'opencode-go', None)])
    (home / 'engine' / 'policy.json').write_text(
        json.dumps({'docs': [{'provider': 'opencode-go', 'alias': 'go-b'}]}))
    _code, out = _explain_json(home, 'docs', monkeypatch, capsys)
    assert out['role'] == 'docs'
    # go-a is first in registry order, so the fallback would select it: finding go-b proves the policy file was used.
    assert out['decision']['selected'] is not None and out['decision']['selected']['alias'] == 'go-b'


def test_missing_policy_file_falls_back_to_configured_accounts(tmp_path, monkeypatch, capsys):
    """With no policy file the shadow falls back to configured registry accounts."""
    home = make_site(tmp_path, {'go-a': go('a', 8), 'go-b': go('b', 9)},
                     [(8, 'opencode-go', None), (9, 'opencode-go', None)])
    _code, out = _explain_json(home, 'docs', monkeypatch, capsys)
    assert out['decision']['selected'] is not None and out['decision']['selected']['alias'] == 'go-a'
