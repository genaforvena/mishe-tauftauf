"""Per-role desired launchers, immutable rollback receipts, and live readiness routing.

JSON, shell argv and procfs decoding are confined to this literal boundary.
Selecting a launcher never respawns a pane or attributes desired config to it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from .observations import executable, validate_home, validate_slug
from .feed import Feed, parse_feed


def _root(home: Path, role: str) -> Path:
    return home / ".mishe-loop" / role


def _validate_role(home: Path, role: str) -> Path:
    from .activity import roles
    home = Path(home).resolve()
    bound = os.environ.get("MISHE_SEED_HOME")
    if bound is not None and Path(bound).resolve() != home:
        raise ValueError("site home does not match MISHE_SEED_HOME")
    home = validate_home(home).resolve()
    validate_slug(role)
    if (role not in roles(home) or not executable(home / "minds" / role)
            or not (home / "charters" / f"{role}.md").is_file()):
        raise ValueError(f"role '{role}' is not registered with a charter and executable mind for {home}")
    return home


def _snapshot(path: Path) -> tuple[bytes, int] | None:
    if not path.exists() and not path.is_symlink():
        return None
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save_snapshot(directory: Path, name: str, snapshot) -> dict | None:
    if snapshot is None:
        return None
    data, mode = snapshot
    path = directory / name
    with path.open("xb") as handle:
        handle.write(data)
    path.chmod(mode & ~0o222)
    return {"sha256": _hash(data), "mode": mode}


def _read_snapshot(directory: Path, name: str, info) -> tuple[bytes, int] | None:
    if info is None:
        return None
    if (directory / name).is_symlink():
        raise ValueError("selection snapshot cannot be a symlink")
    data = (directory / name).read_bytes()
    if (not isinstance(info, dict) or _hash(data) != info.get("sha256")
            or type(info.get("mode")) is not int or not 0 <= info["mode"] <= 0o777):
        raise ValueError("selection receipt snapshot invalid")
    return data, info["mode"]


def _receipt(home: Path, role: str, raw: str) -> tuple[Path, dict]:
    root = _root(home, role)
    path = Path(raw)
    if not path.is_absolute():
        path = root / raw
    # The env marker is data, never authority to execute an arbitrary path.
    if (root.resolve() != root or path.parent != root or len(path.name) != 32
            or any(c not in "0123456789abcdef" for c in path.name)
            or path.is_symlink()):
        raise ValueError("receipt must name an immutable receipt for this home and role")
    try:
        if (path / "receipt.json").is_symlink():
            raise ValueError("selection metadata cannot be a symlink")
        meta = json.loads((path / "receipt.json").read_text(encoding="utf-8"))
        if (not isinstance(meta, dict) or meta.get("version") != 1 or meta.get("home") != str(home)
                or meta.get("role") != role or meta.get("id") != path.name):
            raise ValueError("selection receipt identity invalid")
        for name in ("before-launcher", "before-probe", "before-gate", "launcher", "probe"):
            _read_snapshot(path, name, meta[name])
        return path, meta
    except (OSError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError(f"selection receipt unavailable or invalid: {exc}") from exc


def _current(home: Path, role: str):
    pointer = _root(home, role) / "current"
    if not pointer.is_symlink():
        return None
    return _receipt(home, role, os.readlink(pointer))


def _atomic(path: Path, snapshot) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    data, mode = snapshot
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=".loop-", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _link(path: Path, target: Path) -> None:
    temporary = path.with_name(f".loop-{uuid.uuid4().hex}")
    try:
        temporary.symlink_to(target)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _script(argv, *, marker: Path | None = None, python_path: str | None = None) -> bytes:
    lines = ["#!/bin/sh"]
    if marker is not None:
        lines.append("export MISHE_LOOP_RECEIPT=" + shlex.quote(str(marker)))
    if python_path is not None:
        lines.append("export PYTHONPATH=" + shlex.quote(python_path))
    lines.append("exec " + shlex.join(str(arg) for arg in argv))
    return ("\n".join(lines) + "\n").encode()


def _process_start(raw: bytes) -> str:
    _, separator, rest = raw.rpartition(b")")
    fields = rest.split()
    if not separator or len(fields) <= 19 or not fields[19].isdigit():
        raise ValueError("running pane start identity unavailable")
    return fields[19].decode("ascii")


def _observed_pane(home: Path, role: str):
    from .seed import _tmux, recorded_session
    session = os.environ.get("MISHE_SEED_SESSION") or recorded_session(home)
    if not session:
        raise ValueError("no recorded running session")
    target = f"{session}:{role}.1"
    result = _tmux("display-message", "-p", "-t", target, "#{pane_pid}", check=False)
    raw = result.stdout.decode("ascii").strip()
    if result.returncode or not raw.isdecimal() or int(raw) <= 0:
        raise ValueError("running pane PID unavailable")
    before = Path(f"/proc/{raw}/stat").read_bytes()
    start = _process_start(before)
    environment = Path(f"/proc/{raw}/environ").read_bytes().split(b"\0")
    markers = [item.partition(b"=")[2].decode() for item in environment
               if item.startswith(b"MISHE_LOOP_RECEIPT=")]
    if len(markers) > 1:
        raise ValueError("ambiguous running receipt")
    if _process_start(Path(f"/proc/{raw}/stat").read_bytes()) != start:
        raise ValueError("running pane identity changed during observation")
    return [raw, start], markers[0] if markers else None


def _live_receipt(home: Path, role: str):
    _, marker = _observed_pane(home, role)
    return _receipt(home, role, marker) if marker else None


def _probe_alias(home: Path, role: str, directory: Path, name: str) -> Path:
    # Keep dirname($0)/__file__-relative sibling resources at the original
    # gate directory without replacing or rewriting its immutable executable.
    return home / "checks" / "mind-ready" / f".{role}-loop-{directory.name}-{name}"


def ready(home: Path, role: str) -> bool:
    """Run only the gate belonging to the observed process, never desired state."""
    try:
        home = _validate_role(home, role)
        identity, marker = _observed_pane(home, role)
        live = _receipt(home, role, marker) if marker else None
        if live is not None:
            directory, meta = live
            gate = _read_snapshot(directory, "probe", meta["probe"])
            path = directory / "probe"
        else:
            current = _current(home, role)
            if current is None:
                return False
            directory, meta = current
            if meta["kind"] == "restored" and meta.get("before_process") != identity:
                if meta.get("before_process") is None:
                    return False
                gate = _read_snapshot(directory, "probe", meta["probe"])
                path = directory / "probe"
            else:
                # The original unmarked pane retains its own legacy gate.
                seen = set()
                while meta.get("previous"):
                    if meta["id"] in seen:
                        return False
                    seen.add(meta["id"])
                    directory, meta = _receipt(home, role, meta["previous"])
                gate = _read_snapshot(directory, "before-probe", meta["before-probe"])
                path = directory / "before-probe"
        if gate is None or not gate[1] & 0o111:
            return False
        alias = _probe_alias(home, role, directory, path.name)
        if alias.is_symlink() or not alias.samefile(path):
            return False
        return subprocess.run([str(alias)], capture_output=True, timeout=1.5).returncode == 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        return False




def status(home: Path, role: str) -> str:
    from .seed import _state
    home = _validate_role(home, role)
    launcher = home / "minds" / role
    lines = [f"mind {role}", f"launcher={launcher} sha256={_hash(launcher.read_bytes())}"]
    current = _current(home, role)
    if current is None:
        lines.append("desired=unknown (legacy/custom launcher; no selection receipt)")
    else:
        directory, meta = current
        if _hash(launcher.read_bytes()) != meta["launcher"]["sha256"]:
            lines.append("desired=unknown (launcher changed outside selection)")
        else:
            lines.append(f"desired={meta['kind']} model={meta.get('model') or 'unknown'} receipt={directory}")
        lines.append(f"probe_dispatcher_retained=true preserved_probe={directory / 'probe'} backup={directory / 'before-probe'}")
    try:
        live = _live_receipt(home, role)
        if live is None:
            lines.append("running=unknown (observed legacy/unmarked pane)")
        else:
            directory, meta = live
            lines.append(f"running={meta['kind']} model={meta.get('model') or 'unknown'} receipt={directory}")
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        lines.append("running=unknown (no readable receipt-identified pane)")
    entries = parse_feed(Feed(home).read_bytes(), home=home)
    lines.append(f"pending={_state(home, role, entries=entries)[1] or 'none'} native_busy={(home / 'journals' / f'{role}-native.busy').exists()}")
    lines.append("Desired selection applies at the next canonical idle rotation; no pane was restarted.")
    lines.append("Rollback: --restore RECEIPT restores prior launcher bytes/mode and probe behavior through the retained dispatcher.")
    return "\n".join(lines)


def select(home: Path, role: str, *, kind=None, model=None, bun=None, node_modules=None,
           engine_command=None, max_turns=None, max_calls=None, timeout=None,
           reasoning=None, restore=None) -> str:
    from . import seed
    from .runtime_source import pane_python_path
    home = _validate_role(home, role)
    supplied = (model, bun, node_modules, engine_command, max_turns, max_calls, timeout, reasoning)
    if kind is None and restore is None:
        if any(value is not None for value in supplied):
            raise ValueError("selection options require --kind")
        return status(home, role)
    if restore is not None and (kind is not None or any(value is not None for value in supplied)):
        raise ValueError("--restore cannot be combined with selection options")
    root = _root(home, role)
    if root.resolve() != root:
        raise ValueError("selection store must remain inside this home and role")
    new_id = uuid.uuid4().hex
    directory = root / new_id
    probe_path = home / "checks" / "mind-ready" / role
    python_path = pane_python_path(home, Path(__file__).resolve().parents[2])
    if not (Path(python_path) / "mishe_tauftauf/mind_loop.py").is_file():
        raise ValueError(f"selected runtime source {python_path} lacks the readiness dispatcher; deploy a coherent runtime before selection")
    config = {}
    restored = None
    if restore is not None:
        restored, meta = _receipt(home, role, restore)
        launcher = _read_snapshot(restored, "before-launcher", meta["before-launcher"])
        probe = _read_snapshot(restored, "before-gate", meta["before-gate"])
        if launcher is None or not launcher[1] & 0o111:
            raise ValueError("receipt has no executable prior launcher")
        kind = "restored"
    elif kind == "internal":
        if engine_command is not None:
            raise ValueError("--engine-command is only valid for external selection")
        if model is None or bun is None or node_modules is None:
            raise ValueError("internal selection requires --model, --bun and --node-modules")
        from .native_resident import validate_config
        config = {"model": model, "bun": bun, "node_modules": node_modules,
                  "max_turns": 32 if max_turns is None else max_turns,
                  "max_calls": 64 if max_calls is None else max_calls,
                  "timeout": 900 if timeout is None else timeout,
                  "reasoning": "medium" if reasoning is None else reasoning}
        checked = validate_config(home, role, **config)
        config["bun"], config["node_modules"] = checked.bun, checked.node_modules
        required = ("native_resident.py", "native_pane.py", "mind_loop.py",
                    "inference_loop.py", "inference_effects.py")
        if any(not (Path(python_path) / "mishe_tauftauf" / name).is_file() for name in required):
            raise ValueError(f"selected runtime source {python_path} lacks the internal loop; deploy a coherent runtime before selection")
        argv = [sys.executable, "-m", "mishe_tauftauf.native_resident", "--home", str(home), "--role", role]
        for name, value in config.items():
            argv.extend(("--" + name.replace("_", "-"), str(value)))
        launcher = (_script(argv, marker=directory), 0o755)
        probe = (_script([sys.executable, "-m", "mishe_tauftauf.native_resident", "--ready", "--home", str(home), "--role", role], python_path=python_path), 0o755)
    elif kind == "external":
        if any(value is not None for value in (model, bun, node_modules, max_turns, max_calls, timeout, reasoning)):
            raise ValueError("internal options cannot be combined with external selection")
        argv = shlex.split(engine_command or "")
        if not argv or not (command := shutil.which(argv[0])):
            raise ValueError("external engine command must name an executable")
        argv[0] = str(Path(command).resolve())
        launcher = (_script(argv, marker=directory), 0o755)
        probe = None  # Filled with preserved pre-internal probe under the lock.
    else:
        raise ValueError("kind must be internal or external")
    _current(home, role)  # Validate existing selection provenance before writes.
    # All config/dependency/receipt validation precedes even lock creation.
    with seed._lock(home):
        _validate_role(home, role)
        if seed._state(home, role)[1] is not None:
            raise ValueError(f"mind {role} has a pending wake; selection held")
        if (home / "journals" / f"{role}-native.busy").exists():
            raise ValueError(f"mind {role} is native busy; selection held")
        before_launcher = _snapshot(home / "minds" / role)
        before_probe = _snapshot(probe_path)
        previous = _current(home, role)
        before_gate = (_read_snapshot(previous[0], "probe", previous[1]["probe"])
                       if previous is not None else before_probe)
        try:
            before_process, _ = _observed_pane(home, role)
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            before_process = None
        if kind == "external":
            if previous is None:
                probe = before_probe
            else:
                old_dir, old_meta = previous
                while old_meta.get("previous"):
                    old_dir, old_meta = _receipt(home, role, old_meta["previous"])
                probe = _read_snapshot(old_dir, "before-probe", old_meta["before-probe"])
        directory.mkdir(parents=True, exist_ok=False)
        receipt = {"version": 1, "home": str(home), "role": role, "id": new_id,
                   "kind": kind, "model": model, "config": {k: str(v) for k, v in config.items()},
                   "previous": previous[0].name if previous else None,
                   "restored_from": restored.name if restored else None,
                   "before_process": before_process, "probe_dispatcher_retained": True}
        for name, snapshot in (("before-launcher", before_launcher), ("before-probe", before_probe),
                               ("before-gate", before_gate), ("launcher", launcher), ("probe", probe)):
            receipt[name] = _save_snapshot(directory, name, snapshot)
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        for name in ("before-probe", "probe"):
            if receipt[name] is not None:
                os.link(directory / name, _probe_alias(home, role, directory, name))
        with (directory / "receipt.json").open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, sort_keys=True)
            handle.write("\n")
        (directory / "receipt.json").chmod(0o444)
        directory.chmod(0o555)
        dispatcher = (_script([sys.executable, "-m", "mishe_tauftauf.mind_loop", "--ready", "--home", str(home), "--role", role], python_path=python_path), 0o755)
        # The seed lock serializes wake/clear with the pair installation. The
        # launcher itself is replaced atomically, never edited in place.
        try:
            _atomic(probe_path, dispatcher)
            _atomic(home / "minds" / role, launcher)
            _link(root / "current", Path(new_id))
        except OSError:
            _atomic(home / "minds" / role, before_launcher)
            _atomic(probe_path, before_probe)
            raise
    return (f"Selected desired={kind} for {role}; receipt={directory}; "
            f"probe_dispatcher_retained=true backup={directory / 'before-probe'}\n"
            + status(home, role))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", action="store_true", required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    return 0 if ready(args.home, args.role) else 1


if __name__ == "__main__":
    raise SystemExit(main())
