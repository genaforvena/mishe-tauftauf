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
from datetime import datetime, timezone

PROVIDERS = ('opencode-go', 'openai-codex', 'minimax-code')
REGISTRY_STATES = ('configured', 'validated_but_not_configured')
ALIAS = re.compile(r'[a-z][a-z0-9-]{0,31}')
USAGE_TIMEOUT = 60
OUTPUT_LIMIT = 4 * 1024 * 1024
STDERR_LIMIT = 4096
STORE_DEFAULT = 'omp-recovery/agent'
AUTH_SCHEMA_VERSION = 8


HEARTBEAT_PREFIX = b'{"type":"tool_execution_update"'
THINKING_PREFIX = b'{"type":"message_update","assistantMessageEvent":{"type":"thinking_'
"""Reasoning-stream events (thinking_start, thinking_delta, thinking_end). A model thinking without
acting is not progress (health wake 1794674035779401832 streamed ~32k of these for 14 min past its
soft deadline, then hit the hard cap). The block markers must match too: pub wake 1794722288504192628
was carried 35 min to the hard cap on thinking_start/thinking_end lines, which the delta-only prefix let through."""


JOB_COUNTERS = ('toolCount', 'requests', 'tokens')
"""Counters that show the async job behind a heartbeat is still doing work. Wall-clock fields
(``durationMs``, ``totalDurationMs``) are not counters: they advance while a job is frozen."""


def job_advanced(line, jobs):
    """Record the async-job counters in one heartbeat ``line``; return True if a job advanced.

    ``jobs`` is the caller's state across reads, keyed by job id. A line that repeats the same
    counters advances nothing. A line with no ``progress`` entries (a plain ``wait`` ping carries
    only job ids and durations) advances nothing either.
    """
    try:
        row = json.loads(line)
    except ValueError:
        return False
    details = (row.get('partialResult') or {}).get('details') if isinstance(row, dict) else None
    entries = details.get('progress') if isinstance(details, dict) else None
    if not isinstance(entries, list):
        return False
    advanced = False
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        key = entry.get('id') if isinstance(entry.get('id'), str) else index
        values = tuple(entry.get(name) if type(entry.get(name)) is int else None
                       for name in JOB_COUNTERS)
        previous = jobs.get(key)
        jobs[key] = values
        if previous is not None and any(
                now is not None and before is not None and now > before
                for now, before in zip(values, previous)):
            advanced = True
    return advanced


def progress_bytes(data, jobs=None):
    """Split engine output bytes into (progress bytes, bytes consumed).

    Only complete lines count: the consumed figure stops at the last newline, so a
    half-written line is picked up on the next read. Heartbeat lines (``HEARTBEAT_PREFIX``)
    re-report a running wait tool and are not progress; reasoning deltas (``THINKING_PREFIX``)
    are not progress either, so a model that only thinks cannot extend a wake past its deadline.

    A heartbeat is not automatically silence, though. When ``jobs`` is passed (the caller keeps
    the dict across reads), a heartbeat whose async job advanced its ``JOB_COUNTERS`` counts as
    progress: the wait is on a subagent that is demonstrably working, not on a frozen one. A
    repeated frozen job still advances nothing, and a plain ``wait`` ping still counts nothing.
    """
    end = data.rfind(b'\n')
    if end < 0:
        return 0, 0
    progress = 0
    for line in data[:end + 1].splitlines(keepends=True):
        if not line.startswith((HEARTBEAT_PREFIX, THINKING_PREFIX)):
            progress += len(line)
        elif jobs is not None and line.startswith(HEARTBEAT_PREFIX) and job_advanced(line, jobs):
            progress += len(line)
    return progress, end + 1


BACKGROUND_NOTICE = re.compile(rb'Backgrounded as job ([A-Za-z0-9_]+)')
"""The bash tool's notice that a command outlived its foreground window. Its job result is delivered
as a follow-up only while the engine process lives, so a one-shot wake engine that ends its reply on
this notice never receives the output (operator wake 1794888664164281312 ended on ``bg_1`` and exited
86 s before the job finished; the wake settled ``unknown (engine exit 0 without a yield)``)."""


def background_left_running(stream):
    """The job id the run's last tool result backgrounded, else None.

    ``pending`` is the last ``Backgrounded as job <id>`` notice in the stream, cleared by any later
    user or tool-result message: that message is the job's completion follow-up, or at least proof
    that the turn continued past the backgrounding call. Assistant text is ignored because a model
    that quotes the notice has not received the job. An ``agent_end`` envelope repeats the whole
    transcript, so only ``message_*`` events are read.
    """
    pending = None
    try:
        handle = open(stream, 'rb')
    except OSError:
        return None
    with handle:
        for line in handle:
            if not line.startswith(b'{"type":"message_'):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get('message') if isinstance(row, dict) else None
            if not isinstance(message, dict) or message.get('role') not in ('user', 'toolResult'):
                continue
            match = BACKGROUND_NOTICE.search(line) if message.get('role') == 'toolResult' else None
            pending = match.group(1).decode('ascii', 'replace') if match else None
    return pending


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

    A configured account names the credential it owns with ``credential_id``, or
    leaves it out to follow whichever credential the store currently holds for
    its provider. The id is a hint, not the credential's identity: a later login
    writes a new row, so an account whose id is gone follows the store's
    remaining credential for that provider instead of reporting a stale
    reference forever.
    """
    path = home / 'engine' / 'accounts.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('engine account registry unavailable')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('accounts'), dict):
        raise ValueError('invalid engine account registry')
    default = _store_path(value.get('store', STORE_DEFAULT))
    accounts, credentials, claims = {}, set(), set()
    for alias, row in value['accounts'].items():
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias) or not isinstance(row, dict):
            raise ValueError('invalid engine account entry')
        provider, state, credential = row.get('provider'), row.get('state'), row.get('credential_id')
        if provider not in PROVIDERS or state not in REGISTRY_STATES:
            raise ValueError('invalid engine account entry')
        store = _store_path(row['store']) if 'store' in row else default
        if state == 'configured':
            if credential is not None:
                if type(credential) is not int or credential <= 0 or (store, credential) in credentials:
                    raise ValueError('configured account needs one unique credential reference per store')
                credentials.add((store, credential))
            elif (store, provider) in claims:
                raise ValueError('configured account needs one unique credential reference per store')
            else:
                claims.add((store, provider))
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

    ``agent.db`` may be a symlink into the shared OMP agent store: the plant's
    engine store is one file of the default client, so a login or logout there is
    the same fact this store reports. SQLite resolves the link, so the write-ahead
    log stays a single file beside the real database.
    """
    db = directory / 'agent.db'
    if not db.is_file():
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


ROUTERAI_TURN_PREFIX = 'omp:routerai/'


def decode_routerai_credits(body):
    """The `credits` balance from a RouterAI GET /api/v1/credits body (raw bytes).

    Raises ValueError when the field is missing or is not a finite non-negative number, so
    the caller records UNKNOWN rather than a zero balance."""
    value = json.loads(body.decode('utf-8'))
    credits = value['data']['credits']
    if type(credits) not in (int, float) or not math.isfinite(credits) or credits < 0:
        raise ValueError('credits field missing or not a non-negative number')
    return float(credits)


def routerai_outcome_epoch(row):
    """Epoch seconds of an outcome row for an omp:routerai/* candidate, else None."""
    if not isinstance(row, dict) or row.get('event') != 'outcome':
        return None
    key = row.get('key')
    if not isinstance(key, str) or not key.startswith(ROUTERAI_TURN_PREFIX):
        return None
    try:
        at = datetime.strptime(str(row.get('at')), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return at.timestamp()
