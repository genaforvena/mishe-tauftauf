#!/usr/bin/env python3
"""Producer and detector: the omp token-stats database stays fresh.

The gap this closes.  ``mishe stats`` reads ``stats.db`` (the omp session
usage database) and reports STALE when nobody has run ``omp stats`` for a
while.  Nothing in the plant ran it: the only omp-related producer
(``omp-db``) quick-checks the agent database, not ``stats.db``.  On
2026-11-25 the stats went STALE at 13:21Z and FRESH again only after a manual
``omp stats --json`` at 13:22Z; the same gap stood at 13:52Z.

This script is the duty.  It runs ``omp stats --json`` once, inside a
memory-capped scope (the 2026-11-25 manual refresh ran the same way, under
``MemoryMax=2G``, so the stats sync cannot charge the plant slice), then reads
the database mtime.  The output of ``omp stats`` is discarded: only its side
effect on ``stats.db`` matters here.  A small state file records what was seen.

Exit codes (duties.json maps 2 to RED and 3 to UNKNOWN):
  0  stats.db is younger than ``--max-age`` seconds after the refresh (GREEN)
  2  the refresh ran but stats.db is still older than ``--max-age`` (RED)
  3  the refresh itself failed or timed out, or the database is missing (UNKNOWN)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RED = 2
UNKNOWN = 3


def stats_db_path() -> Path:
    pi_config = os.environ.get("PI_CONFIG_DIR")
    if pi_config:
        root = Path(pi_config) if os.path.isabs(pi_config) else Path.home() / pi_config
        return root / "stats.db"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and (Path(xdg) / "omp").is_dir():
        return Path(xdg) / "omp" / "stats.db"
    return Path.home() / ".omp" / "stats.db"


def refresh(timeout: float, memory_max: str) -> tuple[int, str]:
    """Run ``omp stats --json`` in a memory-capped scope; return (rc, stderr tail)."""
    cmd = ["omp", "stats", "--json"]
    if subprocess.run(["systemd-run", "--version"], capture_output=True).returncode == 0:
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={memory_max}", *cmd]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return UNKNOWN, "timeout"
    except OSError as exc:
        return UNKNOWN, f"spawn failed: {exc}"
    return proc.returncode, (proc.stderr or "").strip()[-300:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", required=True, help="site home (absolute)")
    ap.add_argument("--max-age", type=float, default=1800.0, help="seconds stats.db may age")
    ap.add_argument("--timeout", type=float, default=150.0, help="refresh timeout, seconds")
    ap.add_argument("--memory-max", default="2G", help="MemoryMax for the refresh scope")
    args = ap.parse_args(argv)

    rc, err = refresh(args.timeout, args.memory_max)
    now = time.time()  # after the refresh: the db mtime it just wrote must not read as future
    db = stats_db_path()
    age = None
    try:
        age = now - db.stat().st_mtime
    except OSError:
        pass

    state = {"observed_epoch": round(now, 1), "refresh_rc": rc, "refresh_stderr_tail": err,
             "db": str(db), "db_age_s": None if age is None else round(age, 1)}
    out = Path(args.home) / "health" / "stats-refresh.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(out)
    except OSError as exc:
        print(f"stats-refresh: could not write state: {exc}", file=sys.stderr)

    if rc != 0:
        print(f"stats-refresh UNKNOWN: omp stats rc={rc} {err}", file=sys.stderr)
        return UNKNOWN
    if age is None:
        print(f"stats-refresh UNKNOWN: {db} missing after refresh", file=sys.stderr)
        return UNKNOWN
    if age > args.max_age:
        print(f"stats-refresh RED: stats.db age {age:.0f}s after a successful refresh", file=sys.stderr)
        return RED
    print(f"stats-refresh GREEN: stats.db age {age:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
