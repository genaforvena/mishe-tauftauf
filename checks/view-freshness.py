#!/usr/bin/env python3
"""Generic detector: a view window's running copy matches the code on disk.

Parameterised by window name, the repo-relative files whose content is the
running copy's fingerprint, and the state file the view writes. Exit codes:
0 GREEN (running copy == disk), 2 RED (differs or never reported), 3 UNKNOWN
(files/state unreadable).

Usage: view-freshness.py WINDOW STATE_REL [FILE ...] [--repo REPO] [--home HOME]
  WINDOW     window name (e.g. network, household, budget)
  STATE_REL  repo-or-home-relative state json path (e.g. network/window.json)
  FILE ...   repo-relative files to fingerprint (defaults from network pattern)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[1]


def resolve_home(arg):
    if arg:
        return Path(arg)
    for var in ("MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME"):
        v = os.environ.get(var)
        if v:
            return Path(v)
    return REPO_DEFAULT / ".mishe-tauftauf"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("window")
    ap.add_argument("state_rel")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--home", default=None)
    args = ap.parse_args(argv[1:])

    repo = Path(args.repo) if args.repo else REPO_DEFAULT
    home = resolve_home(args.home)
    files = args.files or []
    state_path = Path(args.state_rel)
    if not state_path.is_absolute():
        # try home first, then repo
        cand = home / state_path
        state_path = cand if cand.exists() else repo / state_path

    try:
        state = json.loads(state_path.read_text())
        running = str(state["fingerprint"])
    except (OSError, ValueError, KeyError, TypeError):
        print(f"{args.window}: the view has not reported its running copy (state missing or pre-fingerprint)",
              file=sys.stderr)
        return 2
    try:
        disk = hashlib.sha256()
        for rel in files:
            disk.update((repo / rel).read_bytes())
    except OSError as exc:
        print(f"{args.window}: view files unreadable: {exc}", file=sys.stderr)
        return 3
    if running != disk.hexdigest():
        print(f"{args.window}: the window runs a copy of the view that differs from the files on disk",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))