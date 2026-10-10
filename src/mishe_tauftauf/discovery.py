"""Read-only local frontier scan; values are evidence, not authority."""

from __future__ import annotations
import bisect
import ast

import hashlib
import json
import math
import os
import time
import re
import shutil
import selectors
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .feed import Feed, parse_feed
from .outcome_events import classify_outcomes
from .runtime_source import source_for
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

    Kernel entries carry no unit ownership: the host journal is shared, so a
    class names the reporting source (a device driver), not a plant unit.
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


def _unit_restart_context(units: list[str]) -> dict[str, dict]:
    """Restart context for failed units: NRestarts and ActiveState.

    A unit that exits nonzero by design and recovers reads differently from
    one crash-looping: ``restarts=N active=running`` vs ``active=failed``.
    User scope first, then system scope for units not loaded there. Returns
    an empty dict when systemctl is unavailable. Only units with
    ``LoadState=loaded`` are accepted — ``systemctl --user show`` reports
    system-scope units as ``inactive`` (not ``not-found``), so ``LoadState``
    is required to distinguish them.
    """
    if not units:
        return {}
    context: dict[str, dict] = {}
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    for scope in (["--user"], []):
        remaining = [u for u in units if u not in context]
        if not remaining:
            break
        try:
            result = subprocess.run(["systemctl", *scope, "show", *remaining,
                                     "-p", "Id,NRestarts,ActiveState,LoadState"],
                                    capture_output=True, text=True, timeout=5,
                                    env=environment)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode:
            continue
        for block in result.stdout.split("\n\n"):
            values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
            unit = str(values.get("Id", ""))
            if not unit or unit in context:
                continue
            if values.get("LoadState") != "loaded":
                continue
            try:
                restarts = int(values.get("NRestarts", "0"))
            except ValueError:
                restarts = 0
            context[unit] = {"restarts": restarts,
                             "active": str(values.get("ActiveState", "unknown"))}
    return context


def _plant_session(home: Path) -> str | None:
    """The session this site raised: the environment override, else its receipt."""
    session = os.environ.get("MISHE_SEED_SESSION")
    if session:
        return session
    try:
        return (Path(home) / ".seed-raised").read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        return None


def _is_plant_unit(name: str, session: str) -> bool:
    """Whether a failed unit belongs to this plant rather than a foreign consumer.

    A plant's units share the session prefix its ``.seed-raised`` receipt
    records; every site on the shared user bus names its units the same way, and
    unrelated consumers also carry a ``mishe-`` prefix, so a bare prefix counts
    another site's or a foreign service's failure as this plant's. The plant's
    tmux server creates ``tmux-spawn-*.scope`` cgroups for its panes; those
    carry no session prefix.
    """
    return name.startswith(session + "-") or name.startswith("tmux-spawn-")


def _journal_unit_failure_window(home: Path, past_minutes: int = 10) -> dict:
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

    The count covers plant-owned units only. The host journal is shared with
    other nodes' test harnesses and other sites on the same user bus, so a
    foreign transient must not read as a plant failure: a unit outside this
    plant's session prefix is disclosed as ``foreign=name:count`` and excluded
    from the count. When the plant's session cannot be read the reading is
    ``unknown`` rather than attributing an unowned unit to the plant.

    Restart context (``NRestarts``, ``ActiveState``) is read for each failed
    unit so a recovered one-off reads differently from a crash-loop: a unit
    that exits nonzero by design and recovers shows ``active=running``, while
    a crash-loop shows ``active=failed``. The context is supplementary —
    when systemctl is unavailable the sample omits it.
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
    session = _plant_session(home)
    if session is None:
        unknown["reason"] = "plant session unavailable"
        return unknown
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
    per_unit_classes: dict[str, dict[str, int]] = {}
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
            unit, unit_class = match.group(1), match.group(2)
            units[unit] = units.get(unit, 0) + 1
            unit_classes = per_unit_classes.setdefault(unit, {})
            unit_classes[unit_class] = unit_classes.get(unit_class, 0) + 1
    except (UnicodeError, ValueError, TypeError):
        unknown["reason"] = "invalid journal entry metadata"
        return unknown
    plant_units = {unit: count for unit, count in units.items()
                   if _is_plant_unit(unit, session)}
    foreign_units = {unit: count for unit, count in units.items()
                     if not _is_plant_unit(unit, session)}
    classes = {}
    for unit in plant_units:
        for name, count in per_unit_classes.get(unit, {}).items():
            classes[name] = classes.get(name, 0) + count
    total = sum(plant_units.values())
    restart_context = _unit_restart_context(sorted(plant_units))
    parts = [f"last-{past_minutes}min unit-failure-count={total}"]
    parts.extend(f"{name}={count}" for name, count in sorted(classes.items()))
    if foreign_units:
        parts.append("foreign=" + ",".join(
            f"{unit}:{count}" for unit, count in sorted(foreign_units.items())))
    parts.extend(f"{unit}:restarts={ctx['restarts']},active={ctx['active']}"
                 for unit, ctx in sorted(restart_context.items()))
    return {"state": "verified", "sample": " ".join(parts),
            "count": total, "units": plant_units, "classes": classes,
            "foreign_units": foreign_units,
            "unit_context": restart_context, "coverage": coverage}


WEDGE_CHAIN_THRESHOLD = 3
"""Open continue chain length at or above which a mind is suspect."""

WEDGE_SPAN_THRESHOLD_MINUTES = 15.0
"""Minutes an open continue chain must span before a mind is suspect."""

WEDGE_ERROR_STALE_MINUTES = 5.0
"""Minutes since the last provider error before a chainless pane is suspect.

Rule R2 fires when a pane has provider errors but no continue chain: the turn
fails instantly (e.g. a 403 FreeTierError), so omp never schedules a retry and
rule R (chain >= 3, span >= 15 min) cannot fire. Five minutes is long enough
that a healthy retry would have produced either a success or a continue chain.
"""


