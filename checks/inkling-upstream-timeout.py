#!/usr/bin/env python3
"""Detector: the Inkling proxy reporting upstream timeouts to its callers.

Observed 2026-11-27: inkling-proxy.service logged upstream "TimeoutError: timed out" at 18:24:35 and 18:24:50
(bel-server stalled under memory pressure, as far as the logs show). No detector fired. The pub wake that
depended on the local route was lost at 18:54 and only wake-waste reported it. A detector on the proxy's own
upstream timeouts would have gone RED about 30 minutes earlier.

Reads the user journal of the proxy unit for the last --window seconds (default 900) and counts lines that
carry --pattern (default "TimeoutError"). Stateless: every run replays the journal, so a window that has
scrolled past goes GREEN again.

Exit status:
  0  no upstream timeout in the window (GREEN)
  2  at least one upstream timeout in the window (RED)
  3  undecidable: journalctl could not be read (UNKNOWN)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

DEFAULT_UNIT = "inkling-proxy.service"
DEFAULT_PATTERN = "TimeoutError"
WINDOW_SECONDS = 900


def read_journal(unit: str, since_epoch: float) -> list[str]:
    """Journal lines for ``unit`` since ``since_epoch``. Raises RuntimeError if journalctl fails."""
    res = subprocess.run(
        ["journalctl", "--user", "-u", unit, f"--since=@{int(since_epoch)}", "--no-pager", "--output=cat"],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError(f"journalctl for {unit} failed: {res.stderr.strip() or res.returncode}")
    return res.stdout.splitlines()


def evaluate(lines: list[str], unit: str, pattern: str, window: float) -> tuple[int, list[str]]:
    """Return (exit code, report lines) for journal ``lines`` already limited to the window."""
    hits = [line for line in lines if pattern in line]
    if hits:
        last = hits[-1]
        return 2, [
            f"{unit}: {len(hits)} upstream {pattern} line(s) in the last {int(window // 60)} min (RED)",
            f"last: {last}",
        ]
    return 0, [f"{unit}: no upstream {pattern} in the last {int(window // 60)} min (GREEN)"]


def main(argv=None, *, read=read_journal, now=time.time) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--unit", default=DEFAULT_UNIT)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--window", type=float, default=WINDOW_SECONDS)
    args = parser.parse_args(argv)
    try:
        lines = read(args.unit, now() - args.window)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"{args.unit}: UNKNOWN cannot read the journal: {exc}")
        return 3
    code, report = evaluate(lines, args.unit, args.pattern, args.window)
    print("\n".join(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
