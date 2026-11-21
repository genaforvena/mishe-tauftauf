#!/usr/bin/env python3
"""Detector: experiments past their decision date, and experiment artifacts nobody registered.

AGENTS.md says Mishe carries nothing dead: every experiment ends adopted (wired to a consumer or
check) or retired (deleted), by a stated date. The committed registry ``experiments.json`` lists
each one with its status and decision date. This duty reports:

- AMBER (exit 0, value AMBER): a running experiment is past its decision date, or a tracked file
  whose path says experiment, exploratory or pilot is not owned by any registry entry.
- RED (exit 2): a running experiment is a week or more past its decision date.
- UNKNOWN (exit 3): the registry is missing or invalid, or git cannot list the tracked files.
- GREEN (exit 0): nothing past due and no unregistered artifact.

Limits: the scan reads tracked files only (``git ls-files``), so a gitignored local doc such as
docs/agent-step-experiments.md is not seen here; its registry entry records it by hand.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NAME = re.compile(r"experiment|exploratory|pilot", re.IGNORECASE)
WEEK_S = 7 * 86400
STATUSES = {"running", "adopted", "retired"}
THIS_CHECK = {"checks/experiments-lifecycle.py", "experiments.json", "tests/test_experiments_lifecycle.py"}


def load_register(repo):
    """The registry entries, or raise ValueError when the file is missing or malformed."""
    try:
        data = json.loads((Path(repo) / "experiments.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"experiments.json unreadable: {exc}") from exc
    items = data.get("experiments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("experiments.json has no experiments list")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("an experiment entry has no id")
        if item.get("status") not in STATUSES:
            raise ValueError(f"{item['id']}: status {item.get('status')!r} is not running, adopted or retired")
        if not isinstance(item.get("paths", []), list):
            raise ValueError(f"{item['id']}: paths is not a list")
    return items


def due_epoch(text):
    """An ISO date (YYYY-MM-DD) as the start of that day in UTC, epoch seconds."""
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def tracked_files(repo):
    done = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, timeout=30,
                          env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin"})
    if done.returncode != 0:
        raise ValueError("git ls-files failed")
    return done.stdout.splitlines()


def evaluate(items, files, now):
    """(red, amber, unregistered) lists of readable findings."""
    red, amber = [], []
    owned = set(THIS_CHECK)
    for item in items:
        owned.update(p for p in item.get("paths", []) if isinstance(p, str))
        if item["status"] != "running":
            continue
        due = due_epoch(item.get("decision_due", "")) if isinstance(item.get("decision_due"), str) else None
        if due is None:
            raise ValueError(f"{item['id']}: decision_due is not an ISO date")
        if now > due:
            late_days = (now - due) / 86400
            line = f"{item['id']} ({late_days:.1f} days past its decision date, owner {item.get('owner', '?')})"
            (red if now - due >= WEEK_S else amber).append(line)
    unregistered = sorted(f for f in files if NAME.search(f) and f not in owned)
    return red, amber, unregistered


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=False, help="site home (unused; the registry is committed)")
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    now = time.time() if a.now is None else a.now
    try:
        items = load_register(a.repo)
        files = tracked_files(a.repo)
        red, amber, unregistered = evaluate(items, files, now)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"experiments-lifecycle: UNKNOWN {exc}", file=sys.stderr)
        return 3
    notes = [f"past decision date: {'; '.join(red + amber)}"] if red or amber else []
    if unregistered:
        notes.append(f"unregistered experiment artifacts: {', '.join(unregistered)}")
    if red:
        print("experiments-lifecycle: RED " + "; ".join(notes), file=sys.stderr)
        return 2
    if notes:
        print("experiments-lifecycle: AMBER " + "; ".join(notes))
        return 0
    print(f"experiments-lifecycle: GREEN {sum(1 for i in items if i['status'] == 'running')} running, "
          f"{len(items)} registered, no unregistered artifact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
