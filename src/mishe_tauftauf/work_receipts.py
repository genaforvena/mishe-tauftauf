"""Factual wake output, distinct from the minds self-reported yield result."""
from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path
from . import work_boundary as boundary
from .state_files import read_json, write_json


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprints(repo):
    return {name: digest(Path(repo) / name) if (Path(repo) / name).is_file() else "ABSENT"
            for name in boundary.dirty(repo)}


def begin(repo, *, role, key, now):
    result = dict(version=1, wake_key=key, role=role, started_at=now, head_start=None,
                  dirty_start={}, evidence_state="KNOWN", repo=str(repo) if repo else None)
    try:
        result.update(head_start=boundary.head(repo), dirty_start=fingerprints(repo))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result.update(evidence_state="UNKNOWN", error=str(exc))
    return result


def finish(start, *, repo, now, claimed_result=None, artifacts=()):
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
    result = finish(start, repo=start["repo"], now=now, claimed_result=receipt.get("result"), artifacts=refs)
    if any(not artifact_matches(home, a) for a in refs):
        result.update(evidence_state="UNKNOWN", classification="awaiting-evidence", error="artifact changed after settlement")
    result["engine_seconds"] = engine_seconds
    write_json(root / f"{key}.work.json", result)
