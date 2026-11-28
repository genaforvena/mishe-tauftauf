#!/usr/bin/env python3
"""Detector: the root drive's sub-threshold band is rising (lead signal for root-fs-stall).

root-fs-stall goes RED only on a probe >= 5 s. Its quiet line has shown, since 2026-11-24,
that the rate of 1 s+ root probes climbs for a window or more before a 5 s cliff. This
detector promotes that rise to its own verdict so it is not only visible on a quiet line.
It reads the same probe logs as root-fs-stall and uses the same rate rule
(rate_counts / is_rising there): RED when probes >= 1 s in the last 30 min are at least
twice the 6 h median and at least 5. It is stateless: each run decides from the window.

Exit status:
  0  the band is not rising
  2  the band is rising (>= 2x its 6 h median, >= 5 probe-seconds in 30 min)
  3  undecidable: no readable root probe log, or a stale one
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("root_fs_stall", Path(__file__).parent / "root-fs-stall.py")
stall = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stall)


def evaluate(records: list[dict], now: float) -> tuple[int, list[str]]:
    seen = [r for r in records if r.get("dir") == stall.TARGET and isinstance(r.get("at"), (int, float))]
    if not seen:
        return 3, ["no root-filesystem probe records"]
    newest = max(r["at"] for r in seen)
    if now - newest > stall.STALE_S:
        return 3, [f"root probe log is stale: newest record {now - newest:.0f}s old (limit {stall.STALE_S:.0f}s)"]
    recent, baseline, _ = stall.rate_counts(seen, now)
    line = (f"probes >= {stall.RATE_MIN_S:g} s: {recent} in the last {stall.RATE_WINDOW_S / 60:.0f} min "
            f"vs 6 h median {baseline:g}")
    if stall.is_rising(recent, baseline):
        return 2, [f"sub-threshold band rising: {line}"]
    return 0, [f"sub-threshold band not rising: {line}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    parser.add_argument("--live-log", default=stall.LIVE_LOG)
    args = parser.parse_args(argv)
    paths = [Path(args.live_log), Path(args.home) / "operator" / "dual-probe.jsonl"]
    records = stall.load(paths, source=os.path.realpath(args.home))
    if not records:
        print(f"UNKNOWN: no readable local root probe records among {', '.join(str(p) for p in paths)}")
        return 3
    code, lines = evaluate(records, time.time())
    print({0: "OK", 2: "FAIL", 3: "UNKNOWN"}[code] + ": " + "; ".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
