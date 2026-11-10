"""Read-only bounded area and retained wake-output views. No runtime actions.

Both panes are for people: every value is rendered as words, clock times and
counts, never as a dict, JSON or an epoch number. The host wrapper keeps only a
screenful, so the most important facts come first (top pane) or sit in the first
two lines (bottom pane). Raw bytes stay on disk and unreadable output is labelled
UNKNOWN rather than hidden.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from . import intentions, owed, signal_store, yielding
from .core.dispatch_record import read_record_fields, valid_role
from .engines import DEFAULT_CLAUDE_MODEL
from .feed import Feed
from .plant_view_boundary import (arguments, brief, cut, last_lines, output_view, routed_models, safe, uncommitted, wake_commits,
                                  wake_key, wall_lines, would_cut)
from .state_files import read_json, write_json
from .wall import addressed

_MAIL_CACHE = {}
_CHANGES_CACHE = {}
LINE = 150          # characters per displayed line; panes are ~170 columns wide
WALL_LINES = 5
WALL_READ_CHARS = 20000
LOG_REPLAY_LINES = 400      # what a restarted pane shows again from its log
LOG_RECENT_WAKES = 3        # what a pane shows the first time, before it has a log
LOG_MAX_BYTES = 5_000_000   # a log rotates to ROLE.log.1 beyond this; the wake files stay on disk anyway
LOG_KEEP_KEYS = 5000        # printed wakes remembered; older ones are covered by a high-water mark, never reprinted


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


# --- time -------------------------------------------------------------------

def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _plausible(epoch) -> bool:
    """A time worth formatting: finite and inside the range `time.gmtime` accepts (years 1654 to 5138)."""
    return _finite(epoch) and -1e10 < epoch < 1e11


def _span(seconds: float, *, coarse: bool = False) -> str:
    """A duration. `coarse` is for ages shown on a refreshing pane: under a minute it is constant text."""
    s = int(abs(seconds))
    if s < 60:
        return 'under a minute' if coarse else f'{s} s'
    if s < 3600:
        return f'{s // 60} min'
    if s < 86400:
        return f'{s // 3600} h {s % 3600 // 60} min'
    return f'{s // 86400} d {s % 86400 // 3600} h'


def _stamp(epoch, now: float) -> str:
    """'18:52Z (4 min ago)'; the date is added when it is not today (UTC)."""
    if not _plausible(epoch):
        return 'time UNKNOWN'
    day = time.gmtime(epoch)[:3] != time.gmtime(now)[:3]
    clock = time.strftime('%Y-%m-%d %H:%MZ' if day else '%H:%MZ', time.gmtime(epoch))
    delta = epoch - now
    return f'{clock} (in {_span(delta, coarse=True)})' if delta > 0 else f'{clock} ({_span(delta, coarse=True)} ago)'


def _clock(epoch) -> str:
    return time.strftime('%H:%M:%SZ', time.gmtime(epoch)) if _plausible(epoch) else '?'


# --- model ------------------------------------------------------------------

def _configured(home, role) -> tuple[str, str | None]:
    """(engine, model) this role will wake with, from engines/ROLE.json or the default."""
    try:
        config = read_json(Path(home) / 'engines' / f'{role}.json') or {}
        if not isinstance(config, dict):
            raise ValueError('engine config is not a record')
    except (OSError, ValueError):
        return 'UNKNOWN engine', None
    kind = config.get('engine', 'claude')
    model = config.get('model', DEFAULT_CLAUDE_MODEL if kind == 'claude' else None)
    return safe(kind), safe(model) if model else None


def _with_routed(home, facts, key, role, running):
    """facts with `models` filled from the router ledger when the run reported none (omp never does)."""
    if facts and facts.get('models'):
        return facts
    by_wake, by_role = routed_models(Path(home) / 'engine' / 'routing-ledger.jsonl')
    model = by_wake.get(key) or (by_role.get(role) if running else None)
    if not model:
        return facts
    blank = {'text': '', 'turns': None, 'seconds': None, 'cost': None, 'tokens_in': None, 'tokens_out': None,
             'error': False, 'subtype': None, 'stop': None, 'denials': None}
    return dict(blank, **(facts or {}), models=[safe(model)], routed=True)


def _model_text(facts, configured) -> str:
    engine, model = configured
    if facts and facts.get('models'):
        actual = ', '.join(facts['models'])
        note = '' if model is None or model in facts['models'] or facts.get('routed') else f' (configured {model}; differs)'
        return f'model {actual}{note}'
    return f'model {model or "UNKNOWN"} (configured, {engine} engine; not reported by the run)'


# --- what a wake changed ------------------------------------------------------

def _wake_span(home, key, now, *, running, out_path=None):
    """(start, end, ended) for a wake from the files it left; None start if unrecorded."""
    try:
        start = yielding.snapshot_path(home, key).stat().st_mtime
    except OSError:
        return None, None, False
    receipt = read_json(yielding.receipt_path(home, key))
    settled = receipt.get('settled_at') if isinstance(receipt, dict) else None
    if _plausible(settled) and settled >= start:
        return start, settled, True
    if running or out_path is None:
        return start, max(now, start), False
    try:
        return start, max(start, out_path.stat().st_mtime), False
    except OSError:
        return start, max(now, start), False


def _changes(home, span):
    """List of commits made in the wake window, or an UNKNOWN string."""
    start, end, ended = span
    if start is None:
        return 'the wake start was not recorded (a wake that has only just begun has none yet)'
    key = (str(home), int(start), int(end))
    if ended and key in _CHANGES_CACHE:
        return _CHANGES_CACHE[key]
    try:
        result = wake_commits(Path(home), start, end)
    except ValueError as exc:
        return safe(exc)
    if ended:
        if len(_CHANGES_CACHE) >= 64:
            _CHANGES_CACHE.clear()
        _CHANGES_CACHE[key] = result
    return result


def _per_file(commits) -> dict:
    merged = {}
    for commit in commits:
        for added, deleted, name in commit['files']:
            old = merged.get(name, (0, 0, False))
            merged[name] = (old[0] + (added or 0), old[1] + (deleted or 0),
                            old[2] or added is None or deleted is None)
    return merged


def _plus_minus(added, deleted) -> str:
    return f'+{added} −{deleted}'


def _changes_summary(commits) -> str:
    """One line: how many lines changed, in how many files and commits, and where."""
    if isinstance(commits, str):
        return f'Changes UNKNOWN: {commits}'
    if not commits:
        return 'Made no commits (walls and site notes are outside git)'
    files = _per_file(commits)
    added, deleted = sum(v[0] for v in files.values()), sum(v[1] for v in files.values())
    top = sorted(files.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:2]
    where = ', '.join(f'{name} {_plus_minus(v[0], v[1])}' for name, v in top)
    more = f', +{len(files) - 2} more' if len(files) > 2 else ''
    return (f'Changed {_plus_minus(added, deleted)} lines in {len(files)} file{"s" * (len(files) != 1)}, '
            f'{len(commits)} commit{"s" * (len(commits) != 1)}: {where}{more}')


def _changes_detail(commits, file_limit=5, commit_limit=3) -> list[str]:
    if isinstance(commits, str) or not commits:
        return [_changes_summary(commits)]
    files = sorted(_per_file(commits).items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
    out = ['Changes in git during this wake:']
    out += [f'  commit {c["id"]} {cut(c["subject"], 110)} ({cut(c["author"], 30)})' for c in commits[:commit_limit]]
    if len(commits) > commit_limit:
        out.append(f'  … {len(commits) - commit_limit} more commits')
    for name, (added, deleted, binary) in files[:file_limit]:
        out.append(f'  {_plus_minus(added, deleted)}{" (binary part)" if binary else ""}  {cut(name, 120)}')
    if len(files) > file_limit:
        out.append(f'  … {len(files) - file_limit} more files')
    return out


def _uncommitted_line(home) -> str | None:
    try:
        state = uncommitted(Path(home))
    except ValueError:
        return None
    if not state['files'] and not state['untracked']:
        return None
    added = sum(a or 0 for a, _, _ in state['files'])
    deleted = sum(d or 0 for _, d, _ in state['files'])
    return (f'Uncommitted in the shared checkout now: {_plus_minus(added, deleted)} lines in '
            f'{len(state["files"])} tracked file(s), {state["untracked"]} untracked')


# --- pieces of the top pane ---------------------------------------------------

def _wall_block(home, role) -> list[str]:
    path = home / 'walls' / f'{role}.md'
    with path.open(encoding='utf-8') as handle:
        raw = handle.read(WALL_READ_CHARS + 1)
    unread = len(raw) > WALL_READ_CHARS
    lines = wall_lines(raw[:WALL_READ_CHARS])
    shown = [cut(ln) for ln in lines[:WALL_LINES]]
    shortened = any(would_cut(ln, LINE) for ln in lines[:WALL_LINES])
    if len(lines) <= WALL_LINES and not shortened and not unread:
        note = 'whole wall'
    else:
        why = [f'first {len(shown)} of {len(lines)}{"+" if unread else ""} lines']
        if shortened:
            why.append('long lines shortened')
        if unread:
            why.append(f'only the first {WALL_READ_CHARS} characters were read')
        note = '; '.join(why)
    return [f'Wall ({note}; the full wall is canonical):'] + shown


def _signal_lines(home, role, now) -> list[str]:
    if not (home / 'signals').is_dir():
        raise ValueError('signal directory missing')
    groups = {}
    for name, entry in sorted(signal_store.registry(home).items()):
        if role not in entry.get('subscribers', []):
            continue
        sig = signal_store.read(home, name, now=now)
        state = str(sig.value) if sig.state == 'OK' else str(sig.state)
        age = f' ({_span(now - sig.observed_at, coarse=True)} ago)' if _plausible(sig.observed_at) and state != 'GREEN' else ''
        groups.setdefault(safe(state), []).append(safe(name) + age)
    if not groups:
        return ['Signals: none subscribed']
    order = sorted(groups, key=lambda s: (s != 'RED', s == 'GREEN', s))
    out = []
    for state in order:
        names = groups[state]
        out.append(cut(f'Signals {state} ({len(names)}): ' + ', '.join(names[:2])
                        + (f', +{len(names) - 2} more' if len(names) > 2 else ''), 2 * LINE))
    return out


def _next_due(home, role, now) -> str:
    entries = intentions.list_(home, role)
    if not entries:
        return 'Next due: nothing scheduled'
    first = sorted(entries, key=lambda i: i.get('due', float('inf')))[0]
    when = first.get('when')
    if isinstance(when, dict) and 'signal' in when:
        wait = f'when signal {safe(when["signal"])} is {safe(when.get("equals", when.get("state")))}'
    else:
        wait = None
    due = _stamp(first['due'], now) if 'due' in first else None
    timing = ' or '.join(x for x in (due, wait) if x)
    more = f' (+{len(entries) - 1} more)' if len(entries) > 1 else ''
    return cut(f'Next due: {timing} — {brief(first.get("note", ""), 90)}{more}')


def _mail_lines(home, role, cursor_start):
    mail = _mail(home, cursor_start)
    mine = [e for e in mail if addressed(e, role)]
    head = f'Unread mail for {role}: {len(mine)}'
    if len(mine) > 2:
        head += ' (latest 2 shown; earlier omitted from screen, none marked read)'
    return [head] + [cut(f'#{e.sequence} {brief(e.body, 200)}') for e in mine[-2:]]


# --- the views ----------------------------------------------------------------

def _header(home, role, now, configured, facts=None):
    held = (home / 'hold').exists()
    if facts and facts.get('models'):
        shown = _model_text(facts, configured)
    else:
        # engines/ROLE.json is only what is configured; the router's ledger says what the role's latest wake really ran on.
        routed = routed_models(Path(home) / 'engine' / 'routing-ledger.jsonl')[1].get(role)
        shown = f'model {safe(routed)} (router\'s latest pick for this role)' if routed else _model_text(facts, configured)
    title = f'{role} — {shown} — view at {_clock(now)[:5]}Z'
    state = ('HELD — autonomous work stopped' if held
             else 'Not held (a hold stops new wakes; its absence does not show work is running)')
    return [title, state]


def _dispatch(home, role, now):
    """(lines, record key or None, running?) for the role's latest wake."""
    try:
        record_path = home / 'dispatch' / f'{role}.json'
        record_path.stat()  # distinguish absence from malformed retained protocol bytes
        record = read_record_fields(record_path)
        if record['role'] != role or Path(record['key']).name != record['key'] or record['key'] in ('.', '..'):
            raise ValueError('dispatch identity does not match this view')
        key = record['key']
        running = str(record['state']).lower() == 'started'
        start, _, _ = _wake_span(home, key, now, running=running)
        facts = [x for x in ((f'started {_stamp(start, now)}' if start is not None else ''),
                             brief(record['detail'], 100)) if x]
        out = [cut(f'Wake {str(record["state"]).upper()}' + (' — ' + '; '.join(facts) if facts else ''))]
        receipt = read_json(yielding.receipt_path(home, key))
        if (isinstance(receipt, dict) and receipt.get('role') == role and receipt.get('key') == key
                and receipt.get('result') in ('changed', 'verified', 'blocked')
                and _plausible(receipt.get('settled_at'))):
            again = {True: 'continues in another wake', False: 'no immediate continuation'}.get(
                receipt.get('continue'), 'continuation UNKNOWN')
            out.append(f'YIELDED {receipt["result"]} at {_stamp(receipt["settled_at"], now)}; {again} '
                       '— a transport receipt, not acceptance')
        elif receipt is not None:
            out.append('Yield UNKNOWN: malformed or mismatched receipt; original bytes retained')
        return out, key, running
    except FileNotFoundError:
        return (['Dispatch UNKNOWN: no dispatch recorded; awaiting a wake or historical reconciliation. '
                 'Absence does not establish that earlier work had no effect.'], None, False)
    except (OSError, ValueError) as exc:
        return [f'Dispatch UNKNOWN: {safe(exc)}'], None, False


