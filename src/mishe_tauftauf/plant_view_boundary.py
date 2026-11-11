"""Literal boundary for presentation arguments and actual engine output.

Only JSON envelopes and terminal control bytes are interpreted. Human content
is opaque: never commands, lifecycle markers, instructions or acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def safe(text: object) -> str:
    return ''.join(c if c in '\n\t' or c.isprintable() else '?' for c in str(text))


def brief(text: object, limit: int = 300) -> str:
    """Whitespace-only display compression; no interpretation of human prose."""
    value = ' '.join(safe(text).split())
    return value[:limit] + (' … [excerpt]' if len(value) > limit else '')


def _content_text(value) -> str:
    if not isinstance(value, dict) or not isinstance(value.get('content'), list):
        return ''
    return '\n'.join(part['text'] for part in value['content']
                     if isinstance(part, dict) and part.get('type') == 'text'
                     and isinstance(part.get('text'), str))


def _tool_args(args) -> str:
    """Tool arguments as words: a command as typed, other fields as name: value pairs."""
    if isinstance(args, dict):
        command = args.get('command')
        if isinstance(command, str):
            return command
        return '; '.join(f'{k}: {v}' for k, v in args.items() if isinstance(v, (str, int, float, bool)))[:400] or '(no arguments)'
    return str(args)[:400] if args is not None else '(no arguments)'


def _block_token(line: str) -> str:
    """The renderer token that opens a block: '[tool output]' in '[tool output] {..}'."""
    close = line.find(']')
    space = line.find(' ', close + 1) if close >= 0 else -1
    return line if space < 0 else line[:space]


def stream_tail(text: str, limit: int) -> str:
    """The last `limit` characters of a rendered stream, always headed by that block's own label.

    `stream_text` emits blocks that each open with a renderer line ('[tool output] ',
    '[assistant] ', ...). A raw character cut can land inside a block and drop that line, so
    the block's content reads as the renderer's own prose — a tool that dumped a JSON record
    becomes a pane full of machine syntax. So the bound is taken just past the line the cut
    lands in: that block's opening token (e.g. '[tool output]') and then the lines after the
    cut. A cut that lands exactly on a block boundary keeps whole blocks instead.
    """
    cut = len(text) - limit
    offset, token = 0, None
    for line in text.splitlines(keepends=True):
        if line.startswith(RENDERER_LINES):
            if offset >= cut and len(text) - offset <= limit:
                return text[offset:]          # the cut landed on a block boundary: keep whole blocks
            token = _block_token(line.rstrip('\n'))
        offset += len(line)
        if offset > cut:
            break
    if token is None:
        return text[cut:]                     # no renderer line at all: nothing labels the fragment
    return token + '\n' + text[offset:]


def stream_text(text: str) -> str:
    """Decode OMP JSON-lines at the literal output boundary, never as actions."""
    out, pending = [], ''
    open_tool = None  # index in `out` of the running tool's output line: updates and the end replace it, not repeat it
    # The last finished tool result, as (index in `out`, its text, times seen). A result identical to it, with only
    # tool-call lines between, is one repeated result: the line is counted, not printed again. The raw file keeps each record.
    last_result = None
    # Pure bookkeeping of the OMP stream, never shown; agent start/end stay visible as the bounds of the run.
    quiet = {'session', 'turn_start', 'turn_end', 'message_start', 'tool_stream_update', 'thinking_level_changed'}
    lifecycle = {'agent_start', 'agent_end'}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('event envelope missing')
            kind = event['type']
            if kind == 'message_update':
                delta = event.get('assistantMessageEvent')
                if not isinstance(delta, dict):
                    raise ValueError('assistant event missing')
                if delta.get('type') == 'text_delta':
                    if not isinstance(delta.get('delta'), str):
                        raise ValueError('text delta missing')
                    pending += delta['delta']
                elif delta.get('type') == 'error':
                    out.append('[assistant error] ' + str(delta.get('reason', 'unknown')))
                # Thinking and incomplete tool arguments are not display text.
                continue
            if kind == 'message_end':
                message = event.get('message')
                if not isinstance(message, dict):
                    raise ValueError('message missing')
                if message.get('role') == 'assistant':
                    completed = _content_text(message)
                    if completed or pending:
                        out.append('[assistant] ' + (completed or pending))
                    pending = ''
                continue
            if pending:
                out.append('[assistant] ' + pending)
                pending = ''
            if kind == 'tool_execution_start':
                out.append('[tool ' + str(event.get('toolName', '?')) + '] ' + _tool_args(event.get('args')))
                open_tool = None
            elif kind in {'tool_execution_update', 'tool_execution_end'}:
                key = 'partialResult' if kind == 'tool_execution_update' else 'result'
                content = _content_text(event.get(key))
                label = 'tool error' if event.get('isError') is True else 'tool output'
                line_text = '[' + label + '] ' + (content or '[non-text result; see raw record]')
                if open_tool is not None:
                    out[open_tool] = line_text
                    last_result = (open_tool, line_text, 1)   # a streamed result counts once, when it is finished
                elif (last_result is not None and last_result[1] == line_text
                      and all(s.startswith('[tool ') and not s.startswith(TOOL_LABELS) for s in out[last_result[0] + 1:])):
                    last_result = (last_result[0], line_text, last_result[2] + 1)
                    out[last_result[0]] = line_text + f' (repeated {last_result[2]} times)'
                else:
                    out.append(line_text)
                    open_tool = len(out) - 1
                    last_result = (open_tool, line_text, 1)
                if kind == 'tool_execution_end':
                    open_tool = None
            elif kind == 'auto_retry_start':
                out.append('[provider retry] attempt ' + str(event.get('attempt', '?')) + ': '
                           + str(event.get('errorMessage') or event.get('error') or 'provider error')[:300])
            elif kind == 'auto_retry_end':
                out.append('[provider retry succeeded]' if event.get('success') is True
                           else '[provider error] ' + str(event.get('finalError', 'retry failed')).splitlines()[0][:300])
            elif kind in quiet:
                continue
            elif kind in lifecycle:
                out.append('[' + kind + ']')
            else:
                out.append('UNKNOWN event ' + kind + '; raw: ' + line)
        except (ValueError, TypeError) as exc:
            out.append('UNKNOWN stream record: ' + str(exc) + '; raw: ' + line)
    if pending:
        out.append('[assistant] ' + pending)
    return '\n'.join(out) or '[waiting for stream events]'


def progress_text(path: Path) -> str:
    """What a running wake is doing, for a progress block: its latest words and its latest tool call.

    Tool output is left out (it is the noise: timings, listings); the retention preamble of a huge file is dropped too."""
    lines = [ln for ln in output_text(path).splitlines()
             if not ln.startswith(('[retained tail', 'UNKNOWN leading stream event truncated'))]
    said = [i for i, ln in enumerate(lines) if ln.startswith('[assistant] ')]
    called = [ln for ln in lines if ln.startswith('[tool ') and not ln.startswith(('[tool output]', '[tool error]'))]
    out = []
    if said:
        block = []
        for ln in lines[said[-1]:]:
            if ln.startswith('[') and not ln.startswith('[assistant] '):
                break
            block.append(ln)
        out.append('\n'.join(block).replace('[assistant] ', 'says: ', 1)[:600])
    if called:
        out.append('last call: ' + called[-1][:200])
    return '\n'.join(out) or '[waiting for stream events]'


def final_answer(text: str) -> str:
    """A finished OMP wake as a Claude wake reads: the last assistant message, with a count of the tool calls behind it.

    `text` is the decoded stream from `stream_text`; a stream that never produced an assistant message is returned whole."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith('[assistant] ')]
    calls = sum(1 for ln in lines if ln.startswith('[tool ') and not ln.startswith(('[tool output]', '[tool error]')))
    if not starts:
        # A wake that ended without a final message (a provider error, a kill): the failure lines, not the whole stream.
        faults = [ln for ln in lines if ln.startswith(('[provider', '[assistant error', 'UNKNOWN'))]
        return (f'({calls} tool calls; the wake ended without a final message)\n' + '\n'.join(faults[-5:])).rstrip()
    tail = []
    finished = any(ln.startswith('[agent_end]') for ln in lines[starts[-1]:])
    for ln in lines[starts[-1]:]:
        # The closing message ends at the next tool call, tool output or fault line: a wake killed mid-work
        # (timeout) has no [agent_end], and everything after its last words is tool output, not an answer.
        if ln.startswith(('[agent_end]', '[tool ', '[provider', '[assistant error')):
            break
        tail.append(ln)
    answer = '\n'.join(tail).replace('[assistant] ', '', 1)
    if len(answer.strip()) < 120 and len(starts) > 1:
        # A terse closing line ("Yielded verified.") says little: show what the mind said just before it too.
        before = []
        for i in starts[-3:-1]:
            block = []
            for ln in lines[i:]:
                if ln.startswith('[') and not ln.startswith('[assistant] ') and block:
                    break
                block.append(ln)
            before.append('\n'.join(block).replace('[assistant] ', '', 1))
        answer = '\n'.join(before + [answer])
    note = '' if finished else '(the wake did not finish: no closing message; these are its last words)\n'
    return (f'({calls} tool calls)\n' if calls else '') + note + answer


