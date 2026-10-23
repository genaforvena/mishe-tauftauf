"""Literal adapters for engine account evidence: registry JSON, bounded OMP usage
reads and read-only OMP credential metadata.

Only decoded, validated values leave this module. Credential values, OAuth
tokens, emails, account ids and org names are never selected or returned; the
credential store is read for id/provider/type/disabled/block columns only.
Raw provider output is never placed in public errors; stderr is returned to the
caller for private evidence only.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile

PROVIDERS = ('opencode-go', 'openai-codex', 'minimax-code')
REGISTRY_STATES = ('configured', 'validated_but_not_configured')
ALIAS = re.compile(r'[a-z][a-z0-9-]{0,31}')
USAGE_TIMEOUT = 60
OUTPUT_LIMIT = 4 * 1024 * 1024
STDERR_LIMIT = 4096
STORE_DEFAULT = 'omp-recovery/agent'
AUTH_SCHEMA_VERSION = 8


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _store_path(value):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('invalid engine credential store path')
    return value


def load_registry(home):
    """Return (default_store, {alias: entry}) from home/engine/accounts.json.

    A store is an OMP agent directory relative to home. Each account reads quota
    through its own store, so a second credential for one provider needs its own
    store to be attributable; accounts without one use the default store.
    """
    path = home / 'engine' / 'accounts.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('engine account registry unavailable')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('accounts'), dict):
        raise ValueError('invalid engine account registry')
    default = _store_path(value.get('store', STORE_DEFAULT))
    accounts, credentials = {}, set()
    for alias, row in value['accounts'].items():
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias) or not isinstance(row, dict):
            raise ValueError('invalid engine account entry')
        provider, state, credential = row.get('provider'), row.get('state'), row.get('credential_id')
        if provider not in PROVIDERS or state not in REGISTRY_STATES:
            raise ValueError('invalid engine account entry')
        store = _store_path(row['store']) if 'store' in row else default
        if state == 'configured':
            if type(credential) is not int or credential <= 0 or (store, credential) in credentials:
                raise ValueError('configured account needs one unique credential reference per store')
            credentials.add((store, credential))
        elif credential is not None:
            raise ValueError('unconfigured account must not reference a credential')
        for key in ('entitlement', 'evidence', 'evidence_at'):
            if row.get(key) is not None and not isinstance(row.get(key), str):
                raise ValueError('invalid engine account note')
        accounts[alias] = {
            'provider': provider,
            'state': state,
            'store': store,
            'credential_id': credential,
            'entitlement': row.get('entitlement'),
            'evidence': row.get('evidence'),
            'evidence_at': row.get('evidence_at'),
        }
    return default, accounts


def credential_store(directory):
    """Return ({credential_id: {provider, type, disabled}}, {credential_id: [blocks]}).

    Reads only metadata columns in read-only mode. The OMP auth schema is pinned to
    version 8 (observed on omp/18.4.4); any other version is refused rather than guessed.
    """
    db = directory / 'agent.db'
    if db.is_symlink() or not db.is_file():
        raise ValueError('credential store unavailable')
    try:
        con = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    except sqlite3.Error:
        raise ValueError('credential store unreadable') from None
    try:
        if con.execute('select version from auth_schema_version where id = 1').fetchall() != [(AUTH_SCHEMA_VERSION,)]:
            raise ValueError('unsupported credential store schema')
        credentials = {}
        for cid, provider, kind, cause in con.execute(
                'select id, provider, credential_type, disabled_cause from auth_credentials'):
            credentials[cid] = {'provider': provider, 'type': kind, 'disabled': bool(cause)}
        blocks = {}
        for cid, scope, until in con.execute(
                'select credential_id, block_scope, blocked_until_ms from auth_credential_blocks'):
            blocks.setdefault(cid, []).append({'scope': scope, 'blocked_until_ms': until})
        return credentials, blocks
    except sqlite3.Error:
        raise ValueError('credential store unreadable') from None
    finally:
        con.close()


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _run(argv, env, timeout):
    """Run argv in its own session with bounded, file-backed output."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(process)
            return None, b'', b'', 'timeout'
        if process.poll() is None:
            _kill(process)
        out.seek(0)
        err.seek(0)
        data = out.read(OUTPUT_LIMIT + 1)
        if len(data) > OUTPUT_LIMIT:
            return process.returncode, b'', b'', 'output-bound'
        return process.returncode, data, err.read(STDERR_LIMIT), None


