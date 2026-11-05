"""Literal Git/CLI protocols for work facts; human walls are never parsed."""
from __future__ import annotations
import subprocess
from pathlib import Path


def work_identifier(value):
    return isinstance(value, str) and bool(value) and value not in (".", "..") and set(value) <= set("abcdefghijklmnopqrstuvwxyz0123456789-_.")


def failure_signature(unit):
    """Only stable systemd failure classes, never timestamps or journal prose."""
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit, "-p",
                                 "Result,ExecMainCode,ExecMainStatus"], capture_output=True, text=True, timeout=10)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        fields = ("Result", "ExecMainCode", "ExecMainStatus")
        if result.returncode or any(not values.get(f) for f in fields):
            return "UNKNOWN-systemd-failure-class"
        return "|".join(f + "=" + values[f] for f in fields)
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN-systemd-failure-class"


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


def full_sha(repo, value):
    import re
    if not re.fullmatch("[0-9a-f]{40}", value): raise ValueError("release needs a full commit SHA")
    resolved = git(repo, "rev-parse", value + "^{commit}").strip()
    if resolved != value: raise ValueError("release SHA mismatch")
    return value


def changed_paths(repo, before, after):
    return [p for p in git(repo, "diff", "--name-only", "-z", before, after).split("\0") if p]


def argv_file(path):
    from .state_files import read_json
    value = read_json(path)
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x and "\0" not in x for x in value):
        raise ValueError("command file needs a nonempty JSON argv array")
    return value



def clean_tree(repo):
    return not git(repo, "status", "--porcelain").strip()


def release_classification(paths):
    return "ordinary" if paths and all((p.startswith("docs/") and p.endswith(".md")) or
        p.startswith("tests/") or p == "src/mishe_tauftauf/producers.py" for p in paths) else "strong"


def tree_files(repo):
    return [x for x in git(repo,"ls-files","-z","--cached","--others","--exclude-standard").split("\0") if x]



def trial_args():
    import argparse
    parser=argparse.ArgumentParser(description="Deterministic useful-work checkpoint producer")
    parser.add_argument("--home",type=Path,required=True)
    return parser.parse_args()


def invoke_arm_consumer(expected):
    """Exercise a future oneshot invocation; never restart an active worker."""
    unit = "mishe-arm.service"
    def prop(name):
        return subprocess.check_output(["systemctl", "--user", "show", unit, "-p", name, "--value"], text=True).strip()
    if prop("ActiveState") not in ("inactive", "failed"):
        raise ValueError("arm consumer already active; cannot prove a fresh invocation")
    if "PYTHONPATH=" + str(expected) not in prop("Environment"):
        raise ValueError("arm consumer does not use intended release")
    before = prop("ExecMainStartTimestampMonotonic")
    subprocess.run(["systemctl", "--user", "start", unit], check=True, capture_output=True, timeout=60)
    after = prop("ExecMainStartTimestampMonotonic")
    if not after.isdecimal() or int(after) <= int(before or "0") or prop("Result") != "success" or prop("ExecMainStatus") != "0":
        raise ValueError("fresh arm consumer invocation was not observed successful")
    return dict(unit=unit, started_monotonic=after, pythonpath=str(expected), result="success")
