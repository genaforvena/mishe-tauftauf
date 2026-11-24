"""Producers: the only polling. Each run observes one thing, writes its signal, exits.

``run`` adapts any exit-code check script: 0 is GREEN, a declared red code
(default 2, this repo's check contract) is RED, and anything else — a crash, the
checks' own "cannot tell" 3, a missing binary, a timeout — leaves the signal
untouched so it ages to UNKNOWN. A declared RED carries the check's own output as the
signal's cause, so a subscriber woken by the edge — and the verdict line — reads why
without opening the detail. The runner fails then, which is what routes it to a resolver; it never
turns a broken check into GREEN or into a quieter RED.

A duty may declare ``unknown_codes``: exit codes the check uses to say "the input is stale or missing,
UNKNOWN" rather than "I broke". Those leave the signal untouched like any other unobserved run, but the
unit succeeds, so a routine stale sample does not mark the producer failed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from . import coverage_event, signal_store, units, wake_memory
from .core.state import DispatchStore
from .engine_process import run_bounded
from .state_files import detail_brief, read_json, write_json
from .wake import EngineNotStarted, EngineTimeout

DECLARED_REOFFER_SECONDS = 3600

DETAIL_BYTES = 4000
REASON_MAX = 300  # a RED signal carries the check's own cause, bounded like the detail view


def _utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _restore_memory_floor(home, name: str) -> dict:
    """Restore this producer's own memory floor and record what the unit ran under.

    A protected host drop-in shadows the generated unit's ``MemoryHigh`` with a
    lower value, and at that throttle the producer stalls instead of finishing
    (2026-11-13/14: 15 slow readings runs peaked at exactly 1.0G, 8 of them
    EngineTimeout, each blocked on I/O at cpu/elapsed 0.03). The floor is applied
    to the running unit from inside the producer, so the protected drop-in is not
    edited and no systemd reload is needed.
    """
    unit = wake_memory.running_unit_name()
    if unit is None:
        record = {"unit": None, "before": None, "after": None, "applied": False,
                  "reason": "not running in a named unit"}
    else:
        record = wake_memory.ensure_memory_high(unit, floor=units.PRODUCER_MEMORY_HIGH_BYTES)
        path = wake_memory.memory_high_path(unit)
        throttle = None
        if path is not None:
            try:
                throttle = (path / "memory.high").read_text(encoding="utf-8")
            except OSError:
                throttle = None
        record["memory_high"] = throttle
    write_json(Path(home) / "signals" / f"{name}.memory.json", record)
    return record


def _cause_key(text: str) -> str:
    """A reason with its numbers removed: the same cause at a later age or count compares equal.

    Each run of digits becomes one "#", so "age 9m" and "age 12m" compare equal.
    """
    out, in_number = [], False
    for ch in text:
        if ch.isdigit():
            if not in_number:
                out.append("#")
            in_number = True
        else:
            out.append(ch)
            in_number = False
    return "".join(out)


def run(home, name: str, command: list, *, now: float, timeout: float, red_codes=(2,), unknown_codes=()) -> int:
    detail = Path(home) / "signals" / f"{name}.detail"
    detail.parent.mkdir(parents=True, exist_ok=True)
    scratch = detail.with_suffix(".detail.tmp")
    started, clock = time.time(), time.monotonic()

    def ran(outcome: str) -> None:
        # One line per run, whatever the outcome, so an overrun or a kill can be attributed to its run.
        print(f"mishe: {name}: ran start={_utc(started)} end={_utc(time.time())} "
              f"elapsed={time.monotonic() - clock:.1f}s limit={timeout:g}s outcome={outcome}", file=sys.stderr)

    floor = _restore_memory_floor(home, name)
    guard = (wake_memory.MemoryFloorGuard(floor["unit"]).start()
             if floor.get("unit") else None)
    try:
        code = run_bounded(command, env=dict(os.environ), cwd=Path(home), timeout=timeout, stdout_to=scratch)
    except (EngineNotStarted, EngineTimeout) as exc:
        scratch.unlink(missing_ok=True)
        ran(type(exc).__name__)
        # Not-started (fork/exec failure) and timeout otherwise leave no trace in the journal.
        print(f"mishe: {name}: not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        signal_store.observe_unknown(home, name, now=now, source=f"run:{command[0]}",
                                     reason=f"not run: {type(exc).__name__}")
        return 2
    finally:
        if guard is not None:
            guard.stop()
            wake_memory.record_guard(Path(home) / "signals" / f"{name}.memory.json", guard)
    ran(f"exit={code}")
    # Cadence heartbeat: the producer completed a run. A declared unknown exit leaves the
    # reading untouched, so without this the freshness stamp ages and coverage reports a missed cadence
    # for a producer that ran on time and said its input is stale.
    (Path(home) / "signals" / f"{name}.ran").touch()
    os.replace(scratch, detail)
    if code == 0:
        # The detail file is overwritten by the next run, so a GREEN run that explains itself (a skip such as
        # "pin is HEAD: nothing to do") would leave no journal trace. One bounded line keeps that reason attributable.
        brief = detail_brief(detail, REASON_MAX)
        if brief:
            print(f"mishe: {name}: exit=0 {brief}", file=sys.stderr)
    if code != 0:
        # The detail file is overwritten by the next run, so the journal carries the check's output for every
        # non-GREEN exit — declared RED included — whose reason would otherwise survive only until that overwrite.
        output = detail.read_bytes()[:DETAIL_BYTES]
        # An empty capture is itself the evidence: the check exited without writing to stdout or stderr.
        shown = repr(output) if output else "(no output on stdout or stderr)"
        label = "unknown" if code in unknown_codes else "exit"
        print(f"mishe: {name}: {label}={code} detail={shown}", file=sys.stderr)
    if code in unknown_codes:
        # A declared "stale or missing input": not observed, so a fresh GREEN still ages to UNKNOWN as declared.
        # A signal already UNKNOWN keeps its stale reason ("expired after ...") unless the check says why now.
        current = signal_store.recorded(home, name)
        if current and current.get("state") == signal_store.UNKNOWN:
            reason = detail_brief(detail, REASON_MAX) or f"check exit {code}"
            # Numbers (ages, counters) change every run; only a change in the cause itself is an edge.
            if _cause_key(reason) != _cause_key(str(current.get("reason") or "")):
                signal_store.observe_unknown(home, name, now=now, source=f"run:{command[0]}", reason=reason)
        return 0
    if code != 0 and code not in red_codes:
        # A crash or "cannot tell" (1, 3, 127, a signal) is not an observation, and the last GREEN must not stand for it.
        signal_store.observe_unknown(home, name, now=now, source=f"run:{command[0]}",
                                     reason=f"check exit {code}")
        return 2
    # A declared RED carries the check's own cause. The detail is the only place the duty wrote it
    # and the next run overwrites it, so a subscriber woken by the edge must be able to read why
    # here; without it the verdict line records no cause for the very state that needs one.
    reason = detail_brief(detail, REASON_MAX) if code != 0 else ""
    # A check may declare its RED's identity (checks/signal-coverage.py: a set of stable causes, no ages).
    # A new identity under the same RED is an edge, so subscribers wake on a new cause, not on age.
    event = coverage_event.declared_event(name, detail) if code != 0 else None
    if event:
        reason = coverage_event.without_event(reason, event)
    signal_store.observe(home, name, "GREEN" if code == 0 else "RED", now=now,
                         source=f"run:{command[0]}", event=event, reason=reason)
    return 0


DISPATCH_OPEN_GRACE_S = 300.0
"""Slack past the engine hard cap before an open dispatch counts as stuck."""


def dispatch_open_bound(home) -> float:
    """Longest a dispatch may stay open before it counts as stuck: the engine's real runtime ceiling plus grace.

    The engine's ceiling is ``RoutedEngine.hard_cap``: the route hard cap only applies when it is
    above the soft deadline (units.WAKE_TIMEOUT); otherwise the wake is cut at the soft deadline.
    So the ceiling is the larger of the two. A record open past that ceiling means the engine bound
    itself failed, which is what this detector must still catch.
    """
    from .engine_router import DEFAULT_HARD_TIMEOUT_S, load_routes
    routes = load_routes(home) or {}
    hard = float(routes.get("hard_timeout_s", DEFAULT_HARD_TIMEOUT_S))
    return max(hard, float(units.WAKE_TIMEOUT)) + DISPATCH_OPEN_GRACE_S


def dispatch(home, *, now: float, max_open: float | None = None) -> str:
    """RED when any dispatch has stayed open longer than ``max_open`` seconds (default: the hard-cap bound)."""
    if max_open is None:
        max_open = dispatch_open_bound(home)
    store = DispatchStore(Path(home) / "dispatch")
    for record in store.open_records():
        age = now - store.path_for(record.role).stat().st_mtime
        if age > max_open:
            return "RED"
    return "GREEN"


class ObservationFailed(RuntimeError):
    """The source could not tell; the signal is left to age to UNKNOWN."""


def _ci_cause(reading: dict, *, superseded_by: dict | None = None) -> str:
    """Name the Actions run behind a ci reading, so a woken resolver need not re-query GitHub.

    The reader already carries the run's id, commit, url and detail; the signal used to store only
    "RED", so every wake spent calls on ``gh run list`` to learn which commit failed and why. The
    formatting (and the collapse of raw error text to one line) lives in the ci boundary.
    """
    from .ci_watch import cause_line
    return cause_line(reading, superseded_by=superseded_by)


def ci(home, *, now: float, read=None, completed=None) -> "str | tuple[str, str] | None":
    """GitHub Actions for the current remote commit: pass GREEN, fail RED, pending falls back to the newest finished run, unknown fails.

    A RED is returned with its cause (``(value, reason)``); GREEN carries none, like every other producer.
    """
    if read is None:
        from .ci_watch import read
    if completed is None:
        from .ci_watch import read_completed as completed
    reading = read(home)
    state = reading["state"]
    if state == "pending":
        # The newest commit has no finished run yet — a run takes ~11 min and pushes land every
        # ~2 min, so this is the normal reading. The monitor did read the world, so publishing the
        # newest *finished* result on the branch beats keeping the standing value fresh: an older
        # RED would otherwise outlive the green run that already superseded it.
        finished = completed(home)
        if finished["state"] == "pass":
            return "GREEN"
        if finished["state"] == "fail":
            return "RED", _ci_cause(finished, superseded_by=reading)
        # Nothing on the branch has finished (a young branch), or GitHub could not be read: keep the
        # standing result fresh instead of aging to a false UNKNOWN.
        signal_store.refresh(home, "ci", now=now)
        return None
    if state not in ("pass", "fail"):
        # The reader's detail (gh/git error text, or the missing/unfinished run) is the only cause on record.
        raise ObservationFailed(f"CI state {state!r}: {reading.get('detail', 'no detail')}")
    return "GREEN" if state == "pass" else ("RED", _ci_cause(reading))


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


def delivery_requests(home, *, now: float) -> str:
    """RED while a delivery admission outlives its owner or a request is UNKNOWN; GREEN otherwise.

    Without this producer the consumer's heartbeat, which runs only at consume time, lets the
    signal age to UNKNOWN between wakes and re-wakes its resolver on every expiry.
    """
    from . import delivery_requests as requests
    return "RED" if requests.standing_problem(home, now) else "GREEN"


def repo_change(home, *, now: float, **_):
    """System 0 repo-change producer: the site's checkout is its parent directory.

    The producer observes the signal itself (value = newest commit, detail = per-subscriber delta),
    so it returns None here; a failed read observes UNKNOWN with its cause, never GREEN.
    """
    from . import repo_change_signal
    try:
        repo_change_signal.produce(home, Path(home).parent, now=now)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        repo_change_signal.observe_unknown_all(home, str(exc), now=now)
    return None


def memory_samples(home, *, now: float, **_) -> str:
    """Append one row each for memory PSI avg10 and anon memory to the signal sample store.

    Also one row for charged memory (Cached + Slab) so the footprint reads anon beside charged.
    Gaps stay gaps: a reading that cannot be taken writes no row for it, and a missing store
    binary raises (the unit fails, the signal ages to UNKNOWN). Nothing is filled or carried.
    """
    from . import memory_reading, signal_samples
    ts_ms = int(now * 1000)
    rows = []
    psi, anon = memory_reading.read_memory()
    charged = memory_reading.read_charged()
    if psi is not None:
        rows.append(signal_samples.format_record(ts_ms, "memory.psi.avg10", "ok", f"{psi:.2f}", "memory-samples"))
    if anon is not None:
        rows.append(signal_samples.format_record(ts_ms, "memory.anon.kib", "ok", str(anon), "memory-samples"))
    if charged is not None:
        rows.append(signal_samples.format_record(ts_ms, "memory.charged.kib", "ok", str(charged), "memory-samples"))
    if not rows:
        # No reading at all is no sample: the unit fails and the signal reads UNKNOWN, never a GREEN with nothing taken.
        raise signal_samples.SignalSampleUnknown("no memory reading could be taken: /proc pressure and meminfo unreadable")
    written = signal_samples.append_samples(Path(home) / "samples" / "memory", rows)
    return f"samples={written}"


def footprint(home, *, now: float, **_) -> str:
    """Honest footprint: latest anon and charged memory and PSI avg10 beside each other.

    Reads the sample store over the last 15 minutes. Any of the three missing means no reading: it raises, the unit
    fails and the signal ages to UNKNOWN. Nothing is carried forward from an older sample.
    The value carries no sample age: an age in the text changes every second and rewrites the signal on every run
    (and path-triggers health). Freshness is the signal's observed_at, and a new sample changes the value.
    """
    from . import signal_samples
    since = int((now - 900) * 1000)
    result = signal_samples.read_samples(Path(home) / "samples" / "memory", since_ms=since)
    latest = result.latest_ok(("memory.anon.kib", "memory.charged.kib", "memory.psi.avg10"))
    missing = [n for n in ("memory.anon.kib", "memory.charged.kib", "memory.psi.avg10") if n not in latest]
    if missing:
        raise signal_samples.SignalSampleUnknown(f"no recent sample for {', '.join(missing)}")
    anon_gib = int(latest["memory.anon.kib"][1]) / (1024 * 1024)
    charged_gib = int(latest["memory.charged.kib"][1]) / (1024 * 1024)
    psi = latest["memory.psi.avg10"][1]
    return f"anon={anon_gib:.2f}GiB charged={charged_gib:.2f}GiB psi_avg10={psi}"


def disk_budget(home, *, now: float, **_):
    """GREEN while the disks Mishe needs have room and what it owns is within its limits.

    Also reclaims unreferenced release worktrees when their tree is over its ceiling. An unreadable
    disk or size raises, so the signal ages to UNKNOWN, never GREEN.
    """
    from . import disk_budget as budget
    return budget.run(home, now=now)


BUILTIN = {"disk-budget": disk_budget, "dispatch": dispatch, "ci": ci, "tape-lock": tape_lock, "delivery-requests": delivery_requests,
           "memory-samples": memory_samples, "footprint": footprint, "repo-change": repo_change}


def produce(home, name: str, *, now: float, **kw) -> str | None:
    """Run one builtin and store its reading; return the value.

    A builtin may return ``(value, reason)`` so that a not-OK reading carries its cause: the signal
    stores it, the dossier prints it, and a resolver woken by the edge reads why without re-deriving it.

    Builtins run in this process under the same protected host drop-in as declared producers, so
    restore the floor and keep it applied while the builtin works, exactly as :func:`run` does for a
    bounded command. Without it a builtin that outlives the detector's persist window sits at the
    drop-in's 1G and trips `wake-memory-floor` (2026-11-18, delivery-requests).
    """
    floor = _restore_memory_floor(home, name)
    guard = (wake_memory.MemoryFloorGuard(floor["unit"]).start()
             if floor.get("unit") else None)
    try:
        result = BUILTIN[name](home, now=now, **kw)
    finally:
        if guard is not None:
            guard.stop()
            wake_memory.record_guard(Path(home) / "signals" / f"{name}.memory.json", guard)
    value, reason = result if isinstance(result, tuple) else (result, "")
    if value is not None:
        signal_store.observe(home, name, value, now=now, source=f"builtin:{name}", reason=reason)
    return value


def load(home, *, python: str, declared=None, register: bool = True) -> list:
    """Declared producers (``producers.json`` unless given); registers their signals.

    ``register=False`` builds the same list without touching the site's registry: an observer
    (``doctor``) must not add watches for a manifest it did not install, or its own next reading
    finds drift it created (2026-11-17: a registry entry for a duty the release did not carry put a
    ``PathChanged`` line into the expected unit while the installed unit had none).
    """
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
        if register:
            signal_store.register(home, name, max_age=spec["max_age"], subscribers=spec.get("subscribers", []),
                                  reoffer=spec.get("reoffer", DECLARED_REOFFER_SECONDS),
                                  wake_on=spec.get("wake_on", "change"))
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
