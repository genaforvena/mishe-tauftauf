"""Read-only local frontier scan; values are evidence, not authority."""

from __future__ import annotations
import ast

import hashlib
import json
import os
import time
import re
import shutil
import selectors
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .feed import Feed
from .scan_freshness import age_bounds, classify_age, endpoint

COMMANDS = ("rg", "git", "tmux", "python3", "systemctl", "journalctl", "ps", "df",
            "lsusb", "lspci", "sensors", "upower", "evtest")

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

def _chip_qualifier(chip: Path) -> str:
    """The shortest label that separates this chip from its same-named peers.

    Two controllers of the same model expose the same channel names under the
    same chip name, so ``nvme:temp1(Composite)`` names a reading of either drive
    and a sample printing both cannot attribute either temperature. The parent
    device distinguishes them; its basename is short enough for a sample string
    and stable across reboots, unlike a ``hwmonN`` index that is enumeration
    order. Falls back to the ``hwmon`` directory name when the chip exposes no
    resolvable parent device, which still separates the chips on this host even
    though it is not stable across replug or reboot.
    """
    device = chip / "device"
    try:
        if device.exists():
            resolved = device.resolve()
            if resolved.name:
                return resolved.name
    except (OSError, RuntimeError):
        pass
    return chip.name



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

    A session names the work to attribute processes to. An empty name names none:
    tmux resolves an empty target to the *calling* session, so `""` would be read
    as a real session and every process beneath this plant's panes would be
    claimed for a session the caller never named. Normalize it here, one level
    below both callers, so a future caller cannot pass the hazard through.
    """
    if session == "":
        session = None
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
_JOURNAL_OUTPUT_CAP = 1024 * 1024

def _kill_journal_process(process: subprocess.Popen, deadline: float) -> None:
    """Kill a failed journal command without waiting past its acquisition deadline."""
    if process.poll() is not None:
        return
    process.kill()
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        pass


def _journal_command(cmd: list[str], timeout: float = 10) -> bytes | None:
    """Return complete bounded stdout, or None when acquisition is incomplete."""
    deadline = time.monotonic() + timeout
    process = None
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            output = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if not selector.select(remaining):
                    raise subprocess.TimeoutExpired(cmd, timeout)
                chunk = os.read(process.stdout.fileno(),
                                min(65536, _JOURNAL_OUTPUT_CAP + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _JOURNAL_OUTPUT_CAP:
                    _kill_journal_process(process, deadline)
                    return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(cmd, timeout)
        if process.wait(timeout=remaining) != 0:
            return None
        if output and not output.endswith(b"\n"):
            return None
        return bytes(output)
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            _kill_journal_process(process, deadline)
        return None


def _journal_boot() -> str | None:
    value = _read(Path("/proc/sys/kernel/random/boot_id"), 128)
    if value is None:
        return None
    value = value.strip().lower().replace("-", "")
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else None


KERNEL_SOURCE_LIMIT = 8
"""Distinct kernel sources named in one sample line; the rest are folded into
``other``. A window is normally dominated by one repeating driver message and
the sample line is what a reader scans, so the largest classes are named first;
the full breakdown stays in ``classes``."""


def _kernel_source(message: str) -> str:
    """The reporting source of a kernel message: text before the first ``': '``.

    Kernel messages follow ``<source>: <text>``, where the source names the
    driver, subsystem or device that logged it. Grouping by that name separates
    a repeating driver message from an unrelated fault in the same window, so a
    raw count cannot be read as a fault count. A source is capped at 64
    characters so a long first line cannot balloon the breakdown. A message with
    no separator keeps its first line, and one that yields nothing is attributed
    to ``unattributed`` rather than dropped, so the classes always sum to the
    count.
    """
    head = message.split("\n", 1)[0]
    source, separator, _ = head.partition(": ")
    source = (source if separator else head).strip()[:64]
    return source or "unattributed"


def _journal_error_window(past_minutes: int = 10) -> dict:
    """Count visible kernel entries in one fixed window; never infer fault or rate.

    Entries are grouped by reporting source so a repeating driver message cannot
    be read as a fault count.
    """
    started = time.monotonic_ns()
    boot = _journal_boot()
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=past_minutes)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def microseconds(value: datetime) -> int:
        delta = value - epoch
        return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds

    lower, upper = microseconds(since), microseconds(until)
    coverage = {"since": since.isoformat(), "until": until.isoformat(),
                "boot_id": boot, "acquisition_started_ns": started}
    unknown = {"state": "unknown", "sample": "journal kernel window unavailable",
               "coverage": coverage}
    if boot is None:
        unknown["reason"] = "boot identity unavailable"
        return unknown
    output = _journal_command([
        "journalctl", f"--boot={boot}", "-k", "-p", "err",
        "--since", since.strftime("%Y-%m-%d %H:%M:%S.%f UTC"),
        "--until", until.strftime("%Y-%m-%d %H:%M:%S.%f UTC"),
        "-o", "json", "--output-fields=__CURSOR,__REALTIME_TIMESTAMP,_BOOT_ID,_TRANSPORT,PRIORITY,MESSAGE",
        "--no-pager", "--quiet"])
    after = _journal_boot()
    finished = time.monotonic_ns()
    coverage["acquisition_finished_ns"] = finished
    if after != boot or finished < started:
        unknown["reason"] = "acquisition epoch changed"
        return unknown
    if output is None:
        unknown["reason"] = "incomplete or failed acquisition"
        return unknown
    cursors: set[str] = set()
    classes: dict[str, int] = {}
    try:
        for line in output.decode("utf-8").splitlines():
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("record is not an object")
            cursor = record.get("__CURSOR")
            timestamp = record.get("__REALTIME_TIMESTAMP")
            priority = record.get("PRIORITY")
            if (not isinstance(cursor, str) or not cursor or cursor in cursors
                    or record.get("_BOOT_ID") != boot
                    or record.get("_TRANSPORT") != "kernel"
                    or not isinstance(priority, str) or priority not in {"0", "1", "2", "3"}
                    or not isinstance(timestamp, str)
                    or not re.fullmatch(r"[0-9]+", timestamp)
                    or not lower <= int(timestamp) <= upper):
                raise ValueError("invalid entry metadata")
            cursors.add(cursor)
            message = record.get("MESSAGE")
            source = _kernel_source(message) if isinstance(message, str) else "unattributed"
            classes[source] = classes.get(source, 0) + 1
    except (UnicodeError, ValueError, TypeError):
        unknown["reason"] = "invalid journal entry metadata"
        return unknown
    ordered = sorted(classes.items(), key=lambda item: (-item[1], item[0]))
    parts = [f"last-{past_minutes}min kernel-error-count={len(cursors)}"]
    parts.extend(f"{name}={count}" for name, count in ordered[:KERNEL_SOURCE_LIMIT])
    other = sum(count for _, count in ordered[KERNEL_SOURCE_LIMIT:])
    if other:
        parts.append(f"other={other}")
    return {"state": "verified", "sample": " ".join(parts), "count": len(cursors),
            "classes": classes, "coverage": coverage}



UNIT_FAILURE = re.compile(r"^(.+?): Failed with result '([^']+)'\.$")


def _journal_unit_failure_window(past_minutes: int = 10) -> dict:
    """Count systemd unit failures in one fixed window; never infer fault or rate.

    A unit that fails leaves ``Failed with result '<class>'`` in the journal,
    naming the failed unit and the class. ``systemctl show -p Result`` does not:
    a restart overwrites the result, and ``NRestarts`` counts only automatic
    restarts. The records are not kernel messages and are logged at warning
    priority, so ``-k -p err`` never sees them; the selector is the systemd
    identifier plus this message pattern, and the failed unit is read from the
    message because the sender's ``_SYSTEMD_UNIT`` may differ. Counts are raw,
    grouped by unit and class, and cover only the window; the window matches the
    scan renewal threshold, so consecutive scans cover the boot without a gap.
    """
    started = time.monotonic_ns()
    boot = _journal_boot()
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=past_minutes)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def microseconds(value: datetime) -> int:
        delta = value - epoch
        return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds

    lower, upper = microseconds(since), microseconds(until)
    coverage = {"since": since.isoformat(), "until": until.isoformat(),
                "boot_id": boot, "acquisition_started_ns": started}
    unknown = {"state": "unknown", "sample": "journal unit-failure window unavailable",
               "coverage": coverage}
    if boot is None:
        unknown["reason"] = "boot identity unavailable"
        return unknown
    output = _journal_command([
        "journalctl", f"--boot={boot}", "-o", "json",
        "--output-fields=__CURSOR,__REALTIME_TIMESTAMP,_BOOT_ID,SYSLOG_IDENTIFIER,MESSAGE",
        "--since", since.strftime("%Y-%m-%d %H:%M:%S.%f UTC"),
        "--until", until.strftime("%Y-%m-%d %H:%M:%S.%f UTC"),
        "--no-pager", "--quiet", "-p", "warning", "SYSLOG_IDENTIFIER=systemd"])
    after = _journal_boot()
    finished = time.monotonic_ns()
    coverage["acquisition_finished_ns"] = finished
    if after != boot or finished < started:
        unknown["reason"] = "acquisition epoch changed"
        return unknown
    if output is None:
        unknown["reason"] = "incomplete or failed acquisition"
        return unknown
    cursors: set[str] = set()
    units: dict[str, int] = {}
    classes: dict[str, int] = {}
    try:
        for line in output.decode("utf-8").splitlines():
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("record is not an object")
            cursor = record.get("__CURSOR")
            timestamp = record.get("__REALTIME_TIMESTAMP")
            if (not isinstance(cursor, str) or not cursor or cursor in cursors
                    or record.get("_BOOT_ID") != boot
                    or record.get("SYSLOG_IDENTIFIER") != "systemd"
                    or not isinstance(timestamp, str)
                    or not re.fullmatch(r"[0-9]+", timestamp)
                    or not lower <= int(timestamp) <= upper):
                raise ValueError("invalid entry metadata")
            cursors.add(cursor)
            message = record.get("MESSAGE")
            if not isinstance(message, str):
                continue
            match = UNIT_FAILURE.match(message)
            if match is None:
                continue
            units[match.group(1)] = units.get(match.group(1), 0) + 1
            classes[match.group(2)] = classes.get(match.group(2), 0) + 1
    except (UnicodeError, ValueError, TypeError):
        unknown["reason"] = "invalid journal entry metadata"
        return unknown
    total = sum(units.values())
    parts = [f"last-{past_minutes}min unit-failure-count={total}"]
    parts.extend(f"{name}={count}" for name, count in sorted(classes.items()))
    return {"state": "verified", "sample": " ".join(parts),
            "count": total, "units": units, "classes": classes,
            "coverage": coverage}


WEDGE_CHAIN_THRESHOLD = 3
"""Open continue chain length at or above which a mind is suspect."""

WEDGE_SPAN_THRESHOLD_MINUTES = 15.0
"""Minutes an open continue chain must span before a mind is suspect."""


def _omp_log_path(pid: int) -> Path | None:
    """The omp session log for one pane pid, or None when absent."""
    logs_dir = Path.home() / ".omp" / "logs"
    if not logs_dir.is_dir():
        return None
    matches = sorted(logs_dir.glob(f"omp.*.{pid}.log"),
                     key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _omp_continue_chain(pid: int, now: datetime) -> dict | None:
    """Open continue chain for one pane pid from its omp session log.

    The chain is the count of ``agent.continue scheduled`` events of *any*
    source since the last ``agent_end maintenance routing`` with
    ``stopReason="stop"`` (a completed turn). The span is minutes from the
    first continue in the open chain to *now*. Counting every source, rather
    than one retry class, keeps a wedge that manifests as stream stalls or
    unexpected stops from escaping; a class allowlist would also fail silently
    as omp adds classes. ``sources`` carries the per-source breakdown. Returns
    None when no log exists for the pid.
    """
    log_path = _omp_log_path(pid)
    if log_path is None:
        return None
    continues: list[datetime] = []
    sources: dict[str, int] = {}
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        timestamp = record.get("timestamp")
        if not isinstance(message, str) or not isinstance(timestamp, str):
            continue
        try:
            ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if message == "agent.continue scheduled":
            continues.append(ts)
            source = record.get("source")
            key = source if isinstance(source, str) and source else "unknown"
            sources[key] = sources.get(key, 0) + 1
        elif (message == "agent_end maintenance routing"
                and record.get("stopReason") == "stop"):
            continues = []
            sources = {}
    if not continues:
        return {"chain": 0, "span_minutes": 0.0, "sources": {}}
    span = (now - continues[0]).total_seconds() / 60.0
    return {"chain": len(continues), "span_minutes": round(span, 1),
            "sources": sources}


def _mind_wedge_suspects() -> dict:
    """Flag minds whose open omp continue chain indicates a wedge.

    Rule R: SUSPECT when the open chain is >= 3 and spans >= 15 min with no
    completed turn between. The signal reads omp's session log
    (``~/.omp/logs/omp.<date>.<pid>.log``); the sample carries a coverage tier
    (``panes=N with_log=M``), and a pane set with no log at all reads UNKNOWN
    rather than a clean bill, so a log format or path change cannot silently
    pass as healthy.
    """
    now = datetime.now(timezone.utc)
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{window_name}|#{pane_pid}"],
            capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.mind.wedge-suspect", "state": "unknown",
                "sample": "tmux pane enumeration unavailable", "suspects": [],
                "kind": "read"}
    if result.returncode != 0:
        return {"id": "sense.mind.wedge-suspect", "state": "unknown",
                "sample": "tmux pane enumeration failed", "suspects": [],
                "kind": "read"}
    suspects = []
    panes = 0
    with_log = 0
    for line in result.stdout.splitlines():
        parts = line.split("|", 1)
        if len(parts) != 2:
            continue
        window_name, pid_str = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        panes += 1
        chain = _omp_continue_chain(pid, now)
        if chain is None:
            continue
        with_log += 1
        if (chain["chain"] >= WEDGE_CHAIN_THRESHOLD
                and chain["span_minutes"] >= WEDGE_SPAN_THRESHOLD_MINUTES):
            suspects.append({"window": window_name, "pid": pid, **chain})
    if suspects:
        sample = "suspects=" + " ".join(
            f"{s['window']}(pid={s['pid']},chain={s['chain']},"
            f"span={s['span_minutes']}min,src="
            + ",".join(f"{name}:{count}"
                       for name, count in sorted(s["sources"].items())) + ")"
            for s in suspects)
    else:
        sample = "suspects=0"
    sample += f" panes={panes} with_log={with_log}"
    state = "verified" if with_log else "unknown"
    return {"id": "sense.mind.wedge-suspect", "state": state,
            "sample": sample, "suspects": suspects,
            "panes": panes, "with_log": with_log, "kind": "read"}

LEDGER_DV_PHASES = frozenset({
    "applied", "review-refused", "reverted", "revert-failed",
    "revert-observation-failed",
})
"""Phases where a boolean ``delivery_verified`` is accepted.

