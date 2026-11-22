#!/usr/bin/env python3
"""Detector: the outcome of the newest pin promotion (``mishe release advance``), as a signal.

Reads the newest record under SITE/work/releases (written by release_promotion.promote) and the live pointer.

  GREEN    delivered and runtime/current resolves to that release directory; or a promotion still
           running and younger than --stuck-min
  RED      the newest promotion ended in check-failed, check-mutated-*, rolled-back or UNKNOWN: names the sha, the
           phase, the failure and the last failing line of the check, so the resolver fixes a cause, not a symptom
  UNKNOWN  no promotion record, an unreadable or unorderable record, a pointer that is unusable
           or disagrees with a delivered record, or a promotion that has run longer than --stuck-min.
           A RUNNING phase is aged from its own start (``waiting_since``, ``activation_started_at``)
           when the record carries it, so a healthy wait for wakes that had to yield first is not UNKNOWN.

A GREEN reading is also this check's own observation of the ``moving-pin-delivery`` latch: that
signal is published only when a promotion runs, so a pin equal to HEAD leaves it ``VERIFIED`` and
unobserved until the generic expiry sweep turns it UNKNOWN after its 7200s ``max_age`` — a wake for
field-review with nothing to review (2026-11-17, a 2h-quiet window after ``fc834468``). Re-observing
the record's own outcome here (same value and event, so no edge and no wake; a swept-UNKNOWN reading
is restored) keeps the latch as fresh as this reading, while a stall still leaves the pin trailing
HEAD, which ``pin-lag`` reports.

Exit: 0 GREEN, 2 RED, 3 UNKNOWN. GREEN prints to stdout, the others to stderr.
"""
import argparse
import json
import sys
import time
from pathlib import Path

FAILED = {"check-failed", "check-mutated-source", "check-mutated-command", "delivery-busy", "rolled-back", "UNKNOWN"}
# delivery-busy is written only as the promote gives up (held_delivery_lock raised), so it is a dead
# run, not a live one: counting it as running reported GREEN for a delivery that never started.
RUNNING = {"checking", "waiting-for-wakes", "activating"}


def newest(directory):
    """Return the newest orderable promotion and evidence of any unreadable record.

    A file with no usable creation time could describe the latest promotion.
    The caller reports UNKNOWN even when an orderable record matches the pin.
    """
    best = None
    unreadable = None
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("expected a JSON object")
            created_at = record.get("created_at")
            if (type(created_at) not in (int, float)
                    or not -sys.float_info.max <= created_at <= sys.float_info.max):
                raise ValueError("created_at must be a finite numeric timestamp")
        except (OSError, ValueError) as error:
            unreadable = (path, error)
            continue
        if best is None or created_at > best["created_at"]:
            best = record
    return best, unreadable


def pointer_sha(home):
    link = home / "runtime" / "current"
    if not link.is_symlink():
        raise OSError("expected a symlink")
    target = link.resolve(strict=True)
    if not target.is_dir():
        raise NotADirectoryError(f"target is not a directory: {target}")
    return target.name


SRC = Path(__file__).resolve().parents[1] / "src"
"""The release's own ``src``: the promotion's observation runtime, as its unit pins PYTHONPATH."""


def keep_outcome_current(home, record, now):
    """Re-observe the newest promotion's delivered outcome as ``moving-pin-delivery``.

    The same value and event are not an edge, so a healthy latch only has its freshness stamp
    advanced and no subscriber is woken; a reading the expiry sweep turned UNKNOWN is restored
    to the outcome the record still shows. A latch the promotion never published is left to its
    never-observed grace: nothing here may stand in for the missing publisher. This check's own
    signal must not fail because the latch could not be re-observed, so the reason goes to stderr
    and the caller's reading stands.
    """
    try:
        if str(SRC) not in sys.path:
            sys.path.insert(0, str(SRC))
        from mishe_tauftauf import signal_store
        if signal_store.recorded(home, "moving-pin-delivery") is None:
            return
        signal_store.observe(home, "moving-pin-delivery", "VERIFIED", now=now, source="pin-delivery",
                             event=f"{record.get('sha', '')}:delivered")
    except (ImportError, OSError, ValueError) as exc:
        print(f"pin-delivery: could not record the moving-pin-delivery outcome "
              f"({type(exc).__name__}: {exc}); its reading stands and may age to UNKNOWN", file=sys.stderr)


