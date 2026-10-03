"""Read-only local frontier scan; values are evidence, not authority."""

from __future__ import annotations
import ast

import json
import os
import time
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed

COMMANDS = ("rg", "git", "tmux", "python3", "systemctl", "journalctl", "ps", "df",
            "lsusb", "lspci", "sensors", "upower", "evtest")

KERNEL_FAULT = "Failed to resubmit video URB"

SENSOR_ID = re.compile(r"^sense\.[a-z0-9]+(?:\.[a-z0-9-]+)+$")

def _thermal_slots(root: Path) -> list[Path]:
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    slots: list[Path] = []
    for entry in entries:
        try:
            slots.extend(sorted(entry.glob("temp*_input"))[:16])
        except OSError:
            continue
    return slots



def _read(path: Path, limit: int = 65536) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return None

def _cpu_busy(path: Path = Path("/proc/stat"), samples: int = 2, interval: float = 0.1) -> dict:
    """Measure short-window host CPU busyness from aggregate /proc/stat deltas."""
    counters: list[list[int]] = []
    for index in range(samples):
        head = _read(path, 512)
        if head is None:
            return {}
        lines = head.splitlines()
        if not lines:
            return {}
        fields = lines[0].split()
        if not fields or fields[0] != "cpu" or len(fields) < 9:
            return {}
        if not all(token.isdigit() for token in fields[1:]):
            return {}
        counters.append([int(token) for token in fields[1:]])
        if index + 1 < samples:
            time.sleep(interval)
    deltas: list[list[int]] = []
    for previous, current in zip(counters, counters[1:]):
        if len(previous) != len(current) or any(now < before for before, now in zip(previous, current)):
            return {}
        deltas.append([now - before for before, now in zip(previous, current)])
    if not deltas:
        return {}
    # user/nice/system/idle/iowait/irq/softirq/steal; guest counters overlap user.
    total = sum(sum(delta[:8]) for delta in deltas)
    idle = sum(delta[3] + delta[4] for delta in deltas)
    if total <= 0 or idle > total:
        return {}
    busy = 100.0 * (total - idle) / total
    return {"busy": busy, "idle": 100.0 - busy}

TOP_CPU_ROWS = 8
"""Rows retained per scan; a later scan attributes an event from this bounded set."""

TOP_CPU_SAMPLE_SECONDS = 1.0
"""Window between the two /proc cpu reads. One clock tick is 1/100 s, so a one
second window resolves a rate in 1%-of-core steps; a shorter window drops a
sustained but modest consumer to zero ticks and discards it again."""

try:
    _CLOCK_TICK = os.sysconf("SC_CLK_TCK")
except (AttributeError, OSError, ValueError):
    _CLOCK_TICK = 100