An observed-accepted set, not a code-derived one: the producer writes
``delivery_verified`` only when bytes move, so the code-derived delivery
outcomes are ``applied``, ``reverted``, ``revert-failed`` and
``revert-observation-failed``. ``review-refused`` stays because one frozen
pre-2026-10-01 record (``culture-audit-repair``) carries a boolean dv at
that phase and no current producer path produces that combination; dropping
it would latch that record as a permanent false positive. Extend the set when
a new delivery-outcome phase legitimately carries a boolean dv.
"""


def _patch_records(home: Path) -> tuple[list[dict], list[str]]:
    """Readable patch records and unreadable patch file names under the site home."""
    records: list[dict] = []
    unreadable: list[str] = []
    for path in sorted((home / "patches").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(path.name)
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            unreadable.append(path.name)
    return records, unreadable


def _ledger_delivery_invariant(home: Path) -> dict:
    """Flag boolean ``delivery_verified`` on a phase that is not a delivery outcome.

    The 38917 revival trigger's mechanical branch, monitored continuously: a
    boolean ``delivery_verified`` on a phase outside ``LEDGER_DV_PHASES``. The
    sense flags; it does not decide — a new legitimate delivery-outcome phase
    and a genuine collapse both read as "outside the set", and the "is a
    delivery outcome" judgment is not fully mechanical. The sample names the
    offending phase (``violations=<phase>:<count>``) so a mind can
    disposition without re-deriving. Coverage tier: ``records=N bool_dv=M``
    ends the sample, and an unreadable or empty store reads UNKNOWN rather
    than a clean bill, so a store-path or format change cannot pass as the
    invariant holding.
    """
    records, unreadable = _patch_records(home)
    bool_dv = [r for r in records if isinstance(r.get("delivery_verified"), bool)]
    if not records or unreadable:
        if not (home / "patches").is_dir():
            sample = "patch store unavailable"
        elif unreadable:
            sample = (f"records={len(records)} bool_dv={len(bool_dv)} "
                      f"unreadable={','.join(unreadable[:3])}"
                      + ("…" if len(unreadable) > 3 else ""))
        else:
            sample = f"records=0 bool_dv=0"
        return {"id": "sense.ledger.delivery-invariant", "state": "unknown",
                "sample": sample, "records": len(records),
                "bool_dv": len(bool_dv), "violations": {}, "kind": "read"}
    violations: dict[str, int] = {}
    for record in bool_dv:
        phase = record.get("phase")
        key = phase if isinstance(phase, str) else "unknown"
        if key not in LEDGER_DV_PHASES:
            violations[key] = violations.get(key, 0) + 1
    if violations:
        sample = ("violations=" + " ".join(
            f"{phase}:{count}" for phase, count in sorted(violations.items())))
    else:
        sample = "violations=0"
    sample += f" records={len(records)} bool_dv={len(bool_dv)}"
    return {"id": "sense.ledger.delivery-invariant", "state": "verified",
            "sample": sample, "records": len(records),
            "bool_dv": len(bool_dv), "violations": violations, "kind": "read"}




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

SITE_UNIT_PREFIX = "mishe-"
"""Shared prefix of every unit this plant installs, across every site session.

