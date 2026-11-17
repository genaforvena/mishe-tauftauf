#!/usr/bin/env python3
"""Detector: delivered releases whose asynchronous review has not merged.

A review is asynchronous: the promotion delivers, then a reviewer unit runs and
its answer merges into the record. When the reviewer is unavailable the record
keeps ``review_pending`` true and a retry ladder re-runs it
(``schedule_review_retry_if_needed``, base 30 min doubling, eight attempts). That
ladder makes an outage survivable, but nothing published the *outage*: on
2026-11-17 thirty delivered releases sat unreviewed on a spent weekly quota and
every signal on the dashboard read GREEN, so the only way to see it was to read
``work/releases/*.json`` by hand.

This reads those records (no writes) and reports the queue:

  GREEN    every delivered record's review merged
  RED      delivered records owe a review. While the retry ladder is carrying
           them the detail names the count, the next rung and the newest failure
           reason (the quota message, verbatim); a record that cannot recover on
           its own — ladder exhausted, no rung and none due after ``--grace``, or
           a rung that far overdue and never fired — is named with its state, so
           an operator or a fix must re-arm it
  UNKNOWN  there is no ``work/releases`` directory, or it holds no delivered
           record at all (nothing to judge is not GREEN)

Exit status: 0 GREEN, 2 RED, 3 UNKNOWN.

There is no intermediate exit code. The producer contract (``producers.run``)
treats a non-zero code that is neither declared red nor declared unknown as an
unreadable observation: the signal becomes UNKNOWN with the reason "check exit
N" — losing this check's own words — and the producer unit exits 2, which the
failure hook reports as a broken producer. So a backlog still inside the
ladder's care is RED, not a fourth code: the review is owed evidence, the ladder
is a retry rather than a resolution, and the detail says when the next attempt
fires. Measured 2026-11-17 22:29Z in a scratch home: exit 1 published
``state: UNKNOWN, value: null, reason: "check exit 1"`` for the 31-record queue.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RECORDS = Path("work/releases")
GRACE_S = 1800.0  # a rung this far past due that never advanced is lost
SUMMARY_S = 400


def _records(site: Path) -> list[dict]:
    found = []
    for path in sorted((site / RECORDS).glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            found.append(record)
    return found


def classify(record: dict, now: float, grace: float = GRACE_S) -> str:
    """One of merged, rung, overdue, stranded or exhausted for a delivered record."""
    if record.get("review_pending") is not True:
        return "merged"
    if record.get("review_retry_state") == "exhausted":
        return "exhausted"
    due = record.get("review_retry_due")
    if isinstance(due, (int, float)):
        return "overdue" if now >= due + grace else "rung"
    # No rung recorded. A review may still be in flight right after delivery, so
    # only call it stranded once that window has passed.
    delivered = record.get("delivered_at") or record.get("created_at") or 0
    return "stranded" if isinstance(delivered, (int, float)) and now - delivered >= grace else "rung"


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _reason(record: dict) -> str:
    """The failure sentence, not the traceback that precedes it."""
    text = " ".join(str(record.get("review_error") or "no reason recorded").split())
    if "RuntimeError:" in text:
        text = text.split("RuntimeError:")[-1].strip()
    # The message was built from a repr, so its quotes and newlines arrive escaped.
    for escaped, plain in (("\\n", " "), ("\\'", "'"), ('\\"', '"')):
        text = text.replace(escaped, plain)
    return text[-SUMMARY_S:]


def evaluate(site: Path, now: float, grace: float = GRACE_S) -> tuple[int, list[str]]:
    if not (site / RECORDS).is_dir():
        return 3, [f"no {RECORDS} directory under {site}; review state cannot be read"]
    delivered = [r for r in _records(site) if r.get("phase") == "delivered"]
    if not delivered:
        return 3, ["no delivered release record; nothing to judge"]

    pending, broken = [], []
    for record in delivered:
        state = classify(record, now, grace)
        if state == "merged":
            continue
        pending.append(record)
        if state in ("overdue", "stranded", "exhausted"):
            broken.append((state, record))

    lines = [f"{len(pending)} of {len(delivered)} delivered release(s) owe an unmerged review"]
    newest = max(pending, key=lambda r: r.get("delivered_at") or r.get("created_at") or 0) if pending else None
    if newest is not None:
        lines.append(f"newest unreviewed release {str(newest.get('sha'))[:12]}: {_reason(newest)}")
    dues = [r["review_retry_due"] for r in pending if isinstance(r.get("review_retry_due"), (int, float))]
    dues = [d for d in dues if d > now]
    if dues:
        lines.append(f"next rung {_when(min(dues))}, {len(dues)} rung(s) armed")
    if broken:
        shas = ", ".join(f"{str(r.get('sha'))[:12]} ({state})" for state, r in broken[:8])
        lines.append(f"cannot recover without a fix or an operator: {shas}")
    return (2 if pending else 0), lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", "--home", dest="site", type=Path,
                        default=Path(__file__).resolve().parent.parent / ".mishe-tauftauf",
                        help="site home (--home is the name the scheduler coverage check passes)")
    parser.add_argument("--grace", type=float, default=GRACE_S,
                        help="seconds a rung may be past due, or a review absent, before it is lost")
    args = parser.parse_args(argv)
    code, lines = evaluate(args.site, time.time(), args.grace)
    state = {0: "GREEN", 2: "RED", 3: "UNKNOWN"}[code]
    print(f"review-backlog {state}")
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
