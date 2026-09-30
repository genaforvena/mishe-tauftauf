"""Host-side registry and checked release refresh for linked plants."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from mishe_tauftauf.ci_watch import latest, read
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.observations import FOOTER_LEASE_RE
from mishe_tauftauf.tmux import _tmux, owns_session

KERNEL = Path(__file__).resolve().parents[1]
ROLES = ("genome", "witness", "discover", "senses", "health", "permissions")


def resident_home() -> Path | None:
    homes = [KERNEL / name for name in (".mishe-seed", ".mishe-tauftauf")
             if (KERNEL / name / ".seed-raised").is_file()]
    if len(homes) > 1:
        raise RuntimeError("multiple core resident sites; choose one before registering linked plants")
    return homes[0] if homes else None


def _registry(home: Path) -> Path:
    return home / "health" / "linked-sites.json"


def _load(home: Path) -> list[dict[str, str]]:
    path = _registry(home)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("sites"), list):
        raise RuntimeError(f"invalid linked-site registry: {path}")
    sites = data["sites"]
    if any(not isinstance(row, dict) or
           not all(isinstance(row.get(key), str) for key in ("home", "session", "sha"))
           for row in sites):
        raise RuntimeError(f"invalid linked-site record: {path}")
    return sites


def _save(home: Path, sites: list[dict[str, str]]) -> None:
    path = _registry(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".linked-sites.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"version": 1, "sites": sites}, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _update_site(core_home: Path, target_home: Path, **changes: str | None) -> None:
    path = _registry(core_home)
    with (path.parent / ".linked-sites.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        sites = _load(core_home)
        for row in sites:
            if row["home"] == str(target_home):
                for key, value in changes.items():
                    if value is None:
                        row.pop(key, None)
                    else:
                        row[key] = value
                _save(core_home, sites)
                return
        raise RuntimeError(f"linked site disappeared from registry: {target_home}")


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed in {workspace}")
    return result.stdout.strip()


def register_site(target_home: Path, session: str) -> bool:
    """Record a successfully planted external site in the core's ignored registry."""
    core_home = resident_home()
    if core_home is None or target_home.parent.resolve() == KERNEL:
        return False
    target_home = target_home.resolve()
    path = _registry(core_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".linked-sites.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        sites = _load(core_home)
        sha = _git(KERNEL, "rev-parse", "HEAD") if not _git(KERNEL, "status", "--porcelain") else ""
        record = {"home": str(target_home), "session": session, "sha": sha}
        sites = [row for row in sites if row["home"] != str(target_home)] + [record]
        _save(core_home, sites)
    return True


def _target_state(home: Path, session: str, *, runtime_only: bool = False) -> None:
    workspace = home.parent
    if home.resolve() != home or workspace.resolve() == KERNEL or _git(workspace, "rev-parse", "--show-toplevel") != str(workspace):
        raise RuntimeError(f"linked site is not a separate direct Git worktree: {home}")
    marker = home / ".seed-raised"
    marker_words = marker.read_text(encoding="utf-8").split() if marker.is_file() else []
    if not marker_words or marker_words[0] != session:
        raise RuntimeError(f"linked site marker does not match {session}: {home}")
    if not owns_session(home, session):
        raise RuntimeError(f"tmux session is not owned by linked site: {session}")
    dirty = _git(workspace, "status", "--porcelain")
    if dirty and not runtime_only:
        raise RuntimeError(f"linked worktree has unlanded changes: {workspace}; {dirty.splitlines()[0]}")


def _verify(home: Path, session: str) -> None:
    expected = set(json.loads((home / "health" / "windows.json").read_text(encoding="utf-8")))
    result = _tmux("list-panes", "-s", "-t", session,
                   "-F", "#{window_name} #{pane_index} #{pane_dead}")
    panes = {}
    for row in result.stdout.decode().splitlines():
        name, index, dead = row.split()
        panes[(name, index)] = dead
    if not expected or not expected <= {name for name, _ in panes} or any(panes.get((name, "0")) != "0" for name in expected):
        raise RuntimeError(f"linked site has missing or dead top panes: {session}")
    if any(panes.get((name, "1")) != "0" for name in ROLES):
        raise RuntimeError(f"linked site has a missing or dead mind pane: {session}")
    leased = set(ROLES) & expected
    first = _leases(session, leased)
    # A frame includes a bounded probe (up to 10s), then its 5s refresh sleep.
    # Allow that whole cycle while retaining a finite frozen-renderer failure.
    for _ in range(3):
        time.sleep(6)
        second = _leases(session, leased)
        if all(second[name] > first[name] for name in leased):
            break
    else:
        raise RuntimeError(f"linked site top-pane lease did not advance: {session}")
    if not (home / "chat.log").is_file():
        raise RuntimeError(f"linked site conversation feed is missing: {home / 'chat.log'}")
    Feed(home).tail_sequence()
    units = json.loads((home / "health" / "services.json").read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    for unit in units:
        result = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], env=env, timeout=10)
        if result.returncode:
            raise RuntimeError(f"linked site service is not active: {unit}")
    if (home / "health/runtime-release.json").exists():
        from mishe_tauftauf.runtime_source import source_for
        source = source_for(home, KERNEL)
        for unit in units:
            if unit.endswith("-coordination.service"):
                continue  # The host release follower observes the development checkout.
            result = subprocess.run(["systemctl", "--user", "show", unit, "-p", "Environment", "--value"],
                                    env=env, capture_output=True, text=True, timeout=10)
            if result.returncode or f"PYTHONPATH={source / 'src'}" not in result.stdout.split():
                raise RuntimeError(f"linked site service source is not pinned: {unit}")
        if str(source / "src") not in (home / "bin/mishe-tauftauf").read_text():
            raise RuntimeError("linked site CLI source is not pinned")


