"""Producers: the only polling. Each run observes one thing, writes its signal, exits.

``run`` adapts any exit-code check script: 0 is GREEN, a declared red code
(default 2, this repo's check contract) is RED, and anything else — a crash, the
checks' own "cannot tell" 3, a missing binary, a timeout — leaves the signal
untouched so it ages to UNKNOWN. The runner fails then, which is what routes it to a resolver; it never
turns a broken check into GREEN or into a quieter RED.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import signal_store, units
from .core.state import DispatchStore
from .engine_process import run_bounded
from .state_files import read_json
from .wake import EngineNotStarted, EngineTimeout

DETAIL_BYTES = 4000


def run(home, name: str, command: list, *, now: float, timeout: float, red_codes=(2,)) -> int:
    detail = Path(home) / "signals" / f"{name}.detail"
    detail.parent.mkdir(parents=True, exist_ok=True)
    scratch = detail.with_suffix(".detail.tmp")
    try:
        code = run_bounded(command, env=dict(os.environ), cwd=Path(home), timeout=timeout, stdout_to=scratch)
    except (EngineNotStarted, EngineTimeout):
        scratch.unlink(missing_ok=True)
        return 2
    os.replace(scratch, detail)
    if code != 0 and code not in red_codes:
        return 2            # a crash or "cannot tell" (1, 3, 127, a signal) is not an observation
    signal_store.observe(home, name, "GREEN" if code == 0 else "RED", now=now, source=f"run:{command[0]}")
    return 0


def dispatch(home, *, now: float, max_open: float = 1800.0) -> str:
    """RED when any dispatch has stayed open longer than ``max_open`` seconds."""
    store = DispatchStore(Path(home) / "dispatch")
    for record in store.open_records():
        age = now - store.path_for(record.role).stat().st_mtime
        if age > max_open:
            return "RED"
    return "GREEN"


class ObservationFailed(RuntimeError):
    """The source could not tell; the signal is left to age to UNKNOWN."""


def ci(home, *, now: float, read=None) -> str | None:
    """GitHub Actions for the current remote commit: pass GREEN, fail RED, pending/unknown no reading."""
    if read is None:
        from .ci_watch import read
    state = read(home)["state"]
    if state == "pending":
        return None
    if state not in ("pass", "fail"):
        raise ObservationFailed(f"CI state {state!r}")
    return "GREEN" if state == "pass" else "RED"


BUILTIN = {"dispatch": dispatch, "ci": ci}


def produce(home, name: str, *, now: float, **kw) -> str | None:
    value = BUILTIN[name](home, now=now, **kw)
    if value is not None:
        signal_store.observe(home, name, value, now=now, source=f"builtin:{name}")
    return value


def load(home, *, python: str, declared=None) -> list:
    """Declared producers (``producers.json`` unless given); registers their signals."""
    if declared is None:
        declared = read_json(Path(home) / "producers.json") or []
    found = []
    base = [python, "-m", "mishe_tauftauf", "--home", str(home)]
    for item in declared:
        name = item["name"]
        spec = item.get("signal", {})
        signal_store.register(home, name, max_age=spec["max_age"], subscribers=spec.get("subscribers", []))
        timeout = item.get("timeout", 120)
        red = ",".join(str(c) for c in item.get("red_codes", [2])) or "none"
        if item.get("builtin"):
            command = base + ["produce", name]
        else:
            command = base + ["signal", "run", name, "--timeout", str(timeout), "--red-codes", red, "--"] + list(item["command"])
        found.append(units.Producer(name=name, command=command, every_seconds=item["every"], timeout=timeout + 30))
    return found
