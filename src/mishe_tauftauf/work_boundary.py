"""Literal Git/CLI protocols for work facts; human walls are never parsed."""
from __future__ import annotations
import calendar
import subprocess
import time
from pathlib import Path

#: The exact paths whose changes cannot reach a mechanism. Each one is
#: admitted by evidence, recorded in the plan's classification inventory.
#: ``README.md`` is the reader's front page: the runtime names it as a
#: default document to render and as a search corpus, never as behaviour.
ORDINARY_PATHS = frozenset({"README.md", "src/mishe_tauftauf/producers.py"})


def work_identifier(value):
    return isinstance(value, str) and bool(value) and value not in (".", "..") and set(value) <= set("abcdefghijklmnopqrstuvwxyz0123456789-_.")


def duty_word(word, *, home, repo, source, python):
    # Executable repository paths come from the committed release; an exact
    # {repo} argument is the shared repository whose state is being observed.
    if word.startswith("{repo}/"):
        word = str(source) + word[len("{repo}"):]
    return word.replace("{home}", str(home)).replace("{repo}", str(repo)).replace("{python}", str(python))


def runtime_source(repo, pythonpath):
    import os
    if os.pathsep in str(pythonpath):
        raise ValueError("generated consumers require a single runtime PYTHONPATH")
    root = Path(pythonpath).resolve().parent
    # Full committed releases carry their own manifest. Legacy partial
    # snapshots retain their historical caller behavior until promoted.
    return root if (root/"duties.json").is_file() else Path(repo).resolve()


def unit_load_state(unit):
    """systemd's LoadState for ``unit``: loaded, not-found, ... or UNKNOWN when systemctl cannot say."""
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit, "-p", "LoadState", "--value"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else "UNKNOWN"


def unit_last_exit(unit):
    """``(exited_at, clean)`` for the unit's latest exit: epoch seconds and whether it exited 0 with Result=success.
    None when systemctl cannot say or the exit time is absent (no exit yet, or unreadable: never a clean exit).

    systemd clears ``ExecMainExitTimestamp`` as soon as the next start is queued (``activating``), so a unit waiting
    behind dispatch read as "never exited" and its RED signal could not clear (2026-11-09: genome, steward, senses).
    ``InactiveEnterTimestamp`` keeps the last exit through that queued start, so it is the fallback.
    """
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit, "-p",
                                 "Result,ExecMainStatus,ExecMainExitTimestamp,InactiveEnterTimestamp"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    stamp = (values.get("ExecMainExitTimestamp", "") or values.get("InactiveEnterTimestamp", "")).strip()
    if not stamp:
        return None
    # systemd prints the exit in local time, e.g. "Sun 2026-11-08 00:21:39 UTC".
    try:
        parsed = time.strptime(stamp, "%a %Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return None
    exited_at = calendar.timegm(parsed) if parsed.tm_zone in ("UTC", "GMT") else time.mktime(parsed)
    return exited_at, values.get("Result") == "success" and values.get("ExecMainStatus") == "0"


def failure_signal(name):
    """True for a failure incident's signal name (``failed-...``); other signals are not failure incidents."""
    return name.startswith("failed-")


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


def commit_time(repo, sha):
    return int(git(repo, "show", "-s", "--format=%ct", sha).strip())


def dirty(repo):
    # -z preserves spaces and newlines in filenames. Include untracked files.
    unstaged = git(repo, "ls-files", "-z", "--modified", "--others", "--exclude-standard")
    staged = git(repo, "diff", "--cached", "--name-only", "-z", "HEAD")
    return sorted(set(x for x in (unstaged + staged).split("\0") if x))


def index_entries(repo):
    entries = {}
    for item in git(repo, "ls-files", "--stage", "-z").split("\0"):
        if not item:
            continue
        metadata, name = item.split("\t", 1)
        mode, blob, stage = metadata.split()
        entries.setdefault(name, []).append(dict(mode=mode, blob=blob, stage=stage))
    return entries


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


def commit_role(repo, sha):
    text = git(repo, "show", "-s", "--format=%B", sha)
    result = subprocess.run(["git", "interpret-trailers", "--parse"], input=text,
                            capture_output=True, text=True, check=True)
    lines = result.stdout.splitlines()
    roles = [x.removeprefix("Mishe-Role: ") for x in lines if x.startswith("Mishe-Role: ")]
    wakes = [x.removeprefix("Mishe-Wake: ") for x in lines if x.startswith("Mishe-Wake: ")]
    return roles[0] if len(roles) == len(wakes) == 1 else None


def wake_time(key):
    try:
        prefix, role, number = key.rsplit("-", 2)
        return int(number) / 1e9 if key.startswith("wake-") else None
    except (ValueError, TypeError):
        return None


def snapshot_key(path):
    return path.name.removesuffix(".snapshot.json")


def work_start_key(path):
    return path.name.removesuffix(".work-start.json")


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
    """Ordinary means the diff cannot reach a mechanism.

    A path is ordinary only when the evidence shows it carries no
    scheduling, dispatch, settlement, locking, protocol, authorization,
    delivery, bootstrap, observation-truth or budget behaviour. Everything
    outside the admitted list is strong, and an empty diff is strong: there
    is no author-supplied flag and no per-path exemption, so a mixed diff
    takes the strong path.

    ``README.md`` is admitted by exact path, not by extension: it is the
    reader's front page. The runtime names it as a default document to
    render and as a search corpus, and never reads it to decide behaviour.
    """
    return "ordinary" if paths and all((p.startswith("docs/") and p.endswith(".md")) or
        p.startswith("tests/") or p in ORDINARY_PATHS for p in paths) else "strong"


def tree_files(repo):
    return [x for x in git(repo,"ls-files","-z","--cached","--others","--exclude-standard").split("\0") if x]



def trial_args():
    import argparse
    parser=argparse.ArgumentParser(description="Deterministic useful-work checkpoint producer")
    parser.add_argument("--home",type=Path,required=True)
    return parser.parse_args()


def pythonpath_entries(environment_text):
    """PYTHONPATH values in a ``systemctl show -p Environment`` line (external text, decoded at this boundary)."""
    return [word[len("PYTHONPATH="):] for word in str(environment_text).split() if word.startswith("PYTHONPATH=")]


def invoke_arm_consumer(expected):
    """Exercise a future oneshot invocation; never restart an active worker."""
    unit = "mishe-arm.service"
    def prop(name):
        return subprocess.check_output(["systemctl", "--user", "show", unit, "-p", name, "--value"], text=True).strip()
    if prop("ActiveState") not in ("inactive", "failed"):
        raise ValueError("arm consumer already active; cannot prove a fresh invocation")
    # The unit names runtime/current (a symlink), the caller names the release: compare the real directories.
    wanted = Path(expected).resolve()
    if not any(Path(v).resolve() == wanted for v in pythonpath_entries(prop("Environment"))):
        raise ValueError("arm consumer does not use intended release")
    before = prop("ExecMainStartTimestampMonotonic")
    subprocess.run(["systemctl", "--user", "start", unit], check=True, capture_output=True, timeout=60)
    after = prop("ExecMainStartTimestampMonotonic")
    if not after.isdecimal() or int(after) <= int(before or "0") or prop("Result") != "success" or prop("ExecMainStatus") != "0":
        raise ValueError("fresh arm consumer invocation was not observed successful")
    return dict(unit=unit, started_monotonic=after, pythonpath=str(expected), result="success")
