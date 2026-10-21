"""Linux uninstall boundary: inspect ownership before changing a selected plant.

No model or resident cooperation is required. Unknown ownership fails closed;
failed postconditions retain the site and its uninstall journal for diagnosis.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import time

from .tmux import OWNED_OPTION, _socket_candidates, _tmux

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,240}\.(service|timer|socket|scope)")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}")


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1:]} failed: {result.stderr.strip()}")
    return result


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unreadable uninstall evidence: {path}: {exc}") from exc


def _inside(path: Path, home: Path) -> bool:
    return path == home or home in path.parents


def _owned_file(path: Path) -> None:
    if path.lstat().st_uid != os.getuid():
        raise ValueError(f"foreign file owner: {path}")


def _identity(home: Path, session: str) -> None:
    if not home.is_absolute() or home.resolve() != home or home.is_symlink():
        raise ValueError("uninstall home must be an exact absolute non-symlink path")
    if not _SESSION.fullmatch(session):
        raise ValueError("invalid uninstall session")
    _owned_file(home)
    root = _run("git", "-C", str(home.parent), "rev-parse", "--show-toplevel").stdout.strip()
    if root != str(home.parent) or home.name in {".git", "src", "docs"}:
        raise ValueError("uninstall requires a site directly inside its Git worktree")
    if _run("git", "-C", root, "ls-files", "--", str(home)).stdout:
        raise ValueError("refusing to remove tracked source inside site")
    if _run("git", "-C", root, "check-ignore", "-q", str(home), check=False).returncode:
        raise ValueError("site must be Git-ignored")
    for relative in (".seed-raised", "chat.log", "health", "health/services.json",
                     "health/uninstall.json", "health/uninstall-result.json",
                     "health/.uninstall.lock", "minds", "pub", "pub/access",
                     "operator", "operator/telegram", "operator/telegram/env"):
        if (home / relative).is_symlink():
            raise ValueError(f"symlinked uninstall control/access path: {relative}")
    marker = home / ".seed-raised"
    receipt = home / "health/uninstall.json"
    if receipt.exists():
        data = _json(receipt)
        expected = {"version": 1, "home": str(home), "session": session, "uid": os.getuid()}
        if data != expected:
            raise ValueError("uninstall ownership receipt mismatch")
    elif not marker.is_file():
        raise ValueError("missing plant ownership receipt")
    if marker.exists():
        words = marker.read_text(encoding="utf-8").split()
        if len(words) != 2 or words[0] != session or not re.fullmatch(r"\$[0-9]+", words[1]):
            raise ValueError("malformed or mismatched .seed-raised receipt")
    for name in ("chat.log", "health/services.json", "minds"):
        if not (home / name).exists():
            raise ValueError(f"missing plant evidence: {name}")
    # rmtree never follows links; reject mounts and foreign-owned contents first.
    device = home.stat().st_dev
    for parent, dirs, files in os.walk(home, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(parent) / name
            _owned_file(path)
            if not path.is_symlink() and (path.stat().st_dev != device or path.is_mount()):
                raise ValueError(f"site contains mounted data: {path}")


def _properties(name: str) -> dict[str, str]:
    raw = _run("systemctl", "--user", "show", name,
               "-p", "FragmentPath,DropInPaths,ActiveState,LoadState,Transient,ExecStart,Environment,WorkingDirectory,ControlGroup").stdout
    rows = {}
    for line in raw.splitlines():
        if line.startswith("ExecStart=") and "ExecStart" in rows:
            # systemctl renders multiple ExecStart lines for one unit; only the
            # first is the real start command, the rest are documented here as
            # continuation. Keep the first, which is what a removal must match.
            continue
        key, separator, value = line.partition("=")
        if not separator or key in rows:
            raise ValueError(f"malformed systemd properties for {name}")
        rows[key] = value
    if not {"FragmentPath", "ActiveState", "LoadState"} <= rows.keys():
        raise ValueError(f"incomplete systemd properties for {name}")
    return rows


def _unit_names() -> set[str]:
    names = set()
    for command, field in (("list-unit-files", "unit_file"), ("list-units", "unit")):
        data = json.loads(_run("systemctl", "--user", command, "--all", "--output=json", "--no-pager").stdout)
        if not isinstance(data, list):
            raise ValueError("malformed systemd unit inventory")
        for row in data:
            name = row.get(field) if isinstance(row, dict) else None
            if not isinstance(name, str):
                raise ValueError("malformed systemd unit inventory row")
            if _NAME.fullmatch(name):
                names.add(name)
    return names


def _units(home: Path, session: str) -> tuple[list[str], list[Path]]:
    manifest = _json(home / "health/services.json")
    if not isinstance(manifest, list) or any(not isinstance(n, str) or not _NAME.fullmatch(n) for n in manifest):
        raise ValueError("malformed service manifest")
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
    roots = (config, Path("/etc/systemd/user"), Path("/run/user") / str(os.getuid()) / "systemd/user")
    names = _unit_names() | set(manifest)
    links = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_symlink() and _inside(path.resolve(), home):
                if not _NAME.fullmatch(path.name):
                    raise ValueError(f"unsupported installed link: {path}")
                names.add(path.name)
                links.append(path)
    owned = []
    for name in sorted(names):
        if "@" in name.rsplit(".", 1)[0]:
            # A template unit's name is not queryable by systemctl show; only
            # its instantiated name@instance units are. A template file under
            # the site is still caught by the fragment/link walk below.
            continue
        properties = _properties(name)
        fragment = Path(properties["FragmentPath"]) if properties["FragmentPath"] else None
        from .unit_properties import declared_paths
        declared = [Path(p) for p in declared_paths("\n".join(properties.values()))]
        belongs = fragment is not None and _inside(fragment.resolve(), home)
        declares_home = any(_inside(path, home) for path in declared)
        candidate = belongs or declares_home or name in manifest or name.startswith(session + "-")
        if not candidate:
            continue
        if properties["LoadState"] == "not-found" and fragment is None:
            continue
        transient = properties.get("Transient") == "yes"
        keeper = transient and re.fullmatch(re.escape(session) + r"-session-[a-f0-9]{32}\.service", name)
        if keeper:
            _check_keeper(home, session, properties)
        elif not belongs and not (declares_home and fragment is not None and fragment.parent == config and not fragment.is_symlink()):
            raise ValueError(f"foreign or unsupported unit ownership: {name} ({fragment})")
        import shlex
        for value in shlex.split(properties.get("DropInPaths", "")):
            path = Path(value)
            if path.parent != config / (name + ".d") or path.is_symlink():
                raise ValueError(f"unsupported unit drop-in: {path}")
            _owned_file(path)
            links.append(path)
        if fragment is not None and not keeper and not _inside(fragment.resolve(), home):
            _owned_file(fragment)
            links.append(fragment)
        owned.append(name)
    for path in links:
        if not _inside(path, config) and not _inside(path, roots[2]):
            raise ValueError(f"system-wide installation is not supported: {path}")
        _owned_file(path)
    return owned, sorted(set(links))


def _check_keeper(home: Path, session: str, properties: dict) -> None:
    """A transient keeper may stop only when its entire cgroup is site-owned."""
    owned = _processes(home)
    group = properties.get("ControlGroup", "")
    if not group.startswith("/") or ".." in Path(group).parts:
        raise ValueError("uninspectable session keeper cgroup")
    group_path = Path("/sys/fs/cgroup") / group.lstrip("/")
    if not (group_path / "cgroup.procs").is_file():
        raise ValueError("missing session keeper cgroup")
    sessions = _sessions(home, session)
    for path in group_path.rglob("cgroup.procs"):
        for value in path.read_text().split():
            if not value.isdigit():
                raise ValueError("malformed session keeper process inventory")
            pid = int(value)
            if pid in owned:
                continue
            try:
                argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            except FileNotFoundError:
                continue
            if argv[:2] == [b"sleep", b"infinity"]:
                continue
            if argv and b"tmux" in argv[0]:
                server_pids = set()
                for item in sessions:
                    result = _tmux("list-sessions", "-F", "#{" + OWNED_OPTION + "}|#{pid}",
                                   socket=item["socket"])
                    for line in result.stdout.decode().splitlines():
                        owner, separator, server = line.partition("|")
                        if not separator or owner != str(home) or not server.isdigit():
                            raise ValueError("session keeper hosts another site's shared tmux server")
                        server_pids.add(int(server))
                if pid not in server_pids:
                    raise ValueError("keeper contains an unattributed tmux server")
                continue
            raise ValueError(f"session keeper contains unowned process {pid}")


def _socket_without_server(result) -> bool:
    """True when tmux states that no server is running on the socket.

    tmux leaves its socket file behind after the last server exits, so the
    file's existence says nothing about a live server: the file outlives the
    server exactly as long as nothing unlinks it. tmux's own answer —
    "no server running on <path>" — is the precise statement that nothing
    listens there, and a socket with no server cannot hold a session. Every
    other inspection failure stays fail-closed.
    """
    if result.returncode == 0:
        return False
    return result.stderr.decode("utf-8", "replace").strip().startswith("no server running on")


def _sessions(home: Path, session: str) -> list[dict]:
    sessions = []
    for socket in _socket_candidates(home):
        if not Path(socket).exists():
            continue
        result = _tmux("list-sessions", "-F", "#{session_name}|#{session_id}|#{" + OWNED_OPTION + "}", socket=socket, check=False)
        if result.returncode:
            if _socket_without_server(result):
                # A leftover socket file with no server behind it: nothing can
                # be running here, so it holds no session of this site.
                continue
            raise ValueError(f"uninspectable tmux socket: {socket}")
        for line in result.stdout.decode().splitlines():
            words = line.split("|")
            if len(words) != 3:
                raise ValueError("malformed tmux ownership table")
            name, identity, owner = words
            if name == session and owner != str(home):
                raise ValueError(f"foreign session with selected name on {socket}")
            if owner == str(home):
                if name != session:
                    raise ValueError(f"ambiguous extra site session: {name}")
                marker = home / ".seed-raised"
                if marker.read_text() != f"{name} {identity}\n":
                    raise ValueError("tmux identity differs from plant receipt")
                sessions.append({"socket": socket, "name": name})
    return sessions


def _processes(home: Path) -> dict[int, str]:
    """Attribute by exact home argv/environment or site cwd, then descendants.

    Linux starttime pins each pid against reuse. Callers/ancestors are never
    killed; a caller inside the site must move outside before uninstalling.
    """
    records = {}
    ancestors = {os.getpid()}
    current = os.getppid()
    while current > 1 and current not in ancestors:
        ancestors.add(current)
        try:
            raw = Path(f"/proc/{current}/stat").read_text().rsplit(")", 1)[1].split()
            current = int(raw[1])
        except FileNotFoundError:
            break
    selected = set()
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
        except FileNotFoundError:
            continue
        pid = int(path.name)
        if pid in ancestors:
            continue
        try:
            raw = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if raw[0] == "Z":
                continue
            argv = (path / "cmdline").read_bytes().split(b"\0")
            try:
                environment = (path / "environ").read_bytes().split(b"\0")
            except PermissionError:
                # yama ptrace_scope=1 denies environ of same-uid processes we
                # did not fork (e.g. (sd-pam)). cmdline is still readable, so
                # attribute by argv alone; cwd is unreadable in the same way.
                environment = []
            try:
                cwd = (path / "cwd").resolve(strict=True)
            except FileNotFoundError:
                cwd = Path("/")
            except PermissionError:
                cwd = None
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as exc:
            raise ValueError(f"cannot inspect same-user process {pid}") from exc
        records[pid] = (int(raw[1]), raw[19])
        target = str(home).encode()
        explicit = [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == b"--home"]
        bound = target in explicit if explicit else any(item in environment for item in (
            b"MISHE_SEED_HOME=" + target, b"MISHE_TAUFTAUF_HOME=" + target))
        if bound or (cwd is not None and _inside(cwd, home)):
            selected.add(pid)
    while True:
        children = {pid for pid, (parent, _) in records.items() if parent in selected}
        if children <= selected:
            break
        selected |= children
    return {pid: records[pid][1] for pid in selected}


def _alive(pid: int, start: str) -> bool:
    try:
        words = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return words[0] != "Z" and words[19] == start
    except FileNotFoundError:
        return False


def _registry_paths(home: Path) -> list[Path]:
    paths = set()
    from .plant import ROOT
    for candidate in ROOT.iterdir():
        registry = candidate / "health/linked-sites.json"
        if candidate.is_dir() and (candidate / ".seed-raised").is_file() and registry.exists():
            paths.add(registry)
    own = home / "health/linked-sites.json"
    if own.exists():
        data = _json(own)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("sites"), list):
            raise ValueError(f"malformed registry: {own}")
        for row in data["sites"]:
            if not isinstance(row, dict) or not isinstance(row.get("home"), str):
                raise ValueError(f"malformed registry row: {own}")
            peer = Path(row["home"])
            if not peer.is_absolute() or peer.resolve() != peer or peer == home:
                raise ValueError(f"ambiguous registry peer: {peer}")
            registry = peer / "health/linked-sites.json"
            if registry.exists():
                paths.add(registry)
            pin = peer / "health/runtime-release.json"
            if pin.exists() and str(home) in pin.read_text():
                raise ValueError(f"another site depends on selected site's releases: {peer}")
    return sorted(paths)


def _registry_rows(path: Path, home: Path, session: str) -> list:
    data = _json(path)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("sites"), list):
        raise ValueError(f"malformed registry: {path}")
    rows = data["sites"]
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("home", "session", "sha")):
            raise ValueError(f"malformed registry row: {path}")
        if row["home"] == str(home) and row["session"] != session:
            raise ValueError(f"mismatched linked registration: {path}")
    return [row for row in rows if row["home"] != str(home)]


def _external_edits(home: Path) -> dict[Path, tuple[str, str]]:
    """Remove only exact generated registration text, preserving user content."""
    from .plant import ROOT, CONTRACT_START, CONTRACT_END
    edits = {}
    raw = _run("git", "-C", str(home.parent), "rev-parse", "--git-path", "info/exclude").stdout.strip()
    exclude = Path(raw)
    if not exclude.is_absolute():
        exclude = home.parent / exclude
    if exclude.exists():
        if exclude.is_symlink():
            raise ValueError("symlinked Git exclude file")
        _owned_file(exclude)
        before = exclude.read_text()
        after = "".join(line for line in before.splitlines(keepends=True) if line.rstrip("\r\n") != f"/{home.name}/")
        if before != after:
            edits[exclude] = (before, after)
    agents = home.parent / "AGENTS.md"
    if home.parent != ROOT and agents.exists():
        if agents.is_symlink():
            raise ValueError("symlinked generated agent contract")
        before = agents.read_text()
        if CONTRACT_START in before or CONTRACT_END in before:
            template = (ROOT / "src/mishe_tauftauf/seed_agent_contract.md").read_text().rstrip() + "\n" + CONTRACT_END
            if before.count(CONTRACT_START) != 1 or before.count(CONTRACT_END) != 1 or template not in before:
                raise ValueError("modified or ambiguous generated AGENTS.md block; preserve and disposition it explicitly")
            edits[agents] = (before, before.replace(template, "", 1))
    return edits


def inspect(home: Path, session: str) -> dict:
    _identity(home, session)
    units, links = _units(home, session)
    sessions = _sessions(home, session)
    registries = []
    for path in _registry_paths(home):
        _owned_file(path)
        remaining = _registry_rows(path, home, session)
        if len(remaining) != len(_json(path)["sites"]):
            registries.append(path)
    external = _external_edits(home)
    return {"home": str(home), "session": session, "units": units,
            "installed_files": [str(p) for p in links], "sessions": sessions,
            "external_registrations": [str(path) for path in external],
            "processes": _processes(home), "registries": [str(p) for p in registries],
            "credentials": [str(home / p) for p in ("pub/access", "operator/telegram/env") if (home / p).exists()]}


def uninstall(home: Path, session: str, *, execute: bool = False,
              confirm_home: str | None = None, purge_site: bool = False) -> dict:
    if execute and confirm_home != str(home):
        raise ValueError("--execute requires --confirm-home with the exact absolute selected home")
    if _inside(Path.cwd().resolve(), home):
        raise ValueError("run uninstall from outside the selected site directory")
    plan = inspect(home, session)
    plan.update({"purge_site": purge_site, "status": "preview"})
    if not execute:
        return plan
    journal = home / "health/uninstall-result.json"
    with contextlib.ExitStack() as stack:
        lock = stack.enter_context((home / "health/.uninstall.lock").open("a+"))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The same locks as the coordinator prevent an already-running release
        # pass from replanting after this site is unregistered.
        for value in plan["registries"]:
            parent = Path(value).parent
            for name in (".linked-sites-sync.lock", ".linked-sites.lock"):
                handle = stack.enter_context((parent / name).open("a+"))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = inspect(home, session)
        plan.update({"purge_site": purge_site, "status": "removing"})
        journal.write_text(json.dumps(plan, indent=2) + "\n")
        (home / "health/uninstall.json").write_text(json.dumps({
            "version": 1, "home": str(home), "session": session, "uid": os.getuid()
        }) + "\n")
        try:
            for value in plan["registries"]:
                path = Path(value)
                rows = _registry_rows(path, home, session)
                temporary = path.with_name(".uninstall-registry.json")
                temporary.write_text(json.dumps({"version": 1, "sites": rows}) + "\n")
                os.replace(temporary, path)
            # Disable every activation source before terminating any consumers.
            persistent = [name for name in plan["units"] if _properties(name).get("Transient") != "yes"]
            if persistent:
                _run("systemctl", "--user", "disable", "--now", *persistent)
            for item in plan["sessions"]:
                _tmux("kill-session", "-t", "=" + item["name"], socket=item["socket"])
            transient = [name for name in plan["units"] if name not in persistent]
            if transient:
                _run("systemctl", "--user", "stop", *transient)
            processes = {int(pid): start for pid, start in plan["processes"].items()}
            processes.update(_processes(home))
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for pid, start in processes.items():
                    if _alive(pid, start):
                        try:
                            os.kill(pid, sig)
                        except ProcessLookupError:
                            pass
                deadline = time.monotonic() + 3
                while any(_alive(pid, start) for pid, start in processes.items()) and time.monotonic() < deadline:
                    time.sleep(.05)
            for value in plan["installed_files"]:
                path = Path(value)
                if path.exists() or path.is_symlink():
                    path.unlink()
                    if path.parent.name.endswith(".d") and not any(path.parent.iterdir()):
                        path.parent.rmdir()
            _run("systemctl", "--user", "daemon-reload")
            for name in plan["units"]:
                properties = _properties(name)
                if properties["ActiveState"] not in {"inactive", "failed"} or properties["FragmentPath"]:
                    raise RuntimeError(f"unit remains installed or active: {name}")
            if _processes(home) or any(_alive(pid, start) for pid, start in processes.items()):
                raise RuntimeError("owned processes remain after termination")
            for item in plan["sessions"]:
                if _tmux("has-session", "-t", "=" + session, socket=item["socket"], check=False).returncode == 0:
                    raise RuntimeError("owned session remains")
                socket = Path(item["socket"])
                # tmux removes its own last-session socket. Never kill a shared
                # server or unlink a live socket belonging to another session.
                if socket.exists():
                    listing = _tmux("list-sessions", socket=str(socket), check=False)
                    if listing.returncode:
                        raise RuntimeError(f"uninspectable socket remnant: {socket}")
            for value in plan["credentials"]:
                path = Path(value)
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            for name in plan["units"]:
                (home / name).unlink(missing_ok=True)
            (home / ".seed-raised").unlink(missing_ok=True)
            (home / "health/services.json").write_text("[]\n")
            for value in plan["registries"]:
                path = Path(value)
                data = _json(path)
                if any(row["home"] == str(home) for row in data["sites"]):
                    raise RuntimeError(f"registration remains: {path}")
            for path, (before, after) in _external_edits(home).items():
                if path.read_text() != before:
                    raise RuntimeError(f"external registration changed during uninstall: {path}")
                path.write_text(after)
            plan["status"] = "removed" if purge_site else "uninstalled-local-history-retained"
            journal.write_text(json.dumps(plan, indent=2) + "\n")
            if purge_site:
                shutil.rmtree(home)
                if home.exists():
                    raise RuntimeError("site directory remains")
            return plan
        except Exception as exc:
            plan.update(status="failed", error=str(exc))
            if home.exists():
                journal.write_text(json.dumps(plan, indent=2) + "\n")
            raise RuntimeError(f"uninstall incomplete; evidence retained at {journal}: {exc}") from exc
