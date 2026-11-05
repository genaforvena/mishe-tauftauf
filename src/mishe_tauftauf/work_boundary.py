"""Literal Git/CLI protocols for work facts; human walls are never parsed."""
from __future__ import annotations
import subprocess
from pathlib import Path


def git(repo, *args):
    if repo is None:
        raise ValueError("no repository configured")
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise ValueError(f"git {args[0]} exit {out.returncode}: {out.stderr[:300]}")
    return out.stdout


def head(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def dirty(repo):
    # -z preserves spaces and newlines in filenames. Include untracked files.
    return [x for x in git(repo, "ls-files", "-z", "--modified", "--others", "--exclude-standard").split("\0") if x]


def new_commits(repo, before, after, role, key):
    git(repo, "merge-base", "--is-ancestor", before, after)
    commits = git(repo, "rev-list", "--reverse", f"{before}..{after}").splitlines()
    own = []
    for sha in commits:
        text = git(repo, "show", "-s", "--format=%B", sha)
        trailers = subprocess.run(["git", "interpret-trailers", "--parse"], input=text,
                                 capture_output=True, text=True, check=True).stdout.splitlines()
        wakes = [x.removeprefix("Mishe-Wake: ") for x in trailers if x.startswith("Mishe-Wake: ")]
        roles = [x.removeprefix("Mishe-Role: ") for x in trailers if x.startswith("Mishe-Role: ")]
        if wakes == [key] and roles == [role]:
            own.append(sha)
    return commits, own


def wake_time(key):
    try:
        prefix, role, number = key.rsplit("-", 2)
        return int(number) / 1e9 if key.startswith("wake-") else None
    except (ValueError, TypeError):
        return None


def snapshot_key(path):
    return path.name.removesuffix(".snapshot.json")
