#!/usr/bin/env python3
"""Detector: the root filesystem completes a create+fsync probe promptly.

The containment guard's own filesystem probe (/var/tmp) was moved to a second
drive on 2026-10-31 because the root drive's fsync stalls (up to 10 s, probe
A/B in operator/dual-probe.jsonl) tripped the whole plant eleven times in 17
hours. Moving the probe stopped the trips; it must not also stop anyone seeing
the stalls. SITE/operator/dual_probe.py keeps probing a root-filesystem
directory every second and mirrors its log to SITE/operator/dual-probe.jsonl.
This check reads that log.

Exit status:
  0  the probe is fresh and no root probe in the window reached STALL_S
  2  a root-filesystem probe took STALL_S or longer inside the window
  3  undecidable: the log is missing, stale, or holds no root probes
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

WINDOW_S = 900.0
STALE_S = 300.0
STALL_S = 5.0
TARGET = "root"


def evaluate(records: list[dict], now: float) -> tuple[int, list[str]]:
    seen = [r for r in records if r.get("dir") == TARGET and isinstance(r.get("at"), (int, float))]
    if not seen:
        return 3, ["no root-filesystem probe records"]
    newest = max(r["at"] for r in seen)
    if now - newest > STALE_S:
        return 3, [f"root probe log is stale: newest record {now - newest:.0f}s old (limit {STALE_S:.0f}s)"]
    stalls = [r for r in seen if r.get("event") == "slow" and now - r["at"] <= WINDOW_S
              and (r.get("total") or 0) >= STALL_S]
    if stalls:
        worst = max(stalls, key=lambda r: r["total"])
        return 2, [f"{len(stalls)} root-filesystem stall(s) >= {STALL_S:.0f}s in the last {WINDOW_S:.0f}s; "
                   f"worst {worst['total']:.1f}s (create {worst.get('create')}, fsync {worst.get('fsync')})"]
    return 0, [f"root probe fresh ({now - newest:.0f}s) with no stall >= {STALL_S:.0f}s in {WINDOW_S:.0f}s"]


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    args = parser.parse_args(argv)
    path = Path(args.home) / "operator" / "dual-probe.jsonl"
    try:
        records = load(path)
    except OSError as exc:
        print(f"UNKNOWN: cannot read {path}: {exc}")
        return 3
    code, lines = evaluate(records, time.time())
    print({0: "OK", 2: "FAIL", 3: "UNKNOWN"}[code] + ": " + "; ".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