def _omp_log_path(pid: int) -> Path | None:
    """The omp session log for one pane pid, or None when absent."""
    logs_dir = Path.home() / ".omp" / "logs"
    if not logs_dir.is_dir():
        return None
    matches = sorted(logs_dir.glob(f"omp.*.{pid}.log"),
                     key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _provider_error_class(record: dict) -> str:
    """The class of a provider error record: its HTTP status when named.

    ``errorStatus`` is the direct field; the message carries ``[500]`` or a
    leading status when the record omits it. Anything else (a closed socket, a
    deadline without a status) reads ``unknown`` rather than being guessed.
    """
    status = record.get("errorStatus")
    if isinstance(status, int):
        return str(status)
    if isinstance(status, str) and re.fullmatch(r"\d{3}", status):
        return status
    message = record.get("errorMessage")
    if isinstance(message, str):
        match = re.search(r"\[(\d{3})\]|(?:\A|\s)(\d{3})\b", message)
        if match:
            return match.group(1) or match.group(2)
    return "unknown"


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

    ``cause`` names the dominant class of ``agent turn ended with provider
    error`` record in the chain (``provider-error:<status>``, the HTTP status
    when the record or its message names one), or ``none`` when the chain holds
    no provider error — so a retry loop against a failing upstream reads
    differently from a hung pane. ``last_error_ts`` is the timestamp of the
    newest such record in the open chain (None when there is none) and
    ``error_count`` the number of them; rule R2 reads both to flag a pane whose
    turn fails instantly with no retry scheduled.
    """
    log_path = _omp_log_path(pid)
    if log_path is None:
        return None
    continues: list[datetime] = []
    sources: dict[str, int] = {}
    error_classes: dict[str, int] = {}
    last_error_ts: datetime | None = None
    error_count: int = 0
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
        elif message == "agent turn ended with provider error":
            error_class = _provider_error_class(record)
            error_classes[error_class] = error_classes.get(error_class, 0) + 1
            last_error_ts = ts
            error_count += 1
        elif (message == "agent_end maintenance routing"
                and record.get("stopReason") == "stop"):
            continues = []
            sources = {}
            error_classes = {}
            last_error_ts = None
            error_count = 0
    cause = "none"
    if error_classes:
        dominant = sorted(error_classes.items(), key=lambda item: (-item[1], item[0]))[0][0]
        cause = f"provider-error:{dominant}"
    span = (now - continues[0]).total_seconds() / 60.0 if continues else 0.0
    return {"chain": len(continues), "span_minutes": round(span, 1),
            "sources": sources, "cause": cause,
            "last_error_ts": last_error_ts.isoformat() if last_error_ts else None,
            "error_count": error_count}

def _mind_wedge_suspects() -> dict:
    """Flag minds whose omp session log indicates a wedge.

    Rule R (chain wedge): SUSPECT when the open continue chain is >= 3 and
    spans >= 15 min with no completed turn between.

    Rule R2 (provider-error wedge): SUSPECT when the pane has provider errors
    but no continue chain and the last error is >= 5 min old. This catches the
    instant-provider-error failure mode (e.g. a 403 FreeTierError) where the
    turn fails immediately, omp never schedules a retry, and rule R cannot fire.

    The signal reads omp's session log
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
        elif (chain["chain"] == 0
                and chain["cause"] != "none"
                and chain["last_error_ts"] is not None):
            last_error = datetime.fromisoformat(chain["last_error_ts"])
            error_age_min = (now - last_error).total_seconds() / 60.0
            if error_age_min >= WEDGE_ERROR_STALE_MINUTES:
                suspects.append({"window": window_name, "pid": pid, **chain,
                                 "rule": "provider-error",
                                 "error_age_minutes": round(error_age_min, 1)})
    if suspects:
        parts = []
        for s in suspects:
            if s.get("rule") == "provider-error":
                parts.append(
                    f"{s['window']}(pid={s['pid']},rule=provider-error,"
                    f"chain={s['chain']},error_age={s['error_age_minutes']}min,"
                    f"cause={s['cause']})")
            else:
                parts.append(
                    f"{s['window']}(pid={s['pid']},chain={s['chain']},"
                    f"span={s['span_minutes']}min,src="
                    + ",".join(f"{name}:{count}"
                               for name, count in sorted(s["sources"].items()))
                    + f",cause={s['cause']})")
        sample = "suspects=" + " ".join(parts)
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
    disposition without re-deriving. The verified sample ends with the
    coverage tier ``records=N bool_dv=M``; an empty, missing or unreadable
    store reads UNKNOWN rather than a clean bill, so a store-path or format
    change cannot pass as the invariant holding, with an unreadable store
    appending ``unreadable=<file>`` after the tier. The UNKNOWN branch still
    carries violations found in the readable records (a trailing
    ``violations=<phase>:<count>`` segment): a collapse co-occurring with a
    torn record must not be dropped just because coverage is incomplete.
    """
    records, unreadable = _patch_records(home)
    bool_dv = [r for r in records if isinstance(r.get("delivery_verified"), bool)]
    violations: dict[str, int] = {}
    for record in bool_dv:
        phase = record.get("phase")
        key = phase if isinstance(phase, str) else "unknown"
        if key not in LEDGER_DV_PHASES:
            violations[key] = violations.get(key, 0) + 1
    if not records or unreadable:
        if not (home / "patches").is_dir():
            sample = "patch store unavailable"
        elif unreadable:
            sample = (f"records={len(records)} bool_dv={len(bool_dv)} "
                      f"unreadable={','.join(unreadable[:3])}"
                      + ("…" if len(unreadable) > 3 else ""))
        else:
            sample = f"records=0 bool_dv=0"
        if violations:
            sample += (" violations=" + " ".join(
                f"{phase}:{count}" for phase, count in sorted(violations.items())))
        return {"id": "sense.ledger.delivery-invariant", "state": "unknown",
                "sample": sample, "records": len(records),
                "bool_dv": len(bool_dv), "violations": violations, "kind": "read"}
    if violations:
        sample = ("violations=" + " ".join(
            f"{phase}:{count}" for phase, count in sorted(violations.items())))
    else:
        sample = "violations=0"
    sample += f" records={len(records)} bool_dv={len(bool_dv)}"
    return {"id": "sense.ledger.delivery-invariant", "state": "verified",
            "sample": sample, "records": len(records),
            "bool_dv": len(bool_dv), "violations": violations, "kind": "read"}

def _ledger_dv_binding(home: Path) -> dict:
    """Flag ``delivery_verified=true`` records that carry no ``verification`` dict.

    A boolean ``delivery_verified`` is a claim that bytes moved; the
    ``verification`` dict is the binding that proves it (command, exit code,
    evidence). A ``true`` without that dict is an unbound claim: the ledger
    says delivered but records nothing that could confirm it. The 38917
    revival trigger's mechanical branch, monitored continuously: exactly one
    frozen record (``health-stale-pend-detection``) carries the unbound shape
    and no current producer path produces it, so the sense flags the state
    rather than the record. The sense flags; it does not decide — a new
    legitimate producer that omits the binding and a genuine collapse both
    read as unbound. The verified sample ends with the coverage tier
    ``records=N bool_dv=M``; an empty, missing or unreadable store reads
    UNKNOWN rather than a clean bill, so a store-path or format change cannot
    pass as the invariant holding, with an unreadable store appending
    ``unreadable=<file>`` after the tier.
    """
    records, unreadable = _patch_records(home)
    bool_dv = [r for r in records if isinstance(r.get("delivery_verified"), bool)]
    unbound = [r for r in bool_dv
               if r.get("delivery_verified") is True
               and not isinstance(r.get("verification"), dict)]
    if not records or unreadable:
        if not (home / "patches").is_dir():
            sample = "patch store unavailable"
        elif unreadable:
            sample = (f"records={len(records)} bool_dv={len(bool_dv)} "
                      f"unreadable={','.join(unreadable[:3])}"
                      + ("…" if len(unreadable) > 3 else ""))
        else:
            sample = f"records=0 bool_dv=0"
        if unbound:
            sample += (" unbound=" + ",".join(
                r.get("phase", "?") if isinstance(r.get("phase"), str) else "?"
                for r in unbound[:3]))
        return {"id": "sense.ledger.dv-binding", "state": "unknown",
                "sample": sample, "records": len(records),
                "bool_dv": len(bool_dv), "unbound": len(unbound), "kind": "read"}
    if unbound:
        sample = ("unbound=" + ",".join(
            r.get("phase", "?") if isinstance(r.get("phase"), str) else "?"
            for r in unbound[:3]))
    else:
        sample = "unbound=0"
    sample += f" records={len(records)} bool_dv={len(bool_dv)}"
    return {"id": "sense.ledger.dv-binding",
            "state": "drift" if unbound else "verified",
            "sample": sample, "records": len(records),
            "bool_dv": len(bool_dv), "unbound": len(unbound), "kind": "read"}




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


EXEC_START_SCRIPT_LIMIT = 128 * 1024
"""Largest ExecStart script read when checking a foreign unit for a coupling."""


def _names_checkout(text: str, root: Path) -> bool:
    """Whether text names the checkout at root as a path or a quoted literal.

    A foreign launcher declares the coupling in its own text: an absolute
    ``<root>`` or ``<root>/src``, a home-relative ``~/<name>``/``$HOME/<name>``,
    or the name as a literal (``Path.home() / "mishe-tauftauf"``). The name is
    matched as a whole path component so a longer name sharing the prefix does
    not read as this checkout.
    """
    if not root.name:
        return False
    pattern = re.compile(
        r"(?:^|[\s\"'=:(,~/{$])" + re.escape(root.name) + r"(?=$|[\s\"'/.,:;)}])")
    return pattern.search(text) is not None


def _imports_checkout(root: str, checkout: Path) -> bool:
    """Whether an import root resolves to the checkout or a path inside it."""
    try:
        candidate = Path(root).resolve()
    except OSError:
        return False
    return candidate == checkout or checkout in candidate.parents


def _exec_start_files(value: str) -> list[Path]:
    """Existing files named as arguments by a ``systemctl show -p ExecStart`` value.

    systemd prints one ``{ path=… ; argv[]=… ; … }`` record per command. A wrapper
    is usually the argv element after the interpreter, but a direct ``path=``
    names one too, so both are candidates. Only existing files are returned; the
    interpreter is filtered later as a binary.
    """
    candidates: list[str] = []
    for match in re.finditer(r"path=(\S+)|argv\[\]=([^;]*)", value):
        if match.group(1) is not None:
            candidates.append(match.group(1))
        else:
            candidates.extend(match.group(2).split())
    files: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.startswith("/") or candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate)
        try:
            if path.is_file():
                files.append(path)
        except OSError:
            continue
    return files


def _unit_names_checkout(unit: str, root: Path) -> bool:
    """Whether a unit's ExecStart names this plant's checkout.

    A foreign launcher that runs ``-m mishe_tauftauf`` with
    ``PYTHONPATH=<checkout>/src`` names the checkout in its own text, so the
    coupling is declared there even when no live process carries the variable at
    scan time. The command line itself is checked for an inline reference and
    each named script is read; an ELF program is skipped, since the interpreter
    is a candidate but its bytes cannot declare a coupling.
    """
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ExecStart"],
                                capture_output=True, text=True, timeout=5, env=environment)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode:
        return False
    value = ""
    for row in result.stdout.splitlines():
        if row.startswith("ExecStart="):
            value = row[len("ExecStart="):]
            break
    if _names_checkout(value, root):
        return True
    for path in _exec_start_files(value):
        try:
            with path.open("rb") as handle:
                if handle.read(4).startswith(b"\x7fELF"):
                    continue
                handle.seek(0)
                raw = handle.read(EXEC_START_SCRIPT_LIMIT)
        except OSError:
            continue
        if _names_checkout(raw.decode("utf-8", "replace"), root):
            return True
    return False


def _foreign_checkout_consumers(units: list[str], root: Path) -> dict[str, str]:
    """Unattributed units whose ExecStart names this plant's checkout, by unit."""
    consumers: dict[str, str] = {}
    for unit in units:
        if _unit_names_checkout(unit, root):
            consumers[unit] = str(root)
    return consumers


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
    coordinates none. A unit no session names is foreign to every site; when it
    consumes this plant's checkout — observed in its import root or declared in
    the script its ``ExecStart`` runs — the sample names the coupling as
    ``foreign=<unit>@<checkout>`` and stays ``unknown`` rather than hiding it in
    an anonymous coverage gap. The release coordinator's declared checkout root
    is not a stale release, so it is skipped rather than reported against the
    pin; only a registry whose site list is unreadable is unknown.
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
    own_session = _plant_session(home)
    if own_session and own_session not in site_by_prefix:
        map_site(str(Path(home).resolve()), own_session)
    if not site_by_prefix:
        return {"id": "sense.runtime.drift-across-sites", "state": "unknown",
                "sample": "registry names no site with a home and a session", "kind": "read"}
    active, failure = _active_site_units()
    if failure is not None:
        return {"id": "sense.runtime.drift-across-sites", "state": "unknown",
                "sample": f"running units unreadable: {failure}", "kind": "read"}
    from .runtime_source import declared_checkout_root
    checkout_root = Path(home).resolve().parent
    session_of = {unit: next((prefix for prefix in site_by_prefix
                              if unit.startswith(prefix + "-")), None)
                  for unit in active}
    # A unit no session names is foreign to every site. One that consumes this
    # plant's checkout is a live coupling rather than an anonymous coverage gap:
    # its own ExecStart declares the checkout even when no live process carries
    # the variable at scan time, so the reading names the consumer.
    foreign = _foreign_checkout_consumers(
        [unit for unit, session in session_of.items() if session is None], checkout_root)
    roots = _unit_import_roots(active)
    stale: dict[str, str] = {}
    uninspectable: list[str] = []
    unpinned: list[str] = []
    unattributed: list[str] = []
    parts = [f"sites={len(site_by_prefix)} running={len(active)}"]
    for unit in active:
        session = session_of[unit]
        if session is None:
            # The import root is read even for an unattributed unit, because a
            # live PYTHONPATH naming the checkout is the coupling observed
            # directly; a unit that neither imports nor names the checkout is a
            # coverage gap this sensor cannot judge.
            root = roots.get(unit)
            if root is not None and _imports_checkout(root, checkout_root):
                foreign[unit] = root
            elif unit not in foreign:
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
    if foreign:
        parts.append("foreign=" + ",".join(
            f"{unit}@{checkout_root.name}" for unit in sorted(foreign)))
    if unattributed:
        parts.append("unattributed=" + ",".join(unattributed))
    if uninspectable:
        parts.append("uninspectable=" + ",".join(uninspectable))
    if unpinned:
        parts.append("unpinned=" + ",".join(unpinned))
    if stale:
        parts.append("stale=" + ",".join(f"{unit}@{Path(root).name}" for unit, root in sorted(stale.items())))
    state = "drift" if stale else "unknown" if unpinned or unattributed or uninspectable or foreign else "verified"
    return {"id": "sense.runtime.drift-across-sites", "state": state,
            "sample": " ".join(parts), "kind": "read",
            "identity": {"sites": sorted(site_by_prefix), "stale": stale,
                         "unpinned": unpinned, "unattributed": unattributed,
                         "foreign": foreign,
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

    The coordinator's declared checkout root is exempt from the added direction
    only: it is declared to import the development checkout, which may lead the
    pin by a commit, so an extra sensor there is expected rather than drift. A
    sensor the checkout drops is still drift — the comparison caught exactly
    that regression on 2026-10-04.
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
    declared = Path(home).resolve().parent
    missing = {root: sorted(baseline - set(names))
               for root, names in per_root.items() if baseline - set(names)}
    extra = {root: sorted(set(names) - baseline)
             for root, names in per_root.items()
             if set(names) - baseline
             and Path(root).resolve() != declared}
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
SITE_SCRIPT_PATH = re.compile(r"""\bsite\s*/\s*f?["']([A-Za-z0-9_.-]+\.py)["']""")
"""A Top Pain's reference to a script in its own site home."""

SITE_SCRIPT_IMPORT = re.compile(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_]*)\s+import\s",
                                re.MULTILINE)
"""A ``from X import`` line whose target may be a site-home script."""

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


def _site_script_chain(text: str, home: Path) -> list[tuple[str, str]]:
    """Site-home scripts a Top Pain runs that themselves import package code.

    Follows one level of site-home script execution: ``site / "X.py"`` in a
    subprocess call and ``from X import`` where ``X.py`` exists in the site
    home. A script that imports ``mishe_tauftauf.*`` makes the Top Pain a
    package executor even though it names no ``-m mishe_tauftauf.X`` entry, so
    the module is reported and its closure hashed against the pin.
    """
    names = {match.group(1) for match in SITE_SCRIPT_PATH.finditer(text)}
    names.update(match.group(1) + ".py" for match in SITE_SCRIPT_IMPORT.finditer(text))
    chain: list[tuple[str, str]] = []
    for name in sorted(names):
        try:
            source = (home / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        chain.extend((name, module) for module in sorted(_package_imports(source)))
    return chain


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

    An uncovered Top Pain can still execute package code through a site-home
    script (``site / "X.py"`` or ``from X import``). One level of that chain is
    followed: a script importing ``mishe_tauftauf.*`` is reported as
    ``indirect_package``, and its closure is hashed against the pin from the
    root the pane environment resolves. A root other than the pin is drift and
    an unresolvable one is unknown, so the indirect path never reads verified
    on environment inheritance alone.

    The aggregate state ranks a definite violation above an incomplete check:
    one drifted renderer makes the state ``drift`` even when another renderer's
    root is unread, and the unread names stay in the sample. A pane executing
    bytes the pin does not govern is a finding a reader must not take for merely
    unknown, so ``unknown`` is reserved for a population with no drift found and
    at least one root unread.

    The pane truncates the sample, so the drift list leads it and is capped at
    two distinct violations with ``+Nmore``; the unread names follow. A reader
    who sees ``drift`` sees what drifted, and the identity keeps every row.
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
    script_unknown: list[str] = []
    indirect: list[str] = []
    for role in sorted(uncovered):
        try:
            text = (home / "top-pains" / role).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            script_unknown.append(role)
            continue
        chain = _site_script_chain(text, home)
        if not chain:
            continue
        # The disclosure is a static fact about the script text: this Top Pain
        # executes package code through a site-home script.
        for _script, module in chain:
            indirect.append(f"{role}:mishe_tauftauf.{module}")
        # The script runs with the watcher's environment and the site home
        # first on its path, so a package dir there shadows every root.
        effective_root: str | None = None
        if (home / "mishe_tauftauf").is_dir():
            effective_root = str(home)
        else:
            pane = pane_info.get(role)
            if pane is not None:
                for component in (*_pane_pythonpath(pane[2]), *_command_pythonpath(pane[0])):
                    candidate = _import_root(component)
                    if candidate:
                        effective_root = candidate
                        break
        if effective_root is None:
            # No pane runs this renderer, so the bytes its script imports are
            # unread rather than the pin's.
            script_unknown.append(role)
            continue
        package_path = Path(effective_root) / "src" / "mishe_tauftauf"
        for _script, module in chain:
            if module not in references:
                references[module] = _module_digests(pinned, module)
            local = _module_digests(package_path, module)
            reference = references[module]
            differing = sorted(name for name in set(local) | set(reference)
                               if local.get(name) != reference.get(name))
            # A root other than the pin is drift even when the closure matches:
            # the renderer executes bytes the pin does not govern.
            if differing or Path(effective_root).resolve() != Path(pin).resolve():
                drift.append({"root": effective_root, "entry": module,
                              "modules": differing if differing else [module]})
            identity_renderers.append(f"{effective_root}:mishe_tauftauf.{module}:script-chain")

    parts = [f"renderers={len(pairs)}"]
    if drift:
        # The pane truncates the sample, so the definite violation leads and is
        # capped: a reader who sees ``drift`` must see what drifted, not only
        # that some other renderer's root was unread. Renderers that share a
        # root and a module set are one violation, not one row per role.
        rows: list[str] = []
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        for item in drift:
            key = (str(item["root"]), str(item["entry"]), tuple(item["modules"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(f"{item['entry']}=" + ",".join(item["modules"]))
        shown = rows[:2]
        parts.append("drift=" + " ".join(shown)
                     + (f" +{len(rows) - len(shown)}more" if len(rows) > len(shown) else ""))
    if inherited_unknown:
        parts.append("inherited_unknown=" + ",".join(sorted(inherited_unknown)))
    if conditional_unknown:
        parts.append("conditional_unknown=" + ",".join(sorted(conditional_unknown)))
    if export_unknown:
        parts.append("export_unknown=" + ",".join(sorted(export_unknown)))
    if script_unknown:
        parts.append("script_unknown=" + ",".join(sorted(script_unknown)))
    if uncovered:
        parts.append("uncovered=" + ",".join(sorted(uncovered)))
    if indirect:
        parts.append("indirect_package=" + ",".join(sorted(indirect)))
    unresolved = (inherited_unknown or conditional_unknown or export_unknown
                  or script_unknown)
    state = "drift" if drift else ("unknown" if unresolved else "verified")
    return {"id": "sense.runtime.renderer-coverage",
            "state": state,
            "sample": " ".join(parts), "kind": "read",
            "identity": {"renderers": identity_renderers,
                         "drift": drift, "uncovered": sorted(uncovered),
                         "indirect_package": sorted(indirect), "pin": pin}}

def _format_age(delta: timedelta) -> str:
    """Human age for a commit waiting on a pin advance, e.g. ``1h20m``."""
    total = int(delta.total_seconds())
    if total < 0:
        return "unknown"
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _runtime_pin_lag(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Count the commits between the runtime pin and the checkout HEAD.

    The pin names the bytes the services import; HEAD is the checkout the
    minds edit. The lag is how far the live code leads the pin, and the age
    is how long the oldest unactivated commit has waited. A pin that is not
    an ancestor of HEAD (a rebase or a foreign checkout) reads UNKNOWN, as
    does a missing pin file or a git failure: the lag cannot be computed, so
    it must not read as zero. ``pin == HEAD`` reads ``lag=0`` verified: the
    checkout is exactly the pinned state.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    pin_path = home / "health" / "runtime-release.json"
    if not pin_path.exists():
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin file unavailable", "kind": "read"}
    try:
        pin = json.loads(pin_path.read_text(encoding="utf-8")).get("sha")
    except (OSError, ValueError):
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin file unreadable", "kind": "read"}
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{7,40}", pin):
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin sha invalid", "kind": "read"}
    checkout_root = Path(home).resolve().parent
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=2,
                              cwd=checkout_root)
        if head.returncode != 0:
            return {"id": "sense.runtime.pin-lag", "state": "unknown",
                    "sample": f"git error: {head.stderr.strip() or 'rev-parse failed'}",
                    "kind": "read"}
        head_sha = head.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            return {"id": "sense.runtime.pin-lag", "state": "unknown",
                    "sample": "git error: malformed HEAD sha", "kind": "read"}
        if head_sha == pin:
            return {"id": "sense.runtime.pin-lag", "state": "verified",
                    "sample": f"pin={pin[:7]} head={head_sha[:7]} lag=0",
                    "pin": pin, "head": head_sha, "lag": 0, "kind": "read"}
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", pin, head_sha],
                                  capture_output=True, text=True, timeout=2,
                                  cwd=checkout_root)
        if ancestor.returncode != 0:
            return {"id": "sense.runtime.pin-lag", "state": "unknown",
                    "sample": "pin not an ancestor of HEAD",
                    "pin": pin, "head": head_sha, "kind": "read"}
        count = subprocess.run(["git", "rev-list", "--count", f"{pin}..{head_sha}"],
                               capture_output=True, text=True, timeout=2,
                               cwd=checkout_root)
        if count.returncode != 0:
            return {"id": "sense.runtime.pin-lag", "state": "unknown",
                    "sample": f"git error: {count.stderr.strip() or 'rev-list failed'}",
                    "pin": pin, "head": head_sha, "kind": "read"}
        lag = int(count.stdout.strip())
        age_str = "unknown"
        oldest = subprocess.run(["git", "log", "--format=%cI", f"{pin}..{head_sha}"],
                                capture_output=True, text=True, timeout=2,
                                cwd=checkout_root)
        if oldest.returncode == 0 and oldest.stdout.strip():
            oldest_time = oldest.stdout.strip().splitlines()[-1]
            try:
                oldest_dt = datetime.fromisoformat(oldest_time.replace("Z", "+00:00"))
                age_str = _format_age(now - oldest_dt)
            except ValueError:
                age_str = "unknown"
        return {"id": "sense.runtime.pin-lag", "state": "verified",
                "sample": f"pin={pin[:7]} head={head_sha[:7]} lag={lag} age={age_str}",
                "pin": pin, "head": head_sha, "lag": lag, "age": age_str, "kind": "read"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": f"git error: {exc}", "kind": "read"}


def _repo_branch_inventory(home: Path) -> dict[str, object]:
    """Report the checkout's branch inventory against the main-only regime.

    The requirement is ``main`` as the sole branch kept in local Git and on
    GitHub. The reading covers local heads, the remote's heads, and registered
    worktrees. A worktree at a commit ``main`` cannot reach is non-main: one
    under ``.mishe-tauftauf/releases/`` is drift (releases are pinned from
    pushed ``main`` SHAs), one under ``.mishe-tauftauf/worktrees/`` is the
    sanctioned preservation area and is reported, and one elsewhere is drift —
    unique commits belong on ``main`` or in the preservation area. Uncommitted
    bytes in a non-release worktree are reported as ``dirty``. The shared
    checkout's untracked, non-ignored entries are named as ``strays``: a local
    note or evidence file written against a repo-root-relative path lands there
    rather than under the ignored site home, where a broad ``git add`` would
    commit it — the worktree's ``dirty`` name alone would hide which bytes. The
    state is unchanged by strays, as by ``dirty``: neither is itself a branch
    violation. State is ``unknown`` when the workspace is not a git repo or the
    remote cannot be read.
    """
    checkout_root = Path(home).resolve().parent
    unknown = {"id": "sense.repo.branch-inventory", "state": "unknown", "kind": "read"}

    def run(*args: str, timeout: float = 10) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True,
                                  timeout=timeout, cwd=checkout_root)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def unread(sample: str) -> dict[str, object]:
        return {**unknown, "sample": sample}

    probe = run("rev-parse", "--git-dir")
    if probe is None or probe.returncode != 0:
        return unread("not a git repo")
    local = run("for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if local is None or local.returncode != 0:
        return unread("local refs unreadable")
    local_names = sorted(set(local.stdout.split()))
    extra = [name for name in local_names if name != "main"]
    remote = run("ls-remote", "--heads", "origin")
    if remote is None or remote.returncode != 0:
        return unread("remote heads unreadable")
    remote_names = sorted({line.split("\t", 1)[1][len("refs/heads/"):]
                           for line in remote.stdout.splitlines()
                           if "\t" in line
                           and line.split("\t", 1)[1].startswith("refs/heads/")})
    extra_remote = [name for name in remote_names if name != "main"]
    listing = run("worktree", "list", "--porcelain")
    if listing is None or listing.returncode != 0:
        return unread("worktree list unreadable")
    main_result = run("rev-parse", "main")
    if main_result is None or main_result.returncode != 0:
        return unread("main ref unreadable")
    main_sha = main_result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", main_sha):
        return unread("main ref unreadable")
    entries: list[tuple[str, str]] = []
    for block in listing.stdout.split("\n\n"):
        path = head = None
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):]
            elif line.startswith("HEAD "):
                head = line[len("HEAD "):].strip()
        if path is not None and head is not None and re.fullmatch(r"[0-9a-f]{40}", head):
            entries.append((path, head))
    site = Path(home).resolve()
    releases_area = site / "releases"
    sanctioned_area = site / "worktrees"

    def display(path: str) -> str:
        try:
            return str(Path(path).relative_to(site))
        except ValueError:
            return path

    nonmain_names: list[str] = []
    drift_names: list[str] = []
    for path, head in entries:
        if head == main_sha:
            continue
        count = run("rev-list", "--count", f"main..{head}")
        if count is None or count.returncode != 0:
            return unread(f"reachability unreadable: {display(path)}")
        ahead = count.stdout.strip()
        if not ahead.isdigit() or int(ahead) == 0:
            continue
        nonmain_names.append(display(path))
        root = Path(path)
        if releases_area in root.parents:
            # Releases are pinned from pushed main SHAs: a non-main commit there
            # is a delivery anomaly.
            drift_names.append(display(path))
        elif sanctioned_area not in root.parents:
            # Unique commits belong on main or in the preservation area.
            drift_names.append(display(path))
    dirty_names: list[str] = []
    strays: list[str] = []
    for path, _head in entries:
        if releases_area in Path(path).parents:
            continue  # pinned release snapshots: their bytes are the pin's business
        status = run("-C", path, "status", "--porcelain", "-z")
        if status is None or status.returncode != 0:
            return unread(f"worktree status unreadable: {display(path)}")
        if status.stdout.strip():
            dirty_names.append(display(path))
        if Path(path).resolve() == checkout_root:
            # Untracked, non-ignored entries in the shared checkout. A local
            # note or evidence file written against a repo-root-relative path
            # lands here; `dirty` names the worktree but not the hazard, and a
            # broad ``git add`` would commit it. The site's own artifacts and
            # notes live under the ignored home, so anything untracked here is
            # a stray the reader should see named. ``-z`` suppresses git's
            # C-quoting, so a name with a space or a non-ASCII byte is reported
            # as itself; the field sits before the long name lists because the
            # pane truncates the sample.
            strays = sorted(entry[3:] for entry in status.stdout.split("\0")
                            if entry.startswith("?? "))
    parts = [f"local={len(local_names)}"]
    parts.append("extra=" + (",".join(extra) if extra else "none"))
    parts.append(f"remote={len(remote_names)}")
    parts.append("extra_remote=" + (",".join(extra_remote) if extra_remote else "none"))
    parts.append(f"worktrees={len(entries)}")
    parts.append(f"nonmain={len(nonmain_names)}")
    parts.append(f"dirty={len(dirty_names)}")
    parts.append("strays=" + (",".join(strays[:3]) if strays else "none"))
    if len(strays) > 3:
        parts.append(f"strays_more=+{len(strays) - 3}")
    parts.append("nonmain_names=" + (",".join(nonmain_names[:3]) if nonmain_names else "none"))
    if len(nonmain_names) > 3:
        parts.append(f"nonmain_more=+{len(nonmain_names) - 3}")
    parts.append("dirty_names=" + (",".join(dirty_names[:3]) if dirty_names else "none"))
    if len(dirty_names) > 3:
        parts.append(f"dirty_more=+{len(dirty_names) - 3}")
    state = "drift" if extra or extra_remote or drift_names else "verified"
    return {"id": "sense.repo.branch-inventory",
            "state": state, "sample": " ".join(parts), "kind": "read",
            "local": local_names, "extra": extra, "remote": remote_names,
            "extra_remote": extra_remote, "worktrees": len(entries),
            "nonmain": len(nonmain_names), "dirty": len(dirty_names),
            "nonmain_names": nonmain_names, "dirty_names": dirty_names,
            "strays": strays,
            "drift": drift_names}

DM_TO_RE = re.compile(r"^\[dm\] to=([A-Za-z0-9_.-]+)")
RECORD_REF_RE = re.compile(r"\[record\] records/([0-9a-f]{64})\.json sha256=[0-9a-f]{64}")
DM_DISPOSITION_WINDOW_SECONDS = 6 * 3600
"""Hours of DM history one disposition sample covers."""
DM_DISPOSITION_MATURITY_SECONDS = 30 * 60
"""A DM younger than this is right-censored: it has not had the full window to
be dispositioned, so it is excluded from the stall estimate."""
WALL_OUTCOME_RE = re.compile(r"^Wall outcome \S+ by ([A-Za-z0-9_.-]+)")
SETTLE_RESULT_WINDOW = 100
"""Trailing settlements one result-mix sample covers."""
SETTLE_INCIDENT_GAP_SECONDS = 60 * 60
"""Blocked settlements closer than this belong to one incident."""
from .chat_protocol import decode_lifecycle


def _coord_dm_disposition_age(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Measure how long addressed DMs wait for a disposition.

    A DM is dispositioned when the target replies by DM to the sender
    (``reply_edge``), records a wall outcome whose body cites the DM's seq
    token (``seq_credit``), or records any later wall outcome (``wall_join``,
    credited at its first tape citation). The decomposition makes the
    construct visible: ``credit_only`` counts DMs the seq rule credits beyond
    a reply edge; ``wall_only`` counts DMs the wall join credits beyond a
    reply edge. ``open`` counts DMs with no credit at all; ``stall`` is the
    age-gated ``open`` — only DMs at least ``DM_DISPOSITION_MATURITY_SECONDS``
    old — so a DM recorded minutes before the window end is not read as a
    stall. The window is the last 6 hours. State is ``verified`` when the
    tape and outcome store are readable; ``unknown`` when either is missing
    or unreadable, so a source change cannot pass as a clean bill.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    tape_path = home / "chat.log"
    if not tape_path.exists():
        return {"id": "sense.coord.dm-disposition-age", "state": "unknown",
                "sample": "chat tape unavailable", "kind": "read"}
    try:
        entries = parse_feed(tape_path.read_bytes(), home=home)
    except (OSError, ValueError) as exc:
        return {"id": "sense.coord.dm-disposition-age", "state": "unknown",
                "sample": f"chat tape unreadable: {exc}", "kind": "read"}
    ref_time: dict[str, datetime] = {}
    outcome_entries: list[tuple[datetime, str, str]] = []
    all_dms: list[tuple[datetime, str, str, int]] = []
    for entry in entries:
        ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        dm_match = DM_TO_RE.match(entry.body)
        if dm_match:
            all_dms.append((ts, entry.source, dm_match.group(1), entry.sequence))
        ref_match = RECORD_REF_RE.search(entry.body)
        if ref_match:
            ref_time.setdefault(ref_match.group(1), ts)
        if entry.body.startswith("Wall outcome "):
            out_match = WALL_OUTCOME_RE.match(entry.body)
            if out_match:
                outcome_entries.append((ts, out_match.group(1), entry.body))
    records_dir = home / "records"
    if not records_dir.is_dir():
        return {"id": "sense.coord.dm-disposition-age", "state": "unknown",
                "sample": "outcome store unavailable", "kind": "read"}
    window_start = now - timedelta(seconds=DM_DISPOSITION_WINDOW_SECONDS)
    dms = [dm for dm in all_dms if dm[0] >= window_start]
    pair_dms: dict[tuple[str, str], list[datetime]] = {}
    for ts, source, target, _seq in all_dms:
        pair_dms.setdefault((source, target), []).append(ts)
    for times in pair_dms.values():
        times.sort()
    if not dms:
        return {"id": "sense.coord.dm-disposition-age", "state": "verified",
                "sample": ("dms=0 reply_edge=0 seq_credit=0 credit_only=0 "
                           "wall_only=0 dispositioned=0 open=0 stall=0"),
                "kind": "read", "dms": 0, "reply_edge": 0, "seq_credit": 0,
                "credit_only": 0, "wall_only": 0, "dispositioned": 0, "open": 0,
                "stall": 0}
    outcomes: list[tuple[datetime, str]] = []
    for sha, ots in ref_time.items():
        path = records_dir / f"{sha}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("kind") != "wall-outcome":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        role = payload.get("role")
        if not isinstance(role, str):
            continue
        outcomes.append((ots, role))
    outcomes.sort()
    seq_by_role: dict[str, tuple[list[datetime], list[str]]] = {}
    for ots, orole, obody in sorted(outcome_entries):
        role_times, role_bodies = seq_by_role.setdefault(orole, ([], []))
        role_times.append(ots)
        role_bodies.append(obody)
    wall_by_role: dict[str, list[datetime]] = {}
    for ots, orole in outcomes:
        wall_by_role.setdefault(orole, []).append(ots)
    reply_n = seq_n = credit_only_n = wall_only_n = dispositioned = stall_n = 0
    ages: list[float] = []
    open_ages: list[tuple[float, str]] = []
    for ts, sender, target, seq in dms:
        disp_time = None
        has_reply = has_seq = has_wall = False
        times = pair_dms.get((target, sender))
        if times:
            idx = bisect.bisect_right(times, ts)
            if idx < len(times):
                has_reply = True
                disp_time = times[idx]
        seq_re = re.compile(rf"(?<!\d){seq}(?!\d)")
        role_times, role_bodies = seq_by_role.get(target, ([], []))
        if role_times:
            idx = bisect.bisect_right(role_times, ts)
            for i in range(idx, len(role_times)):
                if seq_re.search(role_bodies[i]):
                    has_seq = True
                    if disp_time is None or role_times[i] < disp_time:
                        disp_time = role_times[i]
                    break
        wall_times = wall_by_role.get(target)
        if wall_times:
            idx = bisect.bisect_right(wall_times, ts)
            if idx < len(wall_times):
                has_wall = True
                if disp_time is None or wall_times[idx] < disp_time:
                    disp_time = wall_times[idx]
        if has_reply:
            reply_n += 1
        if has_seq:
            seq_n += 1
        if has_seq and not has_reply:
            credit_only_n += 1
        if has_wall and not has_reply:
            wall_only_n += 1
        if disp_time is not None:
            dispositioned += 1
            ages.append((disp_time - ts).total_seconds())
        else:
            open_ages.append(((now - ts).total_seconds(), target))
            if now - ts >= timedelta(seconds=DM_DISPOSITION_MATURITY_SECONDS):
                stall_n += 1
    open_count = len(dms) - dispositioned
    ages.sort()
    open_ages.sort()

    def percentile(sorted_vals: list[float], p: float) -> float:
        if not sorted_vals:
            return 0.0
        k = (len(sorted_vals) - 1) * p
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

    p50 = percentile(ages, 0.5) / 60.0
    p90 = percentile(ages, 0.9) / 60.0
    oldest = ""
    if open_ages:
        oldest_age, oldest_role = open_ages[-1]
        oldest = f"{oldest_role}:{_format_age(timedelta(seconds=oldest_age))}"
    sample = (f"dms={len(dms)} reply_edge={reply_n} seq_credit={seq_n} "
              f"credit_only={credit_only_n} wall_only={wall_only_n} "
              f"dispositioned={dispositioned} open={open_count} stall={stall_n} "
              f"p50={p50:.0f}m p90={p90:.0f}m")
    if oldest:
        sample += f" oldest_open={oldest}"
    return {"id": "sense.coord.dm-disposition-age", "state": "verified",
            "sample": sample, "kind": "read",
            "dms": len(dms), "reply_edge": reply_n, "seq_credit": seq_n,
            "credit_only": credit_only_n, "wall_only": wall_only_n,
            "dispositioned": dispositioned, "open": open_count, "stall": stall_n,
            "p50_minutes": round(p50), "p90_minutes": round(p90),
            "oldest_open": oldest}


def _seed_settle_result_mix(home: Path) -> dict[str, object]:
    """Report the mix of settlement results over the tape's own receipts.

    Each settlement appends one ``seed`` entry whose body names the yield
    (``seed yield <role> wake=<n>``) and the result (``Turn settled
    (<result>)``). The reading counts the trailing ``SETTLE_RESULT_WINDOW``
    settlements by result, clusters the window's ``blocked`` receipts into
    incidents at the disclosed ``SETTLE_INCIDENT_GAP_SECONDS`` gap, and counts
    the settlements after the most recent ``blocked`` receipt on the whole tape
    (``since_last=none`` when none is recorded). A result outside the known
    three is counted as ``other``; a yield with no settled line is not a
    settlement. The window is a count, not a duration, so the sample is current
    as of the scan.

    State is ``verified`` when the window holds no ``blocked`` receipt and
    ``drift`` when one does: a blocked settlement is the signal the mix moved
    off the all-clean reading, and it is what makes the rate visible rather
    than inferred from chat. State is ``unknown`` when the tape is absent or
    unreadable, so a source change cannot pass as a clean bill.
    """
    tape_path = home / "chat.log"
    if not tape_path.exists():
        return {"id": "sense.seed.settle-result-mix", "state": "unknown",
                "sample": "chat tape unavailable", "kind": "read"}
    try:
        entries = parse_feed(tape_path.read_bytes(), home=home)
    except (OSError, ValueError) as exc:
        return {"id": "sense.seed.settle-result-mix", "state": "unknown",
                "sample": f"chat tape unreadable: {exc}", "kind": "read"}
    settlements: list[tuple[datetime, str]] = []
    for entry in entries:
        try:
            control = decode_lifecycle(entry)
        except ValueError as exc:
            return {"id": "sense.seed.settle-result-mix", "state": "unknown",
                    "sample": f"invalid lifecycle control: {exc}", "kind": "read"}
        if control is None or control.kind != "yield" or control.result is None:
            continue
        ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        settlements.append((ts, control.result))
    window = settlements[-SETTLE_RESULT_WINDOW:]
    counts = {"verified": 0, "changed": 0, "blocked": 0, "other": 0}
    for _ts, result in window:
        counts[result if result in counts else "other"] += 1
    blocked_times = sorted(ts for ts, result in window if result == "blocked")
    incidents = 0
    previous: datetime | None = None
    for ts in blocked_times:
        if previous is None or (ts - previous).total_seconds() > SETTLE_INCIDENT_GAP_SECONDS:
            incidents += 1
        previous = ts
    last_blocked = next((index for index in range(len(settlements) - 1, -1, -1)
                         if settlements[index][1] == "blocked"), None)
    since_last = None if last_blocked is None else len(settlements) - 1 - last_blocked
    sample = (f"n={len(window)} verified={counts['verified']} "
              f"changed={counts['changed']} blocked={counts['blocked']} "
              f"other={counts['other']} incidents={incidents} "
              f"since_last={'none' if since_last is None else since_last}")
    return {"id": "sense.seed.settle-result-mix",
            "state": "drift" if blocked_times else "verified",
            "sample": sample, "kind": "read", "window": len(window),
            "verified": counts["verified"], "changed": counts["changed"],
            "blocked": counts["blocked"], "other": counts["other"],
            "incidents": incidents, "since_last": since_last,
            "incident_gap_seconds": SETTLE_INCIDENT_GAP_SECONDS}


EVIDENCE_BINDING_CLAUSE_TIME = datetime(2026, 10, 6, 16, 22, 11, tzinfo=timezone.utc)
"""The write-once outcome clause (docs/wall-coordination.md) landed in 3c22087;
outcomes recorded earlier are not bound by it."""


def _display_path(path: str, home: Path) -> str:
    """Site-relative display form of a stored evidence path.

    The pane must show where a violating file lives, so a stored path is
    rendered relative to the site home when its canonical form stays inside
    it; a path canonicalizing outside the home is shown as stored.
    """
    candidate = Path(path) if os.path.isabs(path) else home / path
    try:
        return candidate.resolve().relative_to(home.resolve()).as_posix()
    except ValueError:
        return str(path)


def _ledger_evidence_binding(home: Path) -> dict[str, object]:
    """Check that post-clause wall outcomes bind write-once evidence.

    A ``wall-outcome`` record binds its per-outcome evidence file with
    ``payload.evidence.{path,sha256}`` at record time. The clause
    (``docs/wall-coordination.md``) prescribes three independent
    requirements: the path is under ``artifacts/`` (A), the file is unedited
    — the bound digest still matches the current bytes (B) — and the path is
    cited by no other outcome (D). The sense re-hashes every tape-referenced
    post-clause outcome's evidence and reports two windows: the latest
    outcome per role (live compliance) and the whole post-clause set
    (standing audit — the store is append-only, so a repaired violation stays
    flagged). ``latest_bad_roles`` names the roles that are non-compliant in
    the live window, so the live set is readable beside the count rather than
    re-derived from ``violating``, which lists the standing audit's newest
    rows. State is ``drift`` when any checked outcome violates, including
    when other publications have incomplete coverage; otherwise incomplete
    coverage, unavailable input or no eligible event is ``unknown``.

    The under-artifacts test resolves the stored path first, so a ``..``
    segment cannot pass it lexically. D counts distinct stored path strings:
    two names for one inode (hardlink aliasing) both read clean.
    """
    coverage = classify_outcomes(home, EVIDENCE_BINDING_CLAUSE_TIME)
    incomplete = coverage["incomplete"]
    if coverage["unavailable"]:
        return {"id": "sense.ledger.evidence-binding", "state": "unknown",
                "sample": coverage["unavailable"], "kind": "read",
                "coverage_incomplete": len(incomplete), "incomplete": incomplete}
    loaded = [(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
               row["sequence"], row["role"], row["evidence"])
              for row in coverage["events"]]
    if not loaded and not incomplete:
        return {"id": "sense.ledger.evidence-binding", "state": "unknown",
                "sample": "no post-clause outcomes", "kind": "read",
                "coverage_incomplete": 0, "incomplete": []}
    artifacts_root = home.resolve() / "artifacts"
    path_counts: dict[str, int] = {}
    for _ts, _sequence, _role, evidence in loaded:
        path = evidence.get("path")
        if isinstance(path, str):
            path_counts[path] = path_counts.get(path, 0) + 1
    checked: list[tuple[datetime, int, str, str, str]] = []
    for ts, sequence, role, evidence in loaded:
        path = evidence.get("path")
        digest = evidence.get("sha256")
        classes: list[str] = []
        file_path = Path(path) if isinstance(path, str) else None
        # Resolve before the test: a stored ``artifacts/../walls/x.md`` passes
        # is_relative_to lexically while its canonical path is outside artifacts.
        resolved = file_path.resolve() if file_path is not None else None
        if resolved is None or not resolved.is_relative_to(artifacts_root):
            classes.append("A")
        try:
            current = (hashlib.sha256(resolved.read_bytes()).hexdigest()
                       if resolved is not None else None)
            if current != digest:
                classes.append("B")
        except OSError:
            classes.append("B")
        if path_counts.get(path, 0) > 1:
            classes.append("D")
        name = _display_path(path, home) if file_path is not None else str(path)
        checked.append((ts, sequence, role, name, "".join(classes)))
    checked.sort()
    violations = [row for row in checked if row[4]]
    latest: dict[str, tuple[datetime, int, str]] = {}
    for ts, sequence, role, _name, classes in checked:
        latest[role] = (ts, sequence, classes)
    latest_bad_roles = sorted((role, classes) for role, (_ts, _sequence, classes)
                              in latest.items() if classes)
    named_bad = ",".join(f"{role}({classes})" for role, classes in latest_bad_roles) or "none"
    parts = [f"bound={len(checked)} latest_roles={len(latest)} "
             f"latest_bad={len(latest_bad_roles)} latest_bad_roles={named_bad} "
             f"all_bad={len(violations)}"]
    if incomplete:
        parts.append(f"coverage_incomplete={len(incomplete)} sequences=" +
                     ",".join(str(row["sequence"]) for row in incomplete[:3]))
    if violations:
        parts.append("violating=" + " ".join(
            f"{role}@{ts.strftime('%Y-%m-%dT%H:%M:%SZ')}:{name}({classes})"
            for ts, _sequence, role, name, classes in violations[-3:][::-1]))
        if len(violations) > 3:
            parts.append(f"+{len(violations) - 3}more")
    return {"id": "sense.ledger.evidence-binding",
            "state": "drift" if violations else "unknown" if incomplete else "verified",
            "sample": " ".join(parts), "kind": "read",
            "bound": len(checked), "latest_roles": len(latest),
            "latest_bad": len(latest_bad_roles),
            "latest_bad_roles": [f"{role}({classes})" for role, classes in latest_bad_roles],
            "all_bad": len(violations),
            "coverage_incomplete": len(incomplete), "incomplete": incomplete,
            "event_sequences": [row["sequence"] for row in coverage["events"]],
            "violations": [f"{role}@{ts.strftime('%Y-%m-%dT%H:%M:%SZ')}:{name}({classes})"
                           for ts, _sequence, role, name, classes in violations]}

SPACE_SOURCE = "http://127.0.0.1:8765/node/note3"
SPACE_PRODUCER = {"name": "android-body-perception", "entry_point": "watch.py",
                  "mode": "space", "contract": 1}
SPACE_VALIDITY_SECONDS = 30.0


def _space_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _space_utc(value: object) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("missing UTC offset")
    return parsed.timestamp()


def _space_stamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def _space_age(seconds: float) -> str:
    """Compact age for the pane; a reader must see how stale a sample is."""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def _space_light_age(light: dict, now: float) -> str | None:
    """Name the last validated phone sample and its age so an UNKNOWN reading
    still says how stale the feed is, not only that it is not current."""
    phone = light.get("phone_sample_epoch_s")
    if not _space_number(phone) or phone < 0 or phone > now:
        return None
    return f"last-phone={_space_stamp(phone)} age={_space_age(now - phone)}"


def _space_endpoint(item: dict, validity: float) -> tuple[float, float]:
    phone, receipt = item["phone_sample_epoch_s"], item["consumer_receipt_epoch_s"]
    if (not _space_number(phone) or not _space_number(receipt)
            or type(item["sequence"]) is not int or item["sequence"] < 0
            or not _space_number(item["lux"]) or item["lux"] < 0
            or not 0 <= receipt - phone <= validity):
        raise ValueError("invalid endpoint")
    return phone, receipt


def _space_light(data: object, now: float) -> dict:
    """Validate original evidence; publication and polling never renew a sample."""
    row = {"id": "sense.space.light", "kind": "read", "state": "unknown",
           "reason": "malformed-boundary", "sample": "Note3 light UNKNOWN: malformed-boundary",
           "current_lux": None, "event_id": None, "historical_transition": None,
           "clock_uncertainty": "phone/host clock agreement unverified"}

    def unknown(reason: str, detail: str | None = None) -> dict:
        row.update(state="unknown", reason=reason, current_lux=None, event_id=None,
                   sample=f"Note3 light UNKNOWN: {reason}"
                          + (f" {detail}" if detail else "")
                          + f"; source={SPACE_SOURCE}")
        return row

    try:
        if (type(data["schema_version"]) is not int or data["schema_version"] != 1
                or data["producer"] != SPACE_PRODUCER or not isinstance(data["nodes"], list)):
            return unknown("invalid-schema-or-producer")
        nodes = [node for node in data["nodes"] if node["node"] == "note3"]
        if len(nodes) != 1:
            return unknown("missing-or-duplicate-note3")
        node = nodes[0]
        # Retain only the owned Note3 boundary; never forward other devices.
        row["boundary"] = {"schema_version": data["schema_version"], "producer": data["producer"],
                           "generated_at_utc": data["generated_at_utc"], "nodes": [node]}
        if (node["source"] != SPACE_SOURCE or not isinstance(node["session"], str)
                or not node["session"].strip() or type(node["cursor"]) is not int):
            return unknown("invalid-provenance")
        row["provenance"] = {"source": node["source"], "session": node["session"]}
        row["historical_transition"] = node.get("last_light_transition")
        if node["backlog_page_pending"] is not False:
            return unknown("backlog-or-missing-coverage")
        if node["transport"] != "reachable" or node["last_error"] is not None:
            return unknown("transport-not-current")
        light = node["light"]
        validity = light["validity_seconds"]
        if not _space_number(validity) or validity <= 0:
            return unknown("invalid-validity")
        validity = min(validity, SPACE_VALIDITY_SECONDS)
        row["validity_seconds"] = validity
        if (light["status"] != "fresh-clock-conditional"
                or light["delayed_at_receipt"] is not False):
            return unknown("light-not-current-or-delayed", _space_light_age(light, now))
        if light["units"] != "lux":
            return unknown("invalid-light-units")
        phone, receipt = _space_endpoint(light, validity)
        if node["cursor"] < light["sequence"]:
            return unknown("invalid-sequence")
        collection_age = light["age_ms_at_phone_collection"]
        if not _space_number(collection_age) or not 0 <= collection_age <= validity * 1000:
            return unknown("invalid-or-stale-collection-age")
        generated, poll = _space_utc(data["generated_at_utc"]), _space_utc(node["last_poll_utc"])
        times = (phone, receipt, generated, poll)
        if any(value > now for value in times):
            return unknown("clock-future")
        if any(now - value > validity for value in times):
            return unknown("stale-original-evidence", _space_light_age(light, now))
        row.update(state="verified", reason="fresh-clock-conditional",
                   current_lux=light["lux"], sequence=light["sequence"],
                   expires_epoch_s=min(times) + validity,
                   sample=(f"Note3 {light['lux']:g}lux #{light['sequence']} fresh-clock-conditional; "
                           f"phone={_space_stamp(phone)} receipt={_space_stamp(receipt)}; "
                           f"validity={validity:g}s source={node['source']} session={node['session']}"))
        event = node.get("last_light_transition")
        if isinstance(event, dict):
            try:
                event_validity = event["validity_seconds"]
                if not _space_number(event_validity) or event_validity <= 0:
                    raise ValueError("invalid event validity")
                event_validity = min(validity, event_validity)
                start, end = event["from"], event["to"]
                start_phone, start_receipt = _space_endpoint(start, event_validity)
                end_phone, end_receipt = _space_endpoint(end, event_validity)
                if (event["kind"] != "measured_light_bucket_transition"
                        or event["source"] != node["source"] or event["session"] != node["session"]
                        or event["event_id"] != f"note3:{node['session']}:{end['sequence']}"
                        or not start["sequence"] < end["sequence"] <= light["sequence"]
                        or not 0 <= end_phone - start_phone <= event_validity
                        or end_receipt < start_receipt
                        or any(not 0 <= now - value <= event_validity
                               for value in (start_phone, start_receipt, end_phone, end_receipt))):
                    raise ValueError("invalid event provenance or window")
                buckets = tuple(math.floor(math.log2(1 + point["lux"])) for point in (start, end))
                if (buckets[0] == buckets[1] or type(event["bucket_from"]) is not int
                        or type(event["bucket_to"]) is not int
                        or buckets != (event["bucket_from"], event["bucket_to"])):
                    raise ValueError("invalid transition")
                row["event_id"] = event["event_id"]
                row["event_sequence"] = end["sequence"]
                row["event_sample"] = (
                    f"historical measured light change #{start['sequence']} {start['lux']:g}lux"
                    f" -> #{end['sequence']} {end['lux']:g}lux; "
                    f"phone={_space_stamp(start_phone)}->{_space_stamp(end_phone)}; "
                    f"fresh-clock-conditional source={node['source']} session={node['session']}")
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                row["event_reason"] = "invalid-or-expired-historical-transition"
        return row
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return unknown("malformed-boundary")


def _space_light_read(home: Path) -> dict:
    path = home / "body" / "space.json"
    text = _read(path, 65536)
    try:
        data = json.loads(text) if text is not None else None
    except (ValueError, TypeError):
        data = None
    row = _space_light(data, time.time())
    if data is None:
        row.update(reason="boundary-absent-or-corrupt",
                   sample=f"Note3 light UNKNOWN: boundary-absent-or-corrupt; file={path}")
    return row


def _space_new_event(previous: dict | None, current: dict) -> bool:
    """Baseline, restart and recovery seed history instead of replaying events."""
    return bool(previous and previous.get("state") == current.get("state") == "verified"
                and previous.get("provenance") == current.get("provenance")
                and previous.get("expires_epoch_s", -1) >= time.time()
                and current.get("event_id") and current["event_id"] != previous.get("event_id")
                and current.get("event_sequence", -1) > previous.get("sequence", -1))


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
                     **_journal_unit_failure_window(home)})
    observed.append(_mind_wedge_suspects())
    observed.append(_ledger_delivery_invariant(home))
    observed.append(_ledger_dv_binding(home))
    observed.append(_ledger_evidence_binding(home))
    observed.append(_space_light_read(home))

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
    observed.append(_runtime_pin_lag(home))
    observed.append(_repo_branch_inventory(home))
    observed.append(_coord_dm_disposition_age(home))
    observed.append(_seed_settle_result_mix(home))
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

def _producer() -> str:
    """Identify the code that produced a scan: package root + git commit.

    A checkout run and a pin-run resolve to different package roots, so the
    field distinguishes them even when their readings are identical. The git
    commit names the exact source tree; ``unknown`` means the root is not a git
    work tree at all (an exported tree rather than a checkout or release).
    """
    package_root = Path(__file__).resolve().parent
    checkout_root = package_root.parent.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=checkout_root,
        )
        sha = result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        sha = "unknown"
    return f"{package_root}@{sha}"


def producer_label(home: Path, producer: object) -> str:
    """Name a scan's recorded producer against this site's pin.

    The producer field names the package root and commit that ran ``scan()``,
    but the readings alone do not say whether that code is the pin the services
    import or an unreviewed checkout run, so ``latest.json`` (last writer wins)
    could otherwise present either as the live fact. The label is
    ``<origin>@<sha>``, where the origin is ``pin`` when the root is the pinned
    package, ``checkout`` when it is the shared checkout beside the site, and
    ``other`` for any other root; an unreadable pin leaves the origin
    ``unknown``. A receipt that recorded no producer at all is ``unknown``. It
    names the provenance; it does not itself change the reading's freshness.
    """
    if not isinstance(producer, str) or "@" not in producer:
        return "unknown"
    root, _, sha = producer.rpartition("@")
    origin = "unknown"
    if (home / "health/runtime-release.json").exists():
        try:
            # The fallback is unreachable: the pin file exists, so a missing
            # one never reaches here.
            pinned = str(source_for(home, home) / "src" / "mishe_tauftauf")
        except ValueError:
            pinned = None
        if pinned is not None:
            if root == pinned:
                origin = "pin"
            elif root == str(Path(home).resolve().parent / "src" / "mishe_tauftauf"):
                origin = "checkout"
            else:
                origin = "other"
    return f"{origin}@{sha[:12] or 'unknown'}"


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
        snapshot["producer"] = _producer()
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
    # Recheck the original boundary at notification time, not the scan timestamp.
    readings = [_space_light(item["boundary"], time.time())
                if item.get("id") == "sense.space.light" and "boundary" in item else item
                for item in readings]
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
            if identifier == "sense.space.light":
                detail = json.dumps((item.get("reason"), item.get("provenance")), sort_keys=True)
            elif item["kind"] == "declaration" or state != "verified":
                detail = sample_text
            elif identifier == "sense.proc.cpu-busy":
                detail = "high" if re.search(r"(?:^|\s)high(?:$|\s)", sample_text) else "not-high"
            elif identifier == "sense.thermal.hwmon":
                detail = json.dumps(item.get("identity", []), ensure_ascii=False, sort_keys=True)
            else:
                detail = ""
            result[identifier] = (state, detail)
        return result

    current_signature = signature({"observations": readings})
    old_signature = signature(previous)
    changed = [name for name in current_signature if current_signature[name] != old_signature.get(name)]
    old_space = next((item for item in (previous or {}).get("observations", [])
                      if item.get("id") == "sense.space.light"), None)
    new_space = next((item for item in readings if item.get("id") == "sense.space.light"), None)
    if new_space and _space_new_event(old_space, new_space) and "sense.space.light" not in changed:
        changed.append("sense.space.light")
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
        if name == "sense.space.light" and old_reading and new_reading:
            if _space_new_event(old_reading, new_reading):
                change_details.append(f"{name} {new_reading['event_sample']}")
            else:
                change_details.append(f"{name} availability/provenance baseline (not a physical transition)")
            continue
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
             f"Producer: {producer_label(home, snapshot.get('producer'))}.",
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
    """Read a complete observation receipt, or treat malformed evidence as absent.

    Samples retain their JSON types (including numeric counters and lists).
    State strings are open-ended: admitting a receipt does not verify its reads.
    """
    path = home / "discovery" / "latest.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("observations"), list):
        return None
    for item in snapshot["observations"]:
        if (not isinstance(item, dict)
                or not all(isinstance(item.get(key), str) for key in ("id", "kind", "state"))
                or "sample" not in item):
            return None
    return snapshot


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
