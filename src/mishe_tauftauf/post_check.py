"""Private fail-closed publication preflight. Never modifies feed or task state."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import selectors
import signal
import time
import tempfile
import shutil
import importlib.util
from .pitfall_questions import VERSION, questions

MAX_BYTES = 2_000_000

class CorrectionRequired(ValueError):
    def __init__(self, report):
        self.report = report
        self.report_path = Path(report['report_path'])
        super().__init__('publication correction required; private report: ' + str(self.report_path))


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _save(path, report):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.draft-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(_json(report) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _deterministic(body):
    failures = []
    decoder = json.JSONDecoder()
    # Decode at every possible object/list start, including prose and fences.
    # No presentation wrapper authorizes JSON on the shared text surface.
    for match in re.finditer(r'[\[{]', body):
        try:
            value, length = decoder.raw_decode(body[match.start():])
        except (ValueError, TypeError):
            continue
        if isinstance(value, (dict, list)):
            failures.append({'id': 'D01', 'verdict': 'suspicious', 'reason': 'Structured JSON belongs in referenced evidence.', 'offending_text': body[match.start():match.start()+length], 'evidence': []})
            break
    text = re.sub(r'\b[0-9a-fA-F]{32,64}\b', '', body)
    text = re.sub(r'\b(?:Evidence|record|hash|sha256|fingerprint)\b', '', text, flags=re.I)
    if not re.search(r'[A-Za-z]{3}', text):
        failures.append({'id':'D02','verdict':'suspicious','reason':'Explain the event and evidence in readable prose.', 'offending_text':body,'evidence':[]})
    return failures


def _worker(command, encoded, timeout):
    # Disk-backed input avoids stdin pipe deadlock; output is capped while running.
    with tempfile.TemporaryFile() as incoming:
        incoming.write(encoded)
        incoming.seek(0)
        proc = subprocess.Popen(command, stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True)
        output = bytearray()
        total = 0
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(proc.stdout, selectors.EVENT_READ)
                ready.register(proc.stderr, selectors.EVENT_READ)
                while ready.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in ready.select(min(remaining, 0.2)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            ready.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError('worker exceeds combined output byte budget')
                        if key.fileobj is proc.stdout:
                            output.extend(chunk)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                code = proc.wait(timeout=remaining)
                if code:
                    raise subprocess.CalledProcessError(code, command)
            return bytes(output)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()


def _file_identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    result = {'path':str(path),'device':stat.st_dev,'inode':stat.st_ino,'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns}
    if path.is_file() and stat.st_size <= 16_000_000:
        result['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _checker_identity(raw):
    identities = [_file_identity(__file__), _file_identity(Path(__file__).with_name('pitfall_questions.py'))]
    if raw is None:
        return identities
    try:
        config = json.loads(raw)
        command = config.get('command', [])
        if not isinstance(command, list):
            return identities
        for index, arg in enumerate(command):
            if not isinstance(arg, str):
                continue
            candidate = shutil.which(arg) if index == 0 else arg
            if candidate and Path(candidate).exists():
                path = Path(candidate)
                if path.is_dir():
                    # Checkpoint trees use metadata manifests, not a multi-GB rehash
                    # on every post. The pinned worker hashes weights when invoked.
                    identities.extend(_file_identity(p) for p in sorted(path.rglob('*')) if p.is_file())
                else:
                    identities.append(_file_identity(path))
            if index and command[index-1] == '-m':
                spec = importlib.util.find_spec(arg)
                if spec and spec.origin and Path(spec.origin).is_file():
                    identities.append(_file_identity(spec.origin))
    except (OSError, ValueError, TypeError, AttributeError, ImportError) as exc:
        identities.append({'unavailable':str(exc)})
    return identities


def review(home, source, body, context=None, stage='post'):
    home = Path(home)
    bank = questions(stage)
    config_path = home / 'publication-check.json'
    raw = config_path.read_bytes() if config_path.exists() else None
    inputs = {'version': VERSION, 'checker_identity':_checker_identity(raw), 'source':source,'body':body,'context':context or {},'stage':stage,'questions':bank,'config':raw.decode('utf-8', errors='replace') if raw is not None else None}
    digest = hashlib.sha256(_json(inputs).encode()).hexdigest()
    path = home / 'post-checks' / (digest + '.json')
    report = {'version':1,'input_hash':digest,'source':source,'body':body,'context':context or {},'stage':stage,'report_path':str(path),'results':_deterministic(body),'semantic_status':'untested','clear':False}
    # Exact input cache binds config, gate/bank and worker file identity. Unknown is retried.
    if path.exists():
        cached = json.loads(path.read_text())
        if cached.get('input_hash') == digest and cached.get('status') in {'clear','suspicious'}:
            return cached
    if raw is not None and not report['results']:
        try:
            config = json.loads(raw)
            command = config['command']
            timeout = config.get('timeout_seconds', 30)
            if not isinstance(command,list) or not command or not all(isinstance(x,str) and x for x in command):
                raise ValueError('command must be a nonempty argv list')
            if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
                raise ValueError('timeout_seconds must be finite and in (0, 300]')
            request = {k:v for k,v in inputs.items() if k != 'config'}
            request['input_hash'] = digest
            request['deadline_seconds'] = timeout
            encoded = _json(request).encode()
            if len(encoded) > MAX_BYTES:
                raise ValueError('episode exceeds input byte budget')
            output = _worker(command, encoded, timeout)
            answer = json.loads(output)
            if not isinstance(answer, dict):
                raise ValueError('worker response must be an object')
            if answer.get('version') != 1 or answer.get('input_hash') != digest:
                raise ValueError('worker protocol/input hash mismatch')
            rows = answer['results']
            if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows) or len(rows)!=len(bank) or {r['id'] for r in rows}!={q['id'] for q in bank}:
                raise ValueError('worker skipped or duplicated questions')
            for row in rows:
                if row.get('verdict') not in {'clear','suspicious','unknown'} or not isinstance(row.get('evidence'),list) or not isinstance(row.get('reason'),str):
                    raise ValueError('invalid typed verdict')
            report['results'].extend(rows)
            report['semantic_status'] = 'suspicious' if any(r['verdict']=='suspicious' for r in rows) else 'unknown' if any(r['verdict']=='unknown' for r in rows) else 'clear'
            report['worker_metadata'] = answer.get('model', {})
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            report['semantic_status'] = 'unknown'
            report['error'] = str(exc)
    report['status'] = 'suspicious' if any(r['verdict']=='suspicious' for r in report['results']) else 'unknown' if report['semantic_status']=='unknown' else 'clear'
    report['clear'] = report['status']=='clear'
    report['untested_questions'] = [q['id'] for q in bank if q['id'] not in {r['id'] for r in report['results']}]
    report['required_correction'] = 'Correct flagged questions; supply missing evidence or retry the exact unavailable checker. Resubmit without repeating prior external effects.' if not report['clear'] else None
    _save(path, report)
    return report


def require(home, source, body, context=None, stage='post'):
    report = review(home, source, body, context=context, stage=stage)
    if not report['clear']:
        raise CorrectionRequired(report)
    return report
