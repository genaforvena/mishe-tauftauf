#!/usr/bin/env python3
"""Score the household hypothesis registry against the data we have.

Reads SITE/spatial/work/household/hypotheses.jsonl and the living-room
signature frames, writes SITE/spatial/work/household/patterns.json and appends
one history line to pattern_scores.jsonl per run.

Honesty: a day scored here was already in the data when the hypothesis was
written, so the numbers are retrospective. Status stays NEW until a day is
predicted before it is read (holdout, not yet implemented).
"""
import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

WORK = Path("spatial/work/household")
MIN_HOURS_PER_DAY = 6
MIN_PAIRS_PER_HOUR = 3
MAX_GAP_S = 300


def site_home(arg):
    if arg:
        return Path(arg)
    env = os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME")
    if not env:
        sys.exit("no site: pass --site or set MISHE_TAUFTAUF_HOME")
    return Path(env)


def load_living_frames(path):
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") != "ok" or row.get("room") != "living room":
            continue
        if "captured_at_epoch_s" not in row or not row.get("grid"):
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["captured_at_epoch_s"])
    return rows


def frame_changes(rows):
    out = []
    for a, b in zip(rows, rows[1:]):
        gap = b["captured_at_epoch_s"] - a["captured_at_epoch_s"]
        if gap <= 0 or gap > MAX_GAP_S:
            continue
        ga, gb = a["grid"], b["grid"]
        if len(ga) != len(gb):
            continue
        change = sum(abs(x - y) for x, y in zip(ga, gb)) / len(ga) / 255.0
        out.append((b["captured_at_epoch_s"], change))
    return out


def daily_hourly(changes):
    bins = defaultdict(list)
    for t, change in changes:
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        bins[(d.date().isoformat(), d.hour)].append(change)
    per_day = defaultdict(dict)
    for (day, hour), values in bins.items():
        if len(values) >= MIN_PAIRS_PER_HOUR:
            per_day[day][hour] = statistics.median(values)
    return per_day


def wilson(hits, n, z=1.96):
    if n == 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(centre - half, 3), round(centre + half, 3)]


def score_h2(per_day):
    rows = []
    for day in sorted(per_day):
        hours = per_day[day]
        if len(hours) < MIN_HOURS_PER_DAY:
            rows.append({"day": day, "scored": False, "reason": f"thin day ({len(hours)} hours)"})
            continue
        peak = max(hours, key=lambda h: hours[h])
        rows.append({"day": day, "scored": True, "peak_hour_utc": peak,
                     "hit": 20 <= peak < 24})
    scored = [r for r in rows if r["scored"]]
    hits = sum(1 for r in scored if r["hit"])
    return rows, len(scored), hits


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", help="site home (default: MISHE_TAUFTAUF_HOME or MISHE_SEED_HOME)")
    args = ap.parse_args()
    home = site_home(args.site)
    work = home / WORK
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    registry = [json.loads(l) for l in (work / "hypotheses.jsonl").read_text().splitlines() if l.strip()]
    frames = load_living_frames(work / "signatures.jsonl")
    per_day = daily_hourly(frame_changes(frames))
    day_rows, n, hits = score_h2(per_day)

    for hyp in registry:
        if hyp["id"] == "H2-living-evening-peak":
            hyp["n"] = n
            hyp["hits"] = hits
            hyp["hit_rate"] = round(hits / n, 3) if n else None
            hyp["interval95"] = wilson(hits, n)
            hyp["status"] = "UNKNOWN" if n == 0 else "NEW"
            hyp["last_tested_utc"] = now
            hyp["days"] = day_rows
            hyp["retrospective"] = True

    out = {"generated_utc": now, "frames_living_room": len(frames),
           "hypotheses": registry}
    (work / "patterns.json").write_text(json.dumps(out, indent=1) + "\n")
    with (work / "pattern_scores.jsonl").open("a") as fh:
        fh.write(json.dumps({"utc": now, "H2": {"n": n, "hits": hits}}) + "\n")

    for hyp in registry:
        rate = hyp.get("hit_rate")
        shown = "-" if rate is None else f"{rate:.2f} {hyp.get('interval95')}"
        print(f"{hyp['id']:<28} n={hyp['n']:<3} rate={shown:<22} {hyp['status']}")


if __name__ == "__main__":
    main()