A site's manifest names its own session's units, so the prefix is what makes a
second site's services visible to a scan of this one.
"""


def _active_site_units() -> tuple[list[str], str | None]:
    """Running ``mishe-*`` services in this user instance, with a failure reason.
    Every site's services share one systemd user bus, so a unit absent from a
    site's own manifest still runs here and can drift against that site's pin.
    """
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "list-units",
                                 "--type=service", "--no-legend", "--all"],
                                capture_output=True, text=True, timeout=5,
                                env=environment)
    except (OSError, subprocess.SubprocessError):
        return [], "systemctl unavailable"
    if result.returncode:
        return [], "systemctl failed"
    # ``list-units`` prints a fixed column table and ignores ``-p``, unlike
    # ``show``, so the state is read positionally rather than as key=value rows.
    units: list[str] = []
    seen: set[str] = set()
    for row in result.stdout.splitlines():
        columns = row.split()
        # ``list-units`` indents the unit column, so the prefix is matched after
        # the split rather than against the raw row.
        if len(columns) < 4 or not columns[0].startswith(SITE_UNIT_PREFIX):
            continue
        if columns[2] != "active" or columns[3] != "running":
            continue
        unit = columns[0]
        if not unit.endswith(".service") or unit in seen:
            continue
        seen.add(unit)
        units.append(unit)
    return units, None


def _pinned_root(home: Path, default: str) -> tuple[str | None, str | None]:
    """The root the runtime pin names, or None with a reason it could not."""
    try:
        from .runtime_source import source_for
        return str(source_for(Path(home), Path(default)).resolve()), None
    except (OSError, TypeError, ValueError, ImportError) as exc:
        return None, f"pin invalid: {exc}"


def _child_pids(pid: int) -> list[int]:
    """Direct children of `pid`, or empty when they cannot be listed."""
    try:
        result = subprocess.run(["ps", "--ppid", str(pid), "-o", "pid=", "--no-headers"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode:
        return []
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def _unit_import_roots(units: list[str]) -> dict[str, str]:
    """The source root each named unit imports, keyed by unit.

    Prefer systemd's configured environment; if it does not name PYTHONPATH,
    inspect the live main process environment, then its direct children, so
    wrapper-exported roots count.
    """
    if not units:
        return {}
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", *units,
                                 "-p", "Id,Environment,MainPID"],
                                capture_output=True, text=True, timeout=5,
                                env=environment)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode:
        return {}
    roots: dict[str, str] = {}
    fallback_roots: dict[str, tuple[int, str]] = {}
    for block in result.stdout.split("\n\n"):
        values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
        unit = str(values.get("Id", ""))
        items = values.get("Environment", "").split()
        root_items = [item for item in items if item.startswith("PYTHONPATH=")]
        if not root_items:
            try:
                pid = int(values.get("MainPID", "0"))
                if pid <= 0:
                    continue
                proc_environment = Path(f"/proc/{pid}/environ").read_bytes()
            except (OSError, ValueError):
                continue
            root_items = [
                item.decode(errors="replace") for item in proc_environment.split(b"\0")
                if item.startswith(b"PYTHONPATH=")
            ]
            if root_items:
                for item in root_items:
                    for component in item[len("PYTHONPATH="):].split(os.pathsep):
                        root = _import_root(component)
                        if root:
                            fallback_roots[unit] = (pid, root)
                            break
                    if unit in fallback_roots:
                        break
            else:
                # A wrapper script exports PYTHONPATH to its children, not itself.
                # Check direct children for a wrapper-exported root.
                for child_pid in _child_pids(pid):
                    try:
                        child_environment = Path(f"/proc/{child_pid}/environ").read_bytes()
                    except OSError:
                        continue
                    child_items = [
                        item.decode(errors="replace") for item in child_environment.split(b"\0")
                        if item.startswith(b"PYTHONPATH=")
                    ]
                    if child_items:
                        for item in child_items:
                            for component in item[len("PYTHONPATH="):].split(os.pathsep):
                                root = _import_root(component)
                                if root:
                                    fallback_roots[unit] = (pid, root)
                                    break
                            if unit in fallback_roots:
                                break
                    if unit in fallback_roots:
                        break
            continue
        for item in root_items:
            for component in item[len("PYTHONPATH="):].split(os.pathsep):
                root = _import_root(component)
                if root:
                    roots[unit] = root
                    break
            if unit in roots:
                break
    if fallback_roots:
        try:
            current = subprocess.run(
                ["systemctl", "--user", "show", *fallback_roots, "-p", "Id,MainPID"],
                capture_output=True, text=True, timeout=5, env=environment)
        except (OSError, subprocess.SubprocessError):
            return roots
        if current.returncode == 0:
            for block in current.stdout.split("\n\n"):
                values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
                unit = str(values.get("Id", ""))
                candidate = fallback_roots.get(unit)
                try:
                    pid = int(values.get("MainPID", "0"))
                except ValueError:
                    continue
                if candidate is not None and pid == candidate[0]:
                    roots[unit] = candidate[1]
    return roots


def _runtime_drift_across_sites(home: Path) -> dict[str, object]:
    """Compare every running site service with the pin of the site it serves.

    A site's manifest names only its own session, yet every site's services share
    one systemd user bus. A service of another site therefore runs here invisible
    to that manifest, and one can carry a release the site's own pin contradicts.
    A session's units share its prefix, so the registry's session maps any unit
    to its site — including a unit the site's own manifest omits, which is how a
    stale release hides from the service coverage that would otherwise name it.
    The scanning site is mapped from its own session whether or not the registry
    lists other sites, so its manifest-omitted units stay read even when it
    coordinates none. The release coordinator's declared checkout root is not a
    stale release, so it is skipped rather than reported against the pin; only a
    registry whose site list is unreadable is unknown.
    """
    registry_path = home / "health" / "linked-sites.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"id": "sense.runtime.drift-across-sites", "state": "unavailable",
                "sample": "no linked-site registry on this plant", "kind": "read"}
    except (OSError, ValueError):
        return {"id": "sense.runtime.drift-across-sites", "state": "unknown",
                "sample": "linked-site registry unreadable", "kind": "read"}
    sites = registry.get("sites") if isinstance(registry, dict) else None
    if not isinstance(sites, list):
        return {"id": "sense.runtime.drift-across-sites", "state": "unknown",
                "sample": "linked-site registry has no site list", "kind": "read"}
    site_by_prefix: dict[str, str] = {}
    pins: dict[str, tuple[str | None, str | None]] = {}

    def map_site(site_home: str, session: str) -> None:
        pins[session] = _pinned_root(Path(site_home), str(Path(site_home).parent))
        # A unit of this session is named by its prefix, so the mapping covers a
        # unit the site's own manifest omits as well as one it lists.
        site_by_prefix[session] = site_home

    for site in sites:
        if not isinstance(site, dict) or not isinstance(site.get("home"), str):
            continue
        session = site.get("session")
        if not isinstance(session, str) or not session:
            continue
        map_site(site["home"], session)
    # The registry lists the *other* sites this one coordinates, so the scanning
    # site is absent from it; its own units share its bus and need its pin too.
    own_session = os.environ.get("MISHE_SEED_SESSION")
    if not own_session:
        try:
            own_session = (home / ".seed-raised").read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError):
            own_session = None
    if own_session and own_session not in site_by_prefix:
        map_site(str(Path(home).resolve()), own_session)
    if not site_by_prefix:
        return {"id": "sense.runtime.drift-across-sites", "state": "unknown",
                "sample": "registry names no site with a home and a session", "kind": "read"}
    active, failure = _active_site_units()
    if failure is not None:
        return {"id": "sense.runtime.drift-across-sites", "state": "unknown",
                "sample": f"running units unreadable: {failure}", "kind": "read"}
    roots = _unit_import_roots(active)
    stale: dict[str, str] = {}
    uninspectable: list[str] = []
    unpinned: list[str] = []
    unattributed: list[str] = []
    parts = [f"sites={len(site_by_prefix)} running={len(active)}"]
    from .runtime_source import declared_checkout_root
    for unit in active:
        session = next((prefix for prefix in site_by_prefix
                        if unit.startswith(prefix + "-")), None)
        if session is None:
            # A site service the registry does not name is a coverage gap of its
            # own, not a release claim this sensor can judge.
            unattributed.append(unit)
            continue
        root = roots.get(unit)
        if root is None:
            # A mapped, running site's unit with no discoverable import root
            # cannot be compared with its pin; do not report coverage as clean.
            uninspectable.append(unit)
            continue
        pinned, pin_failure = pins[session]
        if pin_failure is not None or pinned is None:
            unpinned.append(unit)
            continue
        if Path(root).resolve() != Path(pinned).resolve():
            declared = declared_checkout_root(Path(site_by_prefix[session]), unit)
            if declared is not None and Path(root).resolve() == declared.resolve():
                # The coordinator is declared on its site's checkout, not the pin
                # (coordination/site_sync.py skips the same unit when verifying).
                continue
            stale[unit] = root
    if unattributed:
        parts.append("unattributed=" + ",".join(unattributed))
    if uninspectable:
        parts.append("uninspectable=" + ",".join(uninspectable))
    if unpinned:
        parts.append("unpinned=" + ",".join(unpinned))
    if stale:
        parts.append("stale=" + ",".join(f"{unit}@{Path(root).name}" for unit, root in sorted(stale.items())))
    state = "drift" if stale else "unknown" if unpinned or unattributed or uninspectable else "verified"
    return {"id": "sense.runtime.drift-across-sites", "state": state,
            "sample": " ".join(parts), "kind": "read",
            "identity": {"sites": sorted(site_by_prefix), "stale": stale,
                         "unpinned": unpinned, "unattributed": unattributed,
                         "uninspectable": uninspectable, "running": active}}


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

    Comparing import roots is blind once they agree, because a
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


TOP_PAIN_ENTRY = re.compile(r"-m\s+mishe_tauftauf\.([A-Za-z_][A-Za-z0-9_]*)")
TOP_PAIN_PATH = re.compile(r"""PYTHONPATH=("[^"]*"|'[^']*'|[^\s'\";]+)""")
TOP_PAIN_HOME = re.compile(r"^\s*home=([^\s'\";]+)", re.MULTILINE)
TOP_PAIN_VAR = re.compile(r"\$\{home\}|\$home(?![A-Za-z0-9_])")

