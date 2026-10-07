"""Optional, observable per-wake analysis advice. Never schedules or settles work."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mishe_tauftauf.feed import Feed
from mishe_tauftauf.records import REFERENCE_RE, payload

QUESTION = 'Which witness analysis would be most useful now?'
ANALYSES = {
    'unresolved-requests': 'Find an operator request that has not been taken or followed through.',
    'evidence-audit': 'Check a completion claim or conflicting evidence against its source.',
    'progress-loop': 'Trace repeated work without new evidence or progress.',
    'ownership-review': 'Reconcile missing or conflicting task ownership and next actions.',
    'none': 'No particular analysis stands out; use the charter and live pane.',
    'unknown': 'Context is insufficient to choose; inspect the original evidence.',
}
from mishe_tauftauf.chat_protocol import decode_lifecycle


def _hash(value):
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _lock(home):
    directory = home / 'analysis-advice'
    directory.mkdir(exist_ok=True)
    with (directory / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield directory


def _write(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.advice-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _previous_context(home: Path, entries) -> dict | None:
    previous = next((e for e in reversed(entries) if
                     (e.source == 'seed' and e.body.startswith('[work] channel=witness ')) or
                     (e.source == 'witness' and e.body.startswith('Wall outcome '))), None)
    if previous is None:
        return None
    context = {'sequence': previous.sequence, 'source': previous.source,
               'body_sha256': _hash(previous.body.encode()),
               'type': 'legacy-work' if previous.source == 'seed' else 'wall-outcome',
               'status': 'UNKNOWN', 'semantic_acceptance': 'UNKNOWN',
               'text': '', 'omitted_characters': 0}
    if previous.source == 'seed':
        text = previous.body.split('HANDOFF:\n', 1)[-1]
        context.update(status='legacy-author-report', text=text[:240],
                       omitted_characters=max(0, len(text) - 240))
        return context
    try:
        references = [line for line in previous.body.splitlines() if line.startswith('[record]')]
        if len(references) != 1 or not REFERENCE_RE.fullmatch(references[0]):
            raise ValueError('missing or invalid immutable outcome reference')
        context['reference'] = references[0]
        data = payload(previous)
        text = data['text']
        kind = data['kind']
        if (data['role'] != 'witness' or
                kind not in {'accepted', 'blocker-resolved', 'blocker-retired', 'hypothesis-changed'} or
                not isinstance(text, str) or not text.strip() or
                previous.body != f'Wall outcome {kind} by witness\n{text}\n{references[0]}'):
            raise ValueError('outcome prose or role does not match immutable record')
        evidence = data['evidence']
        path = Path(evidence['path']).resolve()
        context.update(kind=kind, evidence=evidence)
        if not path.is_relative_to(home.resolve()) or not path.is_file():
            raise ValueError('outcome evidence missing or outside owned site')
        raw = path.read_bytes()
        if not raw or _hash(raw) != evidence['sha256']:
            raise ValueError('outcome evidence checksum mismatch')
        context.update(status='verified-author-report', text=text[:240],
                       omitted_characters=max(0, len(text) - 240))
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        context['error'] = str(exc)
    return context


def snapshot(home: Path, cutoff: int) -> dict:
    entries = [e for e in Feed(home).entries() if e.sequence <= cutoff]
    previous = _previous_context(home, entries)
    # Invalid current evidence must not silently resurrect an older analysis
    # or remove earlier external context.
    since = previous['sequence'] if previous and previous['status'] != 'UNKNOWN' else 0
    candidates = [e for e in entries if e.sequence > since and
                  e.source not in {'seed', 'witness', 'mind/witness'}]
    excerpts = []
    for entry in candidates[-6:]:
        # For framed task state JSON, use the producer's readable explanation;
        # the complete source remains available by sequence and body hash.
        text = '\n'.join(line for line in entry.body.splitlines() if not line.startswith('{'))
        excerpt = text[:160]
        excerpts.append({'sequence': entry.sequence, 'source': entry.source,
                         'text': excerpt, 'body_sha256': _hash(entry.body.encode()),
                         'omitted_characters': len(entry.body) - len(excerpt)})
    return {'chat_log': str((home / 'chat.log').resolve()), 'through_sequence': cutoff,
            'since_context_sequence': since, 'previous_context': previous,
            'entries': excerpts, 'omitted_entries': max(0, len(candidates) - len(excerpts)),
            'context_complete': not previous and len(candidates) == len(excerpts) and
                all(e['omitted_characters'] == 0 for e in excerpts),
            'meaning': 'Suggest an investigation focus, not a defect verdict. The mind must read full evidence and honor its wake and tasks.'}


def advise(home: Path, wake: int, *, config_path: Path | None = None) -> dict:
    entries = Feed(home).entries()
    if not any(e.sequence == wake and (control := decode_lifecycle(e)) and control.kind == 'wake' and control.role == 'witness' for e in entries):
        raise ValueError('not a canonical witness wake')
    with _lock(home) as directory:
        path = directory / f'{wake}.json'
        if path.exists():
            return json.loads(path.read_text())
        if config_path is None:
            config_path = home / 'analysis-advisor.json'
            optional = True
        else:
            optional = False
        if optional and not config_path.exists():
            return {'state': 'disabled', 'selection': 'unknown', 'wake': wake}
        started = time.monotonic()
        report = {'wake': wake, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'state': 'unavailable', 'selection': 'unknown', 'advisory_only': True,
                  'source_sha256': _hash(Path(__file__).read_bytes())}
        try:
            config_bytes = config_path.read_bytes()
            config = json.loads(config_bytes)
            command = config['command']
            timeout = float(config.get('timeout_seconds', 60))
            if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
                raise ValueError('invalid worker command')
            if not math.isfinite(timeout) or not 0 < timeout <= 120:
                raise ValueError('invalid worker timeout')
            analyses = config.get('analyses', ANALYSES)
            question = config.get('question', QUESTION)
            if not isinstance(analyses, dict) or set(analyses) != set(ANALYSES) or not all(isinstance(v, str) and 0 < len(v) <= 240 for v in analyses.values()):
                raise ValueError('invalid analysis definitions')
            if not isinstance(question, str) or not 0 < len(question) <= 240:
                raise ValueError('invalid analysis question')
            request = {'question': question, 'analyses': analyses, 'state': snapshot(home, wake)}
            report.update(config_sha256=_hash(config_bytes), request=request, input_sha256=_hash(request))
            # Files avoid unbounded memory capture. Worker output is local, trusted
            # code; oversized output is rejected and never copied into artifacts.
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                result = subprocess.run(command, input=json.dumps(request).encode(), stdout=stdout,
                                        stderr=stderr, timeout=timeout)
                if result.returncode:
                    raise ValueError('worker exited unsuccessfully')
                if stdout.tell() > 65536:
                    raise ValueError('worker output exceeds limit')
                stdout.seek(0)
                answer = json.load(stdout)
            selected = answer['selection']
            if selected not in ANALYSES or not isinstance(answer.get('model'), dict) or not answer['model']:
                raise ValueError('invalid worker answer')
            probabilities = answer.get('probabilities', {})
            if not isinstance(probabilities, dict) or any(k not in ANALYSES or isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for k, p in probabilities.items()):
                raise ValueError('invalid worker probabilities')
            available = answer.get('available', True)
            if not isinstance(available, bool):
                raise ValueError('invalid availability flag')
            report.update(state='suggested' if available else 'unavailable', selection=selected if available else 'unknown', answer=answer)
        except subprocess.TimeoutExpired:
            report['error'] = 'worker timeout'
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            report['error'] = 'configuration or worker response invalid; inspect the local command'
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        _write(path, report)
        return report


def feedback(home: Path, wake: int, used: str, outcome: str, note: str) -> dict:
    if used not in ANALYSES or outcome not in {'useful', 'routine', 'inconclusive'} or not note.strip() or len(note) > 2000:
        raise ValueError('feedback needs an analysis, outcome and evidence note')
    with _lock(home) as directory:
        advice = directory / f'{wake}.json'
        if not advice.exists():
            raise ValueError('no advice recorded for this wake')
        value = {'wake': wake, 'used': used, 'outcome': outcome, 'note': note,
                 'advice_sha256': _hash(advice.read_bytes()), 'reported_by': 'mind; unverified outcome claim'}
        path = directory / f'{wake}-feedback.json'
        if path.exists():
            old = json.loads(path.read_text())
            if old != value:
                raise ValueError('feedback already recorded; preserve prior evidence')
            return old
        _write(path, value)
        return value


def status(home: Path, *, config_path: Path | None = None) -> str:
    if config_path is None:
        if not (home / 'analysis-advisor.json').exists():
            return 'ANALYSIS ADVISOR: disabled (optional)'
    elif not config_path.exists():
        return 'ANALYSIS ADVISOR: UNKNOWN explicit configuration missing'
    try:
        directory = home / 'analysis-advice'
        reports = sorted((p for p in directory.glob('*.json') if p.stem.isdigit()), key=lambda p: int(p.stem))
        if not reports:
            return 'ANALYSIS ADVISOR: UNKNOWN configured; no per-wake advice yet'
        report = json.loads(reports[-1].read_text())
        path = directory / f"{report['wake']}-feedback.json"
        suffix = ''
        if path.exists():
            result = json.loads(path.read_text())
            suffix = f" used={result['used']} outcome={result['outcome']} (mind-reported)"
        else:
            suffix = ' feedback=pending'
        state = 'READY' if report['state'] == 'suggested' else 'UNKNOWN'
        partial = ' context=excerpted' if not report.get('request', {}).get('state', {}).get('context_complete', False) else ' context=complete'
        return f"ANALYSIS ADVISOR: {state} wake={report['wake']} suggested={report['selection']} seconds={report['elapsed_seconds']}{partial}{suffix} artifact={reports[-1]}"
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return 'ANALYSIS ADVISOR: UNKNOWN unreadable local evidence'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--config', type=Path, help='Explicit generation-bound advisor configuration; no site fallback')
    parser.add_argument('--wake', type=int)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--used', choices=ANALYSES)
    parser.add_argument('--outcome', choices=['useful', 'routine', 'inconclusive'])
    parser.add_argument('--note')
    args = parser.parse_args()
    if args.status:
        print(status(args.home, config_path=args.config))
    elif args.wake is None:
        parser.error('--wake is required for advice or feedback')
    elif args.used:
        if not args.outcome or not args.note:
            parser.error('feedback requires --outcome and --note')
        print(json.dumps(feedback(args.home, args.wake, args.used, args.outcome, args.note)))
    else:
        print(json.dumps(advise(args.home, args.wake, config_path=args.config)))


if __name__ == '__main__':
    main()
