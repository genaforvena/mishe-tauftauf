#!/usr/bin/env python3
"""Detector: the cache-miss read cost that would reopen the next Rust slice.

The rust-plan `next-rust-slice` task is closed on one number (plan 10.39): the
forced full fold of a role's tape, `Feed(home).role_entries(role, _force_full=True)`,
measured in a fresh process. The re-open trigger is a cost above 5 CPU-s or a
peak RSS above 200 MB for any role. Until this detector existed nothing ran that
measurement on a schedule, so the slice could only reopen by hand.

This is a System 0 duty: a deterministic measurement, no judgment. Each role is
folded in its own child process, so CPU and peak RSS are not shared between
roles. The host guard comes first: a measurement made on a loaded host would
skew the numbers, so under the load or memory guard the run is skipped and
reads UNKNOWN, never GREEN.

Verdicts:
  GREEN   0  every role measured and under both triggers; the slice stays closed.
  RED     2  a role is over a trigger; the slice's pre-gate A/B may open.
  UNKNOWN 3  skipped by the host guard, a child failed, or the site is missing.

The latest reading is written to an ignored artifact under the site home so a
person can read the raw numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_ROLES = ("senses", "genome")
DEFAULT_CPU_TRIGGER_S = 5.0
DEFAULT_RSS_TRIGGER_KB = 200 * 1024
DEFAULT_MAX_LOAD = 2.0
DEFAULT_MIN_FREE_KB = 8 * 1024 * 1024
DEFAULT_TIMEOUT_S = 240

CHILD = r"""
import json, resource, sys, time
from pathlib import Path
from mishe_tauftauf.feed import Feed
home, role = Path(sys.argv[1]), sys.argv[2]
c0 = time.process_time(); t0 = time.perf_counter()
rows = Feed(home).role_entries(role, _force_full=True)
c1 = time.process_time(); t1 = time.perf_counter()
print(json.dumps({
    "role": role,
    "entries": len(rows),
    "cpu_s": round(c1 - c0, 3),
    "elapsed_s": round(t1 - t0, 3),
    "maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
}))
"""


def host_guard(max_load: float, min_free_kb: int) -> str | None:
    """Return the reason the host is too busy to measure, or None when it is quiet."""
    try:
        load1 = float(Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError) as exc:
        return f"loadavg unreadable: {exc}"
    if load1 > max_load:
        return f"loadavg {load1:.2f} above the {max_load:g} bar"
    try:
        meminfo = Path("/proc/meminfo").read_text()
    except OSError as exc:
        return f"meminfo unreadable: {exc}"
    avail = None
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            avail = int(line.split()[1])
            break
    if avail is None:
        return "MemAvailable not found in meminfo"
    if avail < min_free_kb:
        return f"MemAvailable {avail // 1024} MB below the {min_free_kb // 1024} MB floor"
    return None


def fold_role(repo: Path, home: Path, role: str, timeout: int) -> dict:
    """Measure one role's forced full fold in a fresh child process."""
    env = dict(os.environ)
    src = str(repo / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(home), role],
        capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"{role} fold exited {proc.returncode}: {proc.stderr.strip()[-300:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError(f"{role} fold printed nothing")
    return json.loads(lines[-1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    parser.add_argument("--cpu-trigger", type=float, default=DEFAULT_CPU_TRIGGER_S)
    parser.add_argument("--rss-trigger-kb", type=int, default=DEFAULT_RSS_TRIGGER_KB)
    parser.add_argument("--max-load", type=float, default=DEFAULT_MAX_LOAD)
    parser.add_argument("--min-free-kb", type=int, default=DEFAULT_MIN_FREE_KB)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--no-artifact", action="store_true")
    args = parser.parse_args(argv)

    home: Path = args.home
    repo: Path = args.repo
    if not home.is_dir():
        print(f"rust-measurement: UNKNOWN site home missing: {home}", file=sys.stderr)
        return 3
    if not (repo / "src" / "mishe_tauftauf" / "feed.py").is_file():
        print(f"rust-measurement: UNKNOWN no feed.py under {repo}/src", file=sys.stderr)
        return 3

    skip = host_guard(args.max_load, args.min_free_kb)
    if skip:
        print(f"rust-measurement: UNKNOWN skipped, host busy: {skip}; re-run when quiet",
              file=sys.stderr)
        return 3

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    readings = []
    try:
        for role in roles:
            readings.append(fold_role(repo, home, role, args.timeout))
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"rust-measurement: UNKNOWN fold failed: {exc}", file=sys.stderr)
        return 3

    over = []
    for r in readings:
        if r["cpu_s"] > args.cpu_trigger:
            over.append(f"{r['role']} cpu {r['cpu_s']} CPU-s > {args.cpu_trigger:g}")
        if r["maxrss_kb"] > args.rss_trigger_kb:
            over.append(f"{r['role']} rss {r['maxrss_kb'] // 1024} MB > "
                        f"{args.rss_trigger_kb // 1024} MB")
    summary = "; ".join(f"{r['role']} {r['cpu_s']} CPU-s {r['maxrss_kb'] // 1024} MB "
                        f"{r['entries']} entries" for r in readings)

    if not args.no_artifact:
        out = home / "artifacts" / "rust-plan-20261107" / "rust-measurement-latest.json"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "readings": readings,
                "triggers": {"cpu_s": args.cpu_trigger, "rss_kb": args.rss_trigger_kb},
                "over": over,
            }, indent=1) + "\n")
        except OSError as exc:
            print(f"rust-measurement: artifact not written: {exc.strerror or exc}", file=sys.stderr)

    if over:
        print(f"rust-measurement: RED trigger hit, slice may reopen: {'; '.join(over)} [{summary}]",
              file=sys.stderr)
        return 2
    print(f"rust-measurement: GREEN under triggers, slice stays closed: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