def cut(text: object, limit: int = 150) -> str:
    """One display line: control bytes neutralised, trimmed, ended with an ellipsis if shortened."""
    value = safe(text).strip()
    return value if len(value) <= limit else value[:limit - 1] + '…'


def would_cut(text: object, limit: int = 150) -> bool:
    """Whether `cut` would shorten this text."""
    return len(safe(text).strip()) > limit


def wall_lines(text: str) -> list[str]:
    """Non-blank lines of a wall, kept as written (markdown is prose, not interpreted)."""
    return [ln for ln in (safe(x).rstrip() for x in text.splitlines()) if ln.strip()]


def last_lines(text: str, count: int) -> str:
    """The last `count` lines of text this program wrote itself (a pane log)."""
    return '\n'.join(text.splitlines()[-count:])


def file_tail_lines(path, count: int, tail_bytes: int) -> list[str]:
    """The last `count` non-empty lines of a conversation log other programs write (opaque human text)."""
    with open(path, 'rb') as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - tail_bytes))
        text = handle.read().decode('utf-8', 'replace')
    return [row for row in text.splitlines() if row.strip()][-count:]


def wake_key(path: Path) -> str:
    """The wake key of a retained file named KEY.prompt.out.EXT (keys contain no dot).

    The key is the whole stem, e.g. `wake-genome-1793818293409683255`: exactly what `wake._key` generates and
    what the dispatch record, the snapshot and the yield receipt are named by. It is not the part after the role.
    """
    return path.name.partition('.')[0]


