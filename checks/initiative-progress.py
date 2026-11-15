#!/usr/bin/env python3
"""Evaluate registered initiative checks for a point-in-time progress report.

A passed check is not independent proof that the goal was achieved. Like the
initiative duty, this runs the registered acceptance commands; use a trusted site.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from initiatives import DONE, OPEN, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--now", type=float, default=None)
    args = parser.parse_args()
    now = time.time() if args.now is None else args.now
    try:
        register = json.loads((args.home / "initiatives.json").read_text(encoding="utf-8"))
        items = register["initiatives"]
        if not isinstance(items, list):
            raise ValueError("initiatives is not a list")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"UNKNOWN: cannot read initiative register: {exc}", file=sys.stderr)
        return 3
    charters = {p.stem for p in (args.home / "charters").glob("*.md")}
    rows = []
    for item in items:
        if not isinstance(item, dict):
            rows.append({"status": "unknown", "detail": "entry is not an object"})
            continue
        row = {k: item.get(k) for k in ("id", "owner", "goal", "due", "escalation")}
        due = item.get("due")
        if not isinstance(item.get("id"), str) or item.get("owner") not in charters:
            status, detail = "unknown", "missing id or owner charter"
        elif not isinstance(due, (int, float)) or isinstance(due, bool):
            status, detail = "unknown", "invalid due time"
        else:
            verdict, detail = evaluate(item.get("check"), args.home, args.repo)
            status = "check-passed" if verdict == DONE else ("overdue" if now > due else "open") if verdict == OPEN else "unknown"
        row.update(status=status, detail=detail, check=item.get("check"))
        rows.append(row)
    print(json.dumps({"checked_at": now, "note": "Check results are proxies, not independent acceptance or live delivery evidence.", "items": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
