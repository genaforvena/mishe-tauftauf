"""Durable draft accounting. Audit never changes Git, the index, or the feed."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE,
                                   env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})


@contextmanager
def _ledger(home, repo):
    directory = home / "landing-debt"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.fsencode(repo.resolve())).hexdigest()
    path = directory / (key + ".json")
    with (directory / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {"observed": {}, "claims": {}}
        yield data
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
        temporary.replace(path)


def _dirty(repo, home=None):
    raw = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    result = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        status = record[:2].decode("ascii")
        name = os.fsdecode(record[3:])
        path = repo / name
        if home is not None and path.absolute().is_relative_to(home.resolve()):
            continue
        # Hash both planes: staging different bytes must invalidate an old claim.
        mode = path.lstat().st_mode if path.exists() or path.is_symlink() else 0
        contents = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes() if path.is_file() else b""
        signature = hashlib.sha256(status.encode() + str(mode).encode() + contents +
                                   _git(repo, "ls-files", "-s", "--", name)).hexdigest()
        result[name] = {"path": name, "status": status, "signature": signature}
    return result


def claim(home: Path, repo: Path, task: str, owner: str, next_step: str, paths: list[str], *, open_tasks=None):
    from .task_state import _event
    from .observations import validate_slug
    _event(task)
    validate_slug(owner)
    if not next_step.strip() or not paths:
        raise ValueError("claim requires paths and an actionable next step")
    with _ledger(home, repo) as data:
        dirty = _dirty(repo, home)
        for path in paths:
            if path not in dirty:
                raise ValueError(f"not a dirty repository-relative path: {path}")
            previous = data["claims"].get(path)
            if previous and (previous["task"], previous["owner"]) != (task, owner) and (
                    open_tasks is None or previous["task"] in open_tasks):
                raise ValueError(f"claim conflict: {path} belongs to {previous['owner']}:{previous['task']}")
        for path in paths:
            data["claims"][path] = dict(task=task, owner=owner, next_step=next_step,
                                       signature=dirty[path]["signature"])


def _published(repo, row):
    name = row["path"]
    remote = _git(repo, "ls-tree", "origin/main", "--", name).strip()
    path = repo / name
    index = _git(repo, "ls-files", "-s", "--", name).strip()
    head = _git(repo, "ls-tree", "HEAD", "--", name).strip()
    records = [r.split(b"\t", 1)[0].split() for r in index.splitlines()]
    if any(r[2] != b"0" for r in records) or len(records) > 1:
        return False
    indexed = tuple(records[0][:2]) if records else None
    headed = tuple(head.split(b"\t", 1)[0].split()[::2]) if head else None
    if not remote:
        return "D" in row["status"] and not path.exists() and indexed in {headed, None}
    mode, kind, blob = remote.split(b"\t", 1)[0].split()
    if kind != b"blob" or not (path.is_file() or path.is_symlink()):
        return False
    actual_mode = b"120000" if path.is_symlink() else b"100755" if path.stat().st_mode & 0o111 else b"100644"
    contents = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
    actual_blob = subprocess.check_output(["git", "-C", str(repo), "hash-object", "--stdin"],
                                          input=contents, stderr=subprocess.PIPE).strip()
    if actual_mode != mode or actual_blob != blob:
        return False
    # A distinct staged draft remains unresolved even if the working bytes landed.
    return indexed in {(mode, blob), headed} if indexed else headed is None


def audit(home: Path, repo: Path, *, now=None, stale_hours=24, open_tasks=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    with _ledger(home, repo) as data:
        dirty = _dirty(repo, home)
        ahead, behind = map(int, _git(repo, "rev-list", "--left-right", "--count", "HEAD...origin/main").split()) if _has_origin(repo) else (None, None)
        rows = []
        for name, row in dirty.items():
            prior = data["observed"].get(name, {"first_seen": stamp, "signature": row["signature"]})
            first_seen = prior["first_seen"]
            stale = (now - datetime.fromisoformat(first_seen)).total_seconds() >= stale_hours * 3600
            claimed = data["claims"].get(name, {})
            reason = "unclaimed" if not claimed else "claim bytes changed" if claimed["signature"] != row["signature"] else "task closed or absent" if open_tasks is not None and claimed["task"] not in open_tasks else "stale" if stale else "owned draft"
            rows.append({**row, **{k: v for k, v in claimed.items() if k != "signature"},
                         "first_seen": first_seen, "stale": stale, "reason": reason,
                         "published": _published(repo, row) if behind is not None else False})
        data["observed"] = {r["path"]: {"first_seen": r["first_seen"], "signature": r["signature"]} for r in rows}
        data["claims"] = {p: c for p, c in data["claims"].items() if p in dirty}
        rows.sort(key=lambda r: (r["first_seen"], r["path"]))
        state = "UNKNOWN" if behind is None else "RED" if any(r["reason"] != "owned draft" or r["published"] for r in rows) else "AMBER" if rows else "GREEN"
        report = dict(state=state, repo=str(repo.resolve()), ahead=ahead, behind=behind, paths=rows)
        data["report"] = report
        return report


def _has_origin(repo):
    try:
        _git(repo, "rev-parse", "--verify", "origin/main")
        return True
    except subprocess.CalledProcessError:
        return False


def lines(report):
    rows = report["paths"]
    yield f"LANDING DEBT: {report['state']} — {len(rows)} dirty paths; behind origin/main={report['behind']}; stale={sum(r['stale'] for r in rows)}; already on origin={sum(r['published'] for r in rows)}"
    if report.get("intake"):
        yield f"DEBT INTAKE: {report['intake']} owner=genome; assess need, adopt/test/land or reconcile/retire with evidence"
    for row in rows[:5]:
        owner = f"{row.get('owner', 'UNKNOWN')}:{row.get('task', 'unassigned')}"
        reason = "already on origin; reconcile draft" if row["published"] else row["reason"]
        yield f"DEBT {row['path']!r}: {reason}; owner={owner}; first observed={row['first_seen']}; next={row.get('next_step', 'genome reconcile ownership and prepare isolated candidate')}"
    if len(rows) > 5:
        yield "DEBT full path evidence: site/landing-debt/*.json (repo identity in report)"


def intake(home: Path, report: dict):
    """Discover outside edits as owned work, without touching their source bytes."""
    from .feed import Feed
    from . import task_state
    if report["state"] == "UNKNOWN":
        return None
    rows = [r for r in report["paths"] if r["reason"] != "owned draft" or r["published"]]
    if not rows:
        return None
    repo = Path(report["repo"])
    identity = "genome-draft-intake-" + hashlib.sha256(os.fsencode(repo)).hexdigest()[:12]
    fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    next_step = ("Inspect the captured outside/stale draft diff against current origin; assess whether each "
                 "change is needed. Adopt useful bytes into an isolated candidate and test/review/land; "
                 "reconcile already-published bytes; archive and retire superseded bytes with evidence. "
                 "Coordinate existing owners, preserve concurrent edits, and record a disposition for every path.")
    with _ledger(home, repo) as data:
        tasks = task_state.registry(Feed(home).entries())
        old = tasks.get(identity)
        artifact = home / "artifacts" / f"{identity}-{fingerprint[:16]}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if not artifact.exists():
            artifact.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        if old is not None and old.status not in {"done", "dropped"}:
            previous = data.get("intake_fingerprint")
            active = identity in task_state.pending_tasks(Feed(home).entries()).values()
            if previous != fingerprint and not active and old.evidence_sha256 != hashlib.sha256(artifact.read_bytes()).hexdigest():
                task_state.set_step(home, identity, "genome", next_step,
                                    f"New source debt snapshot {fingerprint[:16]} requires fresh assessment; compare prior disposition.", artifact)
            if not active:
                data["intake_fingerprint"] = fingerprint
            return identity
        reason = "Discovered source debt requires assessment, including edits made outside mesh. No source bytes were changed."
        if old is None:
            task_state.add_task(home, identity, "genome", next_step, reason, artifact)
        else:
            task_state.reopen(home, identity, "genome", next_step,
                              "Closed intake still has unresolved draft bytes; reconcile before completion.", artifact)
        data["intake"] = identity
        data["intake_fingerprint"] = fingerprint
        return identity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("audit")
    check.add_argument("--json", action="store_true")
    check.add_argument("--intake", action="store_true", help="route discovered debt to genome once")
    check.add_argument("--stale-hours", type=float, default=24)
    own = commands.add_parser("claim")
    own.add_argument("--task", required=True)
    own.add_argument("--owner", required=True)
    own.add_argument("--next-step", required=True)
    own.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    try:
        from .feed import Feed
        from .task_state import states
        tasks = states(Feed(args.home).entries())
        if args.command == "claim":
            if args.task not in tasks or tasks[args.task].owner != args.owner:
                raise ValueError("claim requires an open task owned by this role")
            claim(args.home, args.repo, args.task, args.owner, args.next_step, args.paths, open_tasks=tasks)
            print(f"Claimed {len(args.paths)} paths for {args.owner}:{args.task}")
            return 0
        if args.stale_hours <= 0:
            raise ValueError("stale-hours must be positive")
        report = audit(args.home, args.repo, stale_hours=args.stale_hours, open_tasks=set(tasks))
        if args.intake:
            report["intake"] = intake(args.home, report)
        print(json.dumps(report, indent=2) if args.json else "\n".join(lines(report)))
        return 0 if report["state"] in {"GREEN", "AMBER"} else 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"LANDING DEBT: UNKNOWN — {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
