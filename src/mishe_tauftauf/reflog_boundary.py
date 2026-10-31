"""Decode git's reflog and dangling-commit text for the reset-origin read.

The reset-origin sense asks which ``reset: moving to HEAD`` reflog entries a
dangling stash commit explains. Three git outputs carry that evidence as text:
``git reflog --date=unix --format=%H|%gd|%gs`` (one entry per line),
``git fsck --unreachable --no-reflogs`` (``unreachable commit <sha>`` lines) and
``git log --no-walk=unsorted --format=%H|%P|%ct|%s`` (one commit per line).
This module is the only place those lines are split. Callers receive decoded
Python values and never see the columns.

Malformed evidence is not guessed. A reset whose epoch cannot be read, an
unreachable line without a sha, or a stash whose timestamp is not digits makes
the whole decode return None, so the consumer reports the reading UNKNOWN
rather than a partial list of labels. Lines that are simply not the record
being asked for (other reflog subjects, non-stash commits, stash-less parents)
are skipped as ordinary git output.
"""
from __future__ import annotations

import re

RESET_SUBJECT = "reset: moving to HEAD"
UNREACHABLE_KIND = ["unreachable", "commit"]
_REFLOG_EPOCH = re.compile(r"\{(\d+)\}$")
_STASH_SUBJECT = re.compile(r"^(WIP on|On) ")


def decode_reflog_resets(text: str) -> list[tuple[int, str]] | None:
    """``(epoch, head_before_reset)`` per ``reset: moving to HEAD`` entry, newest first.

    Returns None when a matching entry's selector carries no ``{epoch}``.
    """
    resets: list[tuple[int, str]] = []
    for line in text.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3 or parts[2] != RESET_SUBJECT:
            continue
        epoch = _REFLOG_EPOCH.search(parts[1])
        if epoch is None:
            return None
        resets.append((int(epoch.group(1)), parts[0]))
    return resets


def decode_unreachable_commits(text: str) -> list[str] | None:
    """Shas named by ``git fsck`` as unreachable commits; None on a malformed line."""
    shas: list[str] = []
    for line in text.splitlines():
        fields = line.split()
        if fields[:2] != UNREACHABLE_KIND:
            continue
        if len(fields) != 3:
            return None
        shas.append(fields[2])
    return shas


def decode_stash_commits(text: str) -> list[tuple[int, str, str]] | None:
    """``(epoch, first_parent, sha)`` per stash-subjected commit; None on a malformed record.

    A stash is a commit whose subject starts ``WIP on`` or ``On`` and that has a
    first parent. The subject is the last field, so a ``|`` inside it is kept.
    """
    stashes: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4 or not _STASH_SUBJECT.match(parts[3]):
            continue
        parents = parts[1].split()
        if not parents:
            continue
        if not parts[2].isdigit():
            return None
        stashes.append((int(parts[2]), parents[0], parts[0]))
    return stashes