def _leases(session: str, expected: set[str]) -> dict[str, datetime]:
    observed: dict[str, datetime] = {}
    for name in expected:
        result = _tmux("capture-pane", "-p", "-t", f"{session}:{name}.0", "-S", "-50")
        lines = result.stdout.decode("utf-8", "replace").splitlines()
        matches = [line for line in lines if FOOTER_LEASE_RE.fullmatch(line)]
        if not matches:
            raise RuntimeError(f"linked site top pane has no visible lease: {session}:{name}")
        stamp = matches[-1].removeprefix("-- pane live ").split(" · ", 1)[0]
        checked = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        if not 0 <= age <= 20:
            raise RuntimeError(f"linked site top-pane lease is stale: {session}:{name} age={age:.1f}s")
        observed[name] = checked
    return observed


def _release_source(core_home: Path, sha: str) -> Path:
    """Never install a green commit from the development checkout's mutable path."""
    from mishe_tauftauf.runtime_source import checked_source
    release = core_home / "releases" / sha
    if not release.exists():
        release.parent.mkdir(parents=True, exist_ok=True)
        _git(KERNEL, "worktree", "add", "--detach", str(release), sha)
    return checked_source(release, sha)[0]


def sync_registered_sites(core_home: Path, ci: dict[str, str], *, runtime_only: bool = False) -> list[str]:
    """Apply the exact green local commit to clean, owned sites; retry later on a hold."""
    if core_home.parent.resolve() != KERNEL or ci.get("state") != "pass":
        return []
    path = _registry(core_home)
    if not path.exists():
        return []
    with (path.parent / ".linked-sites-sync.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        sites = _load(core_home)
        sha = ci["sha"]
        outcomes = []
        for row in sites:
            if row["sha"] == sha and not row.get("error"):
                continue
            target = Path(row["home"])
            task = f"kernel-sync-{sha[:12]}-{target.parent.name}"
            try:
                _target_state(target, row["session"], runtime_only=runtime_only)
                release = _release_source(core_home, sha)
                if _git(release, "rev-parse", "HEAD") != sha or _git(release, "status", "--porcelain"):
                    raise RuntimeError("release is not clean at the green CI commit")
                env = os.environ.copy()
                env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
                env["PYTHONPATH"] = os.pathsep.join(
                    part for part in (str(release / "src"), env.get("PYTHONPATH", "")) if part)
                result = subprocess.run(
                    [sys.executable, "-m", "mishe_tauftauf.plant",
                     "--workspace", str(target.parent), "--home", str(target), "--session", row["session"],
                     "--runtime-source", str(release),
                     *(["--runtime-only"] if runtime_only else [])],
                    capture_output=True, text=True, timeout=240, env=env)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip()[-400:] or result.stdout.strip()[-400:] or "plant command failed")
                _verify(target, row["session"])
                if _git(release, "rev-parse", "HEAD") != sha or _git(release, "status", "--porcelain"):
                    raise RuntimeError("release changed during linked-site refresh")
                _update_site(core_home, target, sha=sha, error=None)
                detail = f"Replanted {target.parent} from core {sha}; expected top panes and resident services are live."
                core_tag = f"[done] {task}" if row.get("error") else f"[sync] core sha={sha}"
                Feed(core_home).append("sync", f"{core_tag}\n{detail}")
                target_tag = f"[done] {task}" if row.get("error") else f"[sync] core sha={sha}"
                Feed(target).append("sync", f"{target_tag}\n{detail}")
                outcomes.append(f"synced {target.parent} {sha[:12]}")
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                error = str(exc)[:400]
                if not row.get("error"):
                    Feed(core_home).append("sync", f"[task] {task} owner=genome source=ci\n"
                                           f"Refresh {target.parent} from green core commit {sha}; "
                                           f"current blocker: {error}. The CI follower retries after it clears.", once=True)
                if row.get("error") != error:
                    Feed(core_home).append("sync", f"[update] {task} blocked\n{error}. "
                                           "The CI follower will retry when the source or linked worktree changes.")
                    try:
                        Feed(target).append("sync", f"[task] {task} owner=genome source=core-sync\n"
                                            f"The checked core update is waiting: {error}. "
                                            "Resolve the local blocker, then the core CI follower will retry.", once=True)
                    except (OSError, ValueError):
                        pass
                # Keep the old applied SHA until verification succeeds so the next CI tick can retry.
                _update_site(core_home, target, sha=row["sha"], error=error)
                outcomes.append(f"held {target.parent}: {error}")
        return outcomes


def fresh_ci(home: Path) -> dict[str, str] | None:
    sample = latest(home)
    try:
        checked = datetime.fromisoformat(sample["checked"].replace("Z", "+00:00")) if sample else None
        age = (datetime.now(timezone.utc) - checked).total_seconds() if checked else None
        if age is not None and 0 <= age <= 300 and isinstance(sample, dict):
            return sample
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    return None


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Refresh registered plants from a checked core commit")
    parser.add_argument("--home", type=Path, required=True, help="core resident site")
    parser.add_argument("--follow", action="store_true", help="retry after fresh green CI readings")
    parser.add_argument("--runtime-only", action="store_true",
                        help="refresh existing owned runtimes without changing target AGENTS.md; allow target dirt")
    args = parser.parse_args()
    home = args.home.resolve()
    while True:
        ci = fresh_ci(home) if args.follow else read(home)
        if ci:
            try:
                for outcome in sync_registered_sites(home, ci, runtime_only=args.runtime_only):
                    print(outcome, flush=True)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"linked-site sync unavailable: {exc}", flush=True)
        if not args.follow:
            return
        time.sleep(60)


if __name__ == "__main__":
    main()
