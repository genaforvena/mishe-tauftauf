#!/usr/bin/env python3
"""Detector: one registered initiative is done, open within its due time, or overdue.

The register is ``home/initiatives.json`` (site data, not committed)::

    {"initiatives": [{"id": "...", "owner": "ROLE", "goal": "...", "due": "2026-11-20T12:00:00Z",
                      "escalate": "ROLE", "acceptance": ["{python}", "{repo}/checks/x.py"]}]}

``acceptance`` is an argv run in the repository with no shell and a timeout, with ``{repo}``
and ``{home}`` substituted. Its exit status is the acceptance verdict:

  0  done
  1  not done
  anything else, a timeout, or a missing program: the check cannot decide (UNKNOWN)

Reading of one initiative (``--id``):

  done                          GREEN  "done"
  not done, before ``due``      GREEN  "open, due ..."          (follow-through is on time)
  not done, past ``due``        RED    names owner, escalate, goal and how late
  not registered, register unreadable, bad due, bad acceptance  UNKNOWN

A missing or failing observation is never GREEN: it is UNKNOWN, so the owner wakes on missing evidence
rather than on silence. Only stdlib is used, so the check runs from committed bytes.

Exit status: 0 GREEN, 2 RED, 3 UNKNOWN. GREEN prints to stdout; RED and UNKNOWN print to stderr.
"""
from __future__ import annotations

import argparse
import calendar
import json
import subprocess
import sys
import time
from pathlib import Path

ACCEPTANCE_TIMEOUT_S = 20


def _parse_due(text) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def _load_entry(home: Path, wanted: str):
    """Return (entry, None) or (None, reason). Never guesses a missing entry."""
    path = home / "initiatives.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None, f"no register at {path}"
    except (OSError, ValueError) as exc:
        return None, f"register unreadable: {exc}"
    items = data.get("initiatives") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None, "register has no initiatives list"
    for item in items:
        if isinstance(item, dict) and item.get("id") == wanted:
            return item, None
    return None, f"initiative {wanted!r} is not registered"


def _run_acceptance(argv, home: Path, repo: Path):
    """Return 'done', 'open', or an UNKNOWN reason string."""
    if not isinstance(argv, list) or not argv or not all(isinstance(w, str) for w in argv):
        return "acceptance is not an argv list"
    words = [w.replace("{repo}", str(repo)).replace("{home}", str(home)).replace("{python}", sys.executable)
             for w in argv]
    try:
        out = subprocess.run(words, cwd=repo, capture_output=True, timeout=ACCEPTANCE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return f"acceptance timed out after {ACCEPTANCE_TIMEOUT_S}s"
    except OSError as exc:
        return f"acceptance cannot run: {exc}"
    if out.returncode == 0:
        return "done"
    if out.returncode == 1:
        return "open"
    return f"acceptance exited {out.returncode} (cannot decide)"


def evaluate(home: Path, repo: Path, wanted: str, now: float):
    """Return (code, message): 0 GREEN, 2 RED, 3 UNKNOWN."""
    entry, reason = _load_entry(home, wanted)
    if entry is None:
        return 3, f"initiative {wanted}: UNKNOWN, {reason}"
    due = _parse_due(entry.get("due"))
    if due is None:
        return 3, f"initiative {wanted}: UNKNOWN, due is missing or not an ISO UTC time"
    verdict = _run_acceptance(entry.get("acceptance"), home, repo)
    owner = entry.get("owner", "?")
    goal = entry.get("goal", "")
    if verdict == "done":
        return 0, f"initiative {wanted}: GREEN done ({goal})"
    if verdict == "open":
        if now <= due:
            return 0, f"initiative {wanted}: GREEN open, due {entry.get('due')} (owner {owner})"
        late = (now - due) / 60
        escalate = entry.get("escalate", "?")
        return 2, (f"initiative {wanted}: RED not done {late:.0f} min past due {entry.get('due')}; "
                   f"owner {owner}, escalate {escalate}; goal: {goal}")
    return 3, f"initiative {wanted}: UNKNOWN, {verdict}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--id", required=True)
    a = ap.parse_args()
    code, message = evaluate(a.home, a.repo, a.id, time.time())
    (sys.stdout if code == 0 else sys.stderr).write(message + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
