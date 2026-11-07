#!/usr/bin/env python3
"""Daylight curve per room from household brightness signatures (not presence).

Input : signatures.jsonl rows with room, status, captured_at_epoch_s, mean_luma.
Output: daylight.json (per room, per UTC hour: samples, median luma, lamp steps)
        and a text panel with the same counts on stdout.

Lamp steps are separated from the sun curve: a sample is a lamp step when the
median luma of the 180 s after it differs from the median of the 300-780 s before
it by at least STEP_LUMA. Samples within LAMP_GUARD_S of a lamp step are dropped
from the daylight series, so a switched light cannot be read as the sun. Blind
rows (status != ok) are never counted. An hour with no usable sample is reported
with samples 0 and median null, never a guessed value.

Limit: only the onset of a lamp is removed. A lamp that stays on for hours keeps
its level in the hourly median, so read an hour's median as sun only when its
lamp_steps is 0 and the room has no lamp event in the prior 13 minutes.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

STEP_LUMA = 20.0
LAMP_GUARD_S = 300.0
AFTER_S = 180.0
BEFORE_LO_S = 300.0
BEFORE_HI_S = 780.0


def load_rows(path: Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("status") == "ok" and isinstance(row.get("captured_at_epoch_s"), (int, float)) \
                and isinstance(row.get("mean_luma"), (int, float)):
            rows.append(row)
    return rows


def lamp_steps(samples: list[tuple[float, float]]) -> list[float]:
    """Times of lamp steps in one room's sorted (t, luma) samples."""
    steps = []
    for t, _ in samples:
        after = [l for u, l in samples if t <= u < t + AFTER_S]
        before = [l for u, l in samples if t - BEFORE_HI_S <= u < t - BEFORE_LO_S]
        if len(after) < 2 or len(before) < 2:
            continue
        if abs(statistics.median(after) - statistics.median(before)) >= STEP_LUMA:
            # The same step stays visible until the old level leaves the "before"
            # window, so one lamp is one event: suppress for BEFORE_HI_S.
            if not steps or t - steps[-1] >= BEFORE_HI_S:
                steps.append(t)
    return steps


def daylight_by_hour(samples: list[tuple[float, float]], steps: list[float]) -> dict:
    bins: dict[int, list[float]] = defaultdict(list)
    lamp_counts: dict[int, int] = defaultdict(int)
    for s in steps:
        lamp_counts[time.gmtime(s).tm_hour] += 1
    for t, luma in samples:
        if any(abs(t - s) < LAMP_GUARD_S for s in steps):
            continue
        bins[time.gmtime(t).tm_hour].append(luma)
    out = {}
    for hour in range(24):
        vals = bins.get(hour, [])
        out[f"{hour:02d}"] = {
            "samples": len(vals),
            "median_luma": round(statistics.median(vals), 1) if vals else None,
            "lamp_steps": lamp_counts.get(hour, 0),
        }
    return out


def build(rows: list[dict]) -> dict:
    per_room: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "ok":
            continue
        per_room[str(row.get("room", "?"))].append((float(row["captured_at_epoch_s"]), float(row["mean_luma"])))
    report = {"version": 1, "rooms": {}}
    for room in sorted(per_room):
        samples = sorted(per_room[room])
        steps = lamp_steps(samples)
        report["rooms"][room] = {"total_samples": len(samples), "lamp_steps": len(steps),
                                 "hours": daylight_by_hour(samples, steps)}
    return report


def panel(report: dict) -> str:
    lines = ["DAYLIGHT CURVE (UTC hour: samples / median luma / lamp steps)"]
    for room, data in report["rooms"].items():
        lines.append(f"  {room}: {data['total_samples']} ok samples, {data['lamp_steps']} lamp steps")
        for hour, cell in data["hours"].items():
            if cell["samples"] or cell["lamp_steps"]:
                med = "UNKNOWN" if cell["median_luma"] is None else f"{cell['median_luma']:.1f}"
                lines.append(f"    {hour}:00  {cell['samples']:4d} / {med:>7} / {cell['lamp_steps']}")
    if len(lines) == 1:
        lines.append("  no ok rows: everything UNKNOWN")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="daylight.json path (default SITE/spatial/work/household/daylight.json)")
    args = parser.parse_args(argv)
    if not args.site.is_dir():
        raise SystemExit(f"site does not exist: {args.site}")
    report = build(load_rows(args.site / "spatial/work/household/signatures.jsonl"))
    if not report["rooms"]:
        # No usable rows is UNKNOWN for the duty, never a GREEN empty curve.
        print(panel(report), file=sys.stderr)
        return 1
    out = args.out or args.site / "spatial/work/household/daylight.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(panel(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
