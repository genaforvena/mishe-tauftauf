"""Flag repeated handoff next-step text for reconciliation; no semantic verdict."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from mishe_tauftauf.feed import Feed


def _normalize(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def check(home: Path, handoff: Path) -> dict:
    text = handoff.read_text()
    label = re.compile(r'^\s*(?:[-*]\s+)?(?:#{1,6}\s*)?(?:\*\*)?next(?: step| steps| action| follow-up)?(?:\s*/\s*rollback)?(?:\*\*)?\s*(?::\s*(.*)|$)', re.I)
    lines = text.splitlines()
    selected = next(((i, label.match(line)) for i, line in enumerate(lines) if label.match(line)), None)
    if selected is None:
        inline = re.compile(r'(?:^|[.;!?]\s+)Next:\s*(.*)$', re.I)
        selected = next(((i, inline.search(line)) for i, line in enumerate(lines) if inline.search(line)), None)
    parts = []
    if selected:
        index, match = selected
        if match.group(1):
            parts.append(match.group(1).strip())
        for line in lines[index+1:]:
            if re.match(r'^\s*#{1,6}\s+|^\s*(?:Evidence|Rollback|Unresolved|Verification|Outcome|Action):', line, re.I):
                break
            if not line.strip():
                if parts:
                    break
                continue
            parts.append(line.strip())
    step = '\n'.join(parts)
    step = re.split(r'\s+(?:Evidence|Rollback|Verification|Outcome|Artifact-only rollback):\s*', step, maxsplit=1, flags=re.I)[0].strip()
    needle = _normalize(step)
    matches = []
    entries = Feed(home).entries()
    if len(needle.split()) >= 8:
        for entry in entries:
            if needle in _normalize(entry.body):
                matches.append({'sequence': entry.sequence, 'source': entry.source,
                                'body_sha256': hashlib.sha256(entry.body.encode()).hexdigest()})
    return {'state': 'repeat' if matches else 'clear' if step else 'unknown',
            'next_step': step, 'handoff_file': str(handoff.resolve()), 'handoff_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'checked_through_sequence': entries[-1].sequence if entries else 0,
            'matches': matches[-10:], 'match_count': len(matches),
            'action': ('Before yielding, read the matching entries, reconcile prior effects, and revise Next to a genuinely new action or changed evidence/retry condition. Changing wording alone is not progress.' if matches else 'Write a concrete Next action or retry condition.' if not step else 'No exact repeat found; still reconcile task state and prior effects.'),
            'meaning': 'Exact normalized text only. Reconcile prior effects and retry conditions; a match is not proof of a useless loop.'}


def status(home: Path) -> str:
    path = home / 'handoff-check.json'
    if not path.exists():
        return 'HANDOFF NEXT: UNKNOWN no check recorded'
    try:
        report = json.loads(path.read_text())
        if report['state'] not in {'clear', 'repeat', 'unknown'}:
            raise ValueError('invalid state')
        handoff = Path(report['handoff_file'])
        if hashlib.sha256(handoff.read_bytes()).hexdigest() != report['handoff_sha256']:
            return 'HANDOFF NEXT: UNKNOWN handoff changed; rerun check'
        return f"HANDOFF NEXT: {report['state'].upper()} matches={report['match_count']} checked-through={report['checked_through_sequence']} artifact={path}"
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return 'HANDOFF NEXT: UNKNOWN unreadable evidence'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--handoff', type=Path)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.status:
        print(status(args.home))
        return
    if not args.handoff:
        parser.error('--handoff required')
    result = check(args.home, args.handoff)
    import os, tempfile
    fd, name = tempfile.mkstemp(dir=args.home, prefix='.handoff-check-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(result, handle, indent=2)
        os.replace(name, args.home / 'handoff-check.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print(json.dumps(result))
    raise SystemExit(0 if result['state'] == 'clear' else 2)


if __name__ == '__main__':
    main()