def _window(limit):
    row = {'id': None, 'window_id': None, 'label': None, 'remaining_fraction': None,
           'reset_ms': None, 'duration_ms': None, 'provider_status': None, 'problem': None}
    if not isinstance(limit, dict):
        row['problem'] = 'malformed limit'
        return row
    row['id'] = limit['id'] if type(limit.get('id')) is str else None
    row['provider_status'] = limit['status'] if type(limit.get('status')) is str else None
    scope, window, amount = limit.get('scope'), limit.get('window'), limit.get('amount')
    if not isinstance(scope, dict) or not isinstance(window, dict) or not isinstance(amount, dict):
        row['problem'] = 'partial window'
        return row
    row['window_id'] = window['id'] if type(window.get('id')) is str else None
    row['label'] = window['label'] if type(window.get('label')) is str else None
    reset = window.get('resetsAt')
    row['reset_ms'] = reset if _finite(reset) and reset > 0 else None
    duration = window.get('durationMs')
    row['duration_ms'] = duration if _finite(duration) and duration > 0 else None
    remaining = amount.get('remainingFraction')
    if amount.get('unit') != 'percent':
        row['problem'] = 'unsupported unit'
    elif not _finite(remaining) or not 0 <= remaining <= 1:
        row['problem'] = 'invalid remaining fraction'
    else:
        row['remaining_fraction'] = float(remaining)
    if row['id'] is None or row['window_id'] is None or row['reset_ms'] is None:
        row['problem'] = row['problem'] or 'incomplete window identity'
    return row


def _report(row):
    if not isinstance(row, dict) or type(row.get('provider')) is not str or not _finite(row.get('fetchedAt')) or row['fetchedAt'] <= 0:
        return {'provider': None, 'fetched_ms': None, 'problem': 'malformed report', 'windows': []}
    limits = row.get('limits')
    if not isinstance(limits, list):
        return {'provider': row['provider'], 'fetched_ms': row['fetchedAt'], 'problem': 'limits missing', 'windows': []}
    return {'provider': row['provider'], 'fetched_ms': row['fetchedAt'], 'problem': None,
            'windows': [_window(limit) for limit in limits]}


def _counts(rows):
    if not isinstance(rows, list):
        raise ValueError('malformed usage output')
    counts = {}
    for row in rows:
        name = row.get('provider') if isinstance(row, dict) and type(row.get('provider')) is str else '?'
        counts[name] = counts.get(name, 0) + 1
    return counts


def usage(store_dir, omp='omp'):
    """Read `omp usage --json` against one credential store. Never raises for provider faults.

    Returns {'error': None|class, 'generated_ms', 'reports', 'without_usage', 'disabled', 'stderr'}.
    """
    env = dict(os.environ, PI_CODING_AGENT_DIR=str(store_dir))
    executable = shutil.which(omp, path=env.get('PATH'))
    result = {'error': None, 'generated_ms': None, 'reports': [], 'without_usage': {}, 'disabled': {}, 'stderr': ''}
    if not executable:
        result['error'] = 'executable-unavailable'
        return result
    code, data, err, failure = _run([executable, 'usage', '--json', '--no-extensions'], env, USAGE_TIMEOUT)
    result['stderr'] = err.decode('utf-8', 'replace') if isinstance(err, bytes) else ''
    if failure:
        result['error'] = failure
        return result
    if code:
        result['error'] = 'exit-' + str(code)
        return result
    try:
        value = json.loads(data.decode('utf-8'))
        if not isinstance(value, dict) or not _finite(value.get('generatedAt')) or not isinstance(value.get('reports'), list):
            raise ValueError('malformed usage output')
        result['generated_ms'] = value['generatedAt']
        result['reports'] = [_report(row) for row in value['reports']]
        result['without_usage'] = _counts(value.get('accountsWithoutUsage', []))
        result['disabled'] = _counts(value.get('disabledCredentials', []))
    except (ValueError, UnicodeDecodeError, TypeError, AttributeError):
        result['error'] = 'malformed-json'
        result['reports'] = []
    return result


def last_good_samples(path):
    """Return ({alias: {fetched_ms, windows}}, error) from engine/last-good.json.

    A malformed file or entry is reported so the caller refuses to overwrite it.
    """
    if not path.exists():
        return {}, None
    if path.is_symlink() or not path.is_file():
        return {}, 'last-good file is not a regular file'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError):
        return {}, 'last-good file malformed'
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('accounts'), dict):
        return {}, 'last-good file malformed'
    samples, bad = {}, False
    for alias, sample in value['accounts'].items():
        if isinstance(sample, dict) and _finite(sample.get('fetched_ms')) and isinstance(sample.get('windows'), list):
            samples[alias] = sample
        else:
            bad = True
    return samples, 'last-good file has malformed entries' if bad else None
