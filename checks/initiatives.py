#!/usr/bin/env python3
"""Detector: an initiative a role owns is not done and its due time has passed.

Longer tasks used to live only as prose on a wall, so nothing woke the owner when one stalled. The
register SITE/initiatives.json lists each initiative with an owner role, a due time (epoch seconds)
and a deterministic acceptance check; this duty (one per owner role, ``--owner ROLE``) evaluates the
owner's checks and says RED while one is past due and not done.  RED is an edge the owner is woken by
and re-offered on, like any incident signal.  The owner must be a real charter, so a task never waits
on a role that does not exist.

Register entry: {"id", "owner", "goal", "due", "escalation", "check"}; ``check`` is one of
  {"kind": "signal", "name": N, "equals": V}        signals/N.json value equals V (or "state": S)
  {"kind": "path", "path": P}                       P (relative to the site home) exists
  {"kind": "json", "file": F, "key": [..], "equals": V | "min": X | "max": X}
  {"kind": "commit", "grep": TEXT, "since": EPOCH}  a commit on main since EPOCH has TEXT in its subject
  {"kind": "argv", "argv": [...], "timeout": N}     acceptance command: no shell, run in the repo, with
                                                    {repo}, {home} and {python} substituted; exit 0 done,
                                                    1 open, anything else, a timeout or a missing program
                                                    UNKNOWN. ``timeout`` is optional (default 20 s).

Exit: 0 GREEN (everything done, or open and not yet due), 2 RED (a check says not done past its due),
3 UNKNOWN (register missing or invalid, owner has no charter, or a check cannot be evaluated).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DONE, OPEN, UNKNOWN = "done", "open", "unknown"
ARGV_TIMEOUT_S = 20


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def evaluate(check, home, repo):
    """(DONE|OPEN|UNKNOWN, detail) for one acceptance check; never raises."""
    if not isinstance(check, dict):
        return UNKNOWN, "check is not an object"
    kind = check.get("kind")
    if kind == "signal":
        data = _read(home / "signals" / f"{check.get('name')}.json")
        if not isinstance(data, dict):
            return UNKNOWN, f"signal {check.get('name')} has no reading"
        field = "state" if "state" in check else "equals"
        if field not in check:
            return UNKNOWN, "signal check needs equals or state"
        have = data.get("state") if field == "state" else data.get("value")
        return (DONE if have == check[field] else OPEN), f"signal {check['name']} is {have!r}"
    if kind == "path":
        target = check.get("path")
        if not isinstance(target, str) or not target or Path(target).is_absolute() or ".." in Path(target).parts:
            return UNKNOWN, "path check needs a relative path inside the site home"
        return (DONE if (home / target).exists() else OPEN), f"{target} missing"
    if kind == "json":
        data = _read(home / str(check.get("file")))
        key = check.get("key")
        if data is None or not isinstance(key, list) or not key:
            return UNKNOWN, f"cannot read {check.get('file')} or key is empty"
        for part in key:
            if not isinstance(data, dict) or part not in data:
                return UNKNOWN, f"{check.get('file')} has no key {'.'.join(map(str, key))}"
            data = data[part]
        if "equals" in check:
            return (DONE if data == check["equals"] else OPEN), f"value is {data!r}"
        if not _number(data):
            return UNKNOWN, f"value {data!r} is not a number"
        if "min" in check and _number(check["min"]):
            return (DONE if data >= check["min"] else OPEN), f"value {data} below {check['min']}"
        if "max" in check and _number(check["max"]):
            return (DONE if data <= check["max"] else OPEN), f"value {data} above {check['max']}"
        return UNKNOWN, "json check needs equals, min or max"
    if kind == "commit":
        grep, since = check.get("grep"), check.get("since")
        if not isinstance(grep, str) or not grep or not _number(since):
            return UNKNOWN, "commit check needs grep text and an epoch since"
        try:
            # subjects only: git --grep also searches bodies, so a commit that merely mentions the text would pass
            done = subprocess.run(["git", "-C", str(repo), "log", f"--since={int(since)}", "--format=%s"],
                                  capture_output=True, text=True, timeout=30,
                                  env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.SubprocessError) as exc:
            return UNKNOWN, f"git failed: {exc}"
        if done.returncode != 0:
            return UNKNOWN, "git log failed"
        hit = any(grep in subject for subject in done.stdout.splitlines())
        return (DONE if hit else OPEN), f"no commit subject with {grep!r} since the start"
    if kind == "argv":
        return _argv(check, home, repo)
    return UNKNOWN, f"unknown check kind {kind!r}"


def _argv(check, home, repo):
    """Run an acceptance argv: no shell, a timeout, and exit 0/1 as the only verdicts."""
    argv = check.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return UNKNOWN, "argv check needs a non-empty list of strings"
    timeout = check.get("timeout", ARGV_TIMEOUT_S)
    if not _number(timeout) or timeout <= 0:
        return UNKNOWN, "argv timeout must be a positive number"
    tokens = {"{repo}": str(repo), "{home}": str(home), "{python}": sys.executable}
    cmd = []
    for arg in argv:
        for token, value in tokens.items():
            arg = arg.replace(token, value)
        cmd.append(arg)
    try:
        run = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=str(repo),
                             env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        return UNKNOWN, f"acceptance timed out after {timeout} s"
    except OSError as exc:
        return UNKNOWN, f"acceptance cannot run: {exc}"
    if run.returncode == 0:
        return DONE, "acceptance exited 0"
    if run.returncode == 1:
        return OPEN, "acceptance exited 1"
    return UNKNOWN, f"acceptance exited {run.returncode}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    now = time.time() if a.now is None else a.now
    register = _read(a.home / "initiatives.json")
    items = register.get("initiatives") if isinstance(register, dict) else None
    if not isinstance(items, list):
        print("initiatives: register SITE/initiatives.json missing or invalid; cannot tell", file=sys.stderr)
        return 3
    charters = {p.stem for p in (a.home / "charters").glob("*.md")}
    overdue, unknown, open_count, done_count = [], [], 0, 0
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            if a.owner == "steward":
                unknown.append("an entry without an id")
            continue
        owner = item.get("owner")
        if owner not in charters:
            # a task owned by a role that does not exist is nobody's: steward's duty carries it
            if a.owner == "steward":
                unknown.append(f"{item['id']}: owner {owner!r} has no charter")
            continue
        if owner != a.owner:
            continue
        status, detail = evaluate(item.get("check"), a.home, a.repo)
        due = item.get("due")
        if status == UNKNOWN or not _number(due):
            unknown.append(f"{item['id']}: {detail if status == UNKNOWN else 'due is not a time'}")
        elif status == DONE:
            done_count += 1
        elif now > due:
            overdue.append(f"{item['id']} ({item.get('goal', '')}; {detail}; {(now - due) / 3600:.1f} h past due, "
                           f"escalate to {item.get('escalation', 'steward')})")
        else:
            open_count += 1
    if overdue:
        print(f"initiatives[{a.owner}]: past due and not done -> " + "; ".join(overdue), file=sys.stderr)
        return 2
    if unknown:
        print(f"initiatives[{a.owner}]: cannot evaluate -> " + "; ".join(unknown), file=sys.stderr)
        return 3
    print(f"initiatives[{a.owner}]: GREEN {done_count} done, {open_count} open and not yet due")
    return 0


if __name__ == "__main__":
    sys.exit(main())
