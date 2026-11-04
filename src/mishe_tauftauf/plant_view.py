"""Read-only bounded area and retained wake-output views. No runtime actions."""
from __future__ import annotations

import time
import math
from pathlib import Path

from . import intentions, owed, signal_store
from .core.dispatch_record import read_record_fields, valid_role
from .feed import Feed
from .plant_view_boundary import arguments, output_text, safe, brief
from .state_files import read_json
from .wall import addressed

_MAIL_CACHE = {}


def _mail(home, start):
    paths = (home / 'chat.log', Feed(home).index_path)
    signature = tuple((p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in paths)
    key = (str(home), start)
    cached = _MAIL_CACHE.get(key)
    if cached is None or cached[0] != signature:
        entries = Feed(home).view_entries(start=start)
        if len(_MAIL_CACHE) >= 16:
            _MAIL_CACHE.clear()
        _MAIL_CACHE[key] = (signature, entries)
    return _MAIL_CACHE[key][1]


def _text(path, limit=10000):
    with path.open(encoding='utf-8') as handle:
        text = handle.read(limit + 1)
    return safe(text[:limit]) + ('\n[bounded excerpt; read original for remainder]' if len(text) > limit else '')


def render(home, role: str, *, now: float, output=False) -> str:
    home = Path(home)
    if not valid_role(role):
        raise ValueError('invalid role')
    out = [f'{role} — read-only view — sampled {now:.0f}',
           'HELD — autonomous work stopped' if (home / 'hold').exists() else 'hold absent (not proof of work)']
    try:
        record = read_record_fields(home / 'dispatch' / f'{role}.json')
        if record['role'] != role or Path(record['key']).name != record['key'] or record['key'] in ('.', '..'):
            raise ValueError('dispatch identity does not match this view')
        out += [f'Dispatch {record["state"].upper()} {record["key"]}', safe(record['detail'])]
        receipt = read_json(home / 'wakes' / f'{record["key"]}.yield.json')
        if (isinstance(receipt, dict) and receipt.get('role') == role and receipt.get('key') == record['key']
                and receipt.get('result') in ('changed', 'verified', 'blocked')
                and type(receipt.get('settled_at')) in (int, float) and math.isfinite(receipt['settled_at'])):
            out.append(f'YIELDED {safe(receipt)} — transport receipt, not acceptance')
        elif receipt is not None:
            out.append('Yield UNKNOWN: malformed or mismatched receipt; original bytes retained')
    except (OSError, ValueError) as exc:
        out.append(f'Dispatch UNKNOWN: {safe(exc)}')
    if output:
        # Exact role prefix; older records retained on disk and visible across wakes.
        paths = sorted((home / 'wakes').glob(f'wake-{role}-*.prompt.out.*'),
                       key=lambda p: p.stat().st_mtime_ns)[-3:]
        if not paths:
            out.append('IDLE / UNKNOWN output: no reactive engine output retained; see historical archives')
        for path in paths:
            try:
                out += [f'Actual output: {path.name}', output_text(path)]
            except OSError as exc:
                out.append(f'Output UNKNOWN: {safe(exc)}')
        return '\n'.join(out)
    try:
        out += ['Wall / current question and plate (excerpt; full wall remains canonical)',
                brief(_text(home / 'walls' / f'{role}.md'), 650)]
    except (OSError, UnicodeError) as exc:
        out.append(f'Wall UNKNOWN: {safe(exc)}')
    try:
        if not (home / 'signals').is_dir():
            raise ValueError('signal directory missing')
        registry = signal_store.registry(home)
        for name, entry in sorted(registry.items()):
            if role in entry.get('subscribers', []):
                sig = signal_store.read(home, name, now=now)
                out.append(f'Signal {name}: {sig.state} {safe(sig.value)} source={safe(sig.source)} '
                           f'observed={sig.observed_at} max_age={entry["max_age"]}s {safe(sig.reason)}')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        out.append(f'Signals UNKNOWN: {safe(exc)}')
    try:
        entries = intentions.list_(home, role)
        out.append('Intentions (earliest three; full records on disk):')
        for item in sorted(entries, key=lambda i: i.get('due', float('inf')))[:3]:
            out.append(f'  {item["id"]} due={item.get("due")} when={item.get("when")} {brief(item["note"], 140)}')
    except (OSError, ValueError, TypeError) as exc:
        out.append(f'Intentions UNKNOWN: {safe(exc)}')
    try:
        cursor = read_json(owed.cursor_path(home, role)) or {}
        start = cursor.get('tape_seq', 0) + 1
        mail = _mail(home, start)
        out.append(f'Unread mail: bounded tape range {start}..{start + 199}; later entries may be omitted')
        addressed_mail = [e for e in mail if addressed(e, role)]
        out += [f'#{e.sequence} {brief(e.body, 200)}' for e in addressed_mail[-5:]]
        if len(addressed_mail) > 5:
            out.append('Earlier unread messages omitted from screen; no mail was marked read.')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        out.append(f'Mail UNKNOWN: {safe(exc)}')
    return '\n'.join(out)


def main():
    args = arguments()
    previous = None
    while True:
        try:
            text = render(args.home, args.role, now=time.time(), output=args.output)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            text = f'View UNKNOWN: {safe(exc)}'
        if text != previous:
            print(('\033[H\033[2J' if args.follow else '') + text, flush=True)
            previous = text
        if not args.follow:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