PANE_ROLE = re.compile(r"\bpain watch ([A-Za-z0-9_-]+)")
"""The role name a pane watcher runs, extracted from its start command."""


def _pane_info() -> dict[str, tuple[str, str, str]]:
    """Map each pane's role name to its (start_command, current_path, pid).

    Reads ``tmux list-panes -a`` once; returns empty when tmux is unavailable
    or no session is running. The role name is the token after ``pain watch``
    in the pane's start command, matching the Top Pain script name. The pid is
    the pane's watcher process, whose environment the renderer inherits.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_id}|#{pane_pid}|#{pane_start_command}|#{pane_current_path}"],
            capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    info: dict[str, tuple[str, str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        _, pid, start_command, current_path = parts
        match = PANE_ROLE.search(start_command)
        if match:
            info[match.group(1)] = (start_command, current_path, pid)
    return info


def _command_pythonpath(command: str) -> list[str]:
    """PYTHONPATH components a ``PYTHONPATH=`` assignment in ``command`` names."""
    declared = TOP_PAIN_PATH.search(command)
    if declared is None:
        return []
    value = declared.group(1)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.split(os.pathsep)


def _pane_pythonpath(pid: str) -> list[str]:
    """PYTHONPATH components from a pane process's live environment.

    The pane watcher's environment is what ``run_renderer`` copies into the
    renderer, so an inherited renderer's effective root is this value, not the
    start command text: tmux adds the server global environment and any
    ``-e``/``respawn-pane`` override at pane creation, neither of which the
    stored start command shows.
    """
    if not pid:
        return []
    try:
        raw = Path("/proc", pid, "environ").read_bytes()
    except OSError:
        return []
    prefix = b"PYTHONPATH="
    for field in raw.split(b"\0"):
        if field.startswith(prefix):
            return field[len(prefix):].decode("utf-8", "replace").split(os.pathsep)
    return []


def _conditional_export(text: str, match: re.Match) -> bool:
    """Whether a script's PYTHONPATH assignment may not decide the root.

    An assignment guarded by a shell conditional or joined by ``&&``/``||``,
    or one built from the inherited ``$PYTHONPATH``, does not name the
    renderer's effective root: the pane watcher's environment decides. Reading
    the text as the root would report a renderer the pin never governs as
    verified.
    """
    value = match.group(1)
    if "$PYTHONPATH" in value or "${PYTHONPATH" in value:
        return True
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start:match.start()]
    if re.search(r"\b(if|then|elif|else|case|while|until|for|do)\b|&&|\|\|", prefix):
        return True
    before = text[:line_start].rstrip()
    if before:
        previous = before.rsplit("\n", 1)[-1].strip()
        if re.search(r"\b(then|do|else)\s*$", previous) or \
                re.match(r"(if|case|while|until|for)\b", previous):
            return True
    return False


def _shell_home(value: str, script: str) -> str | None:
    """Expand a Top Pain's ``$home`` reference, or None when it stays unknown.

    The pane scripts assign ``home=<site home>`` and then export a PYTHONPATH
    built from it, so the literal text alone does not name a directory. An
    expansion this reader cannot resolve is reported rather than guessed.
    """
    match = TOP_PAIN_HOME.search(script)
    if match is not None:
        value = TOP_PAIN_VAR.sub(lambda _: match.group(1), value)
    return None if "$" in value else value


def _top_pain_roots(home: Path) -> tuple[list[tuple[str, str, str, str]], list[str], str | None]:
    """The (import root, entry module, source, role) each pane renderer runs.

    A Top Pain may export PYTHONPATH itself, which overrides the pin its pane
    watcher runs under, so its renderer imports a root no service manifest
    names. An export that is conditional or built from the inherited
    ``$PYTHONPATH`` does not name the effective root, so it is returned with
    the source "conditional". An export that names no usable root at all (an
    empty ``PYTHONPATH`` or a bare separator) is returned with the source
    "unresolvable": the script still runs a package module, but its effective
    root is unread. A renderer that does not export PYTHONPATH inherits its
    root from the pane watcher's environment; it is returned with an empty
    root and the source "inherited" so the caller can resolve the effective
    root. Only an executable Top Pain can render, so a file without the
    executable bit is ignored. The second value lists executable Top Pains
    that run no package module, so the sense shows its scope rather than only
    the renderers it covers. The role is the Top Pain script filename, matching
    the pane watcher's role name.
    """
    directory = home / "top-pains"
    if not directory.is_dir():
        return [], [], None
    found: list[tuple[str, str, str, str]] = []
    uncovered: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], [], f"top-pain {path.name} unreadable"
        entry = [match.group(1) for match in TOP_PAIN_ENTRY.finditer(text)]
        if not entry:
            uncovered.append(path.name)
            continue
        declared = TOP_PAIN_PATH.search(text)
        if declared is None:
            for module in entry:
                if ("", module, "inherited", path.name) not in seen:
                    seen.add(("", module, "inherited", path.name))
                    found.append(("", module, "inherited", path.name))
            continue
        if _conditional_export(text, declared):
            for module in entry:
                if ("", module, "conditional", path.name) not in seen:
                    seen.add(("", module, "conditional", path.name))
                    found.append(("", module, "conditional", path.name))
            continue
        value = declared.group(1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        value = _shell_home(value, text)
        if value is None:
            return [], [], f"top-pain {path.name} import root unresolvable"
        resolved = False
        for component in value.split(os.pathsep):
            root = _import_root(component)
            if not root:
                continue
            resolved = True
            for module in entry:
                if (root, module, "exported", path.name) not in seen:
                    seen.add((root, module, "exported", path.name))
                    found.append((root, module, "exported", path.name))
        if not resolved:
            # The export names no root the renderer can import from, yet the
            # script runs a package module. Report the role unread so it is
            # neither counted as covered nor dropped from the sense's scope.
            for module in entry:
                if ("", module, "unresolvable", path.name) not in seen:
                    seen.add(("", module, "unresolvable", path.name))
                    found.append(("", module, "unresolvable", path.name))
    return found, uncovered, None


def _package_imports(source: str) -> set[str]:
    """The package modules ``source`` imports, relative or absolute."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    names.add(node.module.split(".")[0])
                else:
                    names.update(alias.name.split(".")[0] for alias in node.names)
            elif node.module and node.module.split(".")[0] == "mishe_tauftauf":
                parts = node.module.split(".")
                if len(parts) > 1:
                    names.add(parts[1])
                else:
                    names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "mishe_tauftauf" and len(parts) > 1:
                    names.add(parts[1])
    return names


