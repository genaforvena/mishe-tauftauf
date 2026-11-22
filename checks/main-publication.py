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
import time
from pathlib import Path

REMOTE = "origin"
BRANCH = "main"

# A local git call that blocks this long is a stalled filesystem, not work: on 2026-11-16 the fsync
# stall made every `rev-parse` as slow as a network call. The duty's timeout used to be hand-computed
# from the three network calls only (240 s), so a run whose nine local calls also stalled was killed
# mid-flight and became UNKNOWN with no reason recorded.
LOCAL_TIMEOUT = 15.0
NETWORK_TIMEOUT = 60.0
# The whole run, enforced here so the duty never has to kill the check silently: every call's timeout
# is clamped to what is left, and a past deadline is reported as undecidable naming the step.
BUDGET = 240.0


def git(repo: Path, *args: str, timeout: float = LOCAL_TIMEOUT,
        deadline: float | None = None) -> tuple[int, str]:
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 127, f"time budget exhausted before git {args[0]}"
        timeout = min(timeout, remaining)
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


def _remote_main(repo: Path, deadline: float | None = None) -> tuple[int, str]:
    """Fetch, then the remote main's sha as this clone knows it."""
    code, out = git(repo, "fetch", "--quiet", REMOTE, f"refs/heads/{BRANCH}",
                    timeout=NETWORK_TIMEOUT, deadline=deadline)
    # A fetch that timed out (127: subprocess timeout) is a dropped connection, not an answer. On
    # 2026-11-22 one 60 s timeout at 15:08Z failed the service RED while the next fetch succeeded in
    # under a second. Retry once while the run's budget allows; any other git failure is reported.
    if code == 127 and "timed out" in out:
        code, out = git(repo, "fetch", "--quiet", REMOTE, f"refs/heads/{BRANCH}",
                        timeout=NETWORK_TIMEOUT, deadline=deadline)
    if code != 0:
        return 3, _tail(out)
    code, out = git(repo, "rev-parse", "--verify", f"refs/remotes/{REMOTE}/{BRANCH}", deadline=deadline)
    if code != 0:
        return 3, _tail(out)
    if not out:
        return 3, f"no {REMOTE}/{BRANCH} in this clone after fetching"
    return 0, out.split()[0]


def _unpushed(repo: Path, theirs: str, head: str, deadline: float | None = None) -> tuple[int, list[str], str]:
    """(count, commit epochs oldest first, error) for theirs..head."""
    code, rows = git(repo, "log", f"{theirs}..{head}", "--format=%ct", deadline=deadline)
    if code != 0:
        return 0, [], _tail(rows)
    epochs = [int(r) for r in rows.split() if r.strip()]
    return len(epochs), sorted(epochs), ""


def publish(repo: Path, remote: str = REMOTE, branch: str = BRANCH,
            deadline: float | None = None) -> tuple[int, list[str]]:
    if deadline is None:
        deadline = time.monotonic() + BUDGET
    branch_code, current = git(repo, "symbolic-ref", "--short", "-q", "HEAD", deadline=deadline)
    # 1 is a detached HEAD; anything else means the clone could not be read at all.
    if branch_code not in (0, 1):
        return 3, [f"cannot read the current branch: {_tail(current, 120)}"]
    if current != branch:
        return 0, [f"HEAD is {current or 'detached'}, not {branch}: nothing to publish"]

    code, head = git(repo, "rev-parse", "HEAD", deadline=deadline)
    if code != 0:
        return 3, [f"cannot read local HEAD: {_tail(head, 120)}"]
    head = head.split()[0]

    code, theirs = _remote_main(repo, deadline)
    if code != 0:
        return 3, [f"cannot read {remote}/{branch}: {theirs}"]

    if theirs == head:
        return 0, [f"{remote}/{branch} already holds HEAD {head[:8]}"]

    have = git(repo, "cat-file", "-e", f"{theirs}^{{commit}}", deadline=deadline)
    if have[0] == 1:
        return 3, [f"{remote}/{branch} is {theirs[:8]}, which this clone does not have: fetch before judging"]
    if have[0] != 0:
        return 3, [f"cannot read {theirs[:8]} in this clone: {_tail(have[1], 120)}"]

    behind = git(repo, "merge-base", "--is-ancestor", head, theirs, deadline=deadline)
    if behind[0] == 0:
        count = _unpushed(repo, head, theirs, deadline)[0]
        return 2, [f"local {branch} is {count} commit(s) behind {remote}/{branch} ({theirs[:8]}): "
                   f"fast-forward before publishing"]
    if behind[0] != 1:
        return 3, [f"cannot compare local {branch} with {remote}/{branch}: {_tail(behind[1], 120)}"]

    ahead = git(repo, "merge-base", "--is-ancestor", theirs, head, deadline=deadline)
    if ahead[0] != 0:
        if ahead[0] != 1:
            return 3, [f"cannot compare local {branch} with {remote}/{branch}: {_tail(ahead[1], 120)}"]
        return 2, [f"local {branch} and {remote}/{branch} have diverged "
                   f"(remote {theirs[:8]}, local {head[:8]}): a mind must reconcile them"]

    count, epochs, error = _unpushed(repo, theirs, head, deadline)
    if error:
        return 3, [f"cannot list unpushed commits: {error}"]

    code, out = git(repo, "push", "--quiet", remote, f"refs/heads/{branch}:refs/heads/{branch}",
                    timeout=NETWORK_TIMEOUT, deadline=deadline)
    if code == 0:
        return 0, [f"pushed {count} unpushed commit(s) to {remote}/{branch}, now {head[:8]}"]

    # A hook-driven push may have won the race; the outcome, not this run's exit, decides.
    code2, theirs2 = _remote_main(repo, deadline)
    if code2 == 0:
        won = 0 if theirs2 == head else git(repo, "merge-base", "--is-ancestor", head, theirs2,
                                            deadline=deadline)[0]
        if won == 0:
            return 0, [f"{remote}/{branch} reached {head[:8]} by another publisher; this push found nothing left"]
        if won != 1:
            return 3, [f"cannot tell whether the push landed: {_tail(out)}"]
    if code == 127 or code2 != 0:
        return 3, [f"cannot tell whether the push landed: {_tail(out)}"]
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