#: The retained record names the wakes pane reads, and the kind each one is.
#: A record's name is a machine-written file name, so deciding what a name
#: means is an interpretation of external text and belongs at this boundary.
RECORD_KINDS = (
    ('.snapshot.json', 'wake'),
    ('.yield.json', 'settled'),
    ('.work.json', 'output'),
)

#: The name a started-but-unfinished wake leaves behind.
START_SUFFIX = '.work-start.json'


def record_kind(name: str) -> str | None:
    """The kind of retained record this file name names, or None."""
    for suffix, kind in RECORD_KINDS:
        if name.endswith(suffix):
            return kind
    return None


def record_names(names: list[str]) -> list[tuple[str, str]]:
    """The (kind, name) pairs of the retained records among these file names.

    Names that name no retained record are skipped: the pane reads records, not
    every file the wakes directory happens to hold.
    """
    found = []
    for name in names:
        kind = record_kind(name)
        if kind is not None:
            found.append((kind, name))
    return found


def start_names(names: list[str]) -> list[str]:
    """The names of the wakes that have started and not yet finished."""
    return [name for name in names if name.endswith(START_SUFFIX)]


def read_records(names: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """The retained records and the started wakes among these file names.

    One pass over the names: the pane polls this every few seconds over a
    directory of thousands of retained files.
    """
    records = []
    starts = []
    for name in names:
        kind = record_kind(name)
        if kind is not None:
            records.append((kind, name))
        elif name.endswith(START_SUFFIX):
            starts.append(name)
    return records, starts


RAW_OPEN, RAW_CLOSE = '[raw unreadable bytes follow]', '[end of raw bytes]'

# A command's output is content the mind asked for and read, not chrome the renderer printed: a tool may
# legitimately print JSON, dicts, paths or epochs. The block runs from its label to the next renderer line,
# and a command may print a line that looks like one, so the end is a known renderer line, not any bracket.
TOOL_LABELS = ('[tool output]', '[tool error]')
# Every tool line opens a block of content the mind produced: the call's own arguments (a command as typed,
# an edit's bytes — both can span lines) and the output that call printed. A pane's machine-syntax reading is
# about the renderer's own chrome, so these blocks are held together and only a known renderer line closes one.
# checks/wakes-view-engines.py states the same rule for the same bytes.
TOOL_CONTENT_OPENS = ('[tool ',)
RENDERER_LINES = ('[assistant', '[tool ', '[provider ', '[agent_', '[rendered tail]', '[retained tail;',
                  '[raw unreadable bytes follow]', '[end of raw bytes]', '[waiting for stream events',
                  '[final text truncated', '[output truncated', '[the mind returned no final text')

# Machine syntax a person should never have to read in a pane: JSON/dict keys, a
# line that opens or closes a structure, escaped newlines, epoch numbers and the
# interpreter's private path. Lines marked UNKNOWN, the delimited raw block and
# what a tool call carried (its own arguments and its printed output,
# TOOL_CONTENT_OPENS) are deliberate evidence and are exempt.
_MACHINE = (
    (re.compile(r'^\s*[\[{]\s*($|["\'{\[])|^\s*[\]}],?\s*$'), 'a line that opens or closes a JSON structure'),
    (re.compile(r'"[A-Za-z_][\w-]*"\s*:|\'[A-Za-z_][\w-]*\'\s*:\s'), 'a quoted key (JSON or dict)'),
    (re.compile(r'\\[nrt]'), 'an escaped newline or tab'),
    (re.compile(r'(?<![\w.])1[5-9]\d{8}(\.\d+)?(?![\w])'), 'a raw epoch time'),
    (re.compile(r'\.venv/'), 'the interpreter private path'),
    (re.compile(r'[\x00-\x08\x0b-\x1f\x7f]'), 'a control byte'),
)


# Prose a mind wrote itself: a wake's answer, the wall excerpt and mail text. A mind cites commands, paths, times and
# snippets to explain things, so inside these blocks only a structure line (one that opens or closes a JSON structure,
# the signature of a dumped record) and a control byte count. Everything else the renderer prints is held to every rule.
_PROSE_OPENS = ('══ model', '── final output', 'Wall (', 'Unread mail for')
_PROSE_CLOSES = ('Changes in git during this wake', 'Made no commits', 'Changes UNKNOWN', 'Uncommitted in the shared',
                 '▶ wake started', '══ model', 'IDLE / UNKNOWN', 'Signals ', 'Signals:', 'Next due', 'Intentions UNKNOWN',
                 'Mail UNKNOWN', 'Wall UNKNOWN')
_PROSE_RULES = ('a line that opens or closes a JSON structure', 'a control byte')


def machine_syntax(text: str) -> list[str]:
    """Describe each non-exempt line of a rendered pane that is not human text."""
    found, raw, prose, tool = [], False, False, False
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip() == RAW_OPEN:
            raw = True
        if raw or line.startswith('UNKNOWN'):
            raw = raw and line.strip() != RAW_CLOSE
            continue
        if line.startswith(TOOL_CONTENT_OPENS):
            tool = True
        elif line.startswith(RENDERER_LINES):
            tool = False
        if tool:
            continue
        if line.startswith(_PROSE_CLOSES):
            prose = False
        for pattern, reason in _MACHINE:
            if prose and reason not in _PROSE_RULES:
                continue
            if pattern.search(line):
                found.append(f'line {number}: {reason}: {line.strip()[:80]}')
                break
        if line.startswith(_PROSE_OPENS):
            prose = True
    return found


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:           # an integer too large for a float is unreadable, not a crash
        return None
    return number if number == number and abs(number) != float('inf') else None


def envelope_facts(value: object) -> dict | None:
    """Decode a ``claude -p --output-format json`` result envelope.

    Returns the facts a human wants (model, turns, duration, cost, tokens,
    outcome) plus the final ``text``; None when the value is not that envelope.
    The model comes from the keys of ``modelUsage``: it is what actually ran.
    """
    if not isinstance(value, dict) or not isinstance(value.get('result'), str):
        return None
    usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
    parts = [_number(usage.get(k)) for k in ('input_tokens', 'cache_creation_input_tokens',
                                              'cache_read_input_tokens')]
    models = value.get('modelUsage')
    duration = _number(value.get('duration_ms'))
    denials = value.get('permission_denials')
    return {
        'text': value['result'],
        'models': [m for m in models if isinstance(m, str)] if isinstance(models, dict) else None,
        'turns': _number(value.get('num_turns')),
        'seconds': duration / 1000 if duration is not None else None,
        'cost': _number(value.get('total_cost_usd')),
        'tokens_in': sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None,
        'tokens_out': _number(usage.get('output_tokens')),
        'error': value.get('is_error') is True,
        'subtype': value.get('subtype') if isinstance(value.get('subtype'), str) else None,
        'stop': value.get('stop_reason') if isinstance(value.get('stop_reason'), str) else None,
        'denials': len(denials) if isinstance(denials, list) else None,
    }


def output_view(path: Path, limit: int = 16000, final: bool = False) -> tuple[dict | None, str]:
    """Return (envelope facts or None, human text) for one retained engine output.

    Parsed JSON is never echoed: a recognised envelope shows its ``result`` text,
    an unrecognised one shows a one-line UNKNOWN. Only bytes that fail to parse
    are shown raw, labelled UNKNOWN, so that failure evidence stays visible.
    """
    # OMP's terminal agent_end can repeat a large conversation. Read a bounded
    # event window so that its preceding message_end remains available, then
    # bound the rendered text separately. Never dump a cut leading event.
    window = max(limit, 1024 * 1024)
    with path.open('rb') as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - window))
        data = handle.read(window)
    text = data.decode('utf-8', 'replace')
    prefix = f'[retained tail; {size} bytes total]\n' if size > window else ''
    facts = None
    if path.suffix == '.jsonl':
        if size > window:
            text = text.partition('\n')[2]
            prefix += 'UNKNOWN leading stream event truncated; original bytes retained in file\n'
        text = stream_text(text)
        if final:
            text = final_answer(text)   # a settled OMP wake reads like a Claude wake: its last message
        if len(text) > limit:
            text = '[rendered tail]\n' + stream_tail(text, limit)
    elif path.suffix == '.json':
        try:
            value = json.loads(text)
            if not isinstance(value, (dict, list)):
                raise ValueError('not an engine envelope')
            facts = envelope_facts(value)
            if facts is not None:
                text = facts['text'].strip() or '[the mind returned no final text]'
                if len(text) > limit:
                    text = text[:limit] + f'\n[final text truncated; {len(text)} characters in the file]'
            else:
                text = f'UNKNOWN: the engine JSON has an unrecognised shape; original kept in {path.name}'
        except (ValueError, RecursionError):      # deeply nested JSON raises RecursionError, which is not a ValueError
            prefix += 'UNKNOWN: partial, malformed, mixed stderr or truncated JSON; raw tail follows\n'
            text = RAW_OPEN + '\n' + text[-3000:] + '\n' + RAW_CLOSE
    if path.suffix not in ('.json', '.jsonl') and len(text) > limit:   # JSON shapes bound themselves above
        text = f'[output truncated to its last {limit} characters; {size} bytes in the file]\n' + text[-limit:]
    return facts, safe(prefix + text)