def last_failing_line(record):
    out = (record.get("check") or {}).get("output")
    if not isinstance(out, str):
        return ""
    lines = [line for line in out.splitlines() if line.startswith("FAILED") or "Error" in line]
    return (lines[-1] if lines else out.splitlines()[-1] if out.strip() else "")[:240]

def delivery_breakdown(record):
    """Check/wait/activate minutes from a delivered record's own stamps, or "" for legacy records.

    Promotion time is the standing runtime question. The record already stamps each stage;
    showing them on the GREEN detail makes the breakdown readable from the signal every wake
    instead of a JSON dig (2026-11-20, 8592c07c: 8.6 min check, 14.5 min waiting for a running
    wake, 0.2 min activation).
    """
    parts = []
    for label, start, stop in (("check", record.get("created_at"), record.get("waiting_since")),
                               ("wait", record.get("waiting_since"), record.get("activation_started_at")),
                               ("activate", record.get("activation_started_at"), record.get("delivered_at"))):
        if isinstance(start, (int, float)) and isinstance(stop, (int, float)) and stop >= start:
            parts.append(f"{label} {(stop - start) / 60:.0f}m")
    return " (" + ", ".join(parts) + ")" if parts else ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--stuck-min", type=float, default=60.0)
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    now = time.time() if a.now is None else a.now
    record, unreadable = newest(a.home / "work" / "releases")
    if unreadable is not None:
        path, error = unreadable
        print(f"pin-delivery: the promotion record {path.name} could not be read "
              f"({type(error).__name__}: {error}); the newest promotion is "
              f"UNKNOWN, not the newest record that could be parsed", file=sys.stderr)
        return 3
    if record is None:
        print("pin-delivery: no promotion record yet; cannot tell", file=sys.stderr)
        return 3
    phase, sha = record.get("phase"), str(record.get("sha", ""))[:8]
    age_min = (now - record["created_at"]) / 60
    # A RUNNING phase is aged from its own start when the record stamps one: the check stage can
    # take 25 min and a healthy wait for the dispatch lock spans its quiet poll (120 s now,
    # 600 s in legacy records without quiet_poll_s) plus wait_s=2400s, so created_at reached
    # --stuck-min 60 while a live promotion was still waiting for wakes that had to yield first.
    since = record.get({"waiting-for-wakes": "waiting_since",
                        "activating": "activation_started_at"}.get(phase))
    phase_min = (now - since) / 60 if isinstance(since, (int, float)) else age_min
    if phase == "delivered":
        try:
            pinned = pointer_sha(a.home)
        except (OSError, RuntimeError) as exc:
            print(f"pin-delivery: {sha} is recorded delivered but runtime/current is unusable: {exc}",
                  file=sys.stderr)
            return 3
        if pinned != record.get("sha"):
            print(f"pin-delivery: {sha} is recorded delivered but runtime/current names {pinned[:8]}", file=sys.stderr)
            return 3
        # The pin moved when the record was marked delivered, not when its promotion
        # started: the promotion's own age read "delivered 32 min ago" for a pin that had
        # named the release for one minute (2026-11-18 05:52Z).
        done = record.get("delivered_at")
        live_min = (now - done) / 60 if isinstance(done, (int, float)) else age_min
        print(f"pin-delivery: GREEN {sha} delivered {live_min:.0f} min ago ({len(record.get('paths', []))} paths)"
              f"{delivery_breakdown(record)}")
        keep_outcome_current(a.home, record, now)
        return 0
    if phase in RUNNING:
        if phase_min > a.stuck_min:
            print(f"pin-delivery: promotion of {sha} has been {phase} for {phase_min:.0f} min", file=sys.stderr)
            return 3
        print(f"pin-delivery: GREEN promotion of {sha} is {phase} ({phase_min:.0f} min)")
        return 0
    if phase in FAILED:
        print(f"pin-delivery: {sha} {phase} {age_min:.0f} min ago: {record.get('failure') or last_failing_line(record) or 'no reason recorded'}",
              file=sys.stderr)
        return 2
    print(f"pin-delivery: {sha} is in phase {phase!r}; cannot tell", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
