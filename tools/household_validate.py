#!/usr/bin/env python3
"""Validate the living-room activity reading against human labels (labels.tsv).

Input : SITE/spatial/work/household/labels.tsv (human lines, tab-separated: UTC time then key=value),
        signatures.jsonl (camera frames) and operator_blind_intervals.json (stated blind spans).
Output: a JSON report on stdout: scored hits and misses, unscored labels with the reason, a threshold
        sweep and caveats. Only labels the explicit rules below decide are scored; anything else is
        UNKNOWN and never guessed. Exit 1 when no label can be scored.
The reading is occupied when the median living-room change (household_activity, 0-255 units) within
+-300 s of the label reaches the threshold. A lamp step inside that window is reported, not scored,
because the lamp itself changes pixels.
"""
from __future__ import annotations

import argparse
import calendar
import importlib.util
import json
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("household_activity", HERE / "household_activity.py")
ha = importlib.util.module_from_spec(_spec)
sys.modules["household_activity"] = ha
_spec.loader.exec_module(ha)

WINDOW_S = 300.0
SWEEP = (1.0, 1.5, ha.QUIET, 3.0, 4.0)
OCCUPIED_RE = re.compile(r"\b(?:ilya|rozalia)=living[_ ]room")
EMPTY_RE = re.compile(r"living_room=empty")
AWAY_RE = re.compile(r"\bilya=(?:outdoors|bedroom|balcony)")
ROZ_BED_RE = re.compile(r"\brozalia=bedroom")


def parse_label(line: str) -> tuple[str, str] | None:
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 2 or not parts[0].endswith("Z"):
        return None
    return parts[0], " ".join(parts[1:])


def truth(text: str) -> bool | None:
    """True occupied, False empty of humans, None when the rules do not decide."""
    occupied = bool(OCCUPIED_RE.search(text))
    empty = bool(EMPTY_RE.search(text)) or (bool(AWAY_RE.search(text)) and bool(ROZ_BED_RE.search(text)))
    if occupied and not empty:
        return True
    if empty and not occupied:
        return False
    return None


def epoch_of(stamp: str) -> float | None:
    """'2026-11-07T09:05:04Z' -> epoch seconds (UTC)."""
    try:
        return float(calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def blind_end(blind: list[dict], label_epoch: float) -> str | None:
    """A stated blind span ends at HH:MMZ on the label's own UTC day; a label before that end is blind."""
    for row in blind:
        if row.get("room") != "living room" or not row.get("to_utc"):
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(label_epoch))
        end_epoch = epoch_of(f"{day}T{row['to_utc'].rstrip('Z')}:00Z")
        if end_epoch is not None and label_epoch <= end_epoch:
            return row["to_utc"]
    return None


def window_values(recs: list[dict], t: float) -> list[float]:
    return [r["activity"] for r in recs if r["activity"] is not None and abs(r["t"] - t) <= WINDOW_S]


def score(labels: list[tuple[str, str]], recs: list[dict], blind: list[dict], threshold: float) -> dict:
    hits, misses, unscored = [], [], []
    lamp_times = [e["t"] for e in ha.luma_events(recs, [r["t"] for r in recs])] if recs else []
    for stamp, text in labels:
        t = epoch_of(stamp)
        if t is None:
            unscored.append({"time": stamp, "reason": "unparseable time"})
            continue
        want = truth(text)
        if want is None:
            unscored.append({"time": stamp, "reason": "rules do not decide the living-room state"})
            continue
        if (end := blind_end(blind, t)) is not None:
            unscored.append({"time": stamp, "reason": f"camera blind until {end} (stated)"})
            continue
        vals = window_values(recs, t)
        if not vals:
            unscored.append({"time": stamp, "reason": "no usable camera reading within 300 s: UNKNOWN"})
            continue
        got_value = statistics.median(vals)
        row = {"time": stamp, "truth": "occupied" if want else "empty", "activity": round(got_value, 2)}
        lamp = any(abs(e - t) <= WINDOW_S for e in lamp_times)
        if lamp:
            row["lamp_step_in_window"] = True
        predicted = got_value >= threshold
        (hits if predicted == want else misses).append(row)
    decided = len(hits) + len(misses)
    return {"threshold": threshold, "scored": decided, "hits": len(hits), "misses": misses,
            "hit_rows": hits, "accuracy": round(len(hits) / decided, 2) if decided else None,
            "unscored": unscored}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args(argv)
    base = args.site / ha.HOUSEHOLD
    try:
        labels = [p for p in (parse_label(x) for x in (base / "labels.tsv").read_text(encoding="utf-8")
                              .splitlines()) if p]
    except OSError:
        print("validate: labels.tsv missing: UNKNOWN", file=sys.stderr)
        return 1
    frames = ha.load_frames(ha.read_jsonl(base / "signatures.jsonl"))["living room"]
    recs = ha.frame_records(frames)[0] if frames else []
    blind = ha.operator_blind(base)
    report = {
        "method": "median living-room activity within +-300 s of each label; thresholds swept, "
                  "primary threshold is the activity tool's QUIET value",
        "days_covered": round((recs[-1]["t"] - recs[0]["t"]) / 86400, 2) if recs else 0,
        "primary": score(labels, recs, blind, ha.QUIET),
        "sweep": [{k: v for k, v in score(labels, recs, blind, th).items() if k != "unscored"} for th in SWEEP],
        "caveats": ["one day of household data", "labels are sparse; the living-room labels are few",
                    "validation is provisional until more days are scored"],
    }
    json.dump(report, sys.stdout, indent=1, ensure_ascii=False)
    print()
    return 0 if report["primary"]["scored"] else 1


if __name__ == "__main__":
    sys.exit(main())