def _module_digests(package: Path, entry: str) -> dict[str, str | None]:
    """Every module in ``entry``'s import closure, as name -> sha256 (None absent).

    The walk stops at a module the root does not carry: its own imports cannot
    be read from there, and its absence is already the drift to report.
    """
    digests: dict[str, str | None] = {}
    stack = [entry]
    while stack:
        name = stack.pop()
        if name in digests:
            continue
        try:
            source = (package / f"{name}.py").read_bytes()
        except OSError:
            digests[name] = None
            continue
        digests[name] = hashlib.sha256(source).hexdigest()
        stack.extend(_package_imports(source.decode("utf-8", "replace")) - digests.keys())
    return digests


def _renderer_coverage(home: Path) -> dict[str, object]:
    """Compare every pane renderer's import closure with the pin's.

    A renderer that exports its own PYTHONPATH renders from a root the service
    comparison never sees, so a stale snapshot there shows old logic in a pane
    while the dashboard still read verified. A renderer that inherits its root
    from the pane watcher's environment is resolved from the watcher process's
    live environment, falling back to the pane's start command. Each renderer's
    package closure is hashed in its effective root and in the pin; a module
    that differs, is missing from the renderer root, or is missing from the pin
    is drift. A conditional export or an unresolvable inherited root is unknown
    rather than guessed. The pane's cwd is checked for a ``mishe_tauftauf/``
    package that would shadow the PYTHONPATH root, since ``python -m`` inserts
    the cwd before PYTHONPATH in ``sys.path``. The identity records each
    renderer's effective root: an inherited renderer that resolved shows the
    root the pane environment supplied, so a reader can tell one that resolved
    to the pin from one that resolved to a different root that happens to match.
    """
    pairs, uncovered, failure = _top_pain_roots(home)
    if failure is not None:
        return {"id": "sense.runtime.renderer-coverage", "state": "unknown",
                "sample": failure, "kind": "read"}
    if not pairs:
        return {"id": "sense.runtime.renderer-coverage", "state": "unavailable",
                "sample": "no Top Pain runs a package module", "kind": "read"}
    pin, pin_failure = _pinned_root(home, str(Path(home).resolve().parent))
    if pin is None:
        return {"id": "sense.runtime.renderer-coverage", "state": "unknown",
                "sample": f"pin unreadable: {pin_failure}", "kind": "read"}
    pinned = Path(pin) / "src" / "mishe_tauftauf"
    references: dict[str, dict[str, str | None]] = {}
    drift: list[dict[str, object]] = []
    inherited_unknown: list[str] = []
    conditional_unknown: list[str] = []
    export_unknown: list[str] = []
    pane_info = _pane_info()
    identity_renderers: list[str] = []
    for root, entry, source, role in pairs:
        effective_root = root
        package_path = Path(root) / "src" / "mishe_tauftauf"
        pane = pane_info.get(role)
        if source == "conditional":
            # The text names a root the watcher's environment may override, and
            # no reading here can decide which fired: report it unread.
            conditional_unknown.append(role)
            identity_renderers.append(f"{root}:{entry}:{source}")
            continue
        if source == "unresolvable":
            # The export names no usable root, so the renderer's effective
            # root is unread rather than the pin.
            export_unknown.append(role)
            identity_renderers.append(f"{root}:{entry}:{source}")
            continue
        if source == "inherited":
            resolved = False
            if pane is not None:
                for component in (*_pane_pythonpath(pane[2]), *_command_pythonpath(pane[0])):
                    candidate = _import_root(component)
                    if candidate:
                        effective_root = candidate
                        package_path = Path(candidate) / "src" / "mishe_tauftauf"
                        resolved = True
                        break
            if not resolved:
                inherited_unknown.append(role)
                identity_renderers.append(f"{root}:{entry}:{source}")
                continue
        # Check the pane's cwd for a shadowing package
        if pane is not None:
            current_path = pane[1]
            cwd_package = Path(current_path) / "mishe_tauftauf"
            if cwd_package.is_dir():
                effective_root = current_path
                package_path = cwd_package
        identity_renderers.append(f"{effective_root}:{entry}:{source}")
        if entry not in references:
            references[entry] = _module_digests(pinned, entry)
        local = _module_digests(package_path, entry)
        # Compare the union: a module the pin's closure reaches but the
        # renderer's does not is drift too, and only the local walk would miss it.
        reference = references[entry]
        differing = sorted(name for name in set(local) | set(reference)
                           if local.get(name) != reference.get(name))
        if differing:
            drift.append({"root": effective_root, "entry": entry, "modules": differing})
    parts = [f"renderers={len(pairs)}"]
    if inherited_unknown:
        parts.append("inherited_unknown=" + ",".join(sorted(inherited_unknown)))
    if conditional_unknown:
        parts.append("conditional_unknown=" + ",".join(sorted(conditional_unknown)))
    if export_unknown:
        parts.append("export_unknown=" + ",".join(sorted(export_unknown)))
    if uncovered:
        parts.append("uncovered=" + ",".join(sorted(uncovered)))
    if drift:
        parts.append("drift=" + " ".join(
            f"{item['entry']}=" + ",".join(item["modules"]) for item in drift))
    unresolved = inherited_unknown or conditional_unknown or export_unknown
    state = "unknown" if unresolved else ("drift" if drift else "verified")
    return {"id": "sense.runtime.renderer-coverage",
            "state": state,
            "sample": " ".join(parts), "kind": "read",
            "identity": {"renderers": identity_renderers,
                         "drift": drift, "uncovered": sorted(uncovered), "pin": pin}}

