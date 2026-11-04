"""Literal boundary for plant revival: arguments, guard/readiness JSON, systemd and tmux text.

Only the framing of these machine outputs is interpreted here. `revive.py` acts on the
decoded values and never parses text itself. Malformed or missing input decodes to None
or is skipped, so an unreadable reading can never be mistaken for a healthy one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def arguments(argv=None):
    parser = argparse.ArgumentParser(description='Bring a dead or tripped plant back to life, within the guard\'s own gates')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true', help='probe and plan; change nothing')
    parser.add_argument('--auto', action='store_true', help='unattended (timer) use: also requires the trip to be at least 10 minutes old')
    parser.add_argument('--allow-repeat', action='store_true', help='human override: restore although the same trip reason was restored within 3 hours')
    parser.add_argument('--diagnosis', default='', help='what is known about the cause of the trip; recorded in the restore evidence')
    return parser.parse_args(argv)


def decode_json(text):
    """A JSON value, or None when absent or malformed."""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError, RecursionError):
        return None


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True)


def jsonl(text) -> list[dict]:
    """Records of a JSON-lines file; malformed lines are skipped, not trusted."""
    out = []
    for line in (text or '').splitlines():
        value = decode_json(line) if line.strip() else None
        if isinstance(value, dict):
            out.append(value)
    return out


def in_mishe_slice(cgroup_text) -> bool:
    return any('/mishe.slice' in line for line in (cgroup_text or '').splitlines())


def parent_pid(status_text):
    for line in (status_text or '').splitlines():
        if line.startswith('PPid:'):
            fields = line.split()
            if len(fields) == 2 and fields[1].isdigit():
                return int(fields[1])
    return None


def restore_archive(name: str, text):
    """(restore time in seconds, original trip reason) of a guard-archived latch `tripped-<ns>.json`."""
    stamp = name.removesuffix('.json').partition('-')[2]
    value = decode_json(text)
    if not stamp.isdigit() or not isinstance(value, dict):
        return None
    return int(stamp) / 1e9, value.get('reason')


def unit_rows(text) -> list[tuple[str, str, str, str]]:
    """(unit, active, sub, description) from `systemctl list-units --plain --no-legend`."""
    rows = []
    for line in (text or '').splitlines():
        fields = line.split(None, 4)
        if len(fields) >= 4:
            rows.append((fields[0], fields[2], fields[3], fields[4] if len(fields) > 4 else ''))
    return rows


def session_names(text) -> list[str]:
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


def first_line(text, limit=160) -> str:
    for line in (text or '').splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ''


def pinned_path(unit_text):
    """The PYTHONPATH a generated wake unit is pinned to."""
    for line in (unit_text or '').splitlines():
        if line.startswith('Environment=PYTHONPATH='):
            return line.partition('Environment=PYTHONPATH=')[2].strip()
    return None


def unit_kind(name: str, description: str = '') -> str:
    """Which part of the plant a systemd unit is: wake-path, wake-service, timer, service, plant-scope or other."""
    if name.startswith('mishe-wake-') and name.endswith('.path'):
        return 'wake-path'
    if name.startswith('mishe-wake@') and name.endswith('.service'):
        return 'wake-service'
    if name.startswith('mishe-') and name.endswith('.timer'):
        return 'timer'
    if name.startswith('mishe-') and name.endswith('.service'):
        return 'service'
    if name.startswith('run-') and name.endswith('.scope') and 'plant_boot' in description:
        return 'plant-scope'
    return 'other'
