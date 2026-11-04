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


def output_text(path: Path, limit: int = 16000) -> str:
    with path.open('rb') as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - limit))
        data = handle.read(limit)
    text = data.decode('utf-8', 'replace')
    prefix = f'[retained tail; {size} bytes total]\n' if size > limit else ''
    if path.suffix == '.json':
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
