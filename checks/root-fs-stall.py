#!/usr/bin/env python3
"""Detector: the root filesystem completes a create+fsync probe promptly.

The containment guard's own filesystem probe (/var/tmp) was moved to a second
drive on 2026-10-31 because the root drive's fsync stalls (up to 10 s, probe
A/B in operator/dual-probe.jsonl) tripped the whole plant eleven times in 17
hours. Moving the probe stopped the trips; it must not also stop anyone seeing
the stalls. SITE/operator/dual_probe.py keeps probing a root-filesystem
directory every second. It appends every probe to /dev/shm/dual-probe.jsonl (its
full history) and mirrors only the last hour into SITE/operator/dual-probe.jsonl
(the mirror trims each minute so it does not put a truncate-and-rewrite flush on
the root drive).

This check reads both: the window verdict comes from whichever log is freshest,
and recurrence is counted over the union, so the "last 24 h" recurrence claim is
backed by the probe's whole history while the tmpfs log is alive, and still by the
durable mirror after it is gone.

Each stall line carries the stalled probe's io_psi (PSI io 'some' avg10): the
share of the prior 10 s during which some task was stalled on I/O. A near-zero
value means nothing was I/O-contended at the stall, which points away from a
write storm; a high value only says the machine *was* I/O-contended and cannot
by itself separate a local write storm from a device latency cliff, since a
blocked task raises the same counter either way.

Exit status:
  0  the probe is fresh and no root probe in the window reached STALL_S
  2  a root-filesystem probe took STALL_S or longer inside the window
  3  undecidable: the logs are missing, stale, or hold no root probes
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

WINDOW_S = 900.0
RECURRENCE_S = 86400.0
STALE_S = 300.0
STALL_S = 5.0
TARGET = "root"
LIVE_LOG = "/dev/shm/dual-probe.jsonl"
"""The probe's full, volatile log (tmpfs). Read with the durable mirror, never alone."""


def psi_suffix(record: dict) -> str:
    """The stalled probe's own I/O-pressure reading, when the recorder carried one.

    dual_probe.py samples PSI io 'some avg10' at the moment of a slow probe, so a
    stall line shows how I/O-contended the machine was there. Near zero at a
    stall means no other task was I/O-blocked (points away from a write storm); a
    high value is expected both from a local write storm and from a device
    latency cliff, so it cannot by itself name the cause. Absent on records
    written before that field existed.
    """
    psi = record.get("psi_io")
    return "" if not isinstance(psi, (int, float)) else f", io_psi {psi:.2f}"


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
                   f"worst {worst['total']:.1f}s (create {worst.get('create')}, fsync {worst.get('fsync')})"
                   f"{psi_suffix(worst)}"]
    recurrence = [r for r in seen if r.get("event") == "slow" and now - r["at"] <= RECURRENCE_S
                  and (r.get("total") or 0) >= STALL_S]
    line = f"root probe fresh ({now - newest:.0f}s) with no stall >= {STALL_S:.0f}s in {WINDOW_S:.0f}s"
    if recurrence:
        last = max(r["at"] for r in recurrence)
        line += (f"; recurrence: {len(recurrence)} stall(s) >= {STALL_S:.0f}s in the last "
                 f"{RECURRENCE_S / 3600:.0f} h, most recent {(now - last) / 60:.0f} min ago")
    return 0, [line]


def load(paths: list[Path]) -> list[dict]:
    """Union of the probe logs; a later file wins per (at, dir, event). Unreadable files are skipped."""
    out: dict[tuple, dict] = {}
    for path in paths:
        try:
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                out[(record.get("at"), record.get("dir"), record.get("event"))] = record
    return list(out.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    parser.add_argument("--live-log", default=LIVE_LOG,
                        help="the probe's full-history log; read together with the durable mirror")
    args = parser.parse_args(argv)
    paths = [Path(args.live_log), Path(args.home) / "operator" / "dual-probe.jsonl"]
    records = load(paths)
    if not records:
        print(f"UNKNOWN: no readable probe log among {', '.join(str(p) for p in paths)}")
        return 3
    code, lines = evaluate(records, time.time())
    print({0: "OK", 2: "FAIL", 3: "UNKNOWN"}[code] + ": " + "; ".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
