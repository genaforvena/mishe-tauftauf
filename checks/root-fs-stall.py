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

A stall line also carries mem_psi (PSI memory 'some' avg10) once the recorder
samples it. A high mem_psi beside a high io_psi points at reclaim — the page
cache being charged and swept (2026-11-16: site-home reads charging the plants
slice) — rather than a plain write storm, so the two pressures together narrow
the cause that io_psi alone could not.

With two or more stalls in the window the verdict also carries the peak io_psi
and peak mem_psi across all of them, not just the longest stall's own readings.
The worst-by-duration cliff need not be the most contended or the one carrying
a reclaim signature; the peak keeps that distinction visible in the signal.

The same diagnostics are carried on the quiet (GREEN) line when the last 24 h
holds a recurrence: worst duration, peak pressures and the number of distinct
bursts. That is the state a reader sees almost all the time; a pressure spike
that would change disposition must not be visible only while the signal is RED.

Exit status:
  0  the probe is fresh and no root probe in the window reached STALL_S
  2  a root-filesystem probe took STALL_S or longer inside the window
  3  undecidable: the logs are missing, stale, or hold no root probes
"""
from __future__ import annotations

BURST_GAP_S = 120.0
"""Episodes separated by at least this much quiet are separate bursts of stalls.

A window can hold several distinct bursts. While the signal stays RED through
all of them, a total count alone cannot tell a responder that stalls recurred
after a quiet gap, and a second RED is not a new edge that wakes one. Naming
the bursts in the refreshed cause keeps that recurrence visible without
changing the signal value, its thresholds or its window.
"""

import argparse
from datetime import datetime, timezone
import json
import sys
import time
from pathlib import Path

WINDOW_S = 900.0
RECURRENCE_S = 86400.0
STALE_S = 300.0
STALL_S = 5.0
TARGET = "root"
EPISODE_CLUSTER_S = 2.0
"""Stall records closer than this are one host-wide cliff seen by several plants' probes.

Each plant runs its own dual_probe.py into the shared /dev/shm log, so a single
root-drive latency cliff yields one stall record per plant within milliseconds.
Counting those as separate stalls overstates the number of events and misattributes
one cliff as several. The probe interval is 1 s; 2 s keeps distinct (10 s-apart)
stalls separate while collapsing same-instant multi-probe reports."""
LIVE_LOG = "/dev/shm/dual-probe.jsonl"
"""The probe's full, volatile log (tmpfs). Read with the durable mirror, never alone."""


def psi_suffix(record: dict) -> str:
    """The stalled probe's own pressure readings, when the recorder carried them.

    dual_probe.py samples PSI io and memory 'some avg10' at the moment of a slow
    probe, so a stall line shows how contended the machine was there. Near-zero
    io_psi means no other task was I/O-blocked (points away from a write storm);
    a high io_psi is expected both from a local write storm and from a device
    latency cliff. A high mem_psi beside it points at reclaim, which io_psi alone
    could not separate. Either field may be absent on records written before it
    existed, so each is rendered only when present.
    """
    parts = []
    io = record.get("psi_io")
    if isinstance(io, (int, float)):
        parts.append(f"io_psi {io:.2f}")
    mem = record.get("psi_mem")
    if isinstance(mem, (int, float)):
        parts.append(f"mem_psi {mem:.2f}")
    return ", " + ", ".join(parts) if parts else ""


def timing_suffix(stalls: list[dict], episode_count: int, now: float) -> str:
    """How recent and how spread the stalls were — the lead time a reader can measure from the signal.

    The episode count alone cannot tell a fresh cliff that is still decaying from a burst spread
    over minutes, or how long ago the last stall was seen. Those two numbers are what decide
    whether a responder is early or late, so they belong in the reason rather than in a 25 MB
    probe log nobody reads at 03:00.
    """
    last = max(r["at"] for r in stalls)
    first = min(r["at"] for r in stalls)
    out = f"; most recent stall {now - last:.0f}s ago"
    if episode_count > 1:
        out += f"; bursts span {(last - first) / 60:.0f} min"
    return out