def _cpu_jiffies(pid: int) -> int | None:
    """utime + stime for one process in clock ticks, or None when unreadable.

    `comm` may contain spaces and parentheses, so the field offset is taken past
    the last closing parenthesis rather than by splitting the whole line.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    fields = raw[raw.rindex(")") + 1:].split()
    # utime and stime follow state, ppid, pgrp, sid, tty, tpgid, flags and four
    # fault counters, so they are fields 11 and 12 after `comm`.
    if len(fields) < 13:
        return None
    try:
        return int(fields[11]) + int(fields[12])
    except ValueError:
        return None


def _ps_processes(session: str | None = None) -> dict[int, dict[str, object]]:
    """One bounded `ps` read: pid, parent, lifetime %CPU, elapsed seconds, command.

    `ppid` comes from the same snapshot as the row, so a retained row's ancestry is
    internally consistent by construction rather than being read in a second pass.
    A later reader cannot reconstruct it: on this host five of eight retained
    top-CPU rows are gone from a snapshot taken seconds later, because the busiest
    processes are short-lived `mesh-*` tools.
    """
    try:
        result = subprocess.run(["ps", "-eo", "pid,ppid,pcpu,etimes,args", "--no-headers"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    processes: dict[int, dict[str, object]] = {}
    parents: dict[int, int] = {}
    commands: dict[int, str] = {}
    for line in result.stdout.splitlines():
        columns = line.split(None, 4)
        if len(columns) < 5:
            continue
        pid_text, ppid_text, pcpu_text, etimes_text, args_text = columns
        if not pid_text.isdigit() or not ppid_text.isdigit():
            continue
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
            pcpu = float(pcpu_text)
            etimes = int(etimes_text)
        except ValueError:
            continue
        args = " ".join(args_text.split())[:160]
        parents[pid] = ppid
        commands[pid] = args
        processes[pid] = {"pid": pid, "ppid": ppid, "pcpu": pcpu,
                          "etimes": etimes, "args": args}
    if session is not None:
        # Mark descent from this session, recording an explicit in_session key so
        # a reader distinguishes "not in this session" from "descent was not
        # assessed". One tmux server hosts every session on a socket, so a server
        # that is an ancestor is not sufficient membership evidence on its own:
        # the peers of this session share it. Descent is therefore claimed only
        # for a process under one of this session's pane roots, whose own
        # ancestry still has to reach the server, so a pane whose tmux tree was
        # severed reports undetermined instead of claiming membership.
        server = _session_server(session)
        roots = _session_pane_roots(session)
        for pid in processes:
            processes[pid]["in_session"] = _session_membership(pid, parents, server, roots)
    return processes


def _session_server(session: str) -> int | None:
    """The pid of the server hosting `session`, or None when it is not running.

    `_new_session` raises the server with `systemd-run --user --scope`, so the
    server's own argv sits behind that wrapper and does not name the session it
    hosts. Worse, one server hosts every session on a socket: on this plant the
    single server started as `tmux new-session -d -s mishe-tiny-fleet` also hosts
    `mishe-self-development-current`, so matching argv words resolved descent for
    one session and left every other session's rows reading undetermined. Asking
    tmux for the server pid answers the socket actually backing the session.

    `has-session` is checked first because a session that has exited still has a
    live server hosting others: without it, the pid would be returned and every
    row would be assessed against a server the session no longer belongs to.
    """
    if shutil.which("tmux") is None:
        return None
    if subprocess.run(["tmux", "has-session", "-t", session],
                      capture_output=True).returncode != 0:
        return None
    result = subprocess.run(["tmux", "display-message", "-p", "-t", session, "#{pid}"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        pid = int(result.stdout.split()[0])
    except (ValueError, IndexError):
        return None
    return pid


def _descends_from(pid: int, parents: dict[int, int], ancestor: int | None,
                   limit: int = 48) -> bool | None:
    """Whether `pid` reaches `ancestor` by parent, claiming only what is known.

    Returns None when the chain cannot be walked to a decision — the ancestor is not
    running, or a parent is missing from this snapshot — instead of reporting that
    as `False`. A missing parent is a process that exited between the snapshot and
    this walk, so descent is genuinely undetermined, not absent.
    """
    if ancestor is None:
        return None
    seen: set[int] = set()
    current = pid
    for _ in range(limit):
        if current == ancestor:
            return True
        if current in seen:
            return None
        seen.add(current)
        parent = parents.get(current)
        if parent is None:
            # This pid is absent from the snapshot, so the chain is broken rather
            # than complete: its own parent may well reach the ancestor, and
            # reporting "outside the session" would be a guess.
            return None
        if parent == 0:
            # ppid 0 is init or a kernel thread, neither of which has a parent to
            # walk; the chain is complete and the ancestor is not above it.
            return False
        current = parent
    return None


def _session_pane_roots(session: str) -> list[int]:
    """The pids tmux reports as the foreground process of the session's panes.

    A server's process subtree spans every session it hosts, so the panes are
    what separates one session's work from another's. A pane whose command has
    exited is still listed: tmux reports the pid it spawned, which is the root a
    descendant is reached from.
    """
    if shutil.which("tmux") is None:
        return []
    result = subprocess.run(["tmux", "list-panes", "-s", "-t", session,
                            "-F", "#{pane_pid}"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    roots: list[int] = []
    for token in result.stdout.split():
        try:
            roots.append(int(token))
        except ValueError:
            continue
    return roots


def _session_membership(pid: int, parents: dict[int, int], server: int | None,
                        roots: list[int]) -> bool | None:
    """Whether `pid` is a member of the session, claiming only what is known.

    Reaching a pane root is not enough on its own: that pane may belong to a
    session sharing this server, and a pane detached from a dead server would
    claim every process beneath it. The root's own ancestry is re-walked to the
    server, so membership needs both a live session pane and the server it
    belongs to. None is returned when the server or the pane roots are unreadable,
    or a chain to a root is broken: an unreadable session is reported as
    undetermined rather than as outside it.
    """
    if server is None or not roots:
        return None
    undecided = False
    for root in roots:
        reached = _descends_from(pid, parents, root)
        if reached is None:
            # The chain to this root is broken or too deep to walk, so this root
            # decides nothing. Another root may still decide, and only if none
            # does is the membership undetermined.
            undecided = True
            continue
        if not reached:
            continue
        return _descends_from(root, parents, server) is True
    return None if undecided else False

def _rate_key(row: dict[str, object]) -> tuple[float, float]:
    """Rank by the sampled rate; the lifetime average only breaks its ties."""
    rate = row.get("rate")
    return ((rate if rate is not None else -1.0), row["pcpu"])


def _row_text(row: dict[str, object]) -> str:
    """Render one retained row, naming the keys that produced it."""
    in_session = row.get("in_session")
    tag = ("" if in_session is None
           else " in-session" if in_session is True else " outside-session")
    if row.get("rate") is None:
        return (f"pid={row['pid']} ppid={row['ppid']} rate=unmeasured "
                f"pcpu={row['pcpu']:.1f}% etimes={row['etimes']}s{tag} {row['args']}")
    return (f"pid={row['pid']} ppid={row['ppid']} rate={row['rate']:.1f}% "
            f"pcpu={row['pcpu']:.1f}% etimes={row['etimes']}s{tag} {row['args']}")


def _top_cpu_processes(session: str | None = None,
                       rows: int = TOP_CPU_ROWS,
                       sample_seconds: float = TOP_CPU_SAMPLE_SECONDS) -> list[dict[str, object]]:
    """Retain the busiest processes so a later scan can attribute, not just detect.

    Aggregate counters notice a pressure event only after it ends, and `ps` polled
    afterwards sees nothing when the cause was sub-second. Keeping a bounded sample
    in every persisted scan makes a sustained event attributable from the scan set
    alone (discover wake 13786, evidenced senses 13884 -> 14053).

    Ranking on `ps` %CPU does not serve that goal: it is a *lifetime* average
    (cputime / etimes), so a long-running process cannot move it within one busy
    second while a young process reaches the top rows by birth, and it is a
    division by zero at `etimes` 0. A two-sample /proc cpu delta rates the window
    the event actually lives in; the lifetime average and `etimes` stay as columns
    for context. A process that burned no tick in the window still ranks below one
    that did, and its lifetime average orders those ties rather than deciding them.

    `ppid` and `in_session` are recorded beside the row because a later reader
    cannot recover them: most retained rows are short-lived tools that have already
    exited by the next snapshot.
    """
    processes = _ps_processes(session)
    if not processes:
        return []
    first = {pid: _cpu_jiffies(pid) for pid in processes}
    time.sleep(sample_seconds)
    for pid, info in processes.items():
        later = _cpu_jiffies(pid)
        start = first.get(pid)
        if start is None or later is None or later < start:
            # Unreadable, or the pid was recycled between the two reads; either
            # way the delta is not about this process, so it stays unmeasured
            # rather than being reported as idle in the window.
            continue
        info["rate"] = 100.0 * (later - start) / _CLOCK_TICK / sample_seconds
    return sorted(processes.values(), key=_rate_key, reverse=True)[:rows]
def _journal_error_window(past_minutes: int = 10, limit: int = 400) -> dict:
    """Read the journal's own error class over a bounded window.

    Returns a neutral count of one well-known kernel-driver message, never a
    degradation verdict: on this host the uvcvideo resubmit rate is idle-camera
    status noise and is inversely related to camera use (discover wake 116), so
    the sample names every contributing endpoint instead of implying health.
    A full-boot scan is unbounded and is never attempted.
    """
    try:
        primary = subprocess.run(
            ["journalctl", "-b", "-p", "err", "--since", f"-{past_minutes}min",
             "-o", "cat", "--no-pager"],
            capture_output=True, text=True, timeout=10)
        if primary.returncode != 0:
            primary = subprocess.run(
                ["journalctl", "-b", "-p", "err", "-n", str(limit),
                 "-o", "cat", "--no-pager"],
                capture_output=True, text=True, timeout=10)
        window = primary.stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return {}
    faults = [line for line in window if KERNEL_FAULT in line]
    if not faults:
        return {"state": "verified" if window else "unknown",
                "sample": (f"last-{past_minutes}min kernel-error-count=0"
                           if window else "journal error window unavailable")}
    endpoints = sorted({line.split(": Failed", 1)[0].strip() for line in faults})
    names = ",".join(endpoints)
    return {"state": "verified",
            "sample": f"last-{past_minutes}min kernel-error-count={len(faults)} endpoints={names}"}



def _import_root(path: str) -> str | None:
    """The source root a PYTHONPATH entry imports, or None when it names none.

    A release install imports from ``<release>/src`` and a checkout from its own
    root, so dropping a trailing ``src`` component yields the root either layout
    names. This matches :func:`mishe_tauftauf.wall_view._import_roots`, keeping
    the dashboard and the scan reading one root per service.
    """
    if not path:
        return None
    candidate = Path(path)
    return str(candidate.parent) if candidate.name == "src" else str(candidate)


def _service_import_roots(home: Path) -> tuple[list[str], str | None]:
    """Import roots the listed seed services actually run, with a failure reason.

    ``systemctl --user show`` reports the *effective* environment, so a drop-in
    overriding the base unit cannot hide: its value is the one systemd applies.
    """
    manifest = home / "health" / "services.json"
    try:
        units = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return [], "service manifest unreadable"
    if not isinstance(units, list) or not units:
        return [], "service manifest empty"
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", *units,
                                 "-p", "Id,Environment"],
                                capture_output=True, text=True, timeout=5,
                                env=environment)
    except (OSError, subprocess.SubprocessError):
        return [], "systemctl unavailable"
    if result.returncode:
        return [], "systemctl failed"
    roots: list[str] = []
    seen: set[str] = set()
    for block in result.stdout.split("\n\n"):
        values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
        for item in values.get("Environment", "").split():
            if not item.startswith("PYTHONPATH="):
                continue
            for component in item[len("PYTHONPATH="):].split(os.pathsep):
                root = _import_root(component)
                if root and root not in seen:
                    seen.add(root)
                    roots.append(root)
    return roots, None


def _pinned_root(home: Path, default: str) -> tuple[str | None, str | None]:
    """The root the runtime pin names, or None with a reason it could not."""
    try:
        from .runtime_source import source_for
        return str(source_for(Path(home), Path(default)).resolve()), None
    except (OSError, TypeError, ValueError, ImportError) as exc:
        return None, f"pin invalid: {exc}"


def _runtime_drift(home: Path) -> dict[str, object]:
    """Compare the pin with the roots the seed services import.

    A service can run a release older or newer than the pin without any pane going
    dark, and a drop-in can outrank the base unit it amends, so the effective
    environment is the only honest source. Distinct roots are a drift even when one
    of them is the pin: the services disagree with each other as well as the record.
    """
    pinned, pin_failure = _pinned_root(home, str(Path(home).resolve().parent))
    roots, failure = _service_import_roots(home)
    if failure is not None:
        return {"id": "sense.runtime.drift", "state": "unknown",
                "sample": f"service roots unreadable: {failure}", "kind": "read"}
    if pin_failure is not None:
        return {"id": "sense.runtime.drift", "state": "unknown",
                "sample": f"service roots={roots} but {pin_failure}", "kind": "read"}
    distinct = sorted(set(roots))
    state = "verified" if distinct == [pinned] else "drift"
    sample = (f"pin={pinned} services={','.join(distinct)} {state}"
              if distinct else f"pin={pinned} services=none {state}")
    return {"id": "sense.runtime.drift", "state": state, "sample": sample,
            "kind": "read", "identity": {"pin": pinned, "services": distinct}}

def _sensor_names(root: str) -> tuple[set[str], str | None]:
    """The ``sense.*`` ids a source root can emit, or None with a reason.

    Roots are directories, not imports, so the module is parsed rather than
    imported: a release with a syntax error reports the failure instead of
    poisoning this process's own namespace. A root names its package under
    ``src/``, the layout a release and a checkout share.
    """
    module = Path(root) / "src" / "mishe_tauftauf" / "discovery.py"
    try:
        source = module.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set(), "module unreadable"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return set(), f"module unparseable: {exc}"
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = SENSOR_ID.match(node.value)
            if match:
                names.add(match.group(0))
    return names, None


def _sensor_coverage(home: Path, roots: list[str]) -> dict[str, object]:
    """Compare the sensors each imported root can emit with the pin's.

    ``sense.runtime.drift`` compares roots and is blind once they agree, because a
    release can carry a source tree that silently drops or adds a sensor. A restart
    into such a release changes what the plant can observe with no other visible
    sign, so the emitted ids are counted per root and compared with the pin's.
    The pin is the reference rather than the previous sample: after the restart
    being caught, ``latest.json`` already lists the reduced set and would hide it.
    """
    if not roots:
        return {"id": "sense.runtime.sensor-coverage", "state": "unknown",
                "sample": "no imported root to read", "kind": "read"}
    per_root: dict[str, list[str]] = {}
    failures: dict[str, str] = {}
    for root in roots:
        names, failure = _sensor_names(root)
        if failure is None:
            per_root[root] = sorted(names)
        else:
            failures[root] = failure
    if failures and not per_root:
        reason = next(iter(failures.values()))
        return {"id": "sense.runtime.sensor-coverage", "state": "unknown",
                "sample": f"sensor set unreadable: {reason}", "kind": "read"}
    pinned, pin_failure = _pinned_root(home, str(Path(home).resolve().parent))
    baseline: set[str] = set()
    if pinned is not None:
        names, pin_module_failure = _sensor_names(pinned)
        baseline = set() if pin_module_failure is not None else names
        if pin_module_failure is not None:
            pin_failure = pin_failure or pin_module_failure
    missing = {root: sorted(baseline - set(names))
               for root, names in per_root.items() if baseline - set(names)}
    extra = {root: sorted(set(names) - baseline)
             for root, names in per_root.items() if set(names) - baseline}
    state = "verified"
    parts = [", ".join(f"{Path(root).name}={len(names)}" for root, names in per_root.items())]
    if baseline:
        # The pin's sensor set is the ground truth. Without it, both directions of
        # the comparison are meaningless: every sensor a service has would read as
        # added, and every one it lacks as dropped.
        if missing:
            state = "drift"
            for root, names in sorted(missing.items()):
                parts.append(f"{Path(root).name} missing={','.join(names)}")
        for root, names in sorted(extra.items()):
            if state == "verified":
                state = "drift"
            parts.append(f"{Path(root).name} added={','.join(names)}")
    if failures:
        state = "drift"
        parts.append("unreadable=" + ",".join(sorted(failures)))
    if not baseline:
        state = "unknown" if state == "verified" else state
        parts.append(f"pin sensor set unreadable: {pin_failure}")
    return {"id": "sense.runtime.sensor-coverage", "state": state,
            "sample": " ".join(parts), "kind": "read",
            "identity": {"roots": per_root, "unreadable": failures,
                         "missing": missing, "extra": extra,
                         "pin": pinned if pin_failure is None else None}}

def sample(home: Path) -> dict[str, object]:
    """Take bounded reads; never open input event streams or record key content."""
    observed: list[dict[str, object]] = []
    for command in COMMANDS:
        found = shutil.which(command)
        observed.append({"id": f"command.{command}", "state": "available" if found else "unavailable",
                         "sample": found or "not on PATH", "kind": "declaration"})
    journal = _journal_error_window()
    if journal:
        observed.append({"id": "sense.journal.kernel-error-rate", "kind": "read", **journal})
    else:
        observed.append({"id": "sense.journal.kernel-error-rate", "kind": "read",
                         "state": "unknown", "sample": "journal error window unavailable"})

    loadavg = _read(Path("/proc/loadavg"), 256)
    observed.append({"id": "sense.proc.loadavg", "state": "verified" if loadavg else "unknown",
                     "sample": loadavg.strip() if loadavg else "read unavailable", "kind": "read"})
    memory = _read(Path("/proc/meminfo"), 8192)
    available = re.search(r"^MemAvailable:\s+(\d+ kB)$", memory or "", re.MULTILINE)
    observed.append({"id": "sense.proc.memory-available", "state": "verified" if available else "unknown",
                     "sample": available.group(1) if available else "read unavailable", "kind": "read"})
    pressure = _read(Path("/proc/pressure/memory"), 512)
    pressure_lines = [line.strip() for line in (pressure or "").splitlines()
                      if re.match(r"^(some|full) avg10=[0-9]+(?:\.[0-9]+)? avg60=[0-9]+(?:\.[0-9]+)? "
                                  r"avg300=[0-9]+(?:\.[0-9]+)? total=[0-9]+$", line.strip())]
    pressure_ok = any(line.startswith("some ") for line in pressure_lines)
    observed.append({"id": "sense.proc.memory-pressure",
                     "state": "verified" if pressure_ok else "unknown",
                     "sample": "; ".join(pressure_lines) if pressure_ok else "pressure data unavailable",
                     "kind": "read"})
    cpu_pressure = _read(Path("/proc/pressure/cpu"), 512)
    cpu_pressure_lines = [line.strip() for line in (cpu_pressure or "").splitlines()
                          if re.match(r"^some avg10=[0-9]+(?:\.[0-9]+)? avg60=[0-9]+(?:\.[0-9]+)? "
                                      r"avg300=[0-9]+(?:\.[0-9]+)? total=[0-9]+$", line.strip())]
    cpu_pressure_ok = bool(cpu_pressure_lines)
    observed.append({"id": "sense.proc.cpu-pressure",
                     "state": "verified" if cpu_pressure_ok else "unknown",
                     "sample": "; ".join(cpu_pressure_lines) if cpu_pressure_ok else "pressure data unavailable",
                     "kind": "read"})
    io_pressure = _read(Path("/proc/pressure/io"), 512)
    io_pressure_lines = [line.strip() for line in (io_pressure or "").splitlines()
                         if re.match(r"^(some|full) avg10=[0-9]+(?:\.[0-9]+)? avg60=[0-9]+(?:\.[0-9]+)? "
                                     r"avg300=[0-9]+(?:\.[0-9]+)? total=[0-9]+$", line.strip())]
    io_pressure_ok = any(line.startswith("some ") for line in io_pressure_lines)
    observed.append({"id": "sense.proc.io-pressure",
                     "state": "verified" if io_pressure_ok else "unknown",
                     "sample": "; ".join(io_pressure_lines) if io_pressure_ok else "pressure data unavailable",
                     "kind": "read"})
    try:
        disk = os.statvfs(home.parent)
        free_bytes = disk.f_bavail * disk.f_frsize
        observed.append({"id": "sense.disk.free", "state": "verified", "sample": free_bytes, "kind": "read"})
        observed.append({"id": "sense.disk.inodes-available", "state": "verified",
                         "sample": disk.f_favail, "kind": "read"})
    except OSError:
        observed.append({"id": "sense.disk.free", "state": "unknown",
                         "sample": "statvfs unavailable", "kind": "read"})
        observed.append({"id": "sense.disk.inodes-available", "state": "unknown",
                         "sample": "statvfs unavailable", "kind": "read"})
    busy = _cpu_busy()
    if busy:
        observed.append({"id": "sense.proc.cpu-busy", "state": "verified",
                         "sample": f"short-window={0.1:.1f}s busy={busy['busy']:.1f}% idle={busy['idle']:.1f}%"
                                   + (" high" if busy["busy"] >= 85.0 else ""),
                         "kind": "read"})
    else:
        observed.append({"id": "sense.proc.cpu-busy", "state": "unknown",
                         "sample": "/proc/stat cpu fields unavailable", "kind": "read"})
    # An empty name means no session was named, which tmux resolves to the
    # calling session: membership would be claimed for every process in it. Pass
    # None instead so descent is reported as not assessed rather than attributed
    # to a session the scan never named.
    session = os.environ.get("MISHE_SEED_SESSION") or None
    top_cpu = _top_cpu_processes(session)
    if top_cpu:
        measured = [row for row in top_cpu if row.get("rate") is not None]
        observed.append({"id": "sense.proc.top-cpu", "state": "verified",
                         "sample": "; ".join(_row_text(row) for row in top_cpu)
                                   + (f" key=delta-rate-over-{TOP_CPU_SAMPLE_SECONDS:.1f}s"
                                      if measured else " key=ps-lifetime"),
                         "processes": top_cpu, "kind": "read"})
    else:
        # A missing ps or a failed read is a transient, not a structural absence: the
        # command is declared available and a retry can produce a sample.
        observed.append({"id": "sense.proc.top-cpu", "state": "unknown",
                         "sample": "ps process sample unavailable", "kind": "read"})
    interrupts = _read(Path("/proc/interrupts"), 65536)
    keyboard = []
    for line in (interrupts or "").splitlines():
        if re.search(r"i8042|atkbd|keyboard", line, re.IGNORECASE):
            columns = line.split(":", 1)
            if len(columns) == 2:
                numbers = []
                for token in columns[1].split():
                    if not token.isdigit():
                        break
                    numbers.append(int(token))
                if numbers:
                    keyboard.append(sum(numbers))
    if keyboard:
        observed.append({"id": "sense.input.keyboard-interrupt-count",
                         "state": "verified", "sample": sum(keyboard), "kind": "counter"})
    elif interrupts is None:
        observed.append({"id": "sense.input.keyboard-interrupt-count", "state": "unknown",
                         "sample": "counter unreadable; /proc/interrupts unavailable",
                         "kind": "counter"})
    else:
        # The interrupt source is readable but names no keyboard device, so no
        # counter exists on this host. That is a structural absence, not a
        # transient read failure: retrying the same read cannot produce one.
        observed.append({"id": "sense.input.keyboard-interrupt-count", "state": "unavailable",
                         "sample": "no keyboard interrupt source on this host", "kind": "counter"})
    wakeup_root = Path("/sys/class/wakeup")
    wakeup_counts = []
    if wakeup_root.is_dir():
        for path in list(wakeup_root.glob("*/event_count"))[:128]:
            value = _read(path, 64)
            if value and value.strip().isdigit():
                wakeup_counts.append(int(value.strip()))
    observed.append({"id": "sense.sys.wakeup-count",
                     "state": "verified" if wakeup_counts else "unknown",
                     "sample": sum(wakeup_counts) if wakeup_counts else "counter unavailable",
                     "kind": "counter"})
    hwmon_root = Path("/sys/class/hwmon")
    temperatures: list[tuple[str, dict[str, str | None]]] = []
    for slot in _thermal_slots(hwmon_root):
        value = _read(slot, 64)
        if value is None or not value.strip().lstrip("-").isdigit():
            continue
        millidegrees = int(value.strip())
        if millidegrees < -273150 or millidegrees > 200000:
            # An out-of-range value is a stuck or absent sensor, not a
            # temperature; skipping keeps the reported sample honest.
            continue
        chip = _read(slot.parent / "name", 64)
        name = chip.strip() if chip else slot.parent.name
        channel = re.fullmatch(r"temp(\d+)_input", slot.name)
        if channel is None:
            continue
        index = channel.group(1)
        label_text = _read(slot.with_name(f"temp{index}_label"), 64)
        label = label_text.strip() if label_text and label_text.strip() else None
        channel_id = f"temp{index}" + (f"({label})" if label else "")
        device_path = slot.parent / "device"
        try:
            parent = str(device_path.resolve()) if device_path.exists() else None
        except (OSError, RuntimeError):
            parent = None
        channel_identity = f"{name}:{channel_id}"
        temperatures.append((f"{channel_identity}={millidegrees / 1000.0:.1f}C",
                             {"channel": channel_identity, "parent": parent}))
    thermal_identity = [identity for _, identity in temperatures]
    observed.append({"id": "sense.thermal.hwmon", "state": "verified" if temperatures else "unknown",
                     "sample": ", ".join(sample_text for sample_text, _ in temperatures)
                     if temperatures else "no readable hwmon temperature sensor",
                     "identity": thermal_identity, "kind": "read"})
    session = os.environ.get("MISHE_SEED_SESSION")
    if not session:
        try:
            session = (home / ".seed-raised").read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError):
            session = None
    if session:
        try:
            result = subprocess.run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
                                    capture_output=True, text=True, timeout=2)
            names = sorted(result.stdout.splitlines()) if result.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired):
            names = []
    else:
        names = []
    observed.append({"id": "sense.tmux.windows", "state": "verified" if names else "unknown",
                     "sample": names or "session unavailable", "kind": "read"})
    roots, _coverage_failure = _service_import_roots(home)
    observed.append(_sensor_coverage(home, roots))
    observed.append(_runtime_drift(home))
    return {"created": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "node": os.uname().nodename, "observations": observed}


def scan(home: Path) -> Path:
    previous = latest(home)
    snapshot = sample(home)
    root = home / "discovery"
    root.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["created"].replace(":", "").replace("-", "")
    artifact = root / f"scan-{stamp}-{os.getpid()}.json"
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    artifact.write_text(payload, encoding="utf-8")
    latest_path = root / "latest.json"
    temporary = root / f".latest-{os.getpid()}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, latest_path)
    readings = snapshot["observations"]
    available = [str(item["id"]).removeprefix("command.") for item in readings
                 if item["kind"] == "declaration" and item["state"] == "available"]
    unavailable = [str(item["id"]).removeprefix("command.") for item in readings
                   if item["kind"] == "declaration" and item["state"] == "unavailable"]
    verified = [item for item in readings if item["kind"] != "declaration" and item["state"] == "verified"]
    unknown = [item for item in readings if item["kind"] != "declaration" and item["state"] != "verified"]
    def signature(data: dict[str, object] | None) -> dict[str, tuple[str, str]]:
        if data is None:
            return {}
        result = {}
        for item in data.get("observations", []):
            identifier = str(item["id"])
            state = str(item["state"])
            sample_text = str(item["sample"])
            if item["kind"] == "declaration" or state != "verified":
                detail = sample_text
            elif identifier == "sense.proc.cpu-busy":
                detail = "high" if re.search(r"(?:^|\s)high(?:$|\s)", sample_text) else "not-high"
            elif identifier == "sense.thermal.hwmon":
                detail = json.dumps(item.get("identity", []), ensure_ascii=False, sort_keys=True)
            else:
                detail = ""
            result[identifier] = (state, detail)
        return result

    current_signature = signature(snapshot)
    old_signature = signature(previous)
    changed = [name for name in current_signature if current_signature[name] != old_signature.get(name)]
    if previous is not None and not changed:
        return artifact
    change_details = []
    for name in changed:
        if previous is None:
            change_details.append(name)
            continue
        old_reading = next((item for item in previous.get("observations", [])
                            if item["id"] == name), None)
        new_reading = next((item for item in readings if item["id"] == name), None)
        if old_reading and new_reading and old_reading["state"] == new_reading["state"] == "verified":
            if name == "sense.proc.cpu-busy":
                old_class = "high" if re.search(
                    r"(?:^|\s)high(?:$|\s)", str(old_reading["sample"])) else "not-high"
                new_class = "high" if re.search(
                    r"(?:^|\s)high(?:$|\s)", str(new_reading["sample"])) else "not-high"
                change_details.append(
                    f"{name} class {old_class} -> {new_class} (short sample: "
                    f"{old_reading['sample']} -> {new_reading['sample']})")
                continue
            if name == "sense.thermal.hwmon":
                def describe_identity(reading):
                    return "; ".join(
                        f"channel {item.get('channel', 'unknown')}, "
                        f"parent device {item.get('parent') or 'unavailable'}"
                        for item in reading.get("identity", []) if isinstance(item, dict)
                    ) or "unavailable"
                old_identity = describe_identity(old_reading)
                new_identity = describe_identity(new_reading)
                change_details.append(f"{name} identity {old_identity} -> {new_identity}")
                continue
        change_details.append(name)
    change_text = ("Initial baseline." if previous is None else
                   "Material changes: " + "; ".join(change_details) + ".")
    lines = [f"[discovery] Read-only scan at {snapshot['created']} on {snapshot['node']}.",
             change_text,
             f"Available commands ({len(available)}): {', '.join(available) or 'none'}.",
             f"Unavailable commands ({len(unavailable)}): {', '.join(unavailable) or 'none'}.",
             "Verified readings: " + ("; ".join(f"{item['id']} = {item['sample']}" for item in verified) or "none") + ".",
             "Unknown readings: " + ("; ".join(f"{item['id']} — {item['sample']}" for item in unknown) or "none") + ".",
             f"Full sample: {artifact.resolve()}.",
             "Next: senses should verify useful unknown readings or record why the source is unavailable; "
             "discover should seek one new useful read."]
    Feed(home).append("discover", "\n".join(lines))
    return artifact


def latest(home: Path) -> dict[str, object] | None:
    path = home / "discovery" / "latest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


RENEWAL_MAX_AGE_SECONDS = 600.0
"""Renew before the panes' 900-second freshness gate, leaving 300 seconds of margin."""


def scan_age(home: Path) -> float | None:
    """Age of the newest scan in seconds, or None when its timestamp is unavailable."""
    snapshot = latest(home)
    if snapshot is None:
        return None
    try:
        created = datetime.fromisoformat(str(snapshot["created"]).replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return None
    return (datetime.now(timezone.utc) - created).total_seconds()


def renew_scan(home: Path, max_age_seconds: float = RENEWAL_MAX_AGE_SECONDS) -> Path | None:
    """Renew a missing or old scan without requiring a resident mind wake.

    Return the new artifact path, or None while the latest scan is fresh.
    scan() updates latest.json and deduplicates unchanged states in the feed.
    """
    age = scan_age(home)
    if age is not None and age <= max_age_seconds:
        return None
    return scan(home)