def output_text(path: Path, limit: int = 16000) -> str:
    return output_view(path, limit)[1]


def _git(cwd: Path, *args: str, timeout: float = 5.0) -> str:
    # Read-only, and never takes the index lock, so a view cannot disturb a mind's commit.
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
    try:
        done = subprocess.run(['git', '-C', str(cwd), *args], env=env, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f'git unavailable: {exc}') from exc
    if done.returncode != 0:
        raise ValueError('not a readable git checkout: ' + ' '.join(done.stderr.split())[:120])
    return done.stdout


def _numstat(lines) -> list[tuple[int | None, int | None, str]]:
    files = []
    for line in lines:
        added, _, rest = line.partition('\t')
        deleted, _, name = rest.partition('\t')
        if not name:
            continue
        files.append((int(added) if added.isdigit() else None,
                      int(deleted) if deleted.isdigit() else None, name))
    return files


def wake_commits(cwd: Path, since: float, until: float) -> list[dict]:
    """Commits dated inside a wake's run window, with per-file line counts.

    Wakes run concurrently (one per role), so commits in a wake's window may include peers';
    treat them as candidates (a human commit in the same window would also appear; the author
    is shown). Walls, notes and other site files are not in git and not counted.
    """
    if not (since <= until):
        raise ValueError('wake window is not ordered')
    out = _git(cwd, 'log', f'--since=@{int(since)}', f'--until=@{int(until) + 1}', '--no-renames',
               '--numstat', '--format=%x1e%h%x1f%an%x1f%s')
    commits = []
    for chunk in out.split('\x1e')[1:]:
        head, _, body = chunk.partition('\n')
        short, _, rest = head.partition('\x1f')
        author, _, subject = rest.partition('\x1f')
        commits.append({'id': safe(short), 'author': safe(author), 'subject': safe(subject),
                        'files': _numstat(body.splitlines())})
    return commits