def sample(home: Path) -> dict[str, object]:
    """Take bounded reads; never open input event streams or record key content."""
    observed: list[dict[str, object]] = []
    for command in COMMANDS:
        found = shutil.which(command)
        observed.append({"id": f"command.{command}", "state": "available" if found else "unavailable",
                         "sample": found or "not on PATH", "kind": "declaration"})
    observed.append({"id": "sense.journal.kernel-error-count", "kind": "read",
                     **_journal_error_window()})
    observed.append({"id": "sense.journal.unit-failure-count", "kind": "read",
                     **_journal_unit_failure_window()})
    observed.append(_mind_wedge_suspects())
    observed.append(_ledger_delivery_invariant(home))

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
    # An empty name names no session, and _ps_processes normalizes it: an empty
    # tmux target resolves to the calling session, so descent would be claimed for
    # a session the scan never named.
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
    slots = _thermal_slots(hwmon_root)
    # A qualifier costs sample-string bytes on a line the dashboard truncates, so
    # pay it only where a name is shared by more than one chip: a sole chip's
    # readings are already attributable, and adding its parent there is noise.
    slot_names = [slot.parent / "name" for slot in slots]
    chip_names = [(_read(path, 64) or path.parent.name).strip() for path in slot_names]
    chips_by_name: dict[str, set[Path]] = {}
    for slot, chip_name in zip(slots, chip_names):
        chips_by_name.setdefault(chip_name, set()).add(slot.parent)
    # A name shared by one chip across several sensors is not ambiguous; only a
    # name on two distinct chips makes a reading unattributable.
    collisions = {name for name, seen in chips_by_name.items() if len(seen) > 1}
    chips: dict[Path, str | None] = {}
    temperatures: list[tuple[str, dict[str, str | None]]] = []
    for slot, name in zip(slots, chip_names):
        value = _read(slot, 64)
        if value is None or not value.strip().lstrip("-").isdigit():
            continue
        millidegrees = int(value.strip())
        if millidegrees < -273150 or millidegrees > 200000:
            # An out-of-range value is a stuck or absent sensor, not a
            # temperature; skipping keeps the reported sample honest.
            continue
        channel = re.fullmatch(r"temp(\d+)_input", slot.name)
        if channel is None:
            continue
        # Two controllers of the same model publish the same channel names under
        # the same chip name, so the name alone cannot attribute a reading. The
        # parent device tells them apart; its basename is short enough for a
        # sample string and stable across reboots, unlike a ``hwmonN`` index that
        # is enumeration order. It is resolved once per chip, keeping the walk
        # linear in sensors rather than in chips squared.
        if name in collisions:
            if slot.parent not in chips:
                chips[slot.parent] = _chip_qualifier(slot.parent)
            qualifier = chips[slot.parent]
            name = f"{name}@{qualifier}"
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
    observed.append(_renderer_coverage(home))
    observed.append(_runtime_drift_across_sites(home))
    return {"created": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "node": os.uname().nodename, "observations": observed}


