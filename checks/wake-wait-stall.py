#!/usr/bin/env python3
"""Detector: a hard-cap wake cut while a subagent wait still held a running job.

``wake-waste`` already reports a lost wake and its cause class, but it never opens the retained
stream, so a hard cap caused by a subagent that never answered stays UNKNOWN until someone reads
the stream by hand.  This reads the routing ledger for hard-cap outcomes in the last two hours and,
for each one, a bounded tail of that wake's retained stream.  It flags the case where the last
``wait`` tool call still showed a job with status ``running``, naming the job and model so the
resolver can route a recurrence to the wait path instead of the generic work-lost signal.

The tail bound (4 MB) is deliberate: the Senses 2026-11-19 wake held the relevant wait updates in
its last 2 MB, and a producer must not have to page in a 44 MB stream to answer.  This detector only
observes; it does not change any engine timeout or wait cap.

Exit: 0 GREEN, 2 RED (a hard-cap wake in the window was cut with a still-running subagent wait),
3 UNKNOWN (the routing ledger cannot be read).
"""
import argparse
import calendar
import json
import sys
import time
from pathlib import Path

WINDOW_S = 2 * 3600
TAIL_BYTES = 4_000_000


def decode(line):
    try:
        ev = json.loads(line)
        at = calendar.timegm(time.strptime(ev["at"], "%Y-%m-%dT%H:%M:%SZ"))
        if ev.get("event") != "outcome" or "hard cap" not in str(ev.get("reason", "")):
            return None
        return {"event": "outcome", "reason": str(ev["reason"]), "role": ev.get("role", "?"),
                "wake": ev.get("wake", ""), "at": at}
    except (ValueError, KeyError, TypeError):
        return None


def _tail_bytes(path):
    """The last TAIL_BYTES of a stream, without reading the whole file."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                raw = fh.read()
                first_newline = raw.find(b"\n")
                return raw[first_newline + 1:] if first_newline >= 0 else b""
            return fh.read()
    except OSError:
        return b""


def last_wait_job(path):
    """The job still running in the last wait of a stream's tail, or None.

    A wait that ended normally resolved its jobs; only an aborted wait or one with no end event
    can be the thing the hard cap cut.  Concurrent waits are disambiguated by tool call id and the
    call with the most recent event wins, so a completed earlier wait cannot mask the last one.
    """
    calls = {}
    seq = 0
    for line in _tail_bytes(path).decode("utf-8", "replace").splitlines():
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict) or ev.get("type") not in ("tool_execution_update", "tool_execution_end"):
            continue
        if ev.get("toolName") != "wait":
            continue
        call = calls.setdefault(str(ev.get("toolCallId", "")), {"jobs": [], "end": None, "seq": 0})
        seq += 1
        call["seq"] = seq
        if ev["type"] == "tool_execution_update":
            details = (ev.get("partialResult") or {}).get("details") or {}
            jobs = details.get("jobs")
            if isinstance(jobs, list):
                call["jobs"] = jobs
        else:
            content = (ev.get("result") or {}).get("content") or []
            text = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
            call["end"] = (bool(ev.get("isError")), text.lower())
    if not calls:
        return None
    call = max(calls.values(), key=lambda c: c["seq"])
    if call["end"] and not call["end"][0] and "abort" not in call["end"][1]:
        return None
    for job in call["jobs"]:
        if isinstance(job, dict) and job.get("status") == "running":
            return {"id": str(job.get("id", "?")),
                    "label": str(job.get("label") or job.get("id", "?")),
                    "type": str(job.get("type", "?")),
                    "model": str(job.get("resolvedModel") or "?"),
                    "duration_ms": job.get("durationMs")}
    return None


def evaluate(events, home, now):
    """Return (exit_code, message).  events are decoded hard-cap ledger events."""
    hits = []
    for ev in events:
        if not ev or not ev["wake"] or not 0 <= now - ev["at"] <= WINDOW_S:
            continue
        path = Path(home) / "wakes" / f"{ev['wake']}.prompt.out.jsonl"
        job = last_wait_job(path)
        if job:
            hits.append((ev, job))
    if not hits:
        return 0, "wake-wait-stall: GREEN no hard-cap wake in the last 2 h was cut with a still-running subagent wait"
    parts = []
    for ev, job in hits:
        duration = f"{job['duration_ms'] / 1000:.0f}s" if isinstance(job["duration_ms"], (int, float)) else "?"
        parts.append(f"{ev['role']} {ev['wake']}: wait on {job['label']} ({job['id']}, {job['type']}, "
                     f"{job['model']}) still running after {duration} when `{ev['reason']}`")
    return 2, (f"wake-wait-stall: {len(hits)} hard-cap wake(s) held by a still-running subagent wait -> "
               + "; ".join(parts))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    a = ap.parse_args(argv)
    ledger = a.home / "engine" / "routing-ledger.jsonl"
    if not ledger.is_file():
        print("wake-wait-stall: no routing ledger; cannot tell", file=sys.stderr)
        return 3
    try:
        events = [decode(line) for line in ledger.read_text().splitlines()[-4000:]]
    except OSError as exc:
        print(f"wake-wait-stall: ledger unreadable: {exc}", file=sys.stderr)
        return 3
    code, message = evaluate(events, a.home, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