def uncommitted(cwd: Path) -> dict:
    """Tracked changes not yet committed (any author) and the count of untracked files."""
    files = _numstat(_git(cwd, 'diff', 'HEAD', '--no-renames', '--numstat').splitlines())
    untracked = [n for n in _git(cwd, 'ls-files', '--others', '--exclude-standard', '-z').split('\0') if n]
    return {'files': files, 'untracked': len(untracked)}


def git_tape(cwd: Path, previous: str | None) -> tuple[str, str]:
    """Render Git's own chronological commit messages and file summaries."""
    head = _git(cwd, 'rev-parse', '--verify', 'HEAD').strip()
    if head == previous:
        return head, ''
    notice = ''
    if previous is not None:
        try:
            ancestor = _git(cwd, 'merge-base', previous, head).strip() == previous
        except ValueError:
            ancestor = False
        if not ancestor:
            notice = 'Git history changed; replaying the latest 30 reachable commits. Earlier output remains in scrollback.\n'
            previous = None
    selection = [f'{previous}..{head}'] if previous else ['-30', head]
    text = _git(cwd, '--no-pager', 'log', '--reverse', '--no-color', '--no-decorate',
                '--stat', '--date=iso-strict', '--format=commit %h%nAuthor: %an%nDate: %cI%n%n%B',
                *selection)
    return head, notice + safe(text)


