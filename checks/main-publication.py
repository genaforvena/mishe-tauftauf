#!/usr/bin/env python3
"""Publisher duty: local main reaches origin/main without depending on a git hook.

Publication used to be the ``post-commit`` hook's job alone (``tools/post-commit-push.sh``). A commit made
with ``git -c core.hooksPath=/dev/null commit`` skips every hook, so nothing pushed it: on 2026-11-17
health made 25 such commits and two of them (``75b782fd`` 22:54Z, ``4290a757`` 22:59Z) sat unpublished for
19 minutes while CI had no run for them and only a mind reading ``git status`` found them. ``checks/push-lag.py``
now reds after a quarter hour, but detection still needs a genome wake to act, and wakes are hours apart.

This is the deterministic repair the detector's RED was asking for: fast-forward ``origin/main`` to local
``main`` whenever it is a strict ancestor, so a hooks-disabled commit cannot stay unpublished past one tick.
It only ever pushes commits that already exist on local main; nothing is created, rewritten or force-pushed.

Exit status:
  0  origin/main holds HEAD, or this run pushed it there (the detail says which)
  2  a push failed, local main and origin/main have diverged, or local main is behind (a mind must rebase)
  3  undecidable: local HEAD or the remote cannot be read
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REMOTE = "origin"
BRANCH = "main"


def git(repo: Path, *args: str, timeout: float = 60.0) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                              env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                                   "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())})
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    text = done.stdout.strip() if done.returncode == 0 else (done.stderr.strip() or done.stdout.strip())
    return done.returncode, text


def _tail(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[-limit:]


def _remote_main(repo: Path) -> tuple[int, str]:
    """Fetch, then the remote main's sha as this clone knows it."""
    code, out = git(repo, "fetch", "--quiet", REMOTE, f"refs/heads/{BRANCH}")
    if code != 0:
        return 3, _tail(out)
    code, out = git(repo, "rev-parse", "--verify", f"refs/remotes/{REMOTE}/{BRANCH}")
    if code != 0 or not out:
        return 3, f"no {REMOTE}/{BRANCH} in this clone after fetching"
    return 0, out.split()[0]


def _unpushed(repo: Path, theirs: str, head: str) -> tuple[int, list[str], str]:
    """(count, commit epochs oldest first, error) for theirs..head."""
    code, rows = git(repo, "log", f"{theirs}..{head}", "--format=%ct")
    if code != 0:
        return 0, [], _tail(rows)
    epochs = [int(r) for r in rows.split() if r.strip()]
    return len(epochs), sorted(epochs), ""


def publish(repo: Path, remote: str = REMOTE, branch: str = BRANCH) -> tuple[int, list[str]]:
    current = git(repo, "symbolic-ref", "--short", "-q", "HEAD")[1]
    if current != branch:
        return 0, [f"HEAD is {current or 'detached'}, not {branch}: nothing to publish"]

    code, head = git(repo, "rev-parse", "HEAD")
    if code != 0:
        return 3, [f"cannot read local HEAD: {_tail(head, 120)}"]
    head = head.split()[0]

    code, theirs = _remote_main(repo)
    if code != 0:
        return 3, [f"cannot read {remote}/{branch}: {theirs}"]

    if theirs == head:
        return 0, [f"{remote}/{branch} already holds HEAD {head[:8]}"]

    if git(repo, "cat-file", "-e", f"{theirs}^{{commit}}")[0] != 0:
        return 3, [f"{remote}/{branch} is {theirs[:8]}, which this clone does not have: fetch before judging"]

    if git(repo, "merge-base", "--is-ancestor", head, theirs)[0] == 0:
        count = _unpushed(repo, head, theirs)[0]
        return 2, [f"local {branch} is {count} commit(s) behind {remote}/{branch} ({theirs[:8]}): "
                   f"fast-forward before publishing"]

    if git(repo, "merge-base", "--is-ancestor", theirs, head)[0] != 0:
        return 2, [f"local {branch} and {remote}/{branch} have diverged "
                   f"(remote {theirs[:8]}, local {head[:8]}): a mind must reconcile them"]

    count, epochs, error = _unpushed(repo, theirs, head)
    if error:
        return 3, [f"cannot list unpushed commits: {error}"]

    code, out = git(repo, "push", "--quiet", remote, f"refs/heads/{branch}:refs/heads/{branch}")
    if code == 0:
        return 0, [f"pushed {count} unpushed commit(s) to {remote}/{branch}, now {head[:8]}"]

    # A hook-driven push may have won the race; the outcome, not this run's exit, decides.
    code2, theirs2 = _remote_main(repo)
    if code2 == 0 and (theirs2 == head or git(repo, "merge-base", "--is-ancestor", head, theirs2)[0] == 0):
        return 0, [f"{remote}/{branch} reached {head[:8]} by another publisher; this push found nothing left"]
    return 2, [f"push of {count} commit(s) failed: {_tail(out)}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="git checkout whose main is published")
    args = parser.parse_args(argv)
    code, lines = publish(Path(args.repo).resolve())
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
