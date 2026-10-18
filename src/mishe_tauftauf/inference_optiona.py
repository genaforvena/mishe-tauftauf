"""One-shot provider-only omp subprocess seam (Option A).

Experimental internal-loop programme Deliverable 1: a single `omp --print`
JSON-mode subprocess per turn, no tools, no session persistence. Provider
failure is reported as `ok=False` on the returned record, never as an
exception, so callers keep honest UNKNOWN records instead of unwinding.

Parsing rules follow the wake-61756 seam verification on the live nvidia
route (artifacts/inference-research-wake61756-optiona-seam-verification.md):
rc != 0 is failure with stderr kept; rc == 0 still requires a final
`message_end` with role == "assistant" (events can precede a late failure);
any malformed JSONL line is failure with the line preserved; absent
usage/stopReason/responseId stay None (UNKNOWN), never zero.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

_OM = 'omp'
_STDERR_TAIL = 4096


def optiona_call(prompt, *, model, config, cwd, thinking='off',
                 timeout=30.0, max_output=4 * 1024 * 1024, run=subprocess.run):
    """Run one provider-only omp turn and return a result record.

    `config` must be an absolute overlay path: omp resolves a relative
    --config against --cwd, not the process cwd (wake-61756 §3). Returns
    {ok, kind, text, stop_reason, usage, provider, model, response_id,
    returncode, stderr_tail}. `ok=False` carries the failure kind and
    bounded evidence; the only raised error is a rejected argument list.
    """
    config_path = Path(config) if config is not None else None
    if config_path is not None and not config_path.is_absolute():
        raise ValueError('config overlay path must be absolute; omp resolves '
                         'relative --config against --cwd')
    command = [_OM, '--model', model, '--thinking', thinking,
               '--mode', 'json', '--print', '--no-tools', '--no-session',
               '--no-prewalk']
    if config_path is not None:
        command += ['--config', str(config_path)]
    command += ['--cwd', str(Path(cwd)), prompt]
    try:
        completed = run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return _record(False, 'timeout', stderr_tail=_tail(exc.stderr))
    stdout = completed.stdout or b''
    if len(stdout) > max_output:
        return _record(False, 'output-limit', returncode=completed.returncode,
                       stderr_tail=_tail(completed.stderr))
    if completed.returncode != 0:
        # rc != 0: stdout may be empty or partial JSONL; never parsed
        # into success (wake-61756 §2.3).
        return _record(False, 'provider', returncode=completed.returncode,
                       stderr_tail=_tail(completed.stderr))
    return _parse_stream(stdout, completed.returncode,
                         _tail(completed.stderr))


def _parse_stream(stdout, returncode, stderr_tail):
    text = None
    stop_reason = None
    usage = None
    provider = None
    model = None
    response_id = None
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw.decode(errors='strict'))
        except (UnicodeDecodeError, ValueError):
            # Torn or malformed line: failure with the line preserved,
            # same honesty rule as the probe's malformed-line counter.
            return _record(False, 'malformed-stream', text=text,
                           stop_reason=stop_reason, usage=usage,
                           provider=provider, model=model,
                           response_id=response_id,
                           returncode=returncode,
                           stderr_tail=stderr_tail,
                           malformed=raw[:1024])
        if not isinstance(event, dict):
            # Valid JSON but not an event object (null, list, scalar):
            # still a malformed stream line, still fail with it preserved.
            return _record(False, 'malformed-stream', text=text,
                           stop_reason=stop_reason, usage=usage,
                           provider=provider, model=model,
                           response_id=response_id,
                           returncode=returncode,
                           stderr_tail=stderr_tail,
                           malformed=raw[:1024])
        if event.get('type') != 'message_end':
            continue
        message = event.get('message')
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            continue
        # Last assistant message_end wins: content is complete there.
        content = ''.join(part.get('text', '') for part in
                          message.get('content', [])
                          if isinstance(part, dict) and 'text' in part)
        text = content
        stop_reason = message.get('stopReason')
        usage = message.get('usage')
        provider = message.get('provider')
        model = message.get('model')
        response_id = message.get('responseId')
    if text is None:
        # rc == 0 but no assistant message_end: a stream that ended
        # without a completed assistant message is not a success.
        return _record(False, 'no-assistant-message', returncode=returncode,
                       stderr_tail=stderr_tail)
    return _record(True, 'ok', text=text, stop_reason=stop_reason,
                   usage=usage, provider=provider, model=model,
                   response_id=response_id, returncode=returncode,
                   stderr_tail=stderr_tail)


def _record(ok, kind, text=None, stop_reason=None, usage=None, provider=None,
            model=None, response_id=None, returncode=None, stderr_tail=None,
            malformed=None):
    record = {'ok': ok, 'kind': kind, 'text': text, 'stop_reason': stop_reason,
              'usage': usage, 'provider': provider, 'model': model,
              'response_id': response_id, 'returncode': returncode,
              'stderr_tail': stderr_tail}
    if malformed is not None:
        record['malformed'] = malformed
    return record


def _tail(stderr):
    if not stderr:
        return None
    if isinstance(stderr, str):
        stderr = stderr.encode(errors='replace')
    text = stderr[-_STDERR_TAIL:].decode(errors='replace')
    return text or None