def tape_arguments():
    parser = argparse.ArgumentParser(description='Append-only canonical Git and wake tapes')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--tape', required=True, choices=('git', 'wakes'))
    parser.add_argument('--follow', action='store_true')
    return parser.parse_args()


def arguments():
    parser = argparse.ArgumentParser(description='Read-only plant presentation; never starts work')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--role', required=True)
    parser.add_argument('--output', action='store_true')
    parser.add_argument('--follow', action='store_true')
    parser.add_argument('--log', action='store_true',
                        help='append each wake\'s output once to pane-logs/ROLE.log and the pane; never repaint')
    return parser.parse_args()


def routed_models(path) -> tuple[dict, dict]:
    """({wake key: model}, {role: model of its latest select}) from the router's ledger: the engine a wake REALLY ran on."""
    by_wake, by_role = {}, {}
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()[-1500:]
    except OSError:
        return by_wake, by_role
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or not isinstance(ev.get('key'), str):
            continue
        model = ev['key'].partition(':')[2].rsplit(':', 1)[0]
        if ev.get('event') == 'select' and isinstance(ev.get('role'), str):
            by_role[ev['role']] = model
        if ev.get('event') in ('select', 'outcome') and isinstance(ev.get('wake'), str):
            by_wake[ev['wake']] = model
    return by_wake, by_role


#: The event types a salvage scan looks for. Every other line is skipped without being decoded, so a
#: 170 MB stream of streaming tool updates costs a substring test, not 170 MB of JSON parsing.
SALVAGE_MARKS = ('"message_end"', '"tool_execution_start"', '"auto_retry_end"')