def _summary_parts(span, facts, commits, now):
    parts = []
    start, end, ended = span
    if start is not None:
        parts.append(f'{_clock(start)}–{_clock(end)}' if ended else f'since {_clock(start)}')
    if facts:
        if facts['error']:
            parts.append('ERROR' + (f' ({facts["subtype"]})' if facts['subtype'] else ''))
        if facts['turns'] is not None:
            parts.append(f'{facts["turns"]:.0f} turns')
        if facts['seconds'] is not None:
            parts.append(_span(facts['seconds']))
        if facts['cost'] is not None:
            parts.append(f'${facts["cost"]:.4f}')
        if facts['tokens_in'] is not None and facts['tokens_out'] is not None:
            parts.append(f'tokens {facts["tokens_in"]:,.0f} in / {facts["tokens_out"]:,.0f} out')
        if facts['denials']:
            parts.append(f'{facts["denials"]} tool call(s) denied')
    if isinstance(commits, str):
        parts.append('changes UNKNOWN')
    elif commits:
        files = _per_file(commits)
        parts.append(f'git {_plus_minus(sum(v[0] for v in files.values()), sum(v[1] for v in files.values()))}'
                     f' in {len(files)} file(s), {len(commits)} commit(s)')
    else:
        parts.append('no commits')
    return parts


