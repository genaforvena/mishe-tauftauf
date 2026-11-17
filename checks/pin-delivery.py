#!/usr/bin/env python3
"""Detector: the outcome of the newest pin promotion (``mishe release advance``), as a signal.

Reads the newest record under SITE/work/releases (written by release_promotion.promote) and the live pointer.

  GREEN    delivered, and runtime/current names that release; or a promotion still running and younger than --stuck-min
  RED      the newest promotion ended in check-failed, check-mutated-*, rolled-back or UNKNOWN: names the sha, the
           phase, the failure and the last failing line of the check, so the resolver fixes a cause, not a symptom
  UNKNOWN  no promotion record, an UNREADABLE record (a corrupt or torn record is not skipped in silence:
           the newest promotion is then unknown, not the newest record that happened to parse), a pointer that
           disagrees with a delivered record, or a promotion that has run longer than --stuck-min

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
    """The newest promotion record, or the reason there is none.

    A record that cannot be read is NOT skipped.  It was, until the review
    of the 2026-11-14 release refused the change on 2026-11-15 with exactly
    this defect: a corrupt or torn record was passed over in silence, and an
    OLDER record with phase "delivered" whose sha matched runtime/current
    then answered GREEN.  The check reported a healthy pin while the record
    of the promotion that actually happened was unreadable.

    A record that cannot be read is a promotion whose outcome nobody can
    rule out, so it is UNKNOWN.  Which record is "newest" cannot be known
    from a file that does not parse, so the newest UNREADABLE record is
    reported and the check refuses to pass on the ones it can read.

    Returns ``(record, unreadable)``: the newest readable record and the
    newest unreadable one, either of which may be None.
    """
    best = None
    unreadable = None
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            unreadable = (path, error)
            continue
        if isinstance(record, dict) and isinstance(record.get("created_at"), (int, float)):
            if best is None or record["created_at"] > best["created_at"]:
                best = record
    return best, unreadable


def pointer_sha(home):
    link = home / "runtime" / "current"
    return link.resolve().name if link.is_symlink() else None


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
    if phase == "delivered":
        if pointer_sha(a.home) != record.get("sha"):
            print(f"pin-delivery: {sha} is recorded delivered but runtime/current names {str(pointer_sha(a.home))[:8]}", file=sys.stderr)
            return 3
        print(f"pin-delivery: GREEN {sha} delivered {age_min:.0f} min ago ({len(record.get('paths', []))} paths)")
        keep_outcome_current(a.home, record, now)
        return 0
    if phase in RUNNING:
        if age_min > a.stuck_min:
            print(f"pin-delivery: promotion of {sha} has been {phase} for {age_min:.0f} min", file=sys.stderr)
            return 3
        print(f"pin-delivery: GREEN promotion of {sha} is {phase} ({age_min:.0f} min)")
        return 0
    if phase in FAILED:
        print(f"pin-delivery: {sha} {phase} {age_min:.0f} min ago: {record.get('failure') or last_failing_line(record) or 'no reason recorded'}",
              file=sys.stderr)
        return 2
    print(f"pin-delivery: {sha} is in phase {phase!r}; cannot tell", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