def peak_psi_suffix(stalls: list[dict]) -> str:
    """Highest pressure across every stall in the window, not only the longest one.

    The worst-by-duration stall can understate contention: in the 2026-11-20
    burst the 23.6 s cliff carried io_psi 57 while a 14.9 s cliff carried 93.
    A reclaim spike on a shorter stall is hidden the same way, so the burst-wide
    peak is what lets a reader separate device latency from reclaim in the
    signal alone. A single stall already renders its own pressure in psi_suffix.
    """
    if len(stalls) < 2:
        return ""
    ios = [r["psi_io"] for r in stalls if isinstance(r.get("psi_io"), (int, float))]
    mems = [r["psi_mem"] for r in stalls if isinstance(r.get("psi_mem"), (int, float))]
    parts = []
    if ios:
        parts.append(f"peak io_psi {max(ios):.2f}")
    if mems:
        parts.append(f"peak mem_psi {max(mems):.2f}")
    return "; " + ", ".join(parts) if parts else ""


def episodes(stalls: list[dict]) -> list[list[dict]]:
    """Group stall records by time proximity; one cliff seen by N probes is one episode."""
    groups: list[list[dict]] = []
    for record in sorted(stalls, key=lambda r: r["at"]):
        if groups and record["at"] - groups[-1][-1]["at"] <= EPISODE_CLUSTER_S:
            groups[-1].append(record)
        else:
            groups.append([record])
    return groups


def bursts(eps: list[list[dict]]) -> list[list[list[dict]]]:
    """Split episodes on quiet gaps; a multi-probe cliff is still one episode."""
    groups: list[list[list[dict]]] = []
    for ep in eps:
        if groups and ep[0]["at"] - groups[-1][-1][-1]["at"] >= BURST_GAP_S:
            groups.append([ep])
        elif groups:
            groups[-1].append(ep)
        else:
            groups.append([ep])
    return groups


def burst_suffix(eps: list[list[dict]], *, with_newest: bool = True) -> str:
    """Name distinct bursts so recurrence inside one window is visible.

    RED also names the newest burst's time. The GREEN recurrence line already
    says how long ago the last stall was, so it asks without the stamp rather
    than print the same instant twice.
    """
    groups = bursts(eps)
    if len(groups) < 2:
        return ""
    out = f"; {len(groups)} separate bursts (gaps >= {BURST_GAP_S / 60:.0f} min)"
    if with_newest:
        newest = max(r["at"] for ep in groups[-1] for r in ep)
        stamp = datetime.fromtimestamp(newest, timezone.utc).strftime("%H:%M:%SZ")
        out += f", newest {stamp}"
    return out


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
        eps = episodes(stalls)
        if len(eps) < len(stalls):
            lead = (f"{len(eps)} root-filesystem stall episode(s) >= {STALL_S:.0f}s in the last "
                    f"{WINDOW_S:.0f}s ({len(stalls)} near-simultaneous probe reports)")
        else:
            lead = (f"{len(stalls)} root-filesystem stall(s) >= {STALL_S:.0f}s in the last "
                    f"{WINDOW_S:.0f}s")
        return 2, [f"{lead}; worst {worst['total']:.1f}s (create {worst.get('create')}, fsync {worst.get('fsync')})"
                   f"{psi_suffix(worst)}{timing_suffix(stalls, len(eps), now)}{peak_psi_suffix(stalls)}"
                   f"{burst_suffix(eps)}"]
    recurrence = [r for r in seen if r.get("event") == "slow" and now - r["at"] <= RECURRENCE_S
                  and (r.get("total") or 0) >= STALL_S]
    line = f"root probe fresh ({now - newest:.0f}s) with no stall >= {STALL_S:.0f}s in {WINDOW_S:.0f}s"
    if recurrence:
        last = max(r["at"] for r in recurrence)
        eps = episodes(recurrence)
        count = (f"{len(eps)} stall(s)" if len(eps) == len(recurrence)
                 else f"{len(eps)} stall episode(s) from {len(recurrence)} probe reports")
        worst_hist = max(recurrence, key=lambda r: r["total"])
        pressure = (peak_psi_suffix(recurrence) if len(recurrence) >= 2
                    else psi_suffix(worst_hist))
        line += (f"; recurrence: {count} >= {STALL_S:.0f}s in the last "
                 f"{RECURRENCE_S / 3600:.0f} h, worst {worst_hist['total']:.1f}s"
                 f"{pressure}"
                 f"{burst_suffix(eps, with_newest=False)}"
                 f", most recent {(now - last) / 60:.0f} min ago")
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
