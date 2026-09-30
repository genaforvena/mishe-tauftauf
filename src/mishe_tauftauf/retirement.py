"""Retire finished author branches, preserving a verified recovery bundle.

The delivery watcher retries a concrete busy/dirty condition; it never retires
unfinished candidates or runtime release directories.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess


def busy(repo: Path, home: Path) -> str | None:
    """Live process references and installed runtime pins protect candidate paths."""
    root = repo.resolve()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            # Linux PF_KTHREAD is 0x00200000 (local linux/sched.h). Kernel
            # threads have no userspace source cwd/environment to protect.
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z" or int(fields[6]) & 0x00200000:
                continue
            cwd = (process / "cwd").resolve(strict=True)
            if cwd == root or cwd.is_relative_to(root):
                return f"process {process.name} owns candidate cwd"
            args = (process / "cmdline").read_bytes().split(b"\0")
            environment = (process / "environ").read_bytes().split(b"\0")
            for field in environment:
                _, separator, value = field.partition(b"=")
                if separator and any(os.fsdecode(part) == str(root) or os.fsdecode(part).startswith(str(root) + "/") for part in value.split(b":")):
                    return f"process {process.name} environment uses candidate path"
            for arg in args:
                # Direct executable/script/config references, not historical prose.
                value = os.fsdecode(arg).removeprefix("file://")
                if value == str(root) or value.startswith(str(root) + "/"):
                    return f"process {process.name} uses candidate path"
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            # An already-granted read-only capability may resolve protected proc
            # entries. Missing capability remains UNKNOWN, never a guessed absence.
            try:
                cwd_result = subprocess.run(["sudo", "-n", "readlink", "-e", str(process / "cwd")],
                    capture_output=True, text=True, timeout=3)
                args_result = subprocess.run(["sudo", "-n", "cat", str(process / "cmdline")],
                    capture_output=True, timeout=3)
                env_result = subprocess.run(["sudo", "-n", "cat", str(process / "environ")],
                    capture_output=True, timeout=3)
                if not process.exists():
                    continue
                if cwd_result.returncode or args_result.returncode or env_result.returncode:
                    return f"process {process.name} reference check unavailable"
                for field in env_result.stdout.split(b"\0"):
                    _, separator, value = field.partition(b"=")
                    if separator and any(os.fsdecode(part) == str(root) or os.fsdecode(part).startswith(str(root) + "/") for part in value.split(b":")):
                        return f"process {process.name} environment uses candidate path"
                cwd = Path(cwd_result.stdout.strip())
                args = args_result.stdout.split(b"\0")
                if cwd == root or cwd.is_relative_to(root) or any(
                    os.fsdecode(arg) == str(root) or os.fsdecode(arg).startswith(str(root) + "/") for arg in args):
                    return f"process {process.name} uses candidate path"
            except (OSError, subprocess.SubprocessError):
                return f"process {process.name} reference check unavailable"
    import json
    for pin in (home / "health").glob("*runtime*.json"):
        try:
            data = json.loads(pin.read_text())
        except (OSError, ValueError):
            return f"runtime pin {pin} unavailable"
        if str(root) in str(data):
            return f"installed runtime pin references {root}"
    return None


def retire(home: Path, identity: str) -> dict:
    from . import delivery
    with delivery._lock(home):
        record = delivery.load(home, identity)
        if record["phase"] != "done":
            raise ValueError("retirement requires completed rollout")
        old = record.get("retirement", {})
        if old.get("state") == "retired":
            return old
        workspace = Path(record["workspace"])
        repo = Path(record["repo"])
        branch, head = record["branch"], record["head"]
        if branch in {"main", "master"} or repo.resolve() == workspace.resolve():
            raise ValueError("retirement must name a temporary isolated candidate")
        row = {**old, "state": "pending", "reason": "retirement checks have not completed"}
        def save():
            record["retirement"] = row
            path = delivery._save(home, record)
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return dict(row)
        try:
            if delivery._git(workspace, "remote", "get-url", "origin") != record["origin"]:
                raise ValueError("canonical origin changed")
            # A checked remote main, rather than a stale local main, proves inclusion.
            delivery._git(workspace, "fetch", "-q", "origin", "refs/heads/main")
            main = delivery._git(workspace, "rev-parse", "FETCH_HEAD")
            delivery._git(workspace, "merge-base", "--is-ancestor", head, main)
            local = delivery._git(workspace, "for-each-ref", "--format=%(objectname)", "refs/heads/" + branch)
            if local and local != head:
                raise ValueError("local candidate branch moved; preserve new work")
            remote = delivery._remote(workspace, branch)
            if remote and remote != head:
                raise ValueError("remote candidate branch moved; preserve new work")
            if repo.exists():
                if not local:
                    raise ValueError("candidate exists without its expected branch")
                delivery._repository(record)
                if delivery._git(repo, "rev-parse", "HEAD") != head or delivery._git(repo, "status", "--porcelain", "--untracked-files=all"):
                    raise ValueError("candidate has new or dirty work")
                ignored = delivery._git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
                for name in ignored.split("\0"):
                    if not name:
                        continue
                    parts = Path(name).parts
                    if not any(part in {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"} or part.endswith(".egg-info") for part in parts):
                        raise ValueError("candidate contains ignored local state; preserve or reconcile before retirement")
                reason = busy(repo, home)
                if reason:
                    raise ValueError(reason)
                # Runtime copies are detached and never delivery-owned branches.
                if delivery._git(repo, "symbolic-ref", "--short", "HEAD") != branch:
                    raise ValueError("candidate checkout branch changed")
            directory = home / "retired-candidates"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            bundle = directory / (identity + "-" + head + ".bundle")
            if not bundle.exists():
                if not local:
                    raise ValueError("candidate branch absent without a durable recovery bundle")
                temp = bundle.with_suffix(".tmp")
                delivery._git(workspace, "bundle", "create", str(temp), "refs/heads/" + branch)
                delivery._git(workspace, "bundle", "verify", str(temp))
                os.chmod(temp, 0o400)
                with temp.open("rb") as handle:
                    os.fsync(handle.fileno())
                temp.replace(bundle)
                descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            delivery._git(workspace, "bundle", "verify", str(bundle))
            listed = delivery._git(workspace, "bundle", "list-heads", str(bundle))
            if f"{head} refs/heads/{branch}" not in listed.splitlines():
                raise ValueError("recovery bundle does not bind exact candidate")
            archive = directory / (identity + "-" + head + ".tree")
            admin_archive = directory / (identity + "-" + head + ".admin")
            common = Path(delivery._git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            if repo.exists():
                if archive.exists() or archive.is_symlink():
                    raise ValueError("recovery tree already exists while candidate exists; reconcile both")
                admin = Path(delivery._git(repo, "rev-parse", "--absolute-git-dir"))
            else:
                if not archive.is_dir() or archive.is_symlink() or not old.get("tree_archive") == str(archive):
                    raise ValueError("missing candidate has no recorded full-tree recovery archive")
                admin = Path(old.get("administrative_directory", ""))
            if admin.parent != common / "worktrees":
                raise ValueError("candidate administrative directory is outside exact linked-worktree boundary")
            if admin.exists():
                if (admin / "locked").exists() or any(admin.glob("*.lock")):
                    raise ValueError("candidate Git registration is locked or an operation is active")
                if (admin / "gitdir").read_text().strip() != str(repo / ".git"):
                    raise ValueError("candidate registration no longer binds original path")
                if admin_archive.exists() or admin_archive.is_symlink():
                    raise ValueError("recovery administrative directory conflicts; preserve both")
            elif not admin_archive.is_dir() or admin_archive.is_symlink():
                raise ValueError("candidate registration missing without preserved metadata")
            row.update(bundle=str(bundle), bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
                       tree_archive=str(archive), admin_archive=str(admin_archive), administrative_directory=str(admin))
            save()  # Durable identity and recovery edge precede every effect.
            if remote:
                delivery._git(workspace, "push", "origin", ":refs/heads/" + branch,
                              "--force-with-lease=refs/heads/" + branch + ":" + head)
            if repo.exists():
                # Preserve the entire directory, including any ignored bytes that
                # arrived after admission. No recursive deletion can race a writer.
                repo.rename(archive)
                for parent in (repo.parent, directory):
                    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            if admin.exists():
                if (admin / "locked").exists() or any(admin.glob("*.lock")):
                    raise ValueError("candidate Git registration became locked; full-tree archive preserved")
                # Preserve only this exact administrative registration; no blanket
                # prune can unregister someone else's missing/recoverable worktree.
                admin.rename(admin_archive)
                for parent in (admin.parent, directory):
                    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            if local:
                # Atomic expected-old deletion refuses a concurrently advanced ref.
                delivery._git(workspace, "update-ref", "-d", "refs/heads/" + branch, head)
            row.update(state="retired", reason="Exact integrated candidate archived; temporary branch retired and exact worktree registration archived")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            row["reason"] = str(exc)
        return save()
