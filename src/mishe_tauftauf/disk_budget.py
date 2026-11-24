"""Disk budget: observe the disks Mishe needs and keep what Mishe owns bounded.

Mishe needs two things from disk: free space on the root filesystem (the host guard trips
below 10 GiB and re-arms only at 12 GiB, so a plant that lets free space fall that far is
stopped by the host, 2026-11-24 16:14Z) and free space where its own probe and scratch
files live (``/var/tmp``). It also owns bytes: the checkout with the site home, of which
``releases/`` grows by one git worktree per delivered commit.

``read`` reports facts and limits; ``prune_releases`` is the one deterministic reclaim for
the part that grows without bound. Every other owner is only observed. The budget is a
detector with teeth for releases and an alarm for the rest: nothing else is deleted.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from pathlib import Path

from .state_files import read_json, write_json

GIB = 2 ** 30

ROOT_FREE_FLOOR = 30 * GIB
"""Alarm well above the guard's 10 GiB trip so a resolver has hours, not minutes."""

SCRATCH_FREE_FLOOR = 10 * GIB
OWNED_LIMIT = 64 * GIB
"""Ceiling for the checkout plus site home (51 GiB on 2026-11-24)."""

RELEASES_LIMIT = 2 * GIB
RELEASES_KEEP = 8
"""Unreferenced release worktrees kept beyond the live set; every name is a commit on main."""

PRUNE_BATCH = 40
"""Removals per run, so one run's IO stays small beside the guard's io_pressure gate."""

SCAN_TTL = 1800
"""A size scan walks millions of files; it is cached this long and run at idle IO priority."""

SCAN_TIMEOUT = 280


def _tree_bytes(path: Path, timeout: int = SCAN_TIMEOUT) -> int:
    """Allocated bytes of one tree on one filesystem (st_blocks, never following links).

    Raises ``OSError`` past the deadline or when the root is unreadable, so the caller reads UNKNOWN.
    Unreadable entries below the root are skipped, as ``du`` does; they cannot be owned bytes.
    """
    deadline = time.monotonic() + timeout
    root_dev = os.lstat(path).st_dev
    total = 0
    stack = [str(path)]
    while stack:
        if time.monotonic() > deadline:
            raise OSError(f"size scan of {path} passed {timeout}s")
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if st.st_dev != root_dev:
                        continue
                    total += st.st_blocks * 512
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except OSError:
            continue
    return total


def _scan(home: Path, now: float) -> dict:
    """Owned sizes, from the cache file when it is fresh."""
    cache = home / "samples" / "disk-budget.json"
    try:
        cached = read_json(cache)
    except ValueError:
        cached = None  # a corrupt cache is rescanned, never trusted
    if isinstance(cached, dict) and isinstance(cached.get("at"), (int, float)) and 0 <= now - cached["at"] < SCAN_TTL:
        return cached
    repo = home.parent
    fresh = {"at": now, "owned": _tree_bytes(repo), "releases": _tree_bytes(home / "releases")
             if (home / "releases").is_dir() else 0}
    write_json(cache, fresh)
    return fresh


def read(home, *, now: float, scratch: str = "/var/tmp") -> tuple[str, str]:
    """(GREEN|RED, reason). Raises when a reading cannot be taken, so the signal reads UNKNOWN."""
    home = Path(home)
    root_free = shutil.disk_usage("/").free
    scratch_free = shutil.disk_usage(scratch).free
    sizes = _scan(home, now)
    problems = []
    if root_free < ROOT_FREE_FLOOR:
        problems.append(f"root free {root_free / GIB:.0f} GiB < {ROOT_FREE_FLOOR // GIB} GiB "
                        f"(host guard trips at 10, re-arms at 12)")
    if scratch_free < SCRATCH_FREE_FLOOR:
        problems.append(f"{scratch} free {scratch_free / GIB:.0f} GiB < {SCRATCH_FREE_FLOOR // GIB} GiB")
    if sizes["owned"] > OWNED_LIMIT:
        problems.append(f"owned {sizes['owned'] / GIB:.0f} GiB > {OWNED_LIMIT // GIB} GiB")
    if sizes["releases"] > RELEASES_LIMIT:
        problems.append(f"releases {sizes['releases'] / GIB:.1f} GiB > {RELEASES_LIMIT // GIB} GiB "
                        f"(pruned beyond keep={RELEASES_KEEP})")
    detail = (f"root_free={root_free // GIB}GiB scratch_free={scratch_free // GIB}GiB "
              f"owned={sizes['owned'] // GIB}GiB releases={sizes['releases'] // GIB}GiB")
    return ("RED", "; ".join(problems) + " | " + detail) if problems else ("GREEN", detail)


def prune_releases(home, *, keep: int = RELEASES_KEEP, batch: int = PRUNE_BATCH, remove=None) -> list[str]:
    """Remove unreferenced release worktrees beyond the newest ``keep``; return the names removed.

    A reference is a live process environment, an installed unit, the site pin, or the target
    of ``runtime/current`` (the moving pin, protected by name here because a pruner that removed
    a pinned tree once failed every unit, 2026-10-29). ``git worktree remove`` without --force
    is the final authority and refuses a dirty or unregistered tree. Reversible exactly with
    ``git worktree add --detach releases/<sha> <sha>``.
    """
    from . import release_retention
    home = Path(home)
    protect = set()
    try:
        protect.add(Path(os.path.realpath(home / "runtime" / "current")).name)
    except OSError:
        pass
    candidates = release_retention.reclaimable_releases(home, protect=protect)
    if not candidates or (home / "runtime" / "current").exists() is False:
        # No readable moving pin means the protected set is incomplete: reclaim nothing.
        return []
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    doomed = candidates[keep:][-batch:]  # oldest first within the batch
    removed = []
    for path in doomed:
        if remove is not None:
            ok = remove(path)
        else:
            ok = subprocess.run(["nice", "-n", "19", "ionice", "-c", "3", "git", "-C", str(home.parent),
                                 "worktree", "remove", str(path)],
                                capture_output=True, text=True, timeout=120).returncode == 0
        if ok:
            removed.append(path.name)
    return removed


def run(home, *, now: float | None = None) -> tuple[str, str]:
    """Producer body: reclaim releases when over their limit, then report the budget."""
    now = time.time() if now is None else now
    home = Path(home)
    sizes = _scan(home, now)
    note = ""
    if sizes["releases"] > RELEASES_LIMIT:
        gone = prune_releases(home)
        if gone:
            cache = home / "samples" / "disk-budget.json"
            cache.unlink(missing_ok=True)  # the next read rescans
            note = f"pruned {len(gone)} releases | "
    value, reason = read(home, now=now)
    return value, note + reason