def render_output(home, role: str, *, now: float) -> str:
    """Bottom pane. Lines 1-2 (model, newest wake summary) survive a screen-sized tail cut."""
    configured = _configured(home, role)
    dispatch_lines, key, running = _dispatch(home, role, now)
    # Exact role prefix; older records retained on disk and visible across wakes.
    paths = sorted((home / 'wakes').glob(f'wake-{role}-*.prompt.out.*'),
                   key=lambda p: p.stat().st_mtime_ns)[-3:]
    views = []
    for path in paths:
        try:
            facts, text = output_view(path)
            file_key = wake_key(path)
            facts = _with_routed(home, facts, file_key, role, running and file_key == key)
            span = _wake_span(home, file_key, now, running=running and file_key == key, out_path=path)
            views.append((path, facts, text, span, _changes(home, span)))
        except OSError as exc:
            views.append((path, None, f'Output UNKNOWN: {safe(exc)}', (None, None, False), 'output unreadable'))
    newest = views[-1] if views else None
    out = _header(home, role, now, configured, newest[1] if newest else None)
    if newest:
        out.insert(1, cut('Latest retained output: ' + ' · '.join(_summary_parts(newest[3], newest[1], newest[4], now))))
    else:
        out.insert(1, 'No wake output retained yet')
    out += dispatch_lines
    for path, facts, _, span, commits in views[:-1]:
        out.append(cut('Earlier wake: ' + ' · '.join([_model_text(facts, configured)]
                                                      + _summary_parts(span, facts, commits, now))))
    if not views:
        out.append('IDLE / UNKNOWN output: no reactive engine output retained; see historical archives')
    else:
        path, facts, text, span, commits = newest
        out += [f'── final output of the newest wake ({path.name}) ──', text, *_changes_detail(commits)]
    pending = _uncommitted_line(home)
    if pending:
        out.append(pending)
    return '\n'.join(out)