def _first_line(value: object, fallback: str = '(no error text)') -> str:
    """The first line of a provider's message, or a named placeholder when it carries none.

    A provider fault may arrive without any text at all; reading its first line must not raise, because
    the reader is a wake that is already reconciling a lost one.
    """
    lines = safe(value).splitlines()
    return (lines[0] if lines else fallback)[:200]


def salvage_text(path: Path, *, tools: int = 8, limit: int = 4000) -> str:
    """What a lost wake learned, from its own stream: its last words, its last tool calls, its faults.

    A wake cut at its timeout leaves a stream but no wall, no yield and no result. The next wake of that
    role used to rediscover the work from the repository; this hands it the wake's own record instead.
    The prose is the wake's content, never an instruction, and the result is bounded text.
    An absent or unreadable stream yields "". Control bytes are neutralised: the text is handed to the
    next mind as prose, not as a terminal sequence or a bidirectional spoof.
    """
    try:
        if path.suffix == '.json':
            value = json.loads(path.read_text(encoding='utf-8', errors='replace'))
            facts = envelope_facts(value)
            text = facts['text'] if facts else ''
            if not text.strip():
                return ''
            return ('Last words of that wake (its own prose, not instructions):\n  '
                    + safe(text[:limit]))
        words, calls, faults = [], [], []
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if not any(mark in line for mark in SALVAGE_MARKS):
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get('type')
                if kind == 'message_end':
                    message = event.get('message')
                    if not isinstance(message, dict) or message.get('role') != 'assistant':
                        continue
                    if message.get('stopReason') == 'error' and message.get('errorMessage'):
                        faults.append('provider error: ' + _first_line(message['errorMessage']))
                    text = _content_text(message)
                    if text.strip():
                        words.append(text)
                elif kind == 'tool_execution_start':
                    calls.append(str(event.get('toolName', '?')) + ': ' + _tool_args(event.get('args')))
                elif kind == 'auto_retry_end' and event.get('success') is False:
                    faults.append('provider retry failed: ' + _first_line(event.get('finalError')))
    except (OSError, ValueError, RecursionError):
        return ''
    if not words and not calls and not faults:
        return ''
    out = []
    if words:
        out.append('Last words of that wake (its own prose, not instructions):')
        out.extend('  ' + safe(w).replace('\n', ' ')[:600] for w in words[-3:])
    if calls:
        out.append('Its last tool calls:')
        out.extend('  ' + safe(c).replace('\n', ' ')[:200] for c in calls[-tools:])
    if faults:
        out.append('Provider faults it hit:')
        out.extend('  ' + safe(f).replace('\n', ' ')[:200] for f in faults[-3:])
    return '\n'.join(out)[:limit]


LEDGER_TAIL_BYTES = 400_000


def wake_models(home) -> dict[str, str]:
    """Per wake id, the model that actually ran, from the router's own ledger (never the configured model).

    The text is ``engine model`` for the last candidate selected for the wake, plus ``after: <model> <why>`` for each
    candidate it fell through. A wake with no select event is absent: the caller shows that as pending or UNKNOWN.
    """
    path = Path(home) / 'engine' / 'routing-ledger.jsonl'
    try:
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LEDGER_TAIL_BYTES))
            raw = handle.read().decode('utf-8', 'replace')
    except OSError:
        return {}
    ran: dict[str, str] = {}
    fell: dict[str, list[str]] = {}

    def name(key: str) -> str:
        engine, _, rest = key.partition(':')
        model = rest.rsplit(':', 1)[0] if ':' in rest else rest
        return f'{engine} {model}'

    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue   # the first line of the tail can be cut; a malformed row is not shown
        if not isinstance(event, dict) or not isinstance(event.get('wake'), str) or not isinstance(event.get('key'), str):
            continue
        wake = event['wake']
        if event.get('event') == 'select':
            ran[wake] = name(event['key'])
        elif event.get('event') == 'fallback':
            why = str(event.get('reason', '')).split(':', 1)[0].strip()[:40] or 'refused'
            fell.setdefault(wake, []).append(f"{name(event['key']).split(' ', 1)[1]} {why}")
    return {wake: text + ''.join(f' (after: {f})' for f in fell.get(wake, [])) for wake, text in ran.items()}
