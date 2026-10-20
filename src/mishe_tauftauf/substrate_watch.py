"""Observe the planted checkout and selected/live runtime without activating either.

Literal Git NUL records and systemd property blocks are decoded only in the
boundary helpers below. File contents, diff bodies, command stderr and environment
values never enter reports or the feed; only paths, statuses and hashes do.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

from . import wall_view
from .feed import Feed

VERSION = 1
TIMEOUT = 5
SOURCE = "sensor/substrate-watch"


class ObservationError(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def _atomic_bytes(path, raw):
    """The post_check._save fsync/replace convention, including private mode."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".draft-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save(path, value):
    # Surrogate-escaped filesystem names must round-trip without exposing bytes
    # to a strict UTF-8 stream. Shared publication persistence stays unchanged.
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode() + b"\n"
    _atomic_bytes(path, raw)


def _preserve(home, path):
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    retained = home / "checks/substrate-watch-corrupt" / (fingerprint + ".bin")
    if retained.exists():
        if retained.read_bytes() != raw:
            raise ObservationError("corrupt evidence retention conflicts")
    else:
        _atomic_bytes(retained, raw)
    return f"sha256={fingerprint}; retained={retained}"


def _clock(now=None):
    value = now if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _git(root, *args):
    env = os.environ.copy()
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root),
                                 "-c", "core.fsmonitor=false", *args],
                                capture_output=True, timeout=TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ObservationError(f"git {args[0]} unavailable ({type(exc).__name__})") from exc
    if result.returncode:
        raise ObservationError(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def _git_scalar(raw):
    """Boundary: a single newline-terminated Git scalar, never a diff body."""
    value = os.fsdecode(raw.removesuffix(b"\n"))
    if not value or "\n" in value or "\0" in value:
        raise ObservationError("invalid Git scalar")
    return value


def _git_paths(raw):
    """Boundary: exact, unquoted paths from Git's NUL-terminated -z output."""
    if raw and not raw.endswith(b"\0"):
        raise ObservationError("truncated Git path records")
    paths = [os.fsdecode(value) for value in raw.split(b"\0")[:-1]]
    if any(not path or Path(path).is_absolute() or ".." in Path(path).parts for path in paths):
        raise ObservationError("invalid Git path")
    return paths


def _git_status(raw):
    """Boundary: porcelain v1 -z; rename/copy destination precedes source."""
    fields = _git_paths(raw)
    rows = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4 or field[2] != " " or any(c not in " MADRCU?!T" for c in field[:2]):
            raise ObservationError("invalid Git status record")
        row = {"status": field[:2], "path": field[3:]}
        if not row["path"] or Path(row["path"]).is_absolute() or ".." in Path(row["path"]).parts:
            raise ObservationError("invalid Git status path")
        if "R" in field[:2] or "C" in field[:2]:
            if index == len(fields):
                raise ObservationError("truncated Git rename record")
            row["from"] = fields[index]
            index += 1
        rows.append(row)
    return rows


def _file(root, relative):
    parts = Path(relative).parts
    descriptors = []
    try:
        descriptors.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for component in parts[:-1]:
            descriptors.append(os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        parent = descriptors[-1]
        name = parts[-1]
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return {"path": relative, "kind": "missing", "sha256": None, "mode": None}
        digest = hashlib.sha256()
        if stat.S_ISLNK(before.st_mode):
            digest.update(os.fsencode(os.readlink(name, dir_fd=parent)))
            kind = "symlink"
        elif stat.S_ISREG(before.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
                    raise ObservationError("file changed during observation: " + relative)
                for chunk in iter(lambda: stream.read(131072), b""):
                    digest.update(chunk)
            kind = "file"
        else:
            raise ObservationError("unsupported checkout object: " + relative)
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode):
            raise ObservationError("file changed during observation: " + relative)
        return {"path": relative, "kind": kind, "sha256": digest.hexdigest(),
                "mode": stat.S_IMODE(before.st_mode)}
    except FileNotFoundError:
        return {"path": relative, "kind": "missing", "sha256": None, "mode": None}
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _checkout(home):
    root = home.parent.resolve()
    if _git_scalar(_git(root, "rev-parse", "--show-toplevel")) != str(root):
        raise ObservationError("planted parent is not the exact Git checkout root")
    head = _git_scalar(_git(root, "rev-parse", "HEAD"))
    index = _git(root, "ls-files", "--stage", "-z")
    status_raw = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    tracked = sorted(set(_git_paths(_git(root, "ls-files", "-z"))))
    untracked_raw = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    untracked = sorted(_git_paths(untracked_raw))
    exposed_site = any((root / path).is_relative_to(home) for path in tracked + untracked)
    tracked = [path for path in tracked if not (root / path).is_relative_to(home)]
    untracked = [path for path in untracked if not (root / path).is_relative_to(home)]
    files = []
    problems = ["site state is not Git-ignored: " + str(home)] if exposed_site else []
    for relative in tracked + untracked:
        try:
            files.append(_file(root, relative))
        except (OSError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, ObservationError) else type(exc).__name__
            problems.append(detail)
            files.append({"path": relative, "kind": "unknown", "sha256": None, "mode": None})
    rows = [row for row in _git_status(status_raw) if not (root / row["path"]).is_relative_to(home)]
    rows.extend({"path": path, "status": "??"} for path in untracked)
    if (head != _git_scalar(_git(root, "rev-parse", "HEAD"))
            or index != _git(root, "ls-files", "--stage", "-z")
            or status_raw != _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no")
            or untracked_raw != _git(root, "ls-files", "--others", "--exclude-standard", "-z")):
        raise ObservationError("checkout changed during observation; sample again")
    result = {"root": str(root), "head": head, "index_sha256": hashlib.sha256(index).hexdigest(),
              "status": rows, "files": files, "state": "UNKNOWN" if problems else "KNOWN", "problems": problems}
    result["sha256"] = _digest(result)
    return result


def _service_pids(units):
    """Boundary: systemctl Id/MainPID blocks, retaining absent/dead units as None."""
    if not units:
        return {}
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    result = subprocess.run(["systemctl", "--user", "show", *units, "-p", "Id,MainPID"],
                            capture_output=True, text=True, timeout=TIMEOUT, env=env)
    if result.returncode:
        raise ObservationError("live service process observation failed")
    pids = dict.fromkeys(units)
    for block in result.stdout.strip().split("\n\n"):
        values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
        name, pid = values.get("Id"), values.get("MainPID", "")
        if name not in units:
            raise ObservationError("unexpected live service process identity")
        pids[name] = int(pid) if pid.isdecimal() and int(pid) > 0 else None
    return pids


def _selected_source(home, raw):
    """Boundary: runtime-release v1, validated without Git writes or hooks."""
    if raw is None:
        return home.parent
    pin = json.loads(raw)
    if (not isinstance(pin, dict) or pin.get("version") != 1
            or not isinstance(pin.get("source"), str) or not isinstance(pin.get("sha"), str)
            or len(pin["sha"]) != 40 or any(c not in "0123456789abcdef" for c in pin["sha"])):
        raise ObservationError("invalid runtime pin")
    try:
        source = Path(pin["source"]).resolve()
    except RuntimeError as exc:
        raise ObservationError("runtime source path cannot be resolved") from exc
    if any(c.isspace() for c in str(source)):
        raise ObservationError("invalid runtime source path")
    if (_git_scalar(_git(source, "rev-parse", "--show-toplevel")) != str(source)
            or _git_scalar(_git(source, "rev-parse", "HEAD")) != pin["sha"]
            or _git(source, "status", "--porcelain=v1", "-z")
            or not (source / "src/mishe_tauftauf/__init__.py").is_file()):
        raise ObservationError("runtime source is not a clean exact-SHA package checkout")
    return source


def _runtime(home):
    result = {"selection": "UNKNOWN", "selection_sha256": None, "source": None, "head": None,
              "selection_state": "UNKNOWN", "services_state": "UNKNOWN", "renderers_state": "UNKNOWN",
              "configured_service_roots": {}, "service_roots": {}, "renderer_roots": {},
              "service_states": {}, "renderer_states": {}, "problems": []}
    problems = result["problems"]
    pin = home / "health/runtime-release.json"
    try:
        raw = pin.read_bytes() if pin.exists() else None
        result["selection"] = "PINNED" if raw is not None else "UNPINNED"
        result["selection_sha256"] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        selected = _selected_source(home, raw)
        result["source"] = str(selected)
        if raw is not None:
            result["head"] = _git_scalar(_git(selected, "rev-parse", "HEAD"))
        if raw != (pin.read_bytes() if pin.exists() else None):
            raise ObservationError("runtime selection changed during observation; sample again")
        result["selection_state"] = "KNOWN"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        problems.append("runtime selection unavailable (" + type(exc).__name__ + ")")
    try:
        # Reuse the existing manifest validation/configured-root observation.
        _, configured, _ = wall_view._service_block(home)
        if configured is None:
            problems.append("configured service roots unavailable")
        else:
            result["configured_service_roots"] = configured
        units = json.loads((home / "health/services.json").read_text())
        if not isinstance(units, list) or any(not isinstance(unit, str) or not unit.endswith(".service")
                                             or unit.startswith("-") or "/" in unit or any(c.isspace() for c in unit)
                                             for unit in units):
            raise ObservationError("invalid service manifest")
        result["service_states"] = dict.fromkeys(units, "UNKNOWN")
        # ``_live_import_roots`` answers ``None`` when ``/proc/<pid>/environ``
        # cannot be read and ``[]`` when it is readable and names no PYTHONPATH.
        # Those are different observations: an unreadable environment is
        # UNKNOWN, while a unit that declares no import root is a known state
        # recorded as an empty root list. Conflating them held the whole runtime
        # surface UNKNOWN forever for standalone units that import nothing from
        # the package, and named a readable environment "unavailable". A unit
        # with no MainPID is not running and is named as such.
        for unit, pid in _service_pids(units).items():
            if pid is None:
                problems.append("live service process not running: " + unit)
                continue
            roots = wall_view._live_import_roots(pid)
            if roots is None:
                problems.append("live service import roots unavailable: " + unit)
                continue
            result["service_roots"][unit] = sorted(set(roots))
            result["service_states"][unit] = "KNOWN"
        if configured is not None and all(state == "KNOWN" for state in result["service_states"].values()):
            result["services_state"] = "KNOWN"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        problems.append("service observation unavailable (" + type(exc).__name__ + ")")
    try:
        # Renderers are the declared windows that have a top-pane script, the
        # same set the RENDERER BYTES line checks. A top-pains entry with no
        # declared window is a stale surface nothing runs, not a renderer whose
        # bytes the observer cannot read. A declared window that is not running
        # cannot be verified and stays UNKNOWN, as does a manifest that cannot
        # be read; a manifest that declares windows but yields no renderer is
        # UNKNOWN rather than a vacuous KNOWN over an empty set.
        from .window_manifest import read as read_window_manifest
        manifest = home / "health" / "windows.json"
        declared = None
        if manifest.exists():
            try:
                declared = read_window_manifest(home)
            except (OSError, ValueError):
                problems.append("renderer window manifest unreadable")
        renderers = sorted(name for name in declared
                           if (home / "top-pains" / name).is_file()) if declared is not None else []
        if declared is not None and not renderers:
            problems.append("no declared renderer window has a top-pane script")
        result["renderer_states"] = dict.fromkeys(renderers, "UNKNOWN")
        if renderers:
            from .seed import recorded_session
            session = recorded_session(home)
            pids = wall_view._renderer_pids(session) if session else None
            if pids is None:
                problems.append("renderer session observation unavailable")
            for role in renderers:
                pid = pids.get(role) if pids is not None else None
                if pid is None:
                    problems.append("renderer not running: " + role)
                    continue
                roots = wall_view._live_import_roots(pid)
                if roots is None:
                    problems.append("live renderer import roots unavailable: " + role)
                    continue
                if not roots:
                    # A renderer that runs and imports nothing is not running the
                    # pinned package: the plant's own renderer contract
                    # (``wall_view.renderer_bytes_lines``) calls that drift, so
                    # it stays a problem here instead of reading as a clean
                    # observation of a rootless renderer.
                    problems.append("live renderer import roots absent: " + role)
                    continue
                result["renderer_roots"][role] = sorted(set(roots))
                result["renderer_states"][role] = "KNOWN"
        if declared is not None and renderers and all(state == "KNOWN" for state in result["renderer_states"].values()):
            result["renderers_state"] = "KNOWN"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        problems.append("renderer observation unavailable (" + type(exc).__name__ + ")")
    result["state"] = "UNKNOWN" if problems else "KNOWN"
    result["sha256"] = _digest(result)
    return result


@contextmanager
def _lock(home):
    directory = home / "checks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / ".substrate-watch.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _save_snapshot(path, row):
    row["snapshot_sha256"] = _digest({key: value for key, value in row.items() if key != "snapshot_sha256"})
    _save(path, row)


def _read_state(path):
    value = json.loads(path.read_text())
    keys = {"version", "status", "identity", "event_id", "event_path", "alerts", "checked_at",
            "kind", "checkout", "runtime", "problems", "state_damage", "snapshot_sha256"}
    if (not isinstance(value, dict) or not keys.issubset(value)
            or value["version"] != VERSION or value["status"] not in {"KNOWN", "UNKNOWN"}
            or value["kind"] not in {"baseline", "change", "recovery", "unknown", "unchanged"}
            or any(not isinstance(value[key], str) or len(value[key]) != 64
                   or any(c not in "0123456789abcdef" for c in value[key])
                   for key in ("identity", "event_id"))
            or value["event_path"] != str(path.parent / "substrate-watch-events" / (value["event_id"] + ".json"))
            or not isinstance(value["alerts"], dict)
            or any(key not in {"genome", "witness", "health"} or type(seq) is not int or seq <= 0
                   for key, seq in value["alerts"].items())
            or not isinstance(value["problems"], list)
            or any(not isinstance(problem, str) for problem in value["problems"])
            or (value["state_damage"] is not None and not isinstance(value["state_damage"], str))):
        raise ValueError("invalid substrate snapshot schema")
    if value["snapshot_sha256"] != _digest({key: item for key, item in value.items() if key != "snapshot_sha256"}):
        raise ValueError("substrate snapshot checksum mismatch")
    if value["status"] == "KNOWN":
        if (not isinstance(value["checkout"], dict) or not isinstance(value["runtime"], dict)
                or not {"root", "head", "sha256", "index_sha256", "status", "files"}.issubset(value["checkout"])
                or not {"selection", "source", "sha256", "service_roots", "renderer_roots"}.issubset(value["runtime"])):
            raise ValueError("incomplete substrate observation")
    _clock(datetime.fromisoformat(value["checked_at"]))
    try:
        _read_event(Path(value["event_path"]))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ObservationError("prior change evidence missing or corrupt") from exc
    return value


def _changes(previous, reading):
    changes = []
    for field in ("checkout", "runtime"):
        before, after = previous.get(field), reading.get(field)
        if before != after:
            changes.append(field)
    return changes


def _decode_event(event, path):
    report = event["report"]
    identity = _digest({key: report[key] for key in ("status", "checkout", "runtime", "problems", "state_damage")})
    expected = _digest({"previous": event["previous_event_id"], "identity": identity, "recipients": event["recipients"]})
    if (event["version"] != VERSION or event["event_id"] != expected
            or report["identity"] != identity or report["event_id"] != expected
            or path.name != expected + ".json"
            or not isinstance(event["recipients"], list)
            or any(recipient not in {"genome", "witness", "health"} for recipient in event["recipients"])):
        raise ObservationError("invalid immutable change evidence")
    if event["event_sha256"] != _digest({key: value for key, value in event.items() if key != "event_sha256"}):
        raise ObservationError("invalid immutable change evidence")
    return event


def _read_event(path):
    return _decode_event(json.loads(path.read_text()), path)


def _save_queue(path, queue):
    queue["queue_sha256"] = _digest({key: value for key, value in queue.items() if key != "queue_sha256"})
    _save(path, queue)


def _read_queue(path, history):
    queue = json.loads(path.read_text())
    event = _decode_event(queue["event"], history / path.name)
    if (queue["version"] != VERSION or not isinstance(queue["alerts"], dict)
            or any(role not in event["recipients"] or type(sequence) is not int or sequence <= 0
                   for role, sequence in queue["alerts"].items())
            or queue["queue_sha256"] != _digest({key: value for key, value in queue.items() if key != "queue_sha256"})):
        raise ObservationError("invalid pending delivery")
    return queue


def _pending(home, history):
    pending = {}
    problems = []
    for path in sorted((home / "checks/substrate-watch-pending").glob("*.json")):
        try:
            queue = _read_queue(path, history)
            event_path = history / path.name
            if not event_path.exists():
                # Enqueue is durable before the immutable archive is published.
                _save(event_path, queue["event"])
            elif _read_event(event_path) != queue["event"]:
                raise ObservationError("pending delivery disagrees with archived evidence")
            pending[queue["event"]["event_id"]] = (path, queue)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            problems.append("pending delivery corrupt; " + _preserve(home, path))
    return pending, problems


def _enqueue(home, event):
    path = home / "checks/substrate-watch-pending" / (event["event_id"] + ".json")
    history = home / "checks/substrate-watch-events"
    if path.exists():
        return path, _read_queue(path, history)
    queue = {"version": VERSION, "event": event, "alerts": {}}
    _save_queue(path, queue)
    return path, queue


def _send(home, source, recipient, text):
    # Same addressed envelope as wall.message, using Feed's atomic once identity
    # because a service may die after append but before recording its sequence.
    text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return Feed(home).append(source, f"[dm] to={recipient}\n{text}", once=True)


def _notice(event, recipient):
    row = event["report"]
    lines = [f"Substrate {row['kind']}: {row['status']}; event={event['event_id']}",
             f"Observed={row['checked_at']}; changed={','.join(row['changed']) or 'none'}"]
    checkout = row.get("checkout")
    if checkout:
        lines.append(f"Checkout={checkout['root']} state={checkout.get('state', 'UNKNOWN')} HEAD={checkout['head']} sha256={checkout['sha256']} index_sha256={checkout['index_sha256']}")
        for item in checkout["status"]:
            line = "Changed path=" + json.dumps(item["path"], ensure_ascii=True)
            line += " status=" + json.dumps(item["status"])
            if "from" in item:
                line += " from=" + json.dumps(item["from"], ensure_ascii=True)
            lines.append(line)
    runtime = row.get("runtime")
    if runtime:
        lines.append(f"Runtime={runtime['source']} selection={runtime['selection']} selection_sha256={runtime['selection_sha256']} sha256={runtime['sha256']}")
        for category in ("service_roots", "renderer_roots"):
            for consumer, roots in sorted(runtime[category].items()):
                lines.append("Live " + category + " consumer=" + json.dumps(consumer, ensure_ascii=True)
                             + " roots=" + ", ".join(json.dumps(root, ensure_ascii=True) for root in roots))
    if row["problems"]:
        lines.append("UNKNOWN evidence=" + "; ".join(row["problems"]))
    lines.append(f"Evidence={row['event_path']} (exact per-path hashes; no file contents).")
    actions = {
        "genome": "Within the next observation interval inspect the changed paths/hashes and runtime roots, choose one bounded source/integration check, then record activation/recovery evidence if warranted. Observation is not permission to execute source; this watcher never activates or restarts.",
        "witness": "Within the next observation interval verify genome has a bounded follow-through action and evidence, or address the unresolved integration gap. Do not treat a changed checkout as deployed runtime.",
        "health": "Within the next observation interval inspect the named failed observation and its saved evidence, restore that observation, and rerun substrate_watch. UNKNOWN is not healthy; recovery is not causal resolution.",
    }
    lines.append("Next bounded action: " + actions[recipient])
    return "\n".join(lines)


def check(home, *, now=None):
    """Sample once; refresh a durable heartbeat and DM only new evidence transitions.

    Corrupt/missing established state is sticky UNKNOWN until repaired explicitly;
    ordinary Git/runtime failures recover automatically on the next valid sample.
    """
    home = Path(home).resolve()
    stamp = _clock(now).isoformat()
    path = home / "checks/substrate-watch.json"
    history = home / "checks/substrate-watch-events"
    with _lock(home):
        previous = None
        damage = None
        try:
            previous = _read_state(path)
            damage = previous.get("state_damage")
        except FileNotFoundError:
            if history.exists() and any(history.iterdir()):
                damage = "established snapshot missing"
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            # Retention must succeed before replacing damaged monitoring state.
            cause = str(exc) if isinstance(exc, ObservationError) else type(exc).__name__
            damage = "prior snapshot corrupt or unreadable (" + cause + "); " + _preserve(home, path)
        pending, delivery_problems = _pending(home, history)
        row = {"version": VERSION, "checked_at": stamp, "status": "KNOWN", "problems": delivery_problems,
               "checkout": None, "runtime": None, "state_damage": damage}
        for name, observe in (("checkout", _checkout), ("runtime", _runtime)):
            try:
                row[name] = observe(home)
                row["problems"].extend(name + ": " + problem for problem in row[name].get("problems", []))
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
                # Do not expose exception text: subprocess stderr/JSON errors may contain private values.
                detail = str(exc) if isinstance(exc, ObservationError) else type(exc).__name__
                row["problems"].append(name + ": " + detail)
        row["surface_states"] = {name: reading.get("state", "UNKNOWN") if reading else "UNKNOWN"
                                 for name, reading in (("checkout", row["checkout"]), ("runtime", row["runtime"]))}
        if damage:
            row["problems"].append(damage)
        if row["problems"]:
            row["status"] = "UNKNOWN"
        row["identity"] = _digest({key: row[key] for key in ("status", "checkout", "runtime", "problems", "state_damage")})
        changed = previous is None or previous["identity"] != row["identity"]
        row["changed"] = _changes(previous or {}, row)
        row["kind"] = ("unknown" if row["status"] == "UNKNOWN" else
                       "baseline" if previous is None else
                       "recovery" if previous["status"] == "UNKNOWN" else "change") if changed else "unchanged"
        if changed:
            recipients = (["health"] if row["status"] == "UNKNOWN" else
                          [] if row["kind"] == "baseline" else ["genome", "witness"])
            # Partial source/runtime evidence still reaches its integration owners.
            if row["status"] == "UNKNOWN" and previous and row["changed"]:
                recipients = ["genome", "witness", "health"]
            event_id = _digest({"previous": previous["event_id"] if previous else None,
                                "identity": row["identity"], "recipients": recipients})
            event_path = history / (event_id + ".json")
            row.update(event_id=event_id, event_path=str(event_path), alerts={})
            event = {"version": VERSION, "event_id": event_id, "previous_event_id": previous["event_id"] if previous else None,
                     "recipients": recipients, "report": dict(row, problems=list(row["problems"]), alerts={})}
            event["event_sha256"] = _digest(event)
            if event_path.exists():
                saved = _read_event(event_path)
                if saved["event_id"] != event_id or saved["report"]["identity"] != row["identity"]:
                    raise ObservationError("immutable substrate event conflicts with observation")
                event = saved
            # The queue copy is the durable enqueue. A crash before either the
            # archive or latest snapshot is saved cannot orphan this notice.
            if event["recipients"]:
                pending[event_id] = _enqueue(home, event)
            if not event_path.exists():
                _save(event_path, event)
        else:
            row.update(event_id=previous["event_id"], event_path=previous["event_path"], alerts=previous["alerts"])
            event = _read_event(Path(row["event_path"]))
        # Checkpoint the event before publishing. Stable event text + canonical Feed
        # identity dedup closes a crash between append and the final checkpoint.
        _save_snapshot(path, row)
        for pending_id, (queue_path, queue) in pending.items():
            queued_event = queue["event"]
            for recipient in queued_event["recipients"]:
                if recipient in queue["alerts"]:
                    continue
                try:
                    entry = _send(home, SOURCE, recipient, _notice(queued_event, recipient))
                    queue["alerts"][recipient] = entry.sequence
                    _save_queue(queue_path, queue)
                except (OSError, ValueError) as exc:
                    row["status"] = "UNKNOWN"
                    row["problems"].append("DM delivery unavailable: " + recipient + " event=" + pending_id
                                           + " (" + type(exc).__name__ + ")")
            if pending_id == row["event_id"]:
                row["alerts"] = dict(queue["alerts"])
                _save_snapshot(path, row)
            if all(recipient in queue["alerts"] for recipient in queued_event["recipients"]):
                queue_path.unlink()
                directory = os.open(queue_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        _save_snapshot(path, row)
        return row


def snapshot_lines(home, *, now=None, max_age=120):
    """Render cached evidence only; never poll Git/systemd from a dashboard."""
    path = Path(home).resolve() / "checks/substrate-watch.json"
    try:
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("invalid maximum age")
        row = _read_state(path)
        age = (_clock(now) - _clock(datetime.fromisoformat(row["checked_at"]))).total_seconds()
        if age < 0:
            return ["SUBSTRATE: UNKNOWN — snapshot is future-dated"]
        if age > max_age:
            return [f"SUBSTRATE: UNKNOWN — stale snapshot checked_at={row['checked_at']}"]
        lines = [f"SUBSTRATE: {row['status']} {row['kind']} checked_at={row['checked_at']} event={row['event_id']}"]
        checkout, runtime = row["checkout"], row["runtime"]
        if checkout:
            lines.append(f"SOURCE: {checkout.get('state', 'UNKNOWN')} HEAD={checkout['head']} sha256={checkout['sha256']} changed_paths={len(checkout['status'])}")
        else:
            lines.append("SOURCE: UNKNOWN — checkout observation unavailable")
        if runtime:
            lines.append(f"RUNTIME: {runtime.get('state', 'UNKNOWN')} selection={runtime['selection']} source={runtime['source']} sha256={runtime['sha256']}")
        else:
            lines.append("RUNTIME: UNKNOWN — runtime observation unavailable")
        if row["problems"]:
            lines.append("SUBSTRATE PROBLEMS: " + "; ".join(row["problems"]))
        return lines
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return ["SUBSTRATE: UNKNOWN — snapshot missing, corrupt or unreadable"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be finite and positive")
    while True:
        try:
            report = check(args.home)
            print("\n".join(snapshot_lines(args.home, max_age=max(120, args.interval * 2))), flush=True)
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            print(f"SUBSTRATE: UNKNOWN — observer persistence/delivery failure ({type(exc).__name__})", flush=True)
            if not args.watch:
                return 1
        if not args.watch:
            return 0 if report["status"] == "KNOWN" else 1
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