# --- the bottom pane as a log: each wake's output is appended once, never repainted ----------

def _wake_block(home, role, path, now, configured, *, running):
    facts, text = output_view(path, final=not running)
    facts = _with_routed(home, facts, wake_key(path), role, running)
    span = _wake_span(home, wake_key(path), now, running=running, out_path=path)
    commits = _changes(home, span)
    head = '══ ' + ' · '.join([_model_text(facts, configured)] + _summary_parts(span, facts, commits, now)) + ' ══'
    return '\n'.join([cut(head, 2 * LINE), text, *_changes_detail(commits), ''])


def _outputs(home, role):
    return sorted((home / 'wakes').glob(f'wake-{role}-*.prompt.out.*'), key=lambda p: p.stat().st_mtime_ns)


def log_step(home, role: str, now: float, *, replay: bool = False) -> list[str]:
    """New text for the pane, also appended to pane-logs/ROLE.log. Nothing when nothing is new.

    A wake is printed once, when it has settled; a running wake gets one "started" line. The
    set of printed wakes is kept in pane-logs/ROLE.json, so a restarted pane (`replay=True` on its
    first step) shows its log tail again and carries on instead of repeating or losing history.
    """
    home = Path(home)
    if not valid_role(role):
        raise ValueError('invalid role')
    if not home.is_dir():     # never create a site tree (a mistyped path would become a stray one)
        return [f'View UNKNOWN: the site home {safe(home)} does not exist; nothing was written']
    directory = home / 'pane-logs'
    log, state_path = directory / f'{role}.log', directory / f'{role}.json'
    state = read_json(state_path)
    fresh = not isinstance(state, dict) or not isinstance(state.get('printed'), list)
    printed = set() if fresh else {x for x in state['printed'] if isinstance(x, str)}
    announced = None if fresh else state.get('announced')
    floor = state.get('floor') if not fresh and type(state.get('floor')) is int else 0   # wakes at or below it were printed
    chunks = []
    if fresh:
        chunks.append(f'{role} — every wake\'s output is appended here; the full log is {log}')
    elif replay:
        try:
            chunks.append(last_lines(log.read_text(encoding='utf-8'), LOG_REPLAY_LINES))
        except (OSError, UnicodeError):
            chunks.append(f'{role} — log UNKNOWN: {log} is unreadable; wakes are printed from now on')
    configured = _configured(home, role)
    _, key, running = _dispatch(home, role, now)
    new = []
    last_error = None if fresh else state.get('error')
    error = None
    try:
        paths = _outputs(home, role)
    except OSError as exc:
        paths, error = [], f'Output UNKNOWN: {safe(exc)}'
        if error != last_error:      # a persistent fault is reported when it appears, not every poll
            new.append(error)
    if fresh:
        paths_to_print = paths[-LOG_RECENT_WAKES:]
        printed |= {wake_key(p) for p in paths[:-LOG_RECENT_WAKES]}     # older ones are on disk, not replayed
    else:
        paths_to_print = [p for p in paths if wake_key(p) not in printed and p.stat().st_mtime_ns > floor]
    for path in paths_to_print:
        file_key = wake_key(path)
        if running and file_key == key:
            continue                                                      # still being written
        try:
            new.append(_wake_block(home, role, path, now, configured, running=False))
        except OSError as exc:       # said once for this wake; the file itself stays on disk as evidence
            new.append(f'Output UNKNOWN: {file_key}: {safe(exc)}')
        printed.add(file_key)
    if running and key is not None and announced != key:
        start, _, _ = _wake_span(home, key, now, running=True)
        new.append(f'▶ wake started {_clock(start) if start is not None else "(start not recorded)"}; '
                   f'{_model_text(_with_routed(home, None, key, role, True), configured)} — its output appears here when it settles')
        announced = key
    if not paths and not new and (fresh or replay):     # said once per pane start; an idle poll prints nothing
        new.append('IDLE / UNKNOWN output: no reactive engine output retained; see historical archives')
    if new or fresh or error != last_error:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
                log.replace(directory / f'{role}.log.1')
        except OSError:
            pass
        with log.open('a', encoding='utf-8') as handle:
            handle.write('\n'.join(new) + ('\n' if new else ''))
        if len(printed) > LOG_KEEP_KEYS:
            # Forget the oldest keys, but remember how far back they reached so their files are never
            # mistaken for new wakes (every retained wake file is still enumerated each step).
            stamps = {wake_key(p): p.stat().st_mtime_ns for p in paths}
            ordered = sorted(printed, key=lambda k: (stamps.get(k, 0), k))
            dropped, kept = ordered[:-LOG_KEEP_KEYS], ordered[-LOG_KEEP_KEYS:]
            floor = max([floor] + [stamps.get(k, 0) for k in dropped])
            printed = set(kept)
        write_json(state_path, {'printed': sorted(printed), 'announced': announced, 'floor': floor, 'error': error})
    if not (new or fresh or replay):
        return []
    return chunks + new


