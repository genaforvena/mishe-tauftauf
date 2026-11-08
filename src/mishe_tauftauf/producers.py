"""Producers: the only polling. Each run observes one thing, writes its signal, exits.

``run`` adapts any exit-code check script: 0 is GREEN, a declared red code
(default 2, this repo's check contract) is RED, and anything else — a crash, the
checks' own "cannot tell" 3, a missing binary, a timeout — leaves the signal
untouched so it ages to UNKNOWN. The runner fails then, which is what routes it to a resolver; it never
turns a broken check into GREEN or into a quieter RED.

A duty may declare ``unknown_codes``: exit codes the check uses to say "the input is stale or missing,
UNKNOWN" rather than "I broke". Those leave the signal untouched like any other unobserved run, but the
unit succeeds, so a routine stale sample does not mark the producer failed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import signal_store, units
from .core.state import DispatchStore
from .engine_process import run_bounded
from .state_files import read_json
from .wake import EngineNotStarted, EngineTimeout

DECLARED_REOFFER_SECONDS = 3600

DETAIL_BYTES = 4000


def run(home, name: str, command: list, *, now: float, timeout: float, red_codes=(2,), unknown_codes=()) -> int:
    detail = Path(home) / "signals" / f"{name}.detail"
    detail.parent.mkdir(parents=True, exist_ok=True)
    scratch = detail.with_suffix(".detail.tmp")
    try:
        code = run_bounded(command, env=dict(os.environ), cwd=Path(home), timeout=timeout, stdout_to=scratch)
    except (EngineNotStarted, EngineTimeout) as exc:
        scratch.unlink(missing_ok=True)
        # Not-started (fork/exec failure) and timeout otherwise leave no trace in the journal.
        print(f"mishe: {name}: not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        signal_store.observe_unknown(home, name, now=now, source=f"run:{command[0]}",
                                     reason=f"not run: {type(exc).__name__}")
        return 2
    os.replace(scratch, detail)
    if code in unknown_codes:
        return 0            # a declared "stale or missing input": not observed, so the signal ages to UNKNOWN
    if code != 0 and code not in red_codes:
        # The detail file is overwritten by the next run, so the journal carries the check's output for this failure.
        output = detail.read_bytes()[:DETAIL_BYTES]
        # An empty capture is itself the evidence: the check exited without writing to stdout or stderr.
        shown = repr(output) if output else "(no output on stdout or stderr)"
        print(f"mishe: {name}: exit={code} detail={shown}", file=sys.stderr)
        # A crash or "cannot tell" (1, 3, 127, a signal) is not an observation, and the last GREEN must not stand for it.
        signal_store.observe_unknown(home, name, now=now, source=f"run:{command[0]}",
                                     reason=f"check exit {code}")
        return 2
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
    reading = read(home)
    state = reading["state"]
    if state == "pending":
        return None
    if state not in ("pass", "fail"):
        # The reader's detail (gh/git error text, or the missing/unfinished run) is the only cause on record.
        raise ObservationFailed(f"CI state {state!r}: {reading.get('detail', 'no detail')}")
    return "GREEN" if state == "pass" else "RED"


def tape_lock(home, *, now: float, attempts: int = 5, pause: float = 1.0) -> str:
    """RED only if the plant-wide tape lock stays unobtainable across every attempt.

    A holder is normal and brief; a lock that cannot be taken for the whole
    window is a stuck writer, which blocks every wall, task and tape write.
    """
    import fcntl
    import time
    path = Path(home) / ".seed.lock"
    for attempt in range(attempts):
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return "GREEN"
            except OSError:
                pass
        if attempt + 1 < attempts:
            time.sleep(pause)
    return "RED"


BUILTIN = {"dispatch": dispatch, "ci": ci, "tape-lock": tape_lock}


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
        # A declared signal that stays RED or UNKNOWN is re-offered to its subscriber at this interval; without
        # reoffer a standing fault woke its resolver once and then nothing returned it (yielding.py re-offers only
        # signals registered with reoffer).
        signal_store.register(home, name, max_age=spec["max_age"], subscribers=spec.get("subscribers", []),
                              reoffer=spec.get("reoffer", DECLARED_REOFFER_SECONDS))
        timeout = item.get("timeout", 120)
        red = ",".join(str(c) for c in item.get("red_codes", [2])) or "none"
        unknown = ",".join(str(c) for c in item.get("unknown_codes", [])) or "none"
        if item.get("builtin"):
            command = base + ["produce", name]
        else:
            command = base + ["signal", "run", name, "--timeout", str(timeout), "--red-codes", red,
                              "--unknown-codes", unknown, "--"] + list(item["command"])
        found.append(units.Producer(name=name, command=command, every_seconds=item["every"], timeout=timeout + 30))
    return found
