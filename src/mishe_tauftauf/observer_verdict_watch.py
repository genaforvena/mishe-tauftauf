"""Route the observer-verdict detector to the resolver that owns the failure.

``checks/observer-verdict-agreement.py`` answers whether the running observer's
reading equals the reviewed tree's reading. It is a check, so nothing runs it:
the divergence it was written to catch stayed invisible for 5 h 56 min on
2026-10-20. This watcher runs it on an interval, keeps each verdict as a
continuing observation record, and routes a disagreement to health.

A disagreement is a fault: it routes as soon as its shape is new. An undecidable
reading is a condition, not a fault — UNKNOWN is a legal steady state, so it
routes only once it has persisted across the cutoff, and again only when its
shape changes. That is the age bound the state previously lacked.

The check is a tracked repository file that no release carries, so the watcher
names it with ``--check`` and records its digest: the detector's own bytes are
part of the observation instead of an unstated dependency on a working tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .observations import validate_home

VERDICT_RE = re.compile(r"^(observer|publisher)\s*:\s+(AGREE|DISAGREE|UNKNOWN)\b")
DEFAULT_INTERVAL = 300.0
DEFAULT_CUTOFF = 3
CHECK = "checks/observer-verdict-agreement.py"
VERDICT_FILE = "health/observer-verdict.json"
ALERT_FILE = "health/observer-verdict-alert.md"
SOURCE = "observer-verdict"
RESOLVER = "health"


def parse(output: str) -> list[str]:
    """The differences the check names, from its printed contract."""
    problems = []
    for line in output.splitlines():
        if VERDICT_RE.match(line):
            continue
        if line.startswith("    ") and line.strip():
            problems.append(line.strip())
    return problems


def summarize(exit_code: int, output: str) -> str:
    """One line a resolver can read: what was seen, or why it could not be."""
    if exit_code == 0:
        return ("the running observer's reading and the published reading both "
                "equal the reviewed tree's")
    problems = parse(output)
    if exit_code == 2:
        return problems[0] if problems else "the running observer's reading differs from the reviewed tree's"
    undecidable = next((line for line in output.splitlines() if line.startswith("UNKNOWN")), "")
    return undecidable.removeprefix("UNKNOWN —").strip() or "the check could not decide"


def observe(home: Path, check: Path) -> dict:
    """Run the check once and report its exit contract as a row."""
    started = time.monotonic()
    try:
        digest = hashlib.sha256(check.read_bytes()).hexdigest()
    except OSError as exc:
        # Python exits 2 for a script it cannot open, which is the check's own
        # disagreement code: an unreadable check must read as undecidable.
        return {"observed_at": time.time(), "exit": 3, "verdict": "UNKNOWN",
                "summary": f"the check could not be read: {exc}", "problems": [],
                "check_sha256": "missing", "elapsed": 0.0}
    try:
        result = subprocess.run([sys.executable, str(check), "--home", str(home)],
                                capture_output=True, text=True, timeout=120)
        output = (result.stdout + result.stderr).strip()
        exit_code = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        output, exit_code = f"UNKNOWN — the check did not run: {exc}", 3
    verdict = {0: "AGREE", 2: "DISAGREE"}.get(exit_code, "UNKNOWN")
    return {"observed_at": time.time(), "exit": exit_code, "verdict": verdict,
            "summary": summarize(exit_code, output), "problems": parse(output),
            "check_sha256": digest, "elapsed": round(time.monotonic() - started, 3)}


def should_route(exit_code: int, consecutive: int, cutoff: int,
                 last_summary: str | None, summary: str) -> bool:
    """Route a new disagreement at once; an undecidable reading only once it persists."""
    if exit_code == 0 or summary == last_summary:
        return False
    return exit_code == 2 or consecutive >= cutoff


def route(home: Path, row: dict) -> None:
    """Write the detector's durable record and address its resolver."""
    from .wall import message
    text = (f"Observer verdict {row['verdict']}: {row['summary']}\n"
            f"Evidence: {VERDICT_FILE} and {ALERT_FILE}. Resolver {RESOLVER}: read the "
            "verdict and the differences it names, then repair the observer's import "
            "root or the published snapshot. Disposition within one observation "
            "interval; recovery is not causal resolution.\n")
    path = home / ALERT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    message(home, SOURCE, RESOLVER, text)


def run(home: Path, check: Path, interval: float, cutoff: int) -> None:
    """Keep the verdict observed and route its failures for as long as the unit runs."""
    home = validate_home(home)
    path = home / VERDICT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    consecutive = 0
    last_summary = None
    while True:
        row = observe(home, check)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(row) + "\n")
        temporary.replace(path)
        consecutive = consecutive + 1 if row["exit"] != 0 else 0
        if should_route(row["exit"], consecutive, cutoff, last_summary, row["summary"]):
            route(home, row)
            last_summary = row["summary"]
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--check", type=Path, default=None,
                        help="verdict check to run (default: the repository holding the home)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="seconds between checks (default: %(default)s)")
    parser.add_argument("--cutoff", type=int, default=DEFAULT_CUTOFF,
                        help="undecidable readings that persist this many checks route "
                             "(default: %(default)s)")
    args = parser.parse_args()
    check = args.check or (args.home.resolve().parent / CHECK)
    run(args.home, check, args.interval, args.cutoff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
