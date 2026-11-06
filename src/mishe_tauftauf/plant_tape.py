"""Append-only presentation of canonical Git and wake history; no runtime actions.

History lives in Git and the retained wakes/ records and engine output. Each pane replays a bounded recent portion
on opening, then follows new records without repainting or clearing scrollback.
"""
from __future__ import annotations

import time
from pathlib import Path

from .state_files import read_json
from .plant_view_boundary import git_tape, safe, tape_arguments, output_text, wake_key


def git_step(repo, cursor):
    return git_tape(Path(repo), cursor)


OUTPUT_GRACE_SECONDS = 300


def wakes_step(home, cursor):
    root = Path(home) / 'wakes'
    if not root.is_dir():
        raise ValueError(f'wake history missing: {root}')
    paths = sorted(((kind, p) for kind, pattern in (('wake', '*.snapshot.json'),
                    ('settled', '*.yield.json'), ('output', '*.work.json'))
                    for p in root.glob(pattern)), key=lambda row: (row[1].stat().st_mtime_ns, row[1].name))
    known = set() if cursor is None else set(cursor)
    names = {p.name for _, p in paths}
    selected = paths[-60:] if cursor is None else [(kind, p) for kind, p in paths if p.name not in known]
    blocks = []
    deferred = set()
    for kind, path in selected:
        if kind == 'output':
            # The work record can appear before the engine output is written: a block emitted now would say 'Output UNKNOWN' and the
            # name, once retained, would never be revisited. Wait for the output, the settlement, or a fixed grace period.
            key = wake_key(path)
            if (not list(root.glob(key + '.prompt.out.*')) and not (root / (key + '.yield.json')).exists()
                    and time.time() - path.stat().st_mtime < OUTPUT_GRACE_SECONDS):
                deferred.add(path.name)
                continue
        record = read_json(path)
        if not isinstance(record, dict) or not isinstance(record.get('role'), str):
            raise ValueError(f'malformed wake record: {path}')
        key = wake_key(path)
        stamp = time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(path.stat().st_mtime))
        role = safe(record['role'])
        if kind == 'wake':
            snapshot = record.get('snapshot')
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('reasons'), list):
                raise ValueError(f'malformed wake snapshot: {path}')
            label, text = 'WAKE', 'Reasons: ' + ', '.join(safe(x) for x in snapshot['reasons'])
        elif kind == 'settled':
            label, text = 'SETTLED', 'Reported result: ' + safe(record.get('result', 'UNKNOWN'))
            text += '\nA settlement records the handoff; it does not prove successful delivery.'
        else:
            label = 'OUTPUT'
            outputs = sorted(root.glob(key + '.prompt.out.*'))
            text = ('\n'.join(output_text(p) for p in outputs) if outputs
                    else 'Output UNKNOWN: no retained engine output.')
            if not (root / (key + '.yield.json')).exists():
                text = 'Settlement UNKNOWN: the engine ended without a yield receipt.\n' + text
        blocks.append(f'══ {stamp} · {label} {role} · {key} ══\n{text}\n')
    if cursor is None and not paths:
        blocks.append('Wake history empty; following new starts and settlements.')
    # Retain seen names across deletion, so history is never silently replayed.
    return known | (names - deferred), '\n'.join(blocks)


class Follower:
    """Keep the cursor on failed reads and make failure/recovery visible once."""
    def __init__(self, step):
        self.step = step
        self.cursor = None
        self.failure = None

    def poll(self):
        try:
            cursor, text = self.step(self.cursor)
        except Exception as exc:
            failure = f'View UNKNOWN: {type(exc).__name__}: {safe(exc)}'
            changed = failure != self.failure
            self.failure = failure
            return failure if changed else ''
        recovered = 'View recovered; following canonical history again.\n' if self.failure else ''
        self.cursor, self.failure = cursor, None
        return recovered + text


def main():
    args = tape_arguments()
    step = (lambda cursor: git_step(args.home.parent, cursor)) if args.tape == 'git' else (
        lambda cursor: wakes_step(args.home, cursor))
    print(f'{args.tape} — recent canonical history, then new entries; scroll with Ctrl-a [.', flush=True)
    follower = Follower(step)
    while True:
        text = follower.poll()
        if text:
            print(text, flush=True)
        if not args.follow:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
