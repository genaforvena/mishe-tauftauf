"""Boundary adapter: decodes cgroup cpu.stat text and the persisted throttle baseline.

slice_throttle.py consumes decoded counters and slice identities only. Reading the
kernel's ``cpu.stat`` body (one ``name value`` pair per line) and the JSON baseline
written by a previous discover scan are external representations, so they are
decoded here and nowhere else.
"""
from __future__ import annotations

import json
from pathlib import Path

COUNTERS = ("nr_periods", "nr_throttled", "throttled_usec")
LOCAL_COUNTER = "throttled_usec"
SLICE_SUFFIX = ".slice"


def parse_cpu_stat_local(text: str | None) -> int | None:
    """``throttled_usec`` from a cpu.stat.local body; None when absent or unparseable."""
    fields = parse_cpu_stat(text, (LOCAL_COUNTER,))
    return None if fields is None else fields[LOCAL_COUNTER]


def parse_cpu_stat(text: str | None, counters: tuple[str, ...] = COUNTERS) -> dict[str, int] | None:
    """Counters from a cpu.stat body; None unless every counter parses."""
    if not text:
        return None
    fields: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in counters:
            try:
                fields[parts[0]] = int(parts[1])
            except ValueError:
                return None
    if set(fields) != set(counters):
        return None
    return fields


def parse_cpu_max_period(text: str | None) -> int | None:
    """Period in microseconds from a cpu.max body (``quota period``); None when unparseable."""
    if not text:
        return None
    parts = text.split()
    if len(parts) != 2:
        return None
    try:
        period = int(parts[1])
    except ValueError:
        return None
    return period if period > 0 else None


def slice_sense_id(slice_name: str) -> str:
    """``mishe-plants.slice`` -> ``sense.cgroup.mishe-plants.throttle``."""
    stem = slice_name[: -len(SLICE_SUFFIX)] if slice_name.endswith(SLICE_SUFFIX) else slice_name
    return f"sense.cgroup.{stem}.throttle"


def read_cpu_stat_file(path: Path) -> str | None:
    """Body of a cpu.stat file, or None when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_baseline(path: Path) -> dict:
    """The persisted per-slice baseline; an empty dict when missing or malformed."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def baseline_text(state: dict) -> str:
    """Serialized baseline written to disk."""
    return json.dumps(state, sort_keys=True)
