#!/usr/bin/env python3
"""Detector: the wakes window renders every engine's newest wake output readably.

Minds run on several engines and models (Claude Haiku, OMP with gpt-6.1-terra,
OMP with glm-5.3, others). Each engine writes its own output shape, and the
wakes window decodes that shape through `plant_view_boundary.output_view`. When
the shape changes, the window prints tool output two or three times, leaks turn
and message bookkeeping, or shows raw JSON. This check renders the newest
retained output of each model in use, through that same live code path, in both
modes the pane uses (the newest output, and a settled wake's final answer), and
fails on:

  - an `UNKNOWN event` line for an event type the renderer knows;
  - an `UNKNOWN stream record` line whose raw record carries a known event type;
  - consecutive duplicate tool-output blocks;
  - bookkeeping lines (turn_start, turn_end, message_start, session, ...);
  - raw JSON or dict syntax in the rendered text (the pane's own machine_syntax rules);
  - an exception raised by the renderer.

Engine change: every SITE/engines/ROLE.json names a model. If a role's
configured model has no retained wake output yet, that model cannot be shown
passing, so it is UNKNOWN until its first wake is rendered. The next wake of
that role after a model change is therefore the one that must pass.

On a failure with --route, the renderer owner (genome) gets one DM per distinct
failure, naming the sample wake key. The same failure is not re-sent: the
signature is kept in SITE/checks/wakes-view-engines-route.json.

Exit status:
  0  every model in use renders readably in both modes
  2  at least one rendered output fails a rule (each is listed with its wake key)
  3  undecidable: no output, no engine config, the renderer cannot be imported,
     or a configured model has no retained output yet
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

KNOWN_KINDS = frozenset({
    'session', 'turn_start', 'turn_end', 'message_start', 'message_update', 'message_end',
    'tool_execution_start', 'tool_execution_update', 'tool_execution_end', 'tool_stream_update',
    'auto_retry_start', 'auto_retry_end', 'thinking_level_changed', 'agent_start', 'agent_end',
})
BOOKKEEPING = re.compile(r'^(UNKNOWN event |\[)?(turn_start|turn_end|message_start|session|tool_stream_update|thinking_level_changed)\b')
TOOL_LABELS = ('[tool output]', '[tool error]')
# Keys only an engine's event envelope carries; a rendered line that shows one is a leaked record.
ENVELOPE_KEY = re.compile(r'"(assistantMessageEvent|partialResult|toolCallId|stopReason|modelUsage|'
                          r'tool_execution_\w+|message_(start|update|end)|turn_(start|end))"\s*:')
MODEL_RE = re.compile(rb'"model"\s*:\s*"([^"\\]{1,120})"')
SAMPLE_LIMIT = 16000


def wake_key(path: Path) -> str:
    return path.name.partition('.')[0]


def model_name(path: Path, read_size: int = 1024 * 1024) -> str | None:
    """The model that produced one retained output, or None when it cannot be told."""
    if path.suffix == '.json':
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, RecursionError):
            return None
        usage = value.get('modelUsage') if isinstance(value, dict) else None
        if isinstance(usage, dict) and usage:
            return sorted(usage)[0].rsplit('/', 1)[-1]
        return None
    with path.open('rb') as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - read_size))
        found = MODEL_RE.findall(handle.read())
    return found[-1].decode('utf-8', 'replace').rsplit('/', 1)[-1] if found else None


def newest_per_model(wakes: Path, limit: int) -> dict[str, Path]:
    paths = sorted(wakes.glob('wake-*.prompt.out.*'), key=lambda p: p.stat().st_mtime_ns, reverse=True)[:limit]
    newest: dict[str, Path] = {}
    for path in paths:
        if path.suffix not in ('.json', '.jsonl'):
            continue
        name = model_name(path)
        if name and name not in newest:
            newest[name] = path
    return newest


def configured_models(home: Path) -> dict[str, tuple[str, str]]:
    """role -> (engine, model) for every current engines/ROLE.json."""
    out = {}
    for path in sorted((home / 'engines').glob('*.json')):
        try:
            config = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        model = config.get('model') if isinstance(config, dict) else None
        if isinstance(model, str) and model:
            out[path.stem] = (str(config.get('engine', '?')), model.rsplit('/', 1)[-1])
    return out


def lint(text: str) -> list[str]:
    """Every rule violation in one rendered output, as short descriptions."""
    problems = []
    lines = text.splitlines()
    in_tool = False  # tool output is what a command printed, e.g. source code quoting envelope keys: content, not a leak
    for line in lines:
        if line.startswith(TOOL_LABELS):
            in_tool = True
        elif line.startswith('[') or line.startswith('UNKNOWN'):
            in_tool = False
        if line.startswith('UNKNOWN event '):
            kind = line[len('UNKNOWN event '):].split(';', 1)[0].strip()
            if kind in KNOWN_KINDS:
                problems.append(f'UNKNOWN event for known type {kind}')
        elif line.startswith('UNKNOWN stream record'):
            raw = line.partition('; raw: ')[2]
            try:
                event = json.loads(raw)
            except ValueError:
                event = None
            if isinstance(event, dict) and event.get('type') in KNOWN_KINDS:
                problems.append(f"UNKNOWN stream record for known type {event['type']}")
        if BOOKKEEPING.match(line):
            problems.append('bookkeeping line: ' + line.strip()[:60])
        if not in_tool and ENVELOPE_KEY.search(line):
            problems.append('raw event envelope key: ' + line.strip()[:60])
        if not in_tool and line.strip().startswith('{'):
            try:
                if isinstance(json.loads(line), dict):
                    problems.append('raw JSON object line: ' + line.strip()[:60])
            except ValueError:
                pass
    blocks, current = [], None
    for line in lines:
        if line.startswith(TOOL_LABELS):
            current = [line]
            blocks.append(current)
        elif line.startswith('[') or line.startswith('UNKNOWN'):
            current = None
        elif current is not None:
            current.append(line)
    for before, after in zip(blocks, blocks[1:]):
        if '\n'.join(after) == '\n'.join(before) and before[0].strip():
            problems.append('consecutive duplicate tool output: ' + before[0][:60])
    return problems


def render_problems(path: Path, machine_syntax, output_view) -> tuple[list[str], str]:
    """Render one output in both pane modes through the live code and lint each."""
    problems = []
    for final in (False, True):
        try:
            _, text = output_view(path, limit=SAMPLE_LIMIT, final=final)
        except Exception as exc:  # any renderer exception is a violation, not a crash of the check
            problems.append(f'render exception (final={final}): {type(exc).__name__}: {str(exc)[:120]}')
            continue
        problems += [f'final={final}: {p}' for p in lint(text)]
        # Only JSON structure and control bytes from the boundary's rules: agent output legitimately
        # carries paths, PIDs and timestamps, which are content, not renderer leaks.
        problems += [f'final={final}: {m}' for m in machine_syntax(text)
                     if ': a line that opens or closes a JSON structure' in m or ': a control byte' in m]
    return problems, wake_key(path)


def signature(sample_key: str, problems: list[str]) -> str:
    return hashlib.sha256('\n'.join([sample_key, *sorted(problems)]).encode()).hexdigest()


def route(home: Path, problems: list[tuple[str, str, str]]) -> None:
    """One DM to the renderer owner per distinct failure; nothing for a repeat."""
    state_path = home / 'checks' / 'wakes-view-engines-route.json'
    key_sig = hashlib.sha256('\n'.join(sorted(f'{k}|{p}' for _, k, p in problems)).encode()).hexdigest()
    try:
        last = json.loads(state_path.read_text(encoding='utf-8')).get('signature')
    except (OSError, ValueError, AttributeError):
        last = None
    if last == key_sig:
        return
    lines = ['[dm] to=genome',
             'Wakes view: a renderer failure for the engine outputs listed below (check wakes-view-engines).',
             'Resolver=genome (renderer). Live path: src/mishe_tauftauf/plant_view_boundary.py output_view and stream_text.']
    for model, sample_key, problem in problems[:12]:
        lines.append(f'- model={model} sample={sample_key}: {problem}')
    lines.append('Re-run: checks/wakes-view-engines.py --home SITE --repo REPO. A repeated failure is not re-sent.')
    cli = home / 'bin' / 'mishe-tauftauf'
    result = subprocess.run([str(cli), '--home', str(home), 'append', '--source', 'sensor/wakes-view-engines'],
                            input='\n'.join(lines), text=True, capture_output=True, timeout=60)
    if result.returncode == 0:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({'signature': key_sig}), encoding='utf-8')


def evaluate(home: Path, repo: Path, limit: int, do_route: bool) -> tuple[int, list[str]]:
    sys.path.insert(0, str(repo / 'src'))
    try:
        from mishe_tauftauf.plant_view_boundary import machine_syntax, output_view
    except Exception as exc:
        return 3, [f'renderer cannot be imported from {repo}/src: {type(exc).__name__}: {exc}']
    newest = newest_per_model(home / 'wakes', limit)
    if not newest:
        return 3, [f'no retained engine outputs under {home}/wakes']
    roles = configured_models(home)
    if not roles:
        return 3, [f'no engine configuration under {home}/engines']
    lines, failures, pending = [], [], []
    for model, path in sorted(newest.items()):
        problems, sample = render_problems(path, machine_syntax, output_view)
        state = 'FAIL' if problems else 'PASS'
        lines.append(f'model {model}: {state} (sample {sample})')
        failures += [(model, sample, p) for p in problems]
    for role, (engine, model) in sorted(roles.items()):
        if model not in newest:
            pending.append(f'role {role}: model {model} ({engine}) has no retained wake output yet; UNKNOWN until its first wake renders')
    if failures:
        if do_route:
            route(home, failures)
        out = [f'{len(failures)} rendering violation(s) across {len(newest)} model(s)']
        out += [f'{m} sample={k}: {p}' for m, k, p in failures[:12]]
        return 2, out + lines + pending
    if pending:
        return 3, lines + pending
    return 0, [f'{len(newest)} model(s) in use render readably in both pane modes'] + lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--limit', type=int, default=400, help='retained outputs scanned for the newest per model')
    parser.add_argument('--route', action='store_true', help='DM genome once per distinct failure')
    args = parser.parse_args(argv)
    code, lines = evaluate(args.home, args.repo, args.limit, args.route)
    print('\n'.join(lines))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