def _write_attempt(home: Path, attempt: dict[str, object]) -> None:
    root = home / "discovery"
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".attempt-{os.getpid()}.tmp"
    temporary.write_text(json.dumps(attempt, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, root / "attempt.json")


def scan_attempt(home: Path) -> dict[str, object] | None:
    """Last acquisition/publication attempt, separate from successful source evidence."""
    try:
        attempt = json.loads((home / "discovery" / "attempt.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        return {"status": "unknown", "stage": "unknown", "error": str(error)}
    if (not isinstance(attempt, dict)
            or attempt.get("status") not in {"running", "succeeded", "failed"}
            or attempt.get("stage") not in {"acquisition", "publication", "notification"}):
        return {"status": "unknown", "stage": "unknown", "error": "invalid attempt record"}
    return attempt


def scan(home: Path) -> Path:
    """Acquire and publish a receipt; notification failure never rolls it back."""
    previous = latest(home)
    scan_id = uuid4().hex
    attempt = {"scan_id": scan_id, "status": "running", "stage": "acquisition",
               "created": datetime.now(timezone.utc).isoformat()}
    _write_attempt(home, attempt)
    try:
        start = endpoint()
        snapshot = dict(sample(home))
        snapshot["acquisition"] = {"start": start, "end": endpoint()}
        snapshot["scan_id"] = scan_id
        attempt["stage"] = "publication"
        _write_attempt(home, attempt)
        root = home / "discovery"
        stamp = snapshot["created"].replace(":", "").replace("-", "")
        artifact = root / f"scan-{stamp}-{scan_id}.json"
        payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        artifact.write_text(payload, encoding="utf-8")
        latest_path = root / "latest.json"
        temporary = root / f".latest-{os.getpid()}.tmp"
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, latest_path)
        attempt.update(stage="notification", artifact=artifact.name)
        _write_attempt(home, attempt)
        _notify_scan(home, snapshot, previous, artifact)
        attempt["status"] = "succeeded"
        _write_attempt(home, attempt)
    except Exception as error:
        attempt.update(status="failed", error=f"{type(error).__name__}: {error}")
        try:
            _write_attempt(home, attempt)
        except OSError as marker_error:
            raise marker_error from error
        raise
    return artifact


def _notify_scan(home: Path, snapshot: dict[str, object],
                 previous: dict[str, object] | None, artifact: Path) -> None:
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
        return
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


def latest(home: Path) -> dict[str, object] | None:
    path = home / "discovery" / "latest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


RENEWAL_MAX_AGE_SECONDS = 600.0
"""Renew before the panes' 900-second freshness gate, leaving 300 seconds of margin."""


def scan_age(home: Path) -> float | None:
    """Conservative upper acquisition age in seconds; legacy/incompatible receipts are unknown."""
    bounds = age_bounds(latest(home))
    return None if bounds is None else bounds[1] / 1_000_000_000


def renew_scan(home: Path, max_age_seconds: float = RENEWAL_MAX_AGE_SECONDS) -> Path | None:
    """Renew a missing or old scan without requiring a resident mind wake.

    Return the new artifact path, or None while the latest scan is fresh.
    scan() updates latest.json and deduplicates unchanged states in the feed.
    """
    bounds = age_bounds(latest(home))
    if classify_age(bounds, max_age_seconds) == "recent":
        return None
    return scan(home)