def render(home, role: str, *, now: float, output=False) -> str:
    home = Path(home)
    if not valid_role(role):
        raise ValueError('invalid role')
    if output:
        return render_output(home, role, now=now)
    configured = _configured(home, role)
    out = _header(home, role, now, configured, _with_routed(home, None, None, role, True))
    lines, key, running = _dispatch(home, role, now)
    out += lines
    if key is not None:
        out.append(cut(_changes_summary(_changes(home, _wake_span(home, key, now, running=running)))))
    try:
        out += _wall_block(home, role)
    except (OSError, UnicodeError) as exc:
        out.append(f'Wall UNKNOWN: {safe(exc)}')
    try:
        out += _signal_lines(home, role, now)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        out.append(f'Signals UNKNOWN: {safe(exc)}')
    try:
        out.append(_next_due(home, role, now))
    except (OSError, ValueError, TypeError) as exc:
        out.append(f'Intentions UNKNOWN: {safe(exc)}')
    try:
        cursor = read_json(owed.cursor_path(home, role)) or {}
        out += _mail_lines(home, role, cursor.get('tape_seq', 0) + 1)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        out.append(f'Mail UNKNOWN: {safe(exc)}')
    return '\n'.join(out)


def main():
    args = arguments()
    if args.log:
        first, last_failure = True, None
        while True:
            try:
                chunks = log_step(args.home, args.role, time.time(), replay=first)
                first, last_failure = False, None
            except Exception as exc:     # the follower must outlive any input: a dead pane shows nothing at all
                failure = f'View UNKNOWN: {type(exc).__name__}: {safe(exc)}'
                chunks = [failure] if failure != last_failure else []     # one line per distinct fault, not per poll
                last_failure = failure
            for chunk in chunks:
                print(chunk, flush=True)
            if not args.follow:
                break
            time.sleep(5)
        return
    previous = None
    while True:
        try:
            text = render(args.home, args.role, now=time.time(), output=args.output)
        except Exception as exc:        # the view must outlive any input; a dead pane shows nothing at all
            text = f'View UNKNOWN: {type(exc).__name__}: {safe(exc)}'
        if text != previous:
            # \033[3J also drops tmux scrollback: without it every redraw adds a frame to history
            # and the pane's tmux server grows until its cgroup kills it (OOM at 19:09Z 2026-11-04).
            print(('\033[H\033[2J\033[3J' if args.follow else '') + text, flush=True)
            previous = text
        if not args.follow:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
