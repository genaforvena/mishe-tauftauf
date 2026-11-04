"""Literal boundary for presentation arguments and actual engine output.

Only JSON envelopes and terminal control bytes are interpreted. Human content
is opaque: never commands, lifecycle markers, instructions or acceptance.
"""
from __future__ import annotations

import argparse
import json
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


def stream_text(text: str) -> str:
    """Decode OMP JSON-lines at the literal output boundary, never as actions."""
    out, pending = [], ''
    lifecycle = {'session', 'agent_start', 'agent_end', 'turn_start', 'turn_end',
                 'message_start', 'tool_stream_update'}
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
                out.append('[tool ' + str(event.get('toolName', '?')) + '] '
                           + json.dumps(event.get('args'), ensure_ascii=False))
            elif kind in {'tool_execution_update', 'tool_execution_end'}:
                key = 'partialResult' if kind == 'tool_execution_update' else 'result'
                content = _content_text(event.get(key))
                label = 'tool error' if event.get('isError') is True else 'tool output'
                out.append('[' + label + '] ' + (content or '[non-text result; see raw record]'))
            elif kind in lifecycle:
                out.append('[' + kind + ']')
            else:
                out.append('UNKNOWN event ' + kind + '; raw: ' + line)
        except (ValueError, TypeError) as exc:
            out.append('UNKNOWN stream record: ' + str(exc) + '; raw: ' + line)
    if pending:
        out.append('[assistant] ' + pending)
    return '\n'.join(out) or '[waiting for stream events]'


def output_text(path: Path, limit: int = 16000) -> str:
    # OMP's terminal agent_end can repeat a large conversation. Read a bounded
    # event window so that its preceding message_end remains available, then
    # bound the rendered text separately. Never dump a cut leading event.
    window = max(limit, 1024 * 1024) if path.suffix == '.jsonl' else limit
    with path.open('rb') as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - window))
        data = handle.read(window)
    text = data.decode('utf-8', 'replace')
    prefix = f'[retained tail; {size} bytes total]\n' if size > window else ''
    if path.suffix == '.jsonl':
        if size > window:
            text = text.partition('\n')[2]
            prefix += 'UNKNOWN leading stream event truncated; original bytes retained in file\n'
        text = stream_text(text)
        if len(text) > limit:
            text = '[rendered tail]\n' + text[-limit:]
    elif path.suffix == '.json':
        try:
            value = json.loads(text)
            if not isinstance(value, (dict, list)):
                raise ValueError('not an engine envelope')
            text = json.dumps(value, indent=2, ensure_ascii=False)
        except ValueError:
            prefix += 'UNKNOWN: partial, malformed, mixed stderr or truncated JSON; raw output follows\n'
    return safe(prefix + text)


def arguments():
    parser = argparse.ArgumentParser(description='Read-only plant presentation; never starts work')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--role', required=True)
    parser.add_argument('--output', action='store_true')
    parser.add_argument('--follow', action='store_true')
    return parser.parse_args()
