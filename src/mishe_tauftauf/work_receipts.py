"""Factual wake output, distinct from the minds self-reported yield result."""
from __future__ import annotations
import hashlib
import os
import time
import subprocess
from pathlib import Path
from . import work_boundary as boundary
from .state_files import read_json, write_json


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprints(repo):
    index = boundary.index_entries(repo)
    result = {}
    for name in boundary.dirty(repo):
        path = Path(repo) / name
        working = ("SYMLINK-ESCAPE" if not path.resolve().is_relative_to(Path(repo).resolve()) else
                   digest(path) if path.is_file() else "ABSENT")
        result[name] = dict(working=working, index=index.get(name, []))
    return result


def begin(repo, *, role, key, now, home=None):
    result = dict(version=1, wake_key=key, role=role, started_at=now, head_start=None,
                  dirty_start={}, evidence_state="KNOWN", repo=str(repo) if repo else None)
    try:
        result.update(head_start=boundary.head(repo), dirty_start=fingerprints(repo))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result.update(evidence_state="UNKNOWN", error=str(exc))
    if home is not None:
        from . import work_state
        result["work_start"] = work_state.load(home, role)
    return result


def finish(start, *, repo, now, claimed_result=None, artifacts=(), home=None):
    result = dict(start, ended_at=now, claimed_result=claimed_result, head_end=None,
                  commits_observed=[], commits_attributed=[], artifacts=list(artifacts))
    try:
        if start["evidence_state"] != "KNOWN":
            raise ValueError(start.get("error", "missing start evidence"))
        result["head_end"] = boundary.head(repo)
        observed, own = boundary.new_commits(repo, start["head_start"], result["head_end"],
                                             start["role"], start["wake_key"])
        result.update(commits_observed=observed, commits_attributed=own, dirty_end=fingerprints(repo))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result.update(evidence_state="UNKNOWN", error=str(exc))
    result["classification"] = ("awaiting-evidence" if result["evidence_state"] != "KNOWN" else
                                "commit-attributed" if result["commits_attributed"] else
                                "commit-observed" if result["commits_observed"] else
                                "artifact-recorded" if artifacts else "bookkeeping")
    result["unchanged_wait"] = None
    if home is not None:
        from . import work_state
        before = start.get("work_start")
        after = work_state.load(home, start["role"])
        if before and after and before.get("state") != "UNKNOWN" and after.get("state") != "UNKNOWN":
            evaluations = [work_state.evaluate_wait(home, t["wait"], repo=repo, now=now)
                           for t in after["tasks"] if t["state"] == "waiting"]
            result["unchanged_wait"] = bool(claimed_result == "blocked" and
                work_state.all_waiting(before) and work_state.all_waiting(after) and
                before["tasks"] == after["tasks"] and evaluations and
                all(e["state"] == "unchanged" for e in evaluations) and
                result["classification"] == "bookkeeping")
            if any(e["state"] == "unknown" for e in evaluations):
                result["unchanged_wait"] = None
    return result


def artifact(home, path):
    path, home = Path(path).resolve(), Path(home).resolve()
    if not path.is_relative_to(home) or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("artifact must be an existing nonempty file under SITE_HOME")
    return dict(path=str(path.relative_to(home)), size=path.stat().st_size, sha256=digest(path))


def artifact_matches(home, fact):
    try:
        return artifact(home, Path(home) / fact["path"]) == fact
    except (ValueError, OSError):
        return False


def complete(home, key, *, now, engine_seconds=None):
    root = Path(home) / "wakes"
    start = read_json(root / f"{key}.work-start.json")
    if start is None:
        return
    receipt = read_json(root / f"{key}.yield.json") or {}
    refs = receipt.get("artifacts", [])
    result = finish(start, repo=start["repo"], home=home, now=now, claimed_result=receipt.get("result"), artifacts=refs)
    if any(not artifact_matches(home, a) for a in refs):
        result.update(evidence_state="UNKNOWN", classification="awaiting-evidence", error="artifact changed after settlement")
    result["engine_seconds"] = engine_seconds
    checks = []
    for path in root.glob(f"{key}.check-*.json"):
        try:
            check = read_json(path)
            if isinstance(check, dict) and check.get("wake_key") == key and check.get("role") == start["role"]:
                checks.append(check)
        except (OSError, ValueError):
            result.setdefault("unreadable_checks", []).append(str(path))
    result["check_latencies"] = []
    if result.get("head_end") in result.get("commits_attributed", []):
        result["check_latencies"] = bound_check_latency(start["repo"], result["head_end"], checks)
    write_json(root / f"{key}.work.json", result)


def tree_digest(repo):
    value=hashlib.sha256()
    for name in sorted(set(boundary.tree_files(repo))):
        path=Path(repo)/name
        value.update(os.fsencode(name)); value.update(b"\0")
        if path.is_symlink():
            value.update(b"SYMLINK\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            value.update(str(path.stat().st_mode & 0o777).encode("ascii") + b"\0" + path.read_bytes())
        else:
            value.update(b"ABSENT")
        value.update(b"\0")
    return value.hexdigest()


def check(repo, argv, *, now):
    before=tree_digest(repo)
    result=subprocess.run(argv,cwd=repo,capture_output=True,text=True,timeout=600)
    after=tree_digest(repo)
    return dict(version=1,kind="work-check",argv=argv,code=result.returncode,at=now,finished_at=time.time(),head=boundary.head(repo),tree_digest=before,
                bound=before==after,output=result.stdout+result.stderr)


def check_matches(repo, record):
    return record.get("code")==0 and record.get("bound") is True and tree_digest(repo)==record["tree_digest"]


def bound_check_latency(repo, sha, checks):
    """Bind one committed tree; Git's one-second timestamp gives an interval."""
    if not checks:
        return []
    try:
        if boundary.head(repo) != sha or not boundary.clean_tree(repo):
            return []
        stamp = boundary.commit_time(repo, sha)
        candidates = [c for c in checks if c.get("kind") == "work-check" and
                      isinstance(c.get("finished_at"), (int, float)) and c.get("head") != sha and
                      c["finished_at"] <= stamp + 1 and check_matches(repo, c)]
        if not candidates:
            return []
        check = min(candidates, key=lambda c: c["finished_at"])
        return [dict(commit=sha, seconds_lower=max(0, stamp-check["finished_at"]),
                     seconds_upper=max(0, stamp+1-check["finished_at"]), check_at=check["finished_at"],
                     argv=check["argv"], tree_digest=check["tree_digest"], precision_seconds=1)]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return []
