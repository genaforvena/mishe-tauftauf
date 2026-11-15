#!/usr/bin/env python3
"""Detector: the RouterAI LLM budget is spent at the target rate (operator DM st-rai3).

Reads engine/routerai-balance.jsonl (one row per successful balance read, written by
budget_producer.append_routerai_balance). Spend is the sum of balance drops between consecutive
reads inside the window (top-ups are not spend). Burn is that spend divided by the measured span.
Target is limit / window_h per hour (200 / 24 = 8.33 per hour by default).

Checks:
  - GREEN when burn is within +/- band of the target (default 25 %).
  - RED when burn is outside the band in either direction (overspend or underspend).

Exit status:
  0  GREEN: burn tracks the target
  2  RED: burn is outside the band
  3  UNKNOWN: no history file, fewer than --min-span-h of samples, or the newest sample is older
     than --max-age (a gap is not zero spend; it proves nothing either way)

Usage: routerai-burn.py --home SITE_HOME [--limit 200] [--window-h 24] [--band 0.25]
                        [--min-span-h 1.0] [--max-age 900] [--now EPOCH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HISTORY = "engine/routerai-balance.jsonl"


def read_rows(path: Path) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
            epoch, balance = float(row["epoch"]), float(row["balance"])
        except (ValueError, KeyError, TypeError):
            continue
        rows.append((epoch, balance))
    return sorted(rows)


def judge(rows, now, limit, window_h, band, min_span_h, max_age) -> tuple[int, dict]:
    window_s = window_h * 3600.0
    inside = [(e, b) for e, b in rows if now - window_s <= e <= now]
    target = limit / window_h
    report = {"target_per_h": round(target, 3), "samples": len(inside), "window_h": window_h}
    if not inside:
        return 3, dict(report, verdict="UNKNOWN", reason="no balance samples in window")
    newest = inside[-1][0]
    if now - newest > max_age:
        return 3, dict(report, verdict="UNKNOWN", reason=f"newest sample {int(now - newest)} s old")
    span_h = (inside[-1][0] - inside[0][0]) / 3600.0
    report["span_h"] = round(span_h, 3)
    if span_h < min_span_h:
        return 3, dict(report, verdict="UNKNOWN", reason=f"span {span_h:.2f} h < {min_span_h} h")
    spend = sum(max(0.0, prev[1] - cur[1]) for prev, cur in zip(inside, inside[1:]))
    burn = spend / span_h
    ratio = burn / target
    report.update(spend=round(spend, 3), burn_per_h=round(burn, 3), ratio=round(ratio, 3))
    if 1 - band <= ratio <= 1 + band:
        return 0, dict(report, verdict="GREEN")
    direction = "overspend" if ratio > 1 else "underspend"
    return 2, dict(report, verdict="RED", reason=f"{direction}: burn {burn:.2f}/h vs target {target:.2f}/h")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--home", required=True, type=Path)
    p.add_argument("--limit", type=float, default=200.0)
    p.add_argument("--window-h", type=float, default=24.0)
    p.add_argument("--band", type=float, default=0.25)
    p.add_argument("--min-span-h", type=float, default=1.0)
    p.add_argument("--max-age", type=float, default=900.0)
    p.add_argument("--now", type=float, default=None)
    p.add_argument("--as-initiative", action="store_true",
                   help="acceptance exit codes for checks/initiatives.py: 0 done, 1 open (was RED), 3 unknown")
    args = p.parse_args(argv)
    now = args.now if args.now is not None else time.time()
    rows = read_rows(args.home / HISTORY)
    code, report = judge(rows, now, args.limit, args.window_h, args.band, args.min_span_h, args.max_age)
    print(json.dumps(report, sort_keys=True))
    if args.as_initiative and code == 2:
        # RED is "not done and past due" for an initiative: the initiatives detector reads only 0/1/else.
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
