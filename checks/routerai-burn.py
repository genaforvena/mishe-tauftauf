#!/usr/bin/env python3
"""Report the shared RouterAI balance trend; the LLM ruble target is UNKNOWN.

Balance drops are observed credits shared by LLMs and decisions. Their ruble
conversion and category attribution are unverified, so no balance trend can
prove or disprove the LLM spending target. Top-ups between samples can hide
spending, and positive balance changes are not counted as drops.

Exit 3 (UNKNOWN), including when samples are missing, stale, or too sparse.
The shared balance-drop series remains visible when there is enough evidence.
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
    if limit <= 0 or window_h <= 0:
        # A zero or negative limit or window is not a measurement of the burn
        # rate: the target is limit per window, and dividing by either of them
        # proves nothing about the spend.  The contract reserves 3 for "proves
        # nothing either way", so an unusable argument reads UNKNOWN instead of
        # raising out of the contract.  (Independent review of release
        # 20c65d73: --window-h 0 and --limit 0 raised ZeroDivisionError and
        # exited 1, a code the check never documents.)
        report = {"target_per_h": None, "samples": len(rows), "window_h": window_h, "limit": limit}
        return 3, dict(report, verdict="UNKNOWN",
                       reason="limit %s and window_h %s must both be positive" % (limit, window_h))
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
    drops = sum(max(0.0, prev[1] - cur[1]) for prev, cur in zip(inside, inside[1:]))
    report.update(balance_drops=round(drops, 3), balance_drop_per_h=round(drops / span_h, 3),
                  balance_unit="RouterAI credits (conversion to rubles unverified)")
    return 3, dict(report, verdict="UNKNOWN",
                   reason="shared balance does not attribute LLM spend and conversion to rubles is unverified")


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
                   help="compatibility with checks/initiatives.py; unknown remains exit 3")
    args = p.parse_args(argv)
    now = args.now if args.now is not None else time.time()
    rows = read_rows(args.home / HISTORY)
    code, report = judge(rows, now, args.limit, args.window_h, args.band, args.min_span_h, args.max_age)
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
