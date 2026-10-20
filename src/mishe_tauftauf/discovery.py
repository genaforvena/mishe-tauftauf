"""Read-only local frontier scan; values are evidence, not authority."""

from __future__ import annotations
import bisect
import io
import ast

import hashlib
import json
import math
import os
import time
import re
import shutil
import selectors
import tarfile
import tempfile
import subprocess
from subprocess import TimeoutExpired as _TimeoutExpired
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from . import host_boot, omp_pane, proc_table, space_text
from .body_perception import (read_redmi10, read_redmi10_dense_baseline,
                             read_redmi10_delivery_gaps, read_redmi10_light,
                             read_redmi10_peer_delivery)
from .multibody import read_multibody_coverage
from .activity import roles as seated_roles
from .feed import Feed, FeedEntry, entry_timestamp as _feed_entry_timestamp, parse_feed, publication_lock, read_scan_series
from .chat_protocol import decode_lifecycle, ProtocolError
from .host_radio import (BLUETOOTH_ROOT, COMMAND_TIMEOUT_SECONDS, NET_ROOT,
                         RFKILL_ROOT, admin_up, bluetooth_controllers, carrier_state,
                         controller_state, decode_git_log_lines,
                         decode_tailnet_status, ethernet_interfaces, link_speed, operstate,
                         rfkill_switches, rfkill_unreadable, rfkill_verdict, wireless_interfaces)
from .coord_tape import claim_source as tape_claim_source
from .outcome_events import classify_outcomes, eligible_evidence_path
from .observations import literal_site_home_name, reserved_home_name
from .runtime_source import source_for
from .scan_freshness import age_bounds, classify_age, endpoint
from .spawn_dialect import (script_spawns_site_package,
                            spawns_site_package)
from .timestamp_text import decode_iso_datetime
from .unit_properties import SITE_UNIT_PREFIX, is_site_unit

COMMANDS = ("rg", "git", "tmux", "python3", "systemctl", "journalctl", "ps", "df",
            "lsusb", "lspci", "bluetoothctl", "sensors", "upower", "evtest", "tailscale")

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
        code = process.wait(timeout=remaining)
        if code != 0:
            # journalctl exits 1 with empty stdout when --grep matches nothing:
            # a healthy empty window, not a failed acquisition. Any output
            # alongside the nonzero exit stays a failure (ambiguous origin).
            if code != 1 or output:
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


def _pane_mind_model(pid: int) -> str | None:
    """The model a pane's own process runs, or None when it cannot be read.

    An omp session log carries provider errors from every agent the pane hosts,
    including the title generator and spawned subagents, whose model differs
    from the mind's. Only a record whose model is the pane's own can name the
    mind's turn, so the reader must know which model that is. The live command
    line is authoritative for the running process and is per-pane, so it also
    resolves a pane belonging to another site. An unreadable command line reads
    None, which the caller treats as "cannot attribute" rather than "no error".
    """
    argv = omp_pane.command_line(pid)
    return omp_pane.model(argv) if argv is not None else None


def _omp_continue_chain(pid: int, now: datetime,
                        mind_model: str | None = None) -> dict | None:
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

    ``mind_model`` is the model the pane's own process runs, when it is known.
    A provider error whose record names a *different* model belongs to another
    agent the pane hosts (its title generator, a subagent) and does not count:
    counting it would accuse a healthy mind's pane. A record with no model, or
    a pane whose model could not be read, is still counted — the reader must
    not hide a failure it cannot attribute.

    ``foreign`` counts the records the reader excluded for naming another model,
    so that suppression is visible in the reading rather than silent.
    """
    log_path = _omp_log_path(pid)
    if log_path is None:
        return None
    continues: list[datetime] = []
    sources: dict[str, int] = {}
    error_classes: dict[str, int] = {}
    last_error_ts: datetime | None = None
    error_count: int = 0
    foreign: int = 0
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
            record_model = record.get("model")
            if (mind_model is not None and isinstance(record_model, str) and record_model
                    and not omp_pane.same_model(record_model, mind_model)):
                # A subagent or the title generator failed on its own model, not
                # the mind's: counting it would accuse a healthy pane. The
                # exclusion is counted so the suppression is visible.
                foreign += 1
                continue
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
            foreign = 0
    cause = "none"
    if error_classes:
        dominant = sorted(error_classes.items(), key=lambda item: (-item[1], item[0]))[0][0]
        cause = f"provider-error:{dominant}"
    span = (now - continues[0]).total_seconds() / 60.0 if continues else 0.0
    return {"chain": len(continues), "span_minutes": round(span, 1),
            "sources": sources, "cause": cause,
            "last_error_ts": last_error_ts.isoformat() if last_error_ts else None,
            "error_count": error_count, "foreign": foreign}

def _mind_wedge_suspects() -> dict:
    """Flag minds whose omp session log indicates a wedge.

    Rule R (chain wedge): SUSPECT when the open continue chain is >= 3 and
    spans >= 15 min with no completed turn between.

    Rule R2 (provider-error wedge): SUSPECT when the pane has provider errors
    but no continue chain and the last error is >= 5 min old. This catches the
    instant-provider-error failure mode (e.g. a 403 FreeTierError) where the
    turn fails immediately, omp never schedules a retry, and rule R cannot fire.

    A pane is identified by window, pid and the tmux session hosting it. More
    than one site renders on this host and their window names collide, so a
    suspect without its session cannot be attributed to this plant or the
    linked site beside it. Live evidence 2026-10-10: eight consecutive scans
    flagged the same six roles with a fresh pid batch each time, and every
    flagged pid's omp session log belonged to the linked android-body site.

    The signal reads omp's session log
    (``~/.omp/logs/omp.<date>.<pid>.log``); the sample carries a coverage tier
    (``panes=N with_log=M``), and a pane set with no log at all reads UNKNOWN
    rather than a clean bill, so a log format or path change cannot silently
    pass as healthy.

    A provider error counts only when its record names the model the pane's own
    process runs (read from the live command line): an omp session log also
    carries its title generator's and subagents' turns, whose model differs, and
    a failure there does not mean the mind is wedged. An unreadable command line
    or a record with no model is still counted, so a failure that cannot be
    attributed is never hidden.
    """
    now = datetime.now(timezone.utc)
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}|#{window_name}|#{pane_pid}"],
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
    foreign = 0
    for session, window_name, pid in proc_table.pane_records(result.stdout):
        panes += 1
        chain = _omp_continue_chain(pid, now, _pane_mind_model(pid))
        if chain is None:
            continue
        with_log += 1
        foreign += chain["foreign"]
        if (chain["chain"] >= WEDGE_CHAIN_THRESHOLD
                and chain["span_minutes"] >= WEDGE_SPAN_THRESHOLD_MINUTES):
            suspects.append({"window": window_name, "pid": pid, "session": session,
                             **chain})
        elif (chain["chain"] == 0
                and chain["cause"] != "none"
                and chain["last_error_ts"] is not None):
            last_error = decode_iso_datetime(chain["last_error_ts"])
            if last_error is None:
                continue
            error_age_min = (now - last_error).total_seconds() / 60.0
            if error_age_min >= WEDGE_ERROR_STALE_MINUTES:
                suspects.append({"window": window_name, "pid": pid,
                                 "session": session, **chain,
                                 "rule": "provider-error",
                                 "error_age_minutes": round(error_age_min, 1)})
        elif (1 <= chain["chain"] < WEDGE_CHAIN_THRESHOLD
                and chain["cause"] != "none"
                and chain["span_minutes"] >= WEDGE_SPAN_THRESHOLD_MINUTES
                and chain["last_error_ts"] is not None):
            last_error = decode_iso_datetime(chain["last_error_ts"])
            if last_error is None:
                continue
            error_age_min = (now - last_error).total_seconds() / 60.0
            if error_age_min >= WEDGE_ERROR_STALE_MINUTES:
                suspects.append({"window": window_name, "pid": pid,
                                 "session": session, **chain,
                                 "rule": "chain-stall",
                                 "error_age_minutes": round(error_age_min, 1)})
    if suspects:
        parts = []
        for s in suspects:
            if s.get("rule") == "provider-error":
                parts.append(
                    f"{s['window']}(pid={s['pid']},session={s['session']},"
                    f"rule=provider-error,"
                    f"chain={s['chain']},error_age={s['error_age_minutes']}min,"
                    f"cause={s['cause']})")
            elif s.get("rule") == "chain-stall":
                parts.append(
                    f"{s['window']}(pid={s['pid']},session={s['session']},"
                    f"rule=chain-stall,"
                    f"chain={s['chain']},span={s['span_minutes']}min,"
                    f"error_age={s['error_age_minutes']}min,"
                    f"cause={s['cause']})")
            else:
                parts.append(
                    f"{s['window']}(pid={s['pid']},session={s['session']},"
                    f"chain={s['chain']},"
                    f"span={s['span_minutes']}min,src="
                    + ",".join(f"{name}:{count}"
                               for name, count in sorted(s["sources"].items()))
                    + f",cause={s['cause']})")
        sample = "suspects=" + " ".join(parts)
    else:
        sample = "suspects=0"
    sample += f" panes={panes} with_log={with_log} foreign={foreign}"
    state = "verified" if with_log else "unknown"
    return {"id": "sense.mind.wedge-suspect", "state": state,
            "sample": sample, "suspects": suspects,
            "panes": panes, "with_log": with_log, "foreign": foreign, "kind": "read"}

WATCH_WAIT_SAMPLE_GAP_SECONDS = 3.0
_WATCH_VERDICTS = frozenset({"watch-treadmill", "watch-docs-treadmill"})
"""Seconds between the two pane captures a watch-wait verdict compares.

Long enough that a live TUI frame advances its elapsed timer between reads
(the probe measured timer ticks inside 15 s), short enough that the two
``capture-pane`` round trips stay a bounded addition to the scan. Shorter
than the CI poll loops the sense excuses (150 s sleeps), so a verdict never
spans the wait it names.
"""

WATCH_WAIT_GH_TIMEOUT_SECONDS = 10
"""Bound for one ``gh run view`` external-state read per pane."""


def _capture_mind_frame(session: str, window: str) -> str | None:
    """One mind-pane capture, or ``None`` when tmux cannot answer.

    Reads the lower (``.1``) pane, where the mind's TUI runs; the upper pane
    is the dashboard renderer, whose lease proves rendering, not mind state.
    """
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", f"{session}:{window}.1"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _ci_run_state(run_id: str) -> dict | None:
    """The external CI state for one run id, or ``None`` when unreadable.

    The ``gh run view --json`` payload dialect is decoded in ``omp_pane``;
    this function only bounds the subprocess read. A transport failure, a
    missing ``gh`` binary, an unknown run id and a malformed payload all
    read ``None``: the caller holds UNKNOWN rather than excusing a wait on
    a state it could not read.
    """
    try:
        result = subprocess.run(
            ["gh", "run", "view", run_id, "--json", "status,conclusion,updatedAt"],
            capture_output=True, text=True, timeout=WATCH_WAIT_GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return omp_pane.ci_run_state(result.stdout)


def _watch_wait_verdict(first: str, second: str, now: str) -> dict:
    """One pane's verdict from two captures and an optional CI read.

    All inputs are decoded values: ``omp_pane`` names the wait pattern and
    run id, ``hashlib`` names frame change. The verdicts:

    * ``watch-wait`` — an allowlisted long-poll pattern is present and the
      external state confirms the wait (a named CI run still in progress),
      or the pattern needs no external state (``sleep``, ``tail -f``) and
      normalized content is still while the raw frame advances.
    * ``active`` — normalized content grew: the mind is producing, not
      waiting, whatever the pattern says.
    * ``suspect`` — frame alive, no pattern, no growth: cross with
      ``sense.mind.wedge-suspect`` session-log evidence before calling it a
      wedge.
    * ``pane-dead`` — both captures empty: route to the supervisor/health,
      not wedge logic.
    * ``unknown`` — pattern present but external state unreadable, or the
      pattern names CI with no run id: stays visible, never clean.
    """
    if omp_pane.frame_empty(first) and omp_pane.frame_empty(second):
        return {"verdict": "pane-dead", "detail": "both captures empty"}
    pattern = omp_pane.wait_pattern(second) or omp_pane.wait_pattern(first)
    stable = (hashlib.sha256(omp_pane.normalize_frame(first).encode("utf-8")).hexdigest()
              == hashlib.sha256(omp_pane.normalize_frame(second).encode("utf-8")).hexdigest())
    frame_live = first != second
    if stable and not frame_live and pattern is None:
        return {"verdict": "suspect", "detail": "frame frozen, no wait pattern",
                "pattern": None, "stable": True, "frame_live": False}
    if not stable:
        return {"verdict": "active", "detail": "normalized content grew",
                "pattern": pattern, "stable": False, "frame_live": frame_live}
    if pattern is None:
        return {"verdict": "suspect",
                "detail": "frame alive, no wait pattern, no growth",
                "pattern": None, "stable": True, "frame_live": frame_live}
    run_id = omp_pane.ci_run_id(second) or omp_pane.ci_run_id(first)
    needs_external = pattern in ("gh run watch", "gh run view", "waiting on",
                                  "Waiting for CI poll result")
    if needs_external:
        if run_id is None:
            return {"verdict": "unknown",
                    "detail": f"wait pattern {pattern!r} names no run id",
                    "pattern": pattern, "stable": True, "frame_live": frame_live}
        state = _ci_run_state(run_id)
        if state is None:
            return {"verdict": "unknown",
                    "detail": f"wait pattern {pattern!r} run {run_id} state unreadable",
                    "pattern": pattern, "stable": True, "frame_live": frame_live,
                    "run_id": run_id}
        if state["status"] == "in_progress":
            return {"verdict": "watch-wait",
                    "detail": (f"wait pattern {pattern!r} run {run_id} "
                               f"status={state['status']} updated={state.get('updated_at')}"),
                    "pattern": pattern, "stable": True, "frame_live": frame_live,
                    "run_id": run_id, "ci_state": state, "sampled": now}
        return {"verdict": "suspect",
                "detail": (f"wait pattern {pattern!r} run {run_id} "
                           f"no longer {state['status']}"),
                "pattern": pattern, "stable": True, "frame_live": frame_live,
                "run_id": run_id, "ci_state": state, "sampled": now}
    return {"verdict": "watch-wait",
            "detail": (f"wait pattern {pattern!r} content still, frame "
                       f"{'advancing' if frame_live else 'still'}"),
            "pattern": pattern, "stable": True, "frame_live": frame_live,
            "sampled": now}


def _mind_watch_wait(home: Path) -> dict:
    """Name each live mind pane's wait state from two bounded captures.

    Rule W (watch-wait): a pane whose normalized capture is still across the
    sample gap while its raw frame advances, whose text carries an allowlisted
    long-poll spelling, and whose external state confirms the wait (a named
    CI run still ``in_progress``) reads ``watch-wait`` — tool-blocked, not
    wedged. A still frame with no pattern reads ``suspect`` (cross with the
    wedge sensor's session-log evidence); an empty frame reads ``pane-dead``
    (supervisor route); a pattern with unreadable external state reads
    ``unknown``. A pane set with no capture at all reads UNKNOWN rather than
    a clean bill, so a ``capture-pane`` format change cannot silently pass
    as healthy.

    The verdict is bound to the sampled window (``sampled`` + gap): a
    ``watch-wait`` expires when the external state changes or the next scan
    runs. The consumer is witness pane rendering (WATCH-WAIT instead of a
    stuck appearance); it complements, never replaces,
    ``sense.mind.wedge-suspect``. Session-qualified attribution follows the
    wedge sensor: two sites share window names on this host, so each verdict
    carries its tmux session.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}|#{window_name}|#{pane_pid}"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.mind.watch-wait", "state": "unknown",
                "sample": "tmux pane enumeration unavailable", "panes": [],
                "kind": "read"}
    if result.returncode != 0:
        return {"id": "sense.mind.watch-wait", "state": "unknown",
                "sample": "tmux pane enumeration failed", "panes": [],
                "kind": "read"}
    records = proc_table.pane_records(result.stdout)
    if not records:
        return {"id": "sense.mind.watch-wait", "state": "unknown",
                "sample": "no panes enumerated", "panes": [], "kind": "read"}
    firsts: dict[tuple[str, str, int], str | None] = {}
    for session, window, pid in records:
        firsts[(session, window, pid)] = _capture_mind_frame(session, window)
    time.sleep(WATCH_WAIT_SAMPLE_GAP_SECONDS)
    panes: list[dict] = []
    readable = 0
    waiting = 0
    for session, window, pid in records:
        first = firsts[(session, window, pid)]
        second = _capture_mind_frame(session, window)
        if first is None or second is None:
            panes.append({"window": window, "pid": pid, "session": session,
                          "verdict": "unknown",
                          "detail": "pane capture unreadable"})
            continue
        readable += 1
        verdict = _watch_wait_verdict(first, second, now)
        if verdict["verdict"] == "watch-wait":
            waiting += 1
        panes.append({"window": window, "pid": pid, "session": session,
                      **verdict})
    if waiting:
        sample = ("watch-wait=" + " ".join(
            f"{pane['window']}(pid={pane['pid']},session={pane['session']},"
            f"pattern={pane.get('pattern')},run={pane.get('run_id')})"
            for pane in panes if pane["verdict"] == "watch-wait"))
    else:
        sample = "watch-wait=0"
    sample += f" panes={len(records)} readable={readable}"
    state = "verified" if readable else "unknown"
    return {"id": "sense.mind.watch-wait", "state": state,
            "sample": sample, "panes": panes, "kind": "read",
            "consumer": "witness pane rendering",
            "validity": {"sampled": now, "gap_seconds": WATCH_WAIT_SAMPLE_GAP_SECONDS}}


TREADMILL_WINDOW_COMMITS = 60
"""Newest commits each role's stagnation/change fold reads.

Wide enough to hold five same-shape wakes for the slowest committer on this
host (inference-research lands roughly one record commit per wake, and the
paired fold needs five to name a chain), narrow enough that one bounded
``git log --name-only`` stays inside the scan's subprocess budget. A role
that cannot fill five commits stays UNKNOWN rather than reading clean.
"""

TREADMILL_MIN_WAKES = 5
"""Consecutive same-shape wakes that name a treadmill.

Five matches the DM spec's N: fewer would fire on one quiet week, more would
outlast the 30-commit positive control's usefulness. Stagnation must reach
this count while change stays zero.
"""

_TREADMILL_RECORD_FILE = "research/inference-continuity/progress.md"
"""The owned record file a reconciliation record commit stands on.

A record commit's subject names the role, wake and prior HEAD it reconciles
in one of two shapes: ``<role> wake <N>: record <sha> CI success`` (older)
or ``<role> wake <N>: CI <run> concluded <success|failure>...`` (current:
the same self-referential reconciliation — close the previous wake's CI
watch, which manufactures the next wake's identical watch). Either shape's
only purpose is to close the previous obligation with no implementation
movement. The file + marker comparison below classifies both
self-referential shapes; no other subject shape counts as record-only.
"""


def _treadmill_class(files: list[str], subject: str) -> str:
    """Name one commit's change class from decoded git values.

    The inputs are already decoded: ``files`` is git's name-only list from
    the ``proc_table`` boundary adapter, the subject is the ``%s`` line.
    A commit touching only the role's own record file whose subject carries
    a reconciliation marker reads ``record-only``; a commit touching any
    path outside ``research/`` reads ``source-change``; everything else
    still counts it as movement without overstating it as implementation.
    The comparisons are substring containment and equality only — no text decoding here.
    """
    lowered = subject.lower()
    hit = ": record " in lowered or (": ci " in lowered and " concluded " in lowered)
    if len(files) == 1 and files[0] == _TREADMILL_RECORD_FILE and hit:
        return "record-only"
    if any(name[:9] != "research/" for name in files):
        return "source-change"
    return "other-change"


def _treadmill_fold(commits: list[dict]) -> dict:
    """Fold one role's newest-first commit stream into stagnation + change.

    Pure: no subprocess, no clock, no text parsing. Each entry carries
    ``files`` (decoded name-only list) and ``subject`` (decoded ``%s``).
    The stagnation accumulator counts the leading run of ``record-only``
    commits; the change accumulator counts the leading run's complement:
    any ``source-change``/``other-change`` entry inside the window. The
    fold reads only the leading run so a stale record tail cannot mask a
    recent implementation wake, and an implementation wake cannot be
    averaged away by older record commits.
    """
    stagnation = 0
    for entry in commits:
        if _treadmill_class(entry.get("files", []), entry.get("subject", "")) == "record-only":
            stagnation += 1
        else:
            break
    window = commits[:TREADMILL_MIN_WAKES]
    change = sum(1 for entry in window
                 if _treadmill_class(entry.get("files", []), entry.get("subject", "")) != "record-only")
    return {"stagnation": stagnation, "change": change}


def _treadmill_commits(checkout: Path, role: str, limit: int = TREADMILL_WINDOW_COMMITS,
                       since: datetime | None = None) -> list[dict] | None:
    """Newest-first ``{subject, files}`` commits for one role, or None.

    Role owns the commits its wake message names: the subject carries
    ``<role> wake``. The log dialect is decoded once in the ``proc_table``
    boundary adapter; this function only slices that decoded stream to the
    role and bounds the subprocess read. ``None`` means the stream is
    unreadable (no git, a non-work-tree checkout, a git failure): the
    caller holds UNKNOWN rather than reading an unreadable stream as clean.

    ``since`` bounds the read to commits at or after that instant, so a
    caller can ask whether progress happened inside a specific interval
    (a role's recent wake window) instead of anywhere in the ``limit``
    window. ``since=None`` keeps the whole window.
    """
    command = ["git", "log", "--format=SUBJECT:%s", "--name-only", f"-{limit}"]
    if since is not None:
        command.append(f"--since={since.isoformat()}")
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, cwd=checkout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    entries = proc_table.git_log_entries(result.stdout)
    return [entry for entry in entries if role in entry["subject"]]

def _treadmill_handoff_digest(home: Path, role: str) -> str | None:
    """Short digest of a role's live handoff bytes, or None when absent.

    Reads ``handoffs/<role>.md`` exactly as settled: the wall and the
    handoff carry the same settled text, so one digest covers both. No
    content parsing — human prose is opaque; byte equality is the only
    claim. A missing file is absence, not stagnation.
    """
    import hashlib

    try:
        raw = (Path(home) / "handoffs" / f"{role}.md").read_bytes()
    except OSError:
        return None
    return hashlib.sha256(raw).hexdigest()[:12]


def _treadmill_previous_digests(home: Path) -> dict[str, str]:
    """Handoff digests from the previous scan's treadmill reading, if any.

    The previous receipt is the only retained cross-scan memory: the commit
    stream shows what landed in git, the prior reading shows what the last
    scan saw live. A malformed or missing receipt means no comparison, not
    a clean bill — callers report ``unknown`` for that leg.
    """
    previous = latest(home)
    if previous is None:
        return {}
    digests: dict[str, str] = {}
    for item in previous.get("observations", []):
        if isinstance(item, dict) and item.get("id") == "sense.mind.treadmill":
            roles = item.get("roles")
            if isinstance(roles, dict):
                for role, pair in roles.items():
                    if isinstance(pair, dict) and isinstance(pair.get("handoff"), str):
                        digests[str(role)] = pair["handoff"]
    return digests


def _mind_role_coverage(home: Path, role: str, now: datetime,
                        entries: list | None) -> dict[str, object]:
    """Per-role temporal coverage: wall update, settled yield, idle, pending.

    The commit stream shows what landed; this shows whether the mind is
    still waking, settling and writing — the busy-idle vs wedged distinction
    the treadmill verdict alone cannot give (witness DM 52175, routed by
    body-research DM 52299). The wall mtime and the seed receipt state are
    read directly; an unreadable source yields None fields, never a guessed
    value.
    """
    try:
        wall_mtime = (home / "walls" / f"{role}.md").stat().st_mtime
        wall_updated = datetime.fromtimestamp(
            wall_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except OSError:
        wall_updated = None
    last_yield: int | None = None
    last_wake_at: datetime | None = None
    pending: int | None = None
    if entries is not None:
        try:
            from . import seed
            (_digest, pending, last_yield, _clear, _obs, _woken, last_wake_at,
             _cont) = seed._state(home, role, entries=entries)
        except (OSError, ValueError):
            pass
    idle_s = None
    if last_wake_at is not None:
        idle_s = max(0, int((now - last_wake_at).total_seconds()))
    return {"wall_updated": wall_updated, "last_yield": last_yield,
            "idle_s": idle_s, "pending": pending}


def _mind_treadmill(home: Path, now: datetime | None = None) -> dict:
    """Stagnation + change accumulators over each role's commit stream.

    Rule T (treadmill): a role whose leading run holds at least
    ``TREADMILL_MIN_WAKES`` consecutive ``record-only`` commits with zero
    change in that run reads ``watch-treadmill`` — the turn manufactures
    its successor's identical obligation with no implementation movement.
    Any change-class commit inside the run, or a run shorter than five,
    reads ``clean``. A role with fewer than one commit in the window, or a
    checkout git cannot read, stays UNKNOWN: absence of evidence is not a
    clean bill.

    Handoff leg: each role's pair also carries the live handoff digest and
    whether it moved since the previous scan (``changed``/``stagnant``/
    ``unknown`` when the prior receipt or the file is absent). A stagnant
    handoff beside a record-only run is corroboration — the wake settled
    byte-identical words. A changed handoff does NOT clear the verdict:
    handoff text renames the wake every turn, so gating on it would silence
    the detector while the commit stream still shows no movement.

    Coverage leg: each role's pair carries temporal coverage from the seed
    receipts and the wall file — ``wall_updated`` (wall mtime), ``last_yield``
    (last settled wake), ``idle_s`` (seconds since the last wake) and
    ``pending`` (open wake, when one is unsettled). The legs answer different
    questions: the commit fold names a manufacturing loop, the coverage
    fields name whether the mind is alive — busy mid-turn, settled and
    idle, or wedged. Coverage reads that cannot be made yield None fields;
    the verdict never turns on them.

    The sample names every role's ``stagnation``/``change`` pair; the
    verdict that matters to witness/health rendering is the worst one.
    Consumer is health's mishe-wide rollup (which roles treadmill this
    window); each role's own pair is the per-mind reading the DM asked
    to be universal. Validity is the sampled HEAD: a new commit
    invalidates the fold.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    checkout = Path(home).resolve().parent
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5, cwd=checkout)
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.mind.treadmill", "state": "unknown",
                "sample": "checkout HEAD unreadable", "roles": {}, "kind": "read"}
    if head.returncode != 0:
        return {"id": "sense.mind.treadmill", "state": "unknown",
                "sample": "not a git checkout", "roles": {}, "kind": "read"}
    sampled = head.stdout[:40]
    roles = ["inference-research", "body-research", "docs", "health", "senses",
             "discover", "genome", "witness", "research-methods", "operator-view"]
    previous = _treadmill_previous_digests(home)
    entries: list | None = None
    try:
        entries = Feed(home).entries()
    except (OSError, ValueError):
        entries = None
    pairs: dict[str, dict] = {}
    unreadable = 0
    worst = "clean"
    for role in roles:
        commits = _treadmill_commits(checkout, role)
        if commits is None:
            # An unreadable stream is inconclusive, not clean: keep the role in
            # the pairs with an unknown verdict so the rollup shows the hole
            # instead of dropping the role from the reading entirely.
            unreadable += 1
            if worst == "clean":
                worst = "unknown"
            pairs[role] = {"stagnation": 0, "change": 0, "verdict": "unknown",
                           "detail": "commit stream unreadable",
                           "handoff": _treadmill_handoff_digest(home, role),
                           "handoff_moved": None,
                           **_mind_role_coverage(home, role, now, entries)}
            continue
        digest = _treadmill_handoff_digest(home, role)
        old = previous.get(role)
        handoff_moved: str | None = None
        if digest is not None and old is not None:
            handoff_moved = "changed" if digest != old else "stagnant"
        if not commits:
            pairs[role] = {"stagnation": 0, "change": 0, "verdict": "unknown",
                           "detail": "no commits in window",
                           "handoff": digest, "handoff_moved": handoff_moved,
                           **_mind_role_coverage(home, role, now, entries)}
            continue
        fold = _treadmill_fold(commits)
        verdict = ("watch-treadmill" if fold["stagnation"] >= TREADMILL_MIN_WAKES
                   and fold["change"] == 0 else "clean")
        if verdict == "watch-treadmill":
            worst = "watch-treadmill"
        pairs[role] = {**fold, "verdict": verdict,
                       "detail": f"leading record-only run over {len(commits)} commits",
                       "handoff": digest, "handoff_moved": handoff_moved,
                       **_mind_role_coverage(home, role, now, entries)}
    sample = "treadmill=" + worst + " " + " ".join(
        f"{role}(stag={pairs[role]['stagnation']},change={pairs[role]['change']})"
        for role in sorted(pairs))
    stagnant = sorted(role for role, pair in pairs.items()
                      if pair.get("handoff_moved") == "stagnant")
    if stagnant:
        sample += " handoff_stagnant=" + ",".join(stagnant)
    if unreadable:
        sample += f" unreadable={unreadable}"
    state = "verified" if pairs else "unknown"
    return {"id": "sense.mind.treadmill", "state": state, "sample": sample,
            "roles": pairs, "verdict": worst, "kind": "read",
            "consumer": "health mishe-wide treadmill rollup",
            "validity": {"sampled": sampled, "window_commits": TREADMILL_WINDOW_COMMITS,
                         "min_wakes": TREADMILL_MIN_WAKES}}


def _docs_treadmill_class(files: list[str]) -> str:
    """Name one commit's frontier class from git's name-only list.

    ``docs-refresh`` means every touched path sits under ``docs/``: the
    commit moves the book, not the implementation frontier (``src/``,
    ``tests/``). ``implementation`` names a commit that touches the
    frontier, whatever else it touches. Everything else (research notes,
    merge commits with no name-only files) reads ``other``. No subject
    markers: the demonstrated refresh pattern renews scan-derived status
    text under many subject shapes, and a path test generalizes without
    tying coverage to one literal phrasing.
    """
    if files and all(name[:5] == "docs/" for name in files):
        return "docs-refresh"
    if any(name[:4] == "src/" or name[:6] == "tests/" for name in files):
        return "implementation"
    return "other"


def _docs_treadmill_fold(commits: list[dict]) -> dict:
    """Fold one role's newest-first stream into refresh stagnation + change.

    Pure: no subprocess, no clock. Stagnation counts the leading run of
    ``docs-refresh`` commits; change counts the complement inside the
    ``TREADMILL_MIN_WAKES`` head window. Only the leading run is read, so
    an older refresh tail cannot mask a recent implementation wake.
    """
    stagnation = 0
    for entry in commits:
        if _docs_treadmill_class(entry.get("files", [])) == "docs-refresh":
            stagnation += 1
        else:
            break
    window = commits[:TREADMILL_MIN_WAKES]
    change = sum(1 for entry in window
                 if _docs_treadmill_class(entry.get("files", [])) != "docs-refresh")
    return {"stagnation": stagnation, "change": change}


def _mind_docs_treadmill(home: Path) -> dict:
    """Docs-book churn rollup over each role's commit stream.

    Rule TD (docs treadmill): a role whose leading run holds at least
    ``TREADMILL_MIN_WAKES`` consecutive docs-only commits with zero
    change in the head window reads ``watch-docs-treadmill`` — the turn
    renews scan-derived book text (sample IDs, cursors, status lines)
    with no implementation movement, the pattern the 2026-10-15
    operator audit demonstrated as ``sense.mind.treadmill``'s blind
    spot. Any frontier-touching commit in the run, or a run under five,
    reads ``clean``. No commits, or a checkout git cannot read, stays
    UNKNOWN: absence of evidence is not a clean bill.

    Coverage note: this detector ADDS the docs-book lane beside the
    narrow record-file lane; it does not replace it and schedules
    nothing — it only routes the pattern to the health rollup, which
    decides. Artifact-only churn cannot appear here because the plant
    gitignores ``.mishe-*``; such commits are not in the log.
    """
    checkout = Path(home).resolve().parent
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5, cwd=checkout)
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.mind.docs-treadmill", "state": "unknown",
                "sample": "checkout HEAD unreadable", "roles": {}, "kind": "read"}
    if head.returncode != 0:
        return {"id": "sense.mind.docs-treadmill", "state": "unknown",
                "sample": "not a git checkout", "roles": {}, "kind": "read"}
    sampled = head.stdout[:40]
    roles = ["inference-research", "body-research", "docs", "health", "senses",
             "discover", "genome", "witness", "research-methods", "operator-view"]
    pairs: dict[str, dict] = {}
    unreadable = 0
    worst = "clean"
    for role in roles:
        commits = _treadmill_commits(checkout, role)
        if commits is None:
            # Inconclusive, not clean: the role stays in the rollup with an
            # unknown verdict instead of vanishing from it.
            unreadable += 1
            if worst == "clean":
                worst = "unknown"
            pairs[role] = {"stagnation": 0, "change": 0, "verdict": "unknown",
                           "detail": "commit stream unreadable"}
            continue
        if not commits:
            pairs[role] = {"stagnation": 0, "change": 0, "verdict": "unknown",
                           "detail": "no commits in window"}
            continue
        fold = _docs_treadmill_fold(commits)
        verdict = ("watch-docs-treadmill" if fold["stagnation"] >= TREADMILL_MIN_WAKES
                   and fold["change"] == 0 else "clean")
        if verdict == "watch-docs-treadmill":
            worst = "watch-docs-treadmill"
        pairs[role] = {**fold, "verdict": verdict,
                       "detail": f"leading docs-only run over {len(commits)} commits"}
    sample = "docs-treadmill=" + worst + " " + " ".join(
        f"{role}(stag={pairs[role]['stagnation']},change={pairs[role]['change']})"
        for role in sorted(pairs))
    if unreadable:
        sample += f" unreadable={unreadable}"
    state = "verified" if pairs else "unknown"
    return {"id": "sense.mind.docs-treadmill", "state": state, "sample": sample,
            "roles": pairs, "verdict": worst, "kind": "read",
            "consumer": "health mishe-wide treadmill rollup",
            "validity": {"sampled": sampled, "window_commits": TREADMILL_WINDOW_COMMITS,
                         "min_wakes": TREADMILL_MIN_WAKES}}

ARTIFACT_RECURRENCE_MIN_WAKES = 3
ARTIFACT_RECURRENCE_WINDOW_WAKES = 5

def _entry_timestamp(entry: FeedEntry) -> datetime | None:
    """Decode a feed entry's ISO timestamp to an aware UTC datetime.

    ``None`` means the stamp is absent or unparseable; a caller that cannot
    bound its interval from the tape must not read that as clean.
    """
    return _feed_entry_timestamp(entry)

def _mind_artifact_recurrence(home: Path, now: datetime | None = None) -> dict:
    """Detect artifact-only unchanged-frontier recurrence.

    Rule AR (artifact recurrence): a role whose last
    ``ARTIFACT_RECURRENCE_MIN_WAKES`` consecutive wakes all use the same
    observation number, with no new commits inside that wake interval, reads
    ``watch-artifact-recurrence`` — the turn manufactures its successor's
    identical obligation with no input movement, the pattern the 2026-10-15
    operator audit demonstrated as ``sense.mind.treadmill``'s blind spot
    for non-commit churn.

    The tape is read oldest-first and the tail is the evidence: a role whose
    first wakes share an observation is history, not recurrence, and a role
    whose last three wakes share one is. The commit leg is bounded by the
    interval those wakes span — a commit before it is stale and cannot clear
    the verdict, a commit inside it is progress and does.

    Coverage note: this detector ADDS the wake-pattern lane beside the
    commit-stream lanes; it does not replace them. A role with new commits
    between wakes is ``clean`` (legitimate multi-turn progress). A role with
    different observation numbers is ``clean`` (observation changed). A role
    with no wake history is ``clean`` (no evidence of recurrence). Missing
    or unreadable feed data stays ``unknown``: absence of evidence is not a
    clean bill.

    Inconclusive legs: a same-observation role whose commit leg cannot be read
    (``_treadmill_commits`` returns ``None``) and one whose interval cannot be
    bounded (no wake stamp in the recurrence window) both keep the role in the
    reading with verdict ``unknown`` and ``resolver=<role>``, and the
    top-level verdict is ``unknown`` when no stronger verdict fires; the sample
    carries ``unreadable=<n>`` and ``unboundable=<n>``. Dropping such a role
    read as ``clean``, and falling back to an unbounded commit read let a
    commit predating every wake clear the role (research-methods DMs
    57036/57055). The sensor ``state`` still reports whether the reading was
    made; the verdict carries the uncertainty, as it already did for a role
    with no commits in the window.
    Routing: the output carries a ``resolver`` field naming the role whose
    continuation policy should be reviewed — the detector does not schedule
    or suppress anything.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    checkout = Path(home).resolve().parent
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5, cwd=checkout)
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.mind.artifact-recurrence", "state": "unknown",
                "sample": "checkout HEAD unreadable", "roles": {}, "kind": "read"}
    if head.returncode != 0:
        return {"id": "sense.mind.artifact-recurrence", "state": "unknown",
                "sample": "not a git checkout", "roles": {}, "kind": "read"}
    sampled = head.stdout[:40]
    roles = ["inference-research", "body-research", "docs", "health", "senses",
             "discover", "genome", "witness", "research-methods", "operator-view"]
    entries: list | None = None
    try:
        entries = Feed(home).entries()
    except (OSError, ValueError):
        entries = None
    if entries is None:
        return {"id": "sense.mind.artifact-recurrence", "state": "unknown",
                "sample": "feed unreadable", "roles": {}, "kind": "read"}
    # Collect wake events per role in tape order (oldest first); the
    # detector's evidence is the tail of that history.
    wake_history: dict[str, list[tuple[int, datetime | None]]] = {role: [] for role in roles}
    for entry in entries:
        if entry.source != "seed":
            continue
        try:
            control = decode_lifecycle(entry)
        except ProtocolError:
            continue
        if control is None or control.kind != "wake":
            continue
        role = control.role
        if role in wake_history and control.observation is not None:
            wake_history[role].append((control.observation, _entry_timestamp(entry)))
    pairs: dict[str, dict] = {}
    unreadable = 0
    unboundable = 0
    worst = "clean"
    worst_role: str | None = None
    for role in roles:
        history = wake_history[role]
        observations = [observation for observation, _stamp in history]
        if not observations:
            pairs[role] = {"wakes": 0, "unique_observations": 0,
                           "verdict": "clean", "detail": "no wake history",
                           "resolver": None}
            continue
        # The most recent wakes are the evidence: an identical old run is
        # history, not recurrence.
        recent = observations[-ARTIFACT_RECURRENCE_MIN_WAKES:]
        same_observation = (len(recent) >= ARTIFACT_RECURRENCE_MIN_WAKES
                            and len(set(recent)) == 1)
        window_start: datetime | None = None
        commits: list[dict] = []
        if same_observation:
            stamps = [stamp for _observation, stamp
                      in history[-ARTIFACT_RECURRENCE_MIN_WAKES:]]
            window_start = next((stamp for stamp in stamps if stamp is not None), None)
            if window_start is None:
                # No stamp in the recurrence window bounds the interval, so the
                # commit leg cannot be read inside it. Reading the whole window
                # instead would let a commit predating every wake clear the role
                # (research-methods DM 57036, path B). Inconclusive, not clean.
                unboundable += 1
                if worst == "clean":
                    worst, worst_role = "unknown", role
                pairs[role] = {"wakes": len(observations),
                               "unique_observations": len(set(
                                   observations[-ARTIFACT_RECURRENCE_WINDOW_WAKES:])),
                               "verdict": "unknown",
                               "detail": "wake interval unboundable: no wake stamp",
                               "resolver": role}
                continue
            commits = _treadmill_commits(checkout, role, since=window_start)
            if commits is None:
                # An unreadable commit leg is inconclusive evidence: the role
                # keeps an unknown verdict and names itself as the resolver, so
                # a git read failure cannot read as a clean bill.
                unreadable += 1
                if worst == "clean":
                    worst, worst_role = "unknown", role
                pairs[role] = {"wakes": len(observations),
                               "unique_observations": len(set(
                                   observations[-ARTIFACT_RECURRENCE_WINDOW_WAKES:])),
                               "verdict": "unknown",
                               "detail": "commit stream unreadable",
                               "resolver": role}
                continue
        has_new_commits = len(commits) > 0
        if same_observation and not has_new_commits:
            verdict = "watch-artifact-recurrence"
            worst = "watch-artifact-recurrence"
            worst_role = role
            detail = (f"last {len(recent)} wakes share observation "
                      f"{recent[0]} with no new commits since their interval")
            resolver = role
        elif same_observation and has_new_commits:
            verdict = "clean"
            detail = (f"last {len(recent)} wakes share observation "
                      f"{recent[0]} but {len(commits)} new commits")
            resolver = None
        else:
            verdict = "clean"
            recent_window = observations[-ARTIFACT_RECURRENCE_WINDOW_WAKES:]
            unique = len(set(recent_window))
            detail = (f"observations vary ({unique} unique in last "
                      f"{len(recent_window)} wakes)")
            resolver = None
        pairs[role] = {"wakes": len(observations),
                       "unique_observations": len(set(
                           observations[-ARTIFACT_RECURRENCE_WINDOW_WAKES:])),
                       "verdict": verdict, "detail": detail,
                       "resolver": resolver}
    sample = "artifact-recurrence=" + worst + " " + " ".join(
        f"{role}(wakes={pairs[role]['wakes']},uniq={pairs[role]['unique_observations']})"
        for role in sorted(pairs))
    if unreadable:
        sample += f" unreadable={unreadable}"
    if unboundable:
        sample += f" unboundable={unboundable}"
    state = "verified" if pairs else "unknown"
    return {"id": "sense.mind.artifact-recurrence", "state": state,
            "sample": sample, "roles": pairs, "verdict": worst, "kind": "read",
            "consumer": "health mishe-wide treadmill rollup",
            "resolver": worst_role,
            "validity": {"sampled": sampled,
                         "min_wakes": ARTIFACT_RECURRENCE_MIN_WAKES}}
LOOP_STALL_GATE_HOURS = 6.0

"""Hours an open resolver gate may age before a treadmilling role reads stalled.

Inference waited ~24h (pause 12:11Z to next-day cutoff); six hours catches the
same shape early without firing on one quiet afternoon. The gate age comes
from the oldest open DM to another role (the witness/senses legs); a role
with no open outbound DM is pausing on its own, not stalled on a gate.
"""

def _mind_loop_stall(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Flag any role stuck in a no-decision-change loop behind an aging gate.

    Generalizes inference-research DM 20438 (self-flagged 73/80 record-only,
    5,0 stagnation, 24h gate-wait on senses + witness legs): the mind sensed
    its own loop manually and waited a day for legs. This producer fires on
    the same shape for any role without anyone self-flagging: a treadmill
    verdict (record-only lane, or the docs-book lane) AND an open outbound DM
    older than the gate bound. Either half alone is not a stall (steady-state
    record runs exist; young gates resolve). State is drift when a role
    stalls, verified when no role pairs both halves, unknown when both lanes
    and the tape are unreadable. The sample names stalled roles with gate age
    so the wake routes to the gate owner, not the stalled mind.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    treadmill = _mind_treadmill(home)
    docs = _mind_docs_treadmill(home)
    if treadmill.get("state") != "verified" and docs.get("state") != "verified":
        return {"id": "sense.mind.loop-stall", "state": "unknown",
                "sample": "treadmill reading unavailable", "kind": "read"}
    # A no-decision-change lane is a stall when it is stuck behind an aging
    # resolver gate. Both lanes share the gate read: the record lane and the
    # docs-book lane are independent detectors of the same shape, so either
    # verdict names the treadmilling role. The docs lane otherwise fires with
    # no consumer — emitted but never routed — so the demonstrated churn pattern
    # would stay invisible to the resolver.
    treadmilling = sorted(
        role for reading in (treadmill, docs)
        if reading.get("state") == "verified"
        for role, pair in reading.get("roles", {}).items()
        if isinstance(pair, dict) and pair.get("verdict") in _WATCH_VERDICTS)
    if not treadmilling:
        return {"id": "sense.mind.loop-stall", "state": "verified",
                "sample": "stall=0 treadmilling=none", "kind": "read",
                "stalled": []}
    # Gate ages come from the disposition reader, not a second tape parse:
    # _coord_dm_disposition_age already pairs each open DM with its target
    # and age-gates stalls, so no new parse sites are needed here.
    disp = _coord_dm_disposition_age(home, now)
    if disp.get("state") != "verified":
        return {"id": "sense.mind.loop-stall", "state": "unknown",
                "sample": "disposition reading unavailable", "kind": "read"}
    oldest = str(disp.get("oldest_open", ""))
    stall_n = int(disp.get("stall", 0))
    # oldest_open names target:age; the sample pairs treadmilling roles with
    # the tape-wide gate head. Per-role outbound attribution would need a
    # second tape parse; until then a treadmilling role beside any mature
    # gate reads drift with the gate head named, so the resolver is visible
    # even though the pairing is tape-wide, not per-role.
    stalled = sorted(f"{role}@{oldest}" for role in treadmilling) if stall_n else []
    state = "drift" if stalled else "verified"
    return {"id": "sense.mind.loop-stall", "state": state,
            "sample": f"stall={len(stalled)} " + ",".join(stalled) if stalled else "stall=0 gates-young",
            "kind": "read", "stalled": stalled,
            "treadmilling": treadmilling}

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

def _unit_description(unit: str) -> str:
    """The unit's Description property, or empty when it cannot be read."""
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit, "-p", "Description"],
                                capture_output=True, text=True, timeout=5, env=environment)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode:
        return ""
    for row in result.stdout.splitlines():
        if row.startswith("Description="):
            return row[len("Description="):]
    return ""


def _unit_declared_paths(unit: str) -> list[str] | None:
    """Absolute paths a unit's own declaration names, or None if unreadable.

    ``systemctl show`` reports the *effective* unit, so a drop-in overriding the
    base unit cannot hide a path. The rendered property text is decoded by the
    :mod:`mishe_tauftauf.unit_properties` boundary; a unit whose properties
    cannot be read is unjudgeable, which is different from a unit that declares
    no site path.
    """
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit,
                                 "-p", "ExecStart,WorkingDirectory,Environment"],
                                capture_output=True, text=True, timeout=5,
                                env=environment)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    from .unit_properties import declared_paths
    return declared_paths(result.stdout)


def _is_tmux_session_holder(unit: str) -> bool:
    """Whether a unit's ExecStart is a tmux session holder.

    Tmux session holders run ``tmux new-session ... sh && exec sleep infinity``,
    not Python services. They have no import root and cannot drift against a
    pin; classifying them as host-scope reduces uninspectable noise.
    """
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit,
                                 "-p", "ExecStart"],
                                capture_output=True, text=True, timeout=5,
                                env=environment)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode:
        return False
    return "tmux new-session" in result.stdout

def _declares_site_path(path: str, site_roots: list[Path]) -> bool:
    """Whether one declared path lies inside a site's checkout or its home."""
    candidate = Path(path)
    return any(root == candidate or root in candidate.parents
               for root in site_roots)

SITE_PACKAGE = "mishe_tauftauf"
"""The Python package this plant's checkout installs.

The drift sense asks whether a unit couples to *this* plant's package, so the
name is a property of the plant, not of the directory its checkout happens to
live in: deriving it from the checkout's directory name would silently stop
detecting the coupling for a checkout named anything else.
"""

def _script_imports_site_package(unit: str) -> bool | None:
    """Whether the script a unit's ExecStart executes imports the site package.

    ``None`` when no executed script can be read or parsed, so the sensor
    cannot say whether the unit couples to the site: the unit then stays
    uninspectable. A script that imports no site package is a host tool
    beside the sites even when its file lives inside a site home, because
    the drift this sense catches runs through imports, not file location.
    """
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit,
                                 "-p", "ExecStart"],
                                capture_output=True, text=True, timeout=5,
                                env=environment)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    from .unit_properties import declared_paths
    checked = False
    for path in declared_paths(result.stdout):
        candidate = Path(path)
        if not candidate.is_file():
            continue
        try:
            raw = candidate.read_bytes()
        except OSError:
            continue
        if candidate.suffix != ".py" and not (
                raw[:2] == b"#!" and b"python" in raw[:128]):
            continue
        checked = True
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    # A relative import resolves inside a package; the only
                    # packages in play are the site's.
                    return True
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == SITE_PACKAGE or name[:len(SITE_PACKAGE) + 1] == SITE_PACKAGE + ".":
                    return True
    return False if checked else None
def _spawns_site_package(tree: ast.AST, package: str) -> bool:
    """Whether a parsed script runs ``-m <package>`` as a command argument.

    A wrapper couples to the site the same way an import does when it spawns
    ``python -m <package>`` as a child: the child carries the wrapper's
    PYTHONPATH, so the spawn is the coupling itself rather than a textual
    mention of the checkout.

    The command dialect is decoded in the spawn dialect boundary
    (:mod:`mishe_tauftauf.spawn_dialect`); this is the name the rest of
    discovery uses.
    """
    return spawns_site_package(tree, package)


def _script_spawns_site_package(unit: str, package: str) -> bool | None:
    """Whether the script a unit's ExecStart executes spawns the site package.

    ``None`` when no executed script can be read or parsed, so the sensor
    cannot say whether the unit couples through a spawned child. A readable
    script with no such spawn is ``False``: the unit runs whatever it runs
    without consuming the site package as a module.

    The wrapper's text and Python's command syntax are decoded in the spawn
    dialect boundary (:mod:`mishe_tauftauf.spawn_dialect`).
    """
    return script_spawns_site_package(unit, package)

def _foreign_checkout_consumers(units: list[str], root: Path) -> dict[str, str]:
    """Unattributed units whose ExecStart names this plant's checkout, by unit.

    A unit whose description names the checkout root is a plant-level service
    of this site, not a foreign consumer: the description is systemd's own
    attribution, so it outranks the script-text heuristic. A wrapper that
    spawns ``-m <package>`` is the other way around: the spawn is the coupling
    itself, so the unit is reported whatever its description says.
    """
    package = SITE_PACKAGE
    consumers: dict[str, str] = {}
    for unit in units:
        spawns = _script_spawns_site_package(unit, package)
        description = _unit_description(unit)
        if description and root.name in description and spawns is not True:
            continue
        if spawns is True or _unit_names_checkout(unit, root):
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
    anonymous coverage gap. A unit that names no site in anything it imports or
    declares — or whose executed script imports no site package at all — is a
    host tool beside the sites, so the sample names it
    ``host-scope=<unit>`` and the reading is ``verified``: the question the
    sense answers is settled for it, and an unattributed UNKNOWN is left for
    the units whose declaration could not be read or whose declared paths do
    name a site. The release coordinator's declared checkout root
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
    package = SITE_PACKAGE
    session_of = {unit: next((prefix for prefix in site_by_prefix
                              if unit.startswith(prefix + "-")), None)
                  for unit in active}
    # Same-site services that don't match any session prefix (e.g.
    # mishe-ifm-shim.service) are mapped to the scanning site's session when
    # they declare a path inside the scanning site's home: they run from the
    # same checkout and must be compared against the same pin.
    if own_session and own_session in site_by_prefix:
        own_home = Path(site_by_prefix[own_session])
        for unit, session in session_of.items():
            if session is None and is_site_unit(unit):
                declared = _unit_declared_paths(unit)
                if declared and any(
                        own_home in Path(path).resolve().parents
                        for path in declared):
                    session_of[unit] = own_session
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
    host_scope: list[str] = []
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
    # A unit no session names can still be judged. When nothing it imports and
    # nothing its declaration names lies inside any site's checkout or home,
    # the unit is a host tool beside the sites: naming it `host-scope` answers
    # the reading's question instead of leaving an UNKNOWN readers learn to
    # skip. A unit that declares a path inside a site, or whose declaration
    # cannot be read, stays an anonymous gap — the sensor cannot yet say the
    # unit is irrelevant, so it does not claim it.
    site_roots = [Path(site_home) for site_home in site_by_prefix.values()]
    site_roots += [Path(site_home).resolve().parent
                   for site_home in site_by_prefix.values()]
    for unit in list(unattributed):
        declared = _unit_declared_paths(unit)
        if not declared:
            continue
        if any(_declares_site_path(path, site_roots) for path in declared):
            continue
        host_scope.append(unit)
        unattributed.remove(unit)
    # A mapped unit with no import root can still be judged by its declaration:
    # when nothing its declaration names lies inside any site's checkout or
    # home, the unit is a host tool beside the sites. A unit whose executed
    # script imports no site package is a host tool by the same rule: the
    # drift this sense catches runs through imports, not file location. A unit
    # that declares a path inside a site stays uninspectable — the sensor
    # cannot yet say the unit is irrelevant, so it does not claim it.
    for unit in list(uninspectable):
        if _is_tmux_session_holder(unit):
            host_scope.append(unit)
            uninspectable.remove(unit)
            continue
        if _script_imports_site_package(unit) is False and (
                _script_spawns_site_package(unit, package) is not True):
            host_scope.append(unit)
            uninspectable.remove(unit)
            continue
        declared = _unit_declared_paths(unit)
        if not declared:
            continue
        if any(_declares_site_path(path, site_roots) for path in declared):
            continue
        host_scope.append(unit)
        uninspectable.remove(unit)

    # The pane shortens a long sample at its tail (`seed_culture_views._clip_sample`),
    # so the fields that decide this reading's state lead and the informational
    # host-scope list trails them: a clipped `stale=` or `foreign=` would render as a
    # reading the reader cannot explain.
    if stale:
        parts.append("stale=" + ",".join(f"{unit}@{Path(root).name}" for unit, root in sorted(stale.items())))
    if foreign:
        parts.append("foreign=" + ",".join(
            f"{unit}@{checkout_root.name}" for unit in sorted(foreign)))
    if unattributed:
        parts.append("unattributed=" + ",".join(unattributed))
    if uninspectable:
        parts.append("uninspectable=" + ",".join(uninspectable))
    if unpinned:
        parts.append("unpinned=" + ",".join(unpinned))
    if host_scope:
        parts.append("host-scope=" + ",".join(sorted(host_scope)))
    state = "drift" if stale else "unknown" if unpinned or unattributed or uninspectable or foreign else "verified"
    return {"id": "sense.runtime.drift-across-sites", "state": state,
            "sample": " ".join(parts), "kind": "read",
            "identity": {"sites": sorted(site_by_prefix), "stale": stale,
                         "unpinned": unpinned, "unattributed": unattributed,
                         "foreign": foreign, "host_scope": host_scope,
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


def _pane_info(home: Path) -> dict[str, tuple[str, str, str]]:
    """Map each pane's role name to its (start_command, current_path, pid).

    Reads ``tmux list-panes -a`` once; returns empty when tmux is unavailable
    or no session is running. The role name is the token after ``pain watch``
    in the pane's start command, matching the Top Pain script name. The pid is
    the pane's watcher process, whose environment the renderer inherits.

    Two sites can share a host, and then one role name owns two panes, one per
    session. Taking whichever pane tmux lists last resolves the role from the
    other site's watcher, so the reading would name bytes this site never ran.
    A session the reading home owns decides the role; a role whose panes all
    live in sessions another home owns stays unresolved, so the caller reports
    that renderer's root unread rather than guessing it. A lone pane in a
    session no home has claimed has no competing candidate, so it stays this
    site's only watcher for the role.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}|#{pane_pid}|#{pane_start_command}|#{pane_current_path}"],
            capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    from .tmux import session_owner
    candidates: dict[str, list[tuple[str, tuple[str, str, str]]]] = {}
    for session, pid, start_command, current_path in proc_table.pane_commands(
            result.stdout):
        match = PANE_ROLE.search(start_command)
        if match:
            candidates.setdefault(match.group(1), []).append(
                (session, (start_command, current_path, pid)))
    wanted = str(Path(home).resolve())
    owners: dict[str, str | None] = {}

    def owner(session: str) -> str | None:
        """The home a session's owner option names, read once per session."""
        if session not in owners:
            owners[session] = session_owner(session)
        return owners[session]

    info: dict[str, tuple[str, str, str]] = {}
    for role, panes in candidates.items():
        owned = [pane for session, pane in panes if owner(session) == wanted]
        if owned:
            # Several panes of this home may watch one role, as a respawn does;
            # the last one listed is the live pane, as it was before the filter.
            info[role] = owned[-1]
        elif len(panes) == 1 and owner(panes[0][0]) is None:
            info[role] = panes[0][1]
        # Several sites watch this role, or a recorded owner names another
        # home: no reading here can say which pane renders this site.
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
    live environment, falling back to the pane's start command, and only from a
    pane in a session the reading home owns: two sites can share a host, and a
    watcher belonging to the other one would otherwise supply this site's root.
    Each renderer's
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
    pane_info = _pane_info(home)
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
                for component in (*proc_table.read_pane_pythonpath(pane[2]), *_command_pythonpath(pane[0])):
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
        pane = pane_info.get(role)
        if (home / "mishe_tauftauf").is_dir():
            effective_root = str(home)
        elif pane is not None:
            for component in (*proc_table.read_pane_pythonpath(pane[2]), *_command_pythonpath(pane[0])):
                candidate = _import_root(component)
                if candidate:
                    effective_root = candidate
                    break
        if effective_root is None:
            # No pane runs this renderer, so the bytes its script imports are
            # unread rather than the pin's. A renderer with no pane is not
            # executing, so there is nothing to verify.
            if pane is None:
                continue
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



_SERVED_STALENESS_LIMIT = 64
"""Sense ids retained per staleness reading; the rest are counted, not listed."""

_SENSE_EMITTERS = ("discovery.py", "body_perception.py")
"""Modules that own ``sense.*`` id literals; all are scanned for staleness."""



_HEAD_REWIND_REFLOG_LIMIT = 200
"""Reflog entries read per scan: the window the dropped-commit count covers.

The count is a count *inside this window*, so the sample names the window: a
40-entry window covered about a week of this checkout's history and reported
one dropped commit where a 70-entry read showed four rewind events, which
read as "one rewind ever happened". The window bounds the work; it is not the
whole history, and the sample says which bound it used.
"""

_HEAD_REWIND_COMMIT_LIMIT = 4
"""Dropped commits adjudicated per scan.

Every dropped commit costs a patch check per changed path, so the number of
adjudicated commits is bounded while the number of dropped commits in the
window is not. The sample names both, because the path verdicts cover only
the adjudicated commits.
"""

_HEAD_REWIND_PATH_LIMIT = 12

_DISK_TREND_FLOOR_BYTES = 10 * 2**30
_DISK_TREND_NEAR_BYTES = 20 * 2**30
_DISK_TREND_FAST_DRAIN_PER_DAY = 10 * 2**30
_DISK_TREND_HORIZON_DAYS = 7
_DISK_TREND_MIN_SPAN_HOURS = 6

_DISK_TREND_SLOPE_WINDOWS_DAYS = (2, 5, 7, 14)
"""Windows whose drain rate is compared before a projection is published.

A projection built from one window is a statement about that window. These
windows disagree often -- a disk drained hard last week and filling since is
ordinary -- and a countdown taken from the disagreeing one sends a reader to
prune the wrong thing. The window a figure belongs to is named in the sample,
and the signs of all of them are published, so a reader can see the
disagreement instead of inheriting one window's number.
"""


def _head_rewinds(home: Path) -> dict[str, object]:
    """Classify reflog-dropped commits' changed paths against HEAD's tree.

    A reset in the shared checkout drops the commits that were HEAD before it.
    When those bytes were re-committed the work survives and the drop is
    lossless; when they were not, the work is gone and a mind must notice.
    The discriminator is per path: ``git apply --check`` of the dropped
    commit's own hunk against HEAD's tree. Reverse-apply succeeds when the
    change is already present (lossless for that path), forward-apply when it
    is absent (a real loss candidate), and neither when the path was
    re-committed in a modified form. That third bucket is reported as its own
    verdict — it needs a mind's judgment, not a byte-level claim — and is
    never folded into "lossless".

    HEAD's tree is extracted to a temporary directory before any apply, so a
    peer's uncommitted edit in the shared checkout cannot make a lost change
    read as present. A git failure reads UNKNOWN rather than "nothing
    dropped": an unreadable reflog is not evidence that nothing was lost.

    The dropped-commit count covers a bounded reflog window and the sample
    names that window, because the count is a count *inside* it: a reader who
    saw "dropped=1" could not tell one rewind from a narrow window that
    stopped early. Only a bounded number of dropped commits are adjudicated
    per scan -- each costs a patch check per changed path -- so the sample
    names that count too, because the path verdicts cover only the
    adjudicated commits. The two units are named apart in the sample: the
    first counts commits, the rest count changed paths.
    """
    checkout_root = Path(home).resolve().parent
    unknown = {"id": "sense.repo.head-rewinds", "state": "unknown", "kind": "read"}

    def run(*args: str, timeout: float = 10, text: bool = True) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=text,
                                  timeout=timeout, cwd=checkout_root)
        except (OSError, subprocess.TimeoutExpired):
            return None

    reflog = run("reflog", "--format=%H %gs", "-n", str(_HEAD_REWIND_REFLOG_LIMIT))
    if reflog is None or reflog.returncode != 0:
        return {**unknown, "sample": "reflog unreadable"}
    # Reachability is read once for the whole window instead of one
    # `merge-base --is-ancestor` per reflog entry: the per-entry call costs
    # a git process each, which at a 200-entry window is the dominant cost of
    # this reading. It also made a git failure read as "not an ancestor",
    # which counted a live commit as dropped; an unreadable ancestry now
    # leaves the whole reading UNKNOWN instead.
    ancestry = run("rev-list", "HEAD")
    if ancestry is None or ancestry.returncode != 0:
        return {**unknown, "sample": "HEAD ancestry unreadable"}
    reachable = proc_table.git_reachable_shas(ancestry.stdout)
    dropped: list[str] = []
    seen: set[str] = set()
    for sha, _message in proc_table.git_reflog_entries(reflog.stdout):
        if sha in seen:
            continue
        seen.add(sha)
        if sha in reachable:
            continue
        dropped.append(sha)
    if not dropped:
        return {"id": "sense.repo.head-rewinds", "state": "verified",
                "sample": (f"dropped-commits=0 of last-{_HEAD_REWIND_REFLOG_LIMIT}"
                           "-reflog-entries classified-commits=0"),
                "dropped": 0, "classified_commits": 0, "lossless": 0,
                "needs_review": 0, "absent": 0,
                "reflog_window": _HEAD_REWIND_REFLOG_LIMIT, "kind": "read"}
    archive = run("archive", "HEAD", timeout=30, text=False)
    if archive is None or archive.returncode != 0:
        return {**unknown, "sample": "HEAD tree unreadable"}
    lossless = review = absent = 0
    try:
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:*") as tar:
                tar.extractall(Path(td), filter="data")
            tree = Path(td)
            for sha in dropped[:_HEAD_REWIND_COMMIT_LIMIT]:
                names = run("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
                if names is None or names.returncode != 0:
                    return {**unknown, "sample": f"changed paths unreadable: {sha[:7]}"}
                for path in proc_table.git_path_names(names.stdout)[:_HEAD_REWIND_PATH_LIMIT]:
                    patch = run("show", sha, "--", path)
                    if patch is None or patch.returncode != 0:
                        return {**unknown, "sample": f"patch unreadable: {sha[:7]}"}
                    if _applies(tree, patch.stdout, reverse=True):
                        lossless += 1
                    elif _applies(tree, patch.stdout, reverse=False):
                        absent += 1
                    else:
                        review += 1
    except (OSError, subprocess.TimeoutExpired):
        return {**unknown, "sample": "HEAD tree unreadable"}
    state = "drift" if (review or absent) else "verified"
    classified = min(len(dropped), _HEAD_REWIND_COMMIT_LIMIT)
    return {"id": "sense.repo.head-rewinds", "state": state,
            "sample": (f"dropped-commits={len(dropped)} of "
                       f"last-{_HEAD_REWIND_REFLOG_LIMIT}-reflog-entries "
                       f"classified-commits={classified} "
                       f"lossless-paths={lossless} "
                       f"needs-review-paths={review} absent-paths={absent}"),
            "dropped": len(dropped), "classified_commits": classified,
            "reflog_window": _HEAD_REWIND_REFLOG_LIMIT,
            "lossless": lossless,
            "needs_review": review, "absent": absent, "kind": "read"}


def _applies(tree: Path, patch: str, *, reverse: bool) -> bool:
    """Whether ``patch`` applies to ``tree``; a failed check is False, not an error."""
    args = ["git", "apply", "--check"] + (["-R"] if reverse else []) + ["-"]
    try:
        result = subprocess.run(args, input=patch, capture_output=True, text=True,
                                timeout=10, cwd=tree)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0

def _disk_trend(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Derive a disk rate/floor verdict from the retained scan corpus.

    A level cannot distinguish "stable at 66 GiB" from "draining at 40 GiB/day,
    floor in 36 h". The retained discovery scan corpus already holds the series,
    so the rate is derivable with no new collector. UNKNOWN when the corpus has
    fewer than 2 samples or a span under 6 h.

    The rate is measured over several windows and the projection is published
    only when they agree on the direction of the trend. A countdown is a
    statement about the future built from one window's slope, and the windows
    disagree often enough that such a countdown sends a reader to prune the
    wrong thing; when they disagree the sample says ``floor=not-robust`` and
    publishes the signs instead, and the robust reason (a trailing-24h
    minimum below the near threshold) still carries the verdict on its own.
    The drain figure itself is a measurement over a named window and stays
    published, because a measured rate does not become untrue when another
    window disagrees with it.
    """
    current = datetime.now(timezone.utc) if now is None else now
    if now is not None and now.tzinfo is None:
        current = now.replace(tzinfo=timezone.utc)

    rows = read_scan_series(home, "sense.disk.free")
    if not rows:
        return {"id": "sense.disk.trend", "state": "unknown",
                "sample": "discovery corpus unavailable", "kind": "read"}

    if len(rows) < 2:
        return {"id": "sense.disk.trend", "state": "unknown",
                "sample": f"fewer than 2 verified disk.free samples (n={len(rows)})", "kind": "read"}

    span_h = (rows[-1][0] - rows[0][0]).total_seconds() / 3600.0
    if span_h < _DISK_TREND_MIN_SPAN_HOURS:
        return {"id": "sense.disk.trend", "state": "unknown",
                "sample": f"span {span_h:.1f}h under {_DISK_TREND_MIN_SPAN_HOURS}h", "kind": "read"}

    cutoff = current - timedelta(hours=24)
    recent = [b for t, b in rows if t >= cutoff]
    min24 = min(recent) if recent else rows[-1][1]

    # The drain rate is measured over several windows, because one window's
    # number is a statement about that window. Each window takes the newest
    # sample at or before its own cut-off as its anchor, so the windows share
    # the corpus and differ only in how far back they look.
    slopes: list[tuple[str, float]] = []
    for window in _DISK_TREND_SLOPE_WINDOWS_DAYS:
        older = [r for r in rows if r[0] <= current - timedelta(days=window)]
        if not older:
            continue
        anchor_at, anchor_bytes = older[-1]
        days = (rows[-1][0] - anchor_at).total_seconds() / 86400.0
        if days <= 0:
            continue
        slopes.append((f"{window}d", (anchor_bytes - rows[-1][1]) / days))
    if not slopes:
        # The corpus is shorter than the shortest window: the whole retained
        # span is the only window there is, and it is named by its own span
        # rather than by a window label it does not have.
        oldest_at, oldest_bytes = rows[0]
        days = (rows[-1][0] - oldest_at).total_seconds() / 86400.0
        if days > 0:
            slopes.append((f"span{days:.1f}d", (oldest_bytes - rows[-1][1]) / days))
    drain_window, drain = slopes[-1] if slopes else ("unknown", 0.0)
    for label, value in slopes:
        if label == "7d":
            drain_window, drain = label, value
            break

    def _sign(value: float) -> str:
        if value > 0:
            return "+"
        if value < 0:
            return "-"
        return "0"

    signs = [(label, _sign(value)) for label, value in slopes]
    # A projection is published only when the windows agree on the direction
    # of the trend. Disagreeing windows are the normal case for a disk that
    # was drained hard and has been filling since, and a countdown taken from
    # one of them is not a fact about the disk.
    projection_robust = (len(signs) >= 2
                         and len({sign for _label, sign in signs}) == 1
                         and signs[0][1] != "0")

    days_to_floor = ((min24 - _DISK_TREND_FLOOR_BYTES) / drain) if drain > 0 else None

    reasons: list[str] = []
    if min24 < _DISK_TREND_NEAR_BYTES:
        reasons.append(f"trailing-24h minimum {min24 / 2**30:.1f} GiB below {_DISK_TREND_NEAR_BYTES / 2**30:.0f} GiB")
    if drain > _DISK_TREND_FAST_DRAIN_PER_DAY:
        reasons.append(f"net drain {drain / 2**30:.1f} GiB/day over the {drain_window} window above {_DISK_TREND_FAST_DRAIN_PER_DAY / 2**30:.0f} GiB/day")
    if days_to_floor is not None and days_to_floor < _DISK_TREND_HORIZON_DAYS and projection_robust:
        reasons.append(f"projected floor in {days_to_floor:.1f} days (< {_DISK_TREND_HORIZON_DAYS})")

    state = "drift" if reasons else "verified"
    parts = [f"min24={min24 / 2**30:.1f}GiB"]
    if slopes:
        parts.append(f"drain={drain / 2**30:.1f}GiB/day({drain_window})")
        parts.append("slopes=" + " ".join(f"{label}:{sign}" for label, sign in signs))
    else:
        parts.append("drain=unknown")
    if days_to_floor is not None:
        parts.append(f"floor={days_to_floor:.1f}d" if projection_robust
                     else "floor=not-robust")
    if reasons:
        parts.append("reasons=" + "|".join(reasons))
    sample = " ".join(parts)

    return {"id": "sense.disk.trend", "state": state, "sample": sample,
            "min24_bytes": min24, "drain_per_day": round(drain, 1),
            "drain_window": drain_window,
            "slope_windows": {label: round(value / 2**30, 2) for label, value in slopes},
            "projection_robust": projection_robust,
            "days_to_floor": (None if days_to_floor is None or not projection_robust
                              else round(days_to_floor, 2)),
            "unrobust_days_to_floor": (None if days_to_floor is None or projection_robust
                                       else round(days_to_floor, 2)),
            "samples": len(rows), "span_hours": round(span_h, 2), "kind": "read"}

def _runtime_pin_lag(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Count the commits between the runtime pin and the checkout HEAD.

    The pin names the bytes the services import; HEAD is the checkout the
    minds edit. The lag is how far the live code leads the pin, and the age
    is how long the oldest unactivated commit has waited. A pin that is not
    an ancestor of HEAD (a rebase or a foreign checkout) reads UNKNOWN, as
    does a missing pin file or a git failure: the lag cannot be computed, so
    it must not read as zero. ``pin == HEAD`` reads ``lag=0`` verified: the
    checkout is exactly the pinned state.

    The pin record also names the release directory the services import
    (``source``). A process whose PYTHONPATH names a missing release silently
    falls back to the checkout's editable install, so a real-syscall stat of
    that directory is part of the reading: an absent source reads UNKNOWN with
    the path named, even though the commit relation still computes. A record
    without a usable ``source`` string cannot be stat-ed and is reported as
    such rather than silently checked.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    pin_path = home / "health" / "runtime-release.json"
    if not pin_path.exists():
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin file unavailable", "kind": "read"}
    try:
        data = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin file unreadable", "kind": "read"}
    if not isinstance(data, dict):
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin record not an object", "kind": "read"}
    pin = data.get("sha")
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{7,40}", pin):
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin sha invalid", "kind": "read"}
    source = data.get("source")
    if not isinstance(source, str) or not source:
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": "pin release source unnamed", "pin": pin, "kind": "read"}
    if not Path(source).is_dir():
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": f"pin={pin[:7]} release-source-missing path={source}",
                "pin": pin, "source": source, "kind": "read"}
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
                    "sample": f"pin={pin[:7]} head={head_sha[:7]} lag=0 stale_senses=0",
                    "pin": pin, "head": head_sha, "lag": 0, "stale_senses": 0,
                    "stale_sense_ids": [], "kind": "read"}
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
        stale_ids, stale_err = _served_staleness(checkout_root, pin, head_sha)
        if stale_err is not None:
            stale_sample = "stale_senses=unknown"
            stale_ids_field = None
        else:
            stale_sample = f"stale_senses={len(stale_ids)}"
            stale_ids_field = (stale_ids[:_SERVED_STALENESS_LIMIT]
                               if len(stale_ids) > _SERVED_STALENESS_LIMIT
                               else stale_ids)
        return {"id": "sense.runtime.pin-lag", "state": "verified",
                "sample": f"pin={pin[:7]} head={head_sha[:7]} lag={lag} age={age_str} {stale_sample}",
                "pin": pin, "head": head_sha, "lag": lag, "age": age_str,
                "stale_senses": len(stale_ids) if stale_err is None else None,
                "stale_sense_ids": stale_ids_field, "kind": "read"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"id": "sense.runtime.pin-lag", "state": "unknown",
                "sample": f"git error: {exc}", "kind": "read"}


def _served_staleness(checkout_root: Path, pin: str, head_sha: str) -> tuple[list[str], str | None]:
    """Count senses whose producing code changed between pin and HEAD.

    For each ``sense.*`` id literal in the checkout's emitters, find the
    innermost producing function (or the exact emitting statement when the
    id sits in the aggregator ``sample()``); when the id is a module-level
    constant, use the functions that reference it. Then run ``git log -L``
    for each unit's line range. Non-empty output means the unit changed after
    the served pin, so a served sample for that id may not reflect HEAD
    behaviour.

    Every module in ``_SENSE_EMITTERS`` is scanned, so a sense defined outside
    the discovery aggregator is still named when its producer changed.

    Returns ``(stale_ids, reason)``: the sorted stale sense ids, or an empty
    list with a reason string when staleness cannot be computed (git failure,
    unreadable emitter). The caller reports UNKNOWN when reason is non-None.
    """
    stale: set[str] = set()
    for name in _SENSE_EMITTERS:
        rel = f"src/mishe_tauftauf/{name}"
        source, err = _committed_source(checkout_root, rel, head_sha)
        if err is not None:
            return [], err
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return [], f"emitter unparseable: {exc}"
        stale |= _emitter_stale_ids(tree, rel, pin, head_sha, checkout_root)
    return sorted(stale), None


def _committed_source(checkout_root: Path, rel: str, head_sha: str) -> tuple[str, str | None]:
    """The emitter bytes at ``head_sha``.

    ``git log -L`` traces committed line ranges, so the AST line numbers must
    come from the same revision: reading the worktree would shift them whenever
    the checkout is dirty and misattribute staleness.
    """
    try:
        shown = subprocess.run(["git", "show", f"{head_sha}:{rel}"],
                               capture_output=True, text=True, encoding="utf-8",
                               timeout=5, cwd=checkout_root)
    except (OSError, UnicodeDecodeError, _TimeoutExpired):
        return "", "emitter unreadable"
    if shown.returncode != 0:
        return "", "emitter unreadable"
    return shown.stdout, None


def _is_sense_id(value: str) -> bool:
    """Whether a string literal names a ``sense.*`` id."""
    return len(value) > 6 and value[:6] == "sense." and value.count(".") >= 2


def _emitter_stale_ids(tree: ast.AST, rel: str, pin: str, head_sha: str,
                       checkout_root: Path) -> set[str]:
    """Ids in one parsed emitter whose producing unit changed in ``pin..head``."""
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def innermost(line: int):
        candidates = [f for f in funcs if f.lineno <= line <= f.end_lineno]
        return min(candidates, key=lambda f: f.end_lineno - f.lineno) if candidates else None

    def statement_range(func, line: int) -> tuple[int, int]:
        if func.name == "sample":
            for stmt in ast.walk(func):
                if (isinstance(stmt, ast.stmt)
                        and not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and stmt.lineno <= line <= stmt.end_lineno):
                    return (stmt.lineno, stmt.end_lineno)
        return (func.lineno, func.end_lineno)

    # A ``sense.*`` id may be a module-level constant (``SENSE_ID = "sense.…"``)
    # whose producers are the functions that reference it. Map each such
    # constant to those functions so the id is still attributed to real code.
    const_line: dict[str, int] = {}
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str) and _is_sense_id(stmt.value.value)):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    const_line[target.id] = stmt.value.lineno
    referenced: dict[str, set] = {name: set() for name in const_line}
    for func in funcs:
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and node.id in referenced:
                referenced[node.id].add(func)
    line_to_const = {line: name for name, line in const_line.items()}

    def units_for(line: int) -> list[tuple[int, int]]:
        func = innermost(line)
        if func is not None:
            return [statement_range(func, line)]
        name = line_to_const.get(line)
        if name is None:
            return []
        return [(f.lineno, f.end_lineno) for f in referenced[name]]

    stale: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        sid = node.value
        if not _is_sense_id(sid):
            continue
        for start, end in units_for(node.lineno):
            try:
                log_result = subprocess.run(
                    ["git", "log", "--oneline", "-L",
                     f"{start},{end}:{rel}", f"{pin}..{head_sha}"],
                    capture_output=True, text=True, timeout=5, cwd=checkout_root)
                if log_result.returncode != 0:
                    continue
                if decode_git_log_lines(log_result.stdout):
                    stale.add(sid)
                    break
            except (OSError, _TimeoutExpired):
                continue
    return stale


def _host_session_stability(home: Path) -> dict[str, object]:
    """Detect tmux-server deaths and missing OOM shields so mishe self-heals.

    A dead tmux server reads as every pane UNKNOWN and every supervisor
    crash-looping; without this reading the operator window is the only
    detector and the fix is manual. A session absent from the live list is
    drift, not unknown: the receipt names the expected session. The live
    sessions' own servers report their oom_score_adj; anything above -900
    means the host-guard shield lapsed and the plant's server is OOM-killable
    again. A server this socket does not host — a test's disposable server on
    its own TMUX_TMPDIR socket, say — is not a live server here and must not
    read as a plant shield lapse; when the socket's own server pids cannot be
    read, every server is checked so a broken read stays visible. Unreadable
    tmux or /proc reads stay unknown rather than reading as stability. All
    external text enters through proc_table decoders, never inline parsing.
    """
    session = os.environ.get("MISHE_SEED_SESSION") or None
    if not session:
        from .seed import recorded_session
        session = recorded_session(home)
    try:
        listed = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                                capture_output=True, timeout=2)
        owners = subprocess.run(["tmux", "list-sessions", "-F", "#{pid}"],
                                capture_output=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.host.session-stability", "state": "unknown",
                "sample": "tmux enumeration unavailable", "kind": "read"}
    # This socket's own servers, named by tmux itself. No live session means no
    # server of this plant's to shield; an unreadable pid list leaves the
    # attribution open (None) so the check falls back to every server.
    if listed.returncode != 0:
        live: list[str] = []
        plant_servers: set[str] | None = set()
    else:
        live = sorted(proc_table.window_names(listed.stdout))
        plant_servers = ({str(pid) for pid in proc_table.pane_pids(owners.stdout)}
                         if owners.returncode == 0 else None)
    shields: dict[str, str] = {}
    # The server's comm is "tmux: server", so pgrep -x misses it; match the
    # full command line and keep only the new-session server processes.
    try:
        servers = subprocess.run(["pgrep", "-f", "tmux new-session"], capture_output=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        servers = None
    if servers is not None and servers.returncode == 0:
        for pid_text in proc_table.pane_pids(servers.stdout):
            if not proc_table.is_tmux_server(str(pid_text)):
                continue
            adj = proc_table.read_oom_score_adj(str(pid_text))
            if adj is None:
                continue
            shields[str(pid_text)] = adj
    unshielded = sorted(pid for pid, adj in shields.items()
                        if adj != "-900" and (plant_servers is None or pid in plant_servers))
    if session is not None and session not in live:
        return {"id": "sense.host.session-stability", "state": "drift",
                "sample": f"live=[{','.join(live)}] session-missing={session} unshielded=[{','.join(unshielded)}]",
                "live": live, "session": session, "unshielded": unshielded, "kind": "read"}
    if unshielded:
        return {"id": "sense.host.session-stability", "state": "drift",
                "sample": f"live=[{','.join(live)}] unshielded-servers={','.join(unshielded)}",
                "live": live, "unshielded": unshielded, "kind": "read"}
    return {"id": "sense.host.session-stability", "state": "verified",
            "sample": f"live=[{','.join(live)}] servers={len(shields)} shielded",
            "live": live, "unshielded": unshielded, "kind": "read"}


def _host_server_keeper(home: Path) -> dict[str, object]:
    """Name the systemd unit that actually holds the tmux server.

    The plant's session keeper is separate from the process serving its
    socket: `systemd-run` raises a keeper beside the session, while the
    server itself is forked by whichever `tmux new-session` first created
    the shared socket. When a finished test's raise wins, the shared server
    lives in a transient unit no live service owns, so the plant's
    presentation liveness depends on a unit nothing will restart or protect.
    Naming the unit makes that ownership visible; this reading never stops
    anything, because stopping a keeper that holds a shared server would
    cgroup-kill every session on the socket.

    Drift is a holding unit that does not belong to the declared session.
    Verified names the plant's own keeper. An absent session or an
    unreadable /proc read stays unknown: attribution that cannot be made is
    not a healthy signal, and a missing session is the session-stability
    reading's to report rather than this one's to duplicate. All external
    text enters through proc_table decoders.
    """
    session = os.environ.get("MISHE_SEED_SESSION") or None
    if not session:
        from .seed import recorded_session
        session = recorded_session(home)
    if not session:
        return {"id": "sense.host.server-keeper", "state": "unknown",
                "sample": "declared session unreadable", "kind": "read"}
    try:
        result = subprocess.run(["tmux", "display-message", "-p", "-t", "=" + session, "#{pid}"],
                                capture_output=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.host.server-keeper", "state": "unknown",
                "sample": "server attribution unavailable", "kind": "read"}
    if result.returncode != 0:
        return {"id": "sense.host.server-keeper", "state": "unknown",
                "sample": f"session {session} not attributable to a server", "kind": "read"}
    pids = proc_table.pane_pids(result.stdout)
    if len(pids) != 1:
        return {"id": "sense.host.server-keeper", "state": "unknown",
                "sample": "server pid unreadable", "kind": "read"}
    server = str(pids[0])
    unit = proc_table.read_cgroup_unit(server)
    if not unit:
        return {"id": "sense.host.server-keeper", "state": "unknown",
                "sample": f"server pid {server} cgroup unreadable", "kind": "read"}
    if not proc_table.belongs_to_session(unit, session):
        return {"id": "sense.host.server-keeper", "state": "drift",
                "sample": f"session={session} server-pid={server} foreign-keeper={unit}",
                "session": session, "server_pid": server, "keeper": unit, "kind": "read"}
    return {"id": "sense.host.server-keeper", "state": "verified",
            "sample": f"session={session} server-pid={server} keeper={unit}",
            "session": session, "server_pid": server, "keeper": unit, "kind": "read"}

def _host_session_restarts(home: Path, past_minutes: int = 60) -> dict[str, object]:
    """Count plant session-scope births over the trailing window; flag churn.

    A session birth leaves ``Started <session>-session.scope`` in the journal
    under the systemd identifier (seed.py raises the resident session with
    ``systemd-run --unit <session>-session.scope``). One birth per boot is
    healthy; repeated births mean the session keeps dying and being re-raised.
    Journal bytes enter through the ``session_restarts`` boundary decoder;
    this function owns acquisition (boot-pinned journalctl, epoch-change
    guard) and cadence verdicts only. The priority selector is dropped:
    ``Started`` lines are daemon info, so ``-p warning`` would blind the read.
    """
    from . import session_restarts
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
    unknown: dict[str, object] = {"id": "sense.host.session-restarts",
                "state": "unknown",
                "sample": "session-restart cadence unavailable", "kind": "read",
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
        "--no-pager", "--quiet", "--case-sensitive=yes",
        "--grep", f"^Started {session}-session\\.scope"])
    after = _journal_boot()
    finished = time.monotonic_ns()
    coverage["acquisition_finished_ns"] = finished
    if after != boot or finished < started:
        unknown["reason"] = "acquisition epoch changed"
        return unknown
    if output is None:
        unknown["reason"] = "incomplete or failed acquisition"
        return unknown
    entries = session_restarts.decode_journal_entries(
        output, boot, "systemd", lower, upper)
    if entries is None:
        unknown["reason"] = "invalid journal entry metadata"
        return unknown
    verdict = session_restarts.assess_restarts(entries, session)
    state = {"steady": "verified", "churn": "drift"}.get(str(verdict.get("verdict")), "unknown")
    sample = f"last-{past_minutes}m " + session_restarts.sample_text(verdict)
    return {"id": "sense.host.session-restarts", "state": state,
            "sample": sample, "kind": "read", "session": session,
            "verdict": verdict.get("verdict"), "reason": verdict.get("reason"),
            "coverage": coverage}

def _coord_authorship_fog(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Name dirty-tree bytes no wall or chat line claims, so landing stops guessing.

    Every uncommitted path is checked against two claim sources: a wall file
    naming the path, and a chat.log line naming it. A path claimed by neither
    is fog: no mind can land it without risking another author's work.
    State is drift when fog exists, verified when every dirty path is
    claimed, unknown when git or the tape is unreadable.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    checkout = Path(home).resolve().parent
    try:
        status = subprocess.run(["git", "status", "--short"], capture_output=True,
                                timeout=5, cwd=checkout)
        if status.returncode != 0:
            raise OSError("git status failed")
        dirty = proc_table.window_names(status.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return {"id": "sense.coord.authorship-fog", "state": "unknown",
                "sample": "dirty set unreadable", "kind": "read"}
    if not dirty:
        return {"id": "sense.coord.authorship-fog", "state": "verified",
                "sample": "dirty=0 fog=0", "kind": "read", "dirty": 0, "fog": [], "claimed": []}
    walls_dir = Path(home) / "walls"
    wall_text = ""
    try:
        for path in walls_dir.glob("*.md"):
            try:
                wall_text += path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    except OSError:
        pass
    try:
        tape = (Path(home) / "chat.log").read_text(encoding="utf-8", errors="replace")[-200000:]
    except OSError:
        tape = ""
    # Exclude the sense's own publications from the claim source: the sense
    # publishes its fog paths in its sample, and on the next evaluation those
    # paths would read as "claimed" while the bytes are still unclaimed. The
    # tape's own line format is decoded in the coordination tape boundary
    # (:mod:`mishe_tauftauf.coord_tape`).
    claim_tape = tape_claim_source(tape)
    fog: list[str] = []
    claimed: list[str] = []
    for line in dirty:
        name = Path(line).name
        if name in wall_text or name in claim_tape:
            claimed.append(line)
        else:
            fog.append(line)
    state = "drift" if fog else "verified"
    return {"id": "sense.coord.authorship-fog", "state": state,
            "sample": f"dirty={len(dirty)} fog={len(fog)} " + ",".join(fog[:8]),
            "kind": "read", "dirty": len(dirty), "fog": fog, "claimed": claimed}

LANDING_GATE_SUITE = "tests/test_proc_table.py"
"""The kernel focused suite the landing gate runs, relative to the checkout.

Its presence is what makes the gate applicable. A linked site whose repo does
not carry the kernel suite cannot be gated by it: running the kernel's command
there fails on the missing interpreter (``env`` exits 127), which would name a
failing test that does not exist in that repo.
"""


def _repo_landing_block(home: Path) -> dict[str, object]:
    """Name what stops the dirty tree from landing, so a mind can unblock it.

    Two block classes: fog paths (no wall/chat claim — see authorship-fog) and
    test-gate state (whether the kernel focused suite passes on the working
    tree). The gate runs ``tests/test_proc_table.py`` from the checkout holding
    the site's repo with a hard timeout; a timeout or an unlaunchable runner
    reads ``gate=unknown``, never pass. The gate applies only where that suite
    exists: a linked site's repo does not carry it, so the kernel suite is not
    a landing block there and the sample names ``gate=absent``.
    State is drift when any block exists, verified when the tree is clean or
    every dirty path is claimed and the gate passes or does not apply.
    """
    fog_reading = _coord_authorship_fog(home)
    fog = fog_reading.get("fog", []) if isinstance(fog_reading.get("fog"), list) else []
    checkout = Path(home).resolve().parent
    applicable = (checkout / LANDING_GATE_SUITE).is_file()
    gate_ok: bool | None = None
    if applicable:
        try:
            gate = subprocess.run(
                ["env", "PYTHONPATH=src", ".venv/bin/pytest", "-q",
                 LANDING_GATE_SUITE, "-x", "--no-header"],
                capture_output=True, timeout=120, cwd=checkout)
            gate_ok = gate.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            gate_ok = None
    blocks: list[str] = []
    if fog:
        blocks.append(f"fog={len(fog)}")
    if gate_ok is False:
        blocks.append("gate=fail")
    elif applicable and gate_ok is None:
        blocks.append("gate=unknown")
    if not applicable:
        gate_label = "absent"
    elif gate_ok is None:
        gate_label = "unknown"
    else:
        gate_label = "pass" if gate_ok else "fail"
    state = "drift" if blocks else "verified"
    sample = (f"blocks=[{','.join(blocks) if blocks else 'none'}] "
              f"fogpaths={len(fog)} gate={gate_label}")
    return {"id": "sense.repo.landing-block", "state": state,
            "sample": sample, "kind": "read", "blocks": blocks, "fog": fog,
            "gate": gate_ok, "gate_applicable": applicable}

def _coord_gate_latency(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Age the oldest unanswered resolver routing, so stalls wake their owner.

    Reads the dm-disposition open set and reports the oldest open DM per
    target role with its age: a resolver whose queue head is older than the
    maturity bound is the bottleneck, named explicitly. State is drift when
    any open DM is mature, verified when the queue is empty or all young,
    unknown when the tape is unreadable.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    tape_path = Path(home) / "chat.log"
    if not tape_path.exists():
        return {"id": "sense.coord.gate-latency", "state": "unknown",
                "sample": "chat tape unavailable", "kind": "read"}
    try:
        entries = parse_feed(tape_path.read_bytes(), home=home)
    except (OSError, ValueError) as exc:
        return {"id": "sense.coord.gate-latency", "state": "unknown",
                "sample": f"chat tape unreadable: {exc}", "kind": "read"}
    disp = _coord_dm_disposition_age(home, now)
    if disp.get("state") != "verified":
        return {"id": "sense.coord.gate-latency", "state": "unknown",
                "sample": "disposition reading unavailable", "kind": "read"}
    oldest = str(disp.get("oldest_open", ""))
    stall = int(disp.get("stall", 0))
    state = "drift" if stall else "verified"
    return {"id": "sense.coord.gate-latency", "state": state,
            "sample": f"stall={stall} oldest={oldest}",
            "kind": "read", "stall": stall, "oldest_open": oldest}

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

_RELEASE_RETENTION_KEEP = 8
"""Unreferenced release worktrees kept beyond the live set before the reading reads
drift. ``coordination/site_sync.py`` creates one per green SHA applied to a linked
site and removes none, so a bounded keep is provenance and rollback while an
unbounded set is a leak; the prune is genome's lane, this reading is its detector."""

_RELEASE_RETENTION_MAX_BYTES = 2 * 2**30
"""Byte ceiling on the whole ``releases`` tree, so a plant that keeps few but large
releases still reads drift."""

_RELEASE_RETENTION_GROWTH_WINDOW_DAYS = 7
"""Trailing window for the created-release count reported as growth evidence."""


def _referenced_release_names(unit_root: Path | None = None) -> set[str]:
    """Release shas referenced by a live process environment or an installed unit.

    A live process names its release in ``PYTHONPATH``; an installed unit names it
    in ``Environment``, and ``systemctl show`` exposes only running units, so the
    unit *files* are read for a configured-but-stopped pin. Only a full 40-hex name
    is returned: a placeholder such as ``releases/0`` is not a release. An
    unreadable source contributes nothing, so an empty set means nothing was read,
    never that no release is referenced.
    """
    refs: set[str] = set()
    try:
        for pid_dir in os.scandir("/proc"):
            if not pid_dir.name.isdigit():
                continue
            try:
                blob = Path(f"/proc/{pid_dir.name}/environ").read_bytes()
            except (OSError, ValueError):
                continue
            refs.update(proc_table.release_names(blob))
    except OSError:
        pass
    if unit_root is None:
        unit_root = Path.home() / ".config" / "systemd" / "user"
    if unit_root.is_dir():
        for path in list(unit_root.rglob("*.service")) + list(unit_root.rglob("*.conf")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            refs.update(proc_table.release_names(text))
    return refs


def _release_tree_bytes(root: Path) -> int | None:
    """Total apparent size of ``root`` in bytes, or None when it cannot be measured.

    The walk is bounded by the filesystem: an unreadable entry contributes
    nothing, and an unreadable root reads unmeasured rather than zero.
    """
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                try:
                    total += os.lstat(os.path.join(dirpath, name)).st_size
                except OSError:
                    continue
    except OSError:
        return None
    return total


def _repo_release_retention(home: Path, now: datetime | None = None) -> dict[str, object]:
    """Report the plant-owned release-worktree accumulation against a retention bound.

    ``coordination/site_sync.py::_release_source`` creates one detached worktree at
    ``releases/<sha>`` per green SHA applied to a linked site (and the core pin is
    expressed the same way) and nothing in the repo removes it. ``branch-inventory``
    reports the worktree count as a level, which cannot show that the level is an
    unbounded leak: 371 unreferenced releases and 1.3 GiB of plant-owned bytes read
    the same as a handful. This reading classifies each ``releases/`` entry as live
    (referenced by a live process environment or an installed unit) or reclaimable,
    reports the oldest unreferenced age and the trailing-window creation count, and
    reads drift when the unreferenced set exceeds the retention bound
    (``_RELEASE_RETENTION_KEEP`` newest, plus a bytes ceiling).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    site = Path(home).resolve()
    releases = site / "releases"
    unknown = {"id": "sense.repo.release-retention", "state": "unknown", "kind": "read"}
    if not releases.is_dir():
        return {**unknown, "sample": "releases directory absent"}

    def age_days(path: Path) -> float | None:
        try:
            return (now.timestamp() - path.stat().st_mtime) / 86400.0
        except OSError:
            return None

    referenced = _referenced_release_names()
    entries = sorted(p for p in releases.iterdir() if p.is_dir())
    worktrees = [p for p in entries if (p / ".git").exists()]
    live = [p for p in worktrees if p.name in referenced]
    reclaimable = [p for p in worktrees if p.name not in referenced]
    legacy = [p for p in entries if not (p / ".git").exists()]
    newest = sorted(reclaimable, key=lambda p: (age_days(p) is None, age_days(p) or 0.0))
    kept = newest[:_RELEASE_RETENTION_KEEP]
    over = newest[_RELEASE_RETENTION_KEEP:]
    oldest = age_days(newest[-1]) if newest else None
    cutoff = now.timestamp() - _RELEASE_RETENTION_GROWTH_WINDOW_DAYS * 86400.0
    created = 0
    for path in worktrees:
        try:
            if path.stat().st_mtime >= cutoff:
                created += 1
        except OSError:
            continue
    total_bytes = _release_tree_bytes(releases)

    reasons: list[str] = []
    if over:
        reasons.append(f"{len(over)} unreferenced releases beyond keep={_RELEASE_RETENTION_KEEP}")
    if total_bytes is not None and total_bytes > _RELEASE_RETENTION_MAX_BYTES:
        reasons.append(f"{total_bytes / 2**30:.1f} GiB exceeds "
                       f"{_RELEASE_RETENTION_MAX_BYTES / 2**30:.0f} GiB")
    state = "drift" if reasons else "verified"
    oldest_text = "none" if oldest is None else f"{oldest:.1f}d"
    bytes_text = "unread" if total_bytes is None else f"{total_bytes / 2**30:.2f}GiB"
    sample = (f"worktrees={len(worktrees)} live={len(live)} reclaimable={len(reclaimable)} "
              f"legacy={len(legacy)} kept={len(kept)} over={len(over)} "
              f"oldest={oldest_text} "
              f"created{_RELEASE_RETENTION_GROWTH_WINDOW_DAYS}d={created} "
              f"bytes={bytes_text}")
    if reasons:
        sample += " reasons=" + "|".join(reasons)
    return {"id": "sense.repo.release-retention", "state": state, "sample": sample,
            "kind": "read", "worktrees": len(worktrees), "live": len(live),
            "reclaimable": len(reclaimable), "legacy": len(legacy), "kept": len(kept),
            "over": len(over),
            "oldest_days": None if oldest is None else round(oldest, 2),
            "created_recent": created, "bytes": total_bytes,
            "live_names": sorted(p.name for p in live),
            "reclaimable_names": sorted(p.name for p in reclaimable)[:5],
            "reasons": reasons}



GHOST_FILE_LIMIT = 10_000
"""Enumeration bound across ghost-home trees: an exact count to the limit, then
a lower bound with ``files_at_least`` set, so a scan stays bounded while a
present tree never reads as none."""

GHOST_ROOT_LIMIT = 64
"""Ghost trees examined per scan: reserved roots beyond this are counted, not
walked, and ``roots_at_least`` marks the tree count as a lower bound."""

GHOST_IDENTITY_LIMIT = 100
"""Names retained per identity class: nested names beyond this are counted, not
retained, and ``nested_truncated`` marks the count as a lower bound."""

GHOST_WORK_LIMIT = 50_000
"""Directory and file names inspected across all ghost trees before the scan
stops with ``work_truncated`` set. Each name is charged as it is yielded by a
directory scan, not per materialized list, so a tree of empty or directory-only
entries cannot stretch one reading without bound."""

GHOST_SCAN_LIMIT = 512
"""Checkout-root names inspected per scan: every entry charges this budget
whether or not it is reserved, so a long non-reserved prefix cannot stretch one
reading. A truncated enumeration that examined no ghost tree cannot claim
absence, and a retained root count stays a lower bound."""


def _home_referenced_by_process(path: Path) -> bool:
    """Whether a running process names this directory.

    Only ``/proc/*/cmdline`` is read, so a ``False`` verdict means exactly "no
    running process names this path" and nothing more. A systemd unit whose
    ``ExecStart`` (or ``WorkingDirectory``/``Environment``) names the path but
    that is not running, a system unit, a timer and every other non-process
    reference are invisible here. ``systemctl --user list-units`` was previously
    consulted too, but its output exposes unit name/state/description, not
    ExecStart or unit-file contents, so a path in a unit file stayed invisible
    while a description that happened to match the path produced a false
    positive. The caller must never read a ``False`` verdict as "no runtime
    references this" or "the ghost is abandoned".
    """
    path_str = str(path)
    try:
        for pid_dir in os.scandir("/proc"):
            if not pid_dir.name.isdigit():
                continue
            try:
                cmdline = Path(f"/proc/{pid_dir.name}/cmdline").read_bytes()
            except (OSError, ValueError):
                continue
            if path_str.encode() in cmdline:
                return True
    except OSError:
        pass
    return False


def _ghost_home_name(name: str) -> bool:
    """Whether one checkout-root name is a site-home literal at all.

    Two shapes reach a checkout root when a site-home literal is corrupted.
    The reserved prefix (``.mishe-``) is the first: the checkout's ignore
    rule matches it, so such a name is invisible to Git and to every peer.
    An unexpanded shell variable (``$MISHE_SEED_HOME``,
    ``$MISHE_TAUFTAUF_HOME``) is the second: the ignore rule does not cover
    it, so Git stages it, and the name carries no prefix a prefix rule can
    see. A detector that knows only the first shape reads green over a live
    violation of the same class, so both shapes are decided here once.

    Only names are decided here; whether a name is a *site home* is a
    different question, answered by the caller against the site it serves.
    """
    return reserved_home_name(name) or literal_site_home_name(name)


def _ghost_site_homes(home: Path, now: float | None = None) -> dict[str, object]:
    """Enumerate site-home literal directories under the checkout that are not
    this site's home, with their files and the age of their newest content,
    and name the reserved root names that are not directories.

    Two name shapes are site-home literals (:func:`_ghost_home_name`): the
    reserved ``.mishe-`` prefix, and an unexpanded site-home shell variable
    such as ``$MISHE_SEED_HOME``. Only this site's home is a sanctioned
    ``.mishe-*`` directory in a plant's
    checkout, and ``validate_home`` rejects any other one that lacks the
    initialized runtime layout, so a second such directory cannot come from
    the CLI: it is a mind's own file write against a corrupted site literal.
    That write lands where no renderer, ``wall show``, feed reader, peer, diff
    or CI can read it — ``.gitignore`` matches ``.mishe-*``, so even Git never
    stages it — and a mind can believe a note landed where peers read it while
    the canonical wall silently stays stale. The reading turns that silent
    class of loss into a visible one: it counts the ghost trees and their
    files, names the nested ghosts inside a ghost tree, and ages the newest
    content so a lane can prioritize counsel. An empty root has no file to
    age, so its own last-change time dates the stray instead: a fresh typo
    reads as recent rather than undated. Presence of a ghost directory is
    itself the violation; the file count quantifies it.

    A reserved root name that is not a directory is a stray name: a file, or
    another non-directory, carrying a site-home literal. A stray name cannot
    become a site home, so it does not change the verdict -- this sense
    answers "is there another site home here", and a file is not one. It is
    named in the sample and in ``stray`` all the same, because a stray name
    is the other half of the same corrupted write: content that landed
    outside any site home, under a name the ignore rule hides from Git and
    from every peer, so a note can live only there. A sample that carried no
    stray field could be read as covering those names, so it names them
    instead. Identity lists are
    capped and counts are exact only while every cap holds: a truncated tree
    count, nested count or name budget leaves its lower-bound marker on the
    sample line, so an uncapped scale never reads as a clean complete count.
    Work is bounded at the point of inspection — the checkout scan charges
    one budget name per entry it yields, and a walked tree charges the shared
    name budget per entry a directory scan yields — and a truncated
    enumeration that examined no ghost tree cannot claim a clean zero.

    A broken enumeration reads UNKNOWN naming the unreadable tree: a partial
    count must never read as a clean zero. Directory symlinks inside a tree
    are not followed, counted or stat'ed, so the count cannot walk out and
    double-count, and no age is read from an object outside the checkout. A
    reserved root that is itself a symlink is recorded by name and never
    traversed either: its content lives outside the checkout, so reading its
    names, counts and ages would report external state under a checkout name.

    A ghost no running process names is reported ``no-process``: the process
    scan (:func:`_home_referenced_by_process`) is the only reference signal read
    here, so the sample names what it checked and never calls the ghost
    ``stale`` or abandoned. An installed systemd unit that names the path, a
    system unit, a timer or any other non-process configuration is UNKNOWN here,
    not absent, so this label is not a disposition to delete the tree.
    """
    if now is None:
        now = time.time()
    site = Path(home).resolve()
    checkout = site.parent
    checkout_truncated = False
    try:
        # Enumeration is bounded by name, not by materializing the whole
        # directory: every checkout-root entry charges one budget name whether
        # or not it is reserved, so a long non-reserved prefix cannot stretch
        # one sample, and the scan also stops once it has seen more reserved
        # roots than one reading walks. The excess is counted rather than
        # examined; determinism comes from sorting the retained names, not
        # from reading everything first.
        with os.scandir(checkout) as scan:
            found: list = []
            inspected = 0
            for item in scan:
                inspected += 1
                if inspected > GHOST_SCAN_LIMIT:
                    checkout_truncated = True
                    break
                if _ghost_home_name(item.name):
                    found.append(item)
                    if len(found) > GHOST_ROOT_LIMIT:
                        break
    except OSError as exc:
        return {"id": "sense.site.ghost-homes", "state": "unknown", "kind": "read",
                "sample": f"checkout root unreadable: {exc}"}
    found.sort(key=lambda item: item.name)
    entries = found[:GHOST_ROOT_LIMIT]
    over_cap = len(found) - len(entries)
    ghosts: list[str] = []
    holding: list[str] = []
    empty: list[str] = []
    nested: list[str] = []
    symlinked: list[str] = []
    stray: list[str] = []
    files_per_ghost: dict[str, int] = {}
    newest: dict[str, tuple[float, str]] = {}
    total = 0
    capped = False
    nested_truncated = False
    symlinked_truncated = False
    stray_truncated = False
    work_truncated = False
    names_left = GHOST_WORK_LIMIT
    for entry in entries:
        if not _ghost_home_name(entry.name):
            continue
        if len(ghosts) >= GHOST_ROOT_LIMIT:
            over_cap += 1  # counted by name only, never walked
            continue
        tree = Path(entry.path)
        try:
            if tree.resolve() == site:
                continue
        except OSError:
            pass  # an unresolvable name is still reported, never silently skipped
        if os.path.islink(entry.path):
            # A reserved root that is a symlink is a checkout-scope name whose
            # content lives elsewhere: `Path.is_dir()` would follow it, so the
            # walk below would read names, file counts and ages from outside
            # the checkout and could count the same tree twice through two
            # names. The root name itself is in-scope evidence of the hazard,
            # so it is recorded as a ghost and never traversed, and its file
            # count is left unmeasured rather than read as a clean zero.
            ghosts.append(entry.name)
            if len(symlinked) < GHOST_IDENTITY_LIMIT:
                symlinked.append(entry.name)
            else:
                symlinked_truncated = True
            continue
        if not tree.is_dir():
            # A reserved name that is not a directory cannot become a site
            # home, so it does not change the homes verdict -- a file is not
            # a home and the verdict is not a false negative about one. It is
            # still the other shape of the same corrupted literal: content
            # written to a name that is not a site home, and (with the
            # reserved prefix) invisible to Git and to every peer, so a note
            # can live only there. It is named here, so a sample that says
            # nothing about stray names cannot be read as covering them.
            if len(stray) < GHOST_IDENTITY_LIMIT:
                stray.append(entry.name)
            else:
                stray_truncated = True
            continue
        ghosts.append(entry.name)
        failures: list[OSError] = []

        files = 0
        newest_at: float | None = None
        # Walk one directory scan at a time, charging the shared name budget as
        # each entry is yielded: a directory with more entries than the budget
        # can never materialize them all, and each pushed directory already
        # charged its name, so the pending stack is within the same budget.
        pending: list = [tree]
        while pending:
            dirpath = pending.pop()
            try:
                with os.scandir(dirpath) as walk_scan:
                    for item in walk_scan:
                        names_left -= 1
                        if names_left <= 0:
                            work_truncated = True
                            break
                        if item.is_dir(follow_symlinks=False):
                            if _ghost_home_name(item.name):
                                if len(nested) < GHOST_IDENTITY_LIMIT:
                                    nested.append(f"{entry.name}/{str(Path(item.path).relative_to(tree))}")
                                else:
                                    nested_truncated = True
                            pending.append(item.path)
                            continue
                        if item.is_symlink():
                            # A name in the tree whose identity lives where the
                            # link points: it is neither a checkout directory
                            # to descend (already excluded above by
                            # follow_symlinks=False) nor a checkout file, so it
                            # is left out of both the count and the newest-file
                            # age. Stat'ing it would read an external object's
                            # mtime into this reading.
                            continue
                        if total + files >= GHOST_FILE_LIMIT:
                            capped = True
                            break
                        files += 1
                        try:
                            mtime = item.stat().st_mtime
                        except OSError:
                            continue
                        if newest_at is None or mtime > newest_at:
                            newest_at = mtime
            except OSError as exc:
                failures.append(exc)
            if capped or work_truncated:
                break
        if failures:
            return {"id": "sense.site.ghost-homes", "state": "unknown", "kind": "read",
                    "sample": f"ghost home unreadable: {entry.name}: {failures[0]}"}
        files_per_ghost[entry.name] = files
        total += files
        if newest_at is None:
            # An empty root holds no file to age, but its own last-change time
            # dates the stray — its creation, or the removal of its last child
            # — so a lane can tell a fresh write from an ancient one instead of
            # reading every empty ghost as undated.
            try:
                newest_at = tree.stat().st_mtime
            except OSError:
                newest_at = None
        if newest_at is not None:
            newest[entry.name] = (newest_at,
                                  datetime.fromtimestamp(newest_at, timezone.utc)
                                  .isoformat(timespec="seconds"))
        (holding if files else empty).append(entry.name)
        if capped or work_truncated:
            break  # identity lists hold at most the examined trees
    # Name the ghosts no running process holds. The process scan is the only
    # runtime signal read here, so a miss is ``no-process`` — never ``stale`` or
    # ``abandoned``. A configured-but-not-running unit and every other
    # non-process reference stay UNKNOWN, not absent.
    no_process: list[str] = []
    for name in ghosts:
        tree = Path(checkout / name)
        if not _home_referenced_by_process(tree):
            no_process.append(name)

    if checkout_truncated and not ghosts:
        return {"id": "sense.site.ghost-homes", "state": "unknown", "kind": "read",
                "sample": (f"checkout enumeration truncated at {GHOST_SCAN_LIMIT} names: "
                           f"{checkout}; ghost absence not established")}
    roots_at_least = over_cap > 0 or capped or work_truncated or checkout_truncated
    newest_by_age = sorted(newest.items(), key=lambda item: (-item[1][0], item[0]))
    newest_text = (f"{name} age={_space_age(now - mtime)}"
                   for name, (mtime, _stamp) in newest_by_age[:1])

    def named(values: list[str]) -> str:
        return (",".join(values[:3]) + (f" +{len(values) - 3}more" if len(values) > 3 else "")
                if values else "none")

    parts = [f"ghost-homes={len(ghosts)}{'+' if roots_at_least else ''}"]
    parts.append(f"files>={total}"
                 if capped or work_truncated or symlinked else f"files={total}")
    parts.append("holding=" + named(holding))
    if empty:
        parts.append("empty=" + named(empty))
    if stray:
        parts.append("stray=" + named(stray)
                     + ("+" if stray_truncated else ""))
    if no_process:
        parts.append("no-process=" + named(no_process))

    parts.append(f"nested={len(nested)}{'+' if nested_truncated else ''}")
    if symlinked:
        parts.append("symlink=" + named(symlinked)
                     + ("+" if symlinked_truncated else ""))  # named, never walked
    if work_truncated:
        parts.append("names-truncated")
    if checkout_truncated:
        parts.append("checkout-truncated")
    parts.append("newest=" + (next(newest_text, "none")))
    sample = " ".join(parts)
    state = "verified" if not ghosts else "drift"
    return {"id": "sense.site.ghost-homes", "state": state, "sample": sample,
            "kind": "read", "checkout": str(checkout),
            "ghosts": sorted(ghosts), "files": files_per_ghost, "total": total,
            "holding": sorted(holding), "empty": sorted(empty),
            "nested": sorted(nested),
            "stray": sorted(stray),
            "stray_truncated": stray_truncated,
            "no_process": sorted(no_process),

            "files_at_least": capped or work_truncated or bool(symlinked),
            "roots_at_least": roots_at_least,
            "nested_truncated": nested_truncated,
            "symlinked": sorted(symlinked),
            "symlinked_truncated": symlinked_truncated,
            "work_truncated": work_truncated,
            "checkout_truncated": checkout_truncated,
            "newest": {name: {"stamp": stamp, "age_s": round(now - mtime)}
                       for name, (mtime, stamp) in sorted(newest.items())}}


DM_TO_RE = re.compile(r"^\[dm\] to=([A-Za-z0-9_.-]+)")
RECORD_REF_RE = re.compile(r"\[record\] records/([0-9a-f]{64})\.json sha256=[0-9a-f]{64}")
DM_DISPOSITION_WINDOW_SECONDS = 6 * 3600
"""Hours of DM history one disposition sample covers."""
DM_DISPOSITION_MATURITY_SECONDS = 30 * 60
"""A DM younger than this is right-censored: it has not had the full window to
be dispositioned, so it is excluded from the stall estimate."""
WALL_OUTCOME_RE = re.compile(r"^Wall outcome \S+ by ([A-Za-z0-9_.-]+)")
PSEUDO_TARGETS = frozenset({"chat", "all"})
"""Broadcast targets that can never reply or record a wall outcome. DMs to
these targets are excluded from disposition stall/open counts: they are
one-way notes, not resolver routings, and no reply edge or wall-outcome
credit can ever disposition them."""
SETTLE_RESULT_WINDOW = 100
"""Trailing settlements one result-mix sample covers."""
SETTLE_INCIDENT_GAP_SECONDS = 60 * 60
"""Blocked settlements closer than this belong to one incident."""


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

    Broadcast pseudo-targets (``chat``, ``all``) are excluded from all counts:
    they can never reply or record a wall outcome, so their DMs would
    permanently inflate stall/open without naming a real resolver wait.
    The excluded count is reported as ``broadcast_excluded`` for visibility.
    Targets with no mind at this site — a watcher, feed or the human shell
    (``ci``, ``operator-view``, ``session-watch``, ``silence-watch``,
    ``operator``) — are excluded the same way and reported as
    ``nonmind_excluded``: they have no reply edge to give and no wall outcome
    to record, so their DMs would otherwise hold the stall count above zero
    for the rest of the window while every real mind-to-mind request was
    answered. Seated means the role set the site supervises
    (``activity.roles``), so a renamed role reads excluded until it is seated
    here again.
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
    seated = seated_roles(home)
    window_start = now - timedelta(seconds=DM_DISPOSITION_WINDOW_SECONDS)
    scoped = [dm for dm in all_dms
              if dm[0] >= window_start and dm[2] not in PSEUDO_TARGETS]
    broadcast_excluded = sum(1 for dm in all_dms
                             if dm[0] >= window_start and dm[2] in PSEUDO_TARGETS)
    nonmind_excluded = sum(1 for dm in scoped if dm[2] not in seated)
    dms = [dm for dm in scoped if dm[2] in seated]
    pair_dms: dict[tuple[str, str], list[datetime]] = {}
    for ts, source, target, _seq in all_dms:
        pair_dms.setdefault((source, target), []).append(ts)
    for times in pair_dms.values():
        times.sort()
    if not dms:
        return {"id": "sense.coord.dm-disposition-age", "state": "verified",
                "sample": (f"dms=0 reply_edge=0 seq_credit=0 credit_only=0 "
                           f"wall_only=0 dispositioned=0 open=0 stall=0 "
                           f"broadcast_excluded={broadcast_excluded} "
                           f"nonmind_excluded={nonmind_excluded}"),
                "kind": "read", "dms": 0, "reply_edge": 0, "seq_credit": 0,
                "credit_only": 0, "wall_only": 0, "dispositioned": 0, "open": 0,
                "stall": 0, "broadcast_excluded": broadcast_excluded,
                "nonmind_excluded": nonmind_excluded}
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
              f"p50={p50:.0f}m p90={p90:.0f}m "
              f"broadcast_excluded={broadcast_excluded} "
              f"nonmind_excluded={nonmind_excluded}")
    if oldest:
        sample += f" oldest_open={oldest}"
    return {"id": "sense.coord.dm-disposition-age", "state": "verified",
            "sample": sample, "kind": "read",
            "dms": len(dms), "reply_edge": reply_n, "seq_credit": seq_n,
            "credit_only": credit_only_n, "wall_only": wall_only_n,
            "dispositioned": dispositioned, "open": open_count, "stall": stall_n,
            "p50_minutes": round(p50), "p90_minutes": round(p90),
            "oldest_open": oldest, "broadcast_excluded": broadcast_excluded,
            "nonmind_excluded": nonmind_excluded}


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
EVIDENCE_PATH_POLICY = "eligible-paths-v2"
"""Current prospective allowlist; re-evaluates standing rows without rewriting them."""

def _display_path(path: str, home: Path) -> str:
    """Site-relative display form of a stored evidence path."""
    candidate = Path(path) if os.path.isabs(path) else home / path
    try:
        return candidate.resolve().relative_to(home.resolve()).as_posix()
    except ValueError:
        return str(path)


def _ledger_evidence_binding(home: Path) -> dict[str, object]:
    """Check that post-clause wall outcomes bind write-once evidence.

    A ``wall-outcome`` record binds its per-outcome evidence file with
    ``payload.evidence.{path,sha256}`` at record time. The clause
    (``docs/wall-coordination.md``) prescribes four independent
    requirements: an explicitly allowlisted single file under the owned site
    (A), unchanged bytes (B), retained file (C), and a path cited by no other
    outcome (D). ``EVIDENCE_PATH_POLICY`` is a prospective policy version;
    historic rows are re-evaluated and the old scan evidence is not rewritten.
    The sense re-hashes every tape-referenced post-clause outcome's evidence
    and the whole post-clause set (standing audit — the store is append-only,
    so a repaired violation stays flagged). ``latest_bad_roles`` names roles
    in the live window, so the live set is readable beside the count rather than
    re-derived from ``violating``, which lists the standing audit's newest
    rows. ``latest_bad_last`` stamps each live-bad role's last outcome time:
    the live window is the latest outcome per role over the whole post-clause
    set, so it is unbounded in time and an inactive role's row stands until
    current. ``count_excluded`` names retained-byte violations (B). The
    allowlist contains direct single files under ``artifacts/``, matching
    traversal are class A. The sample preserves the standing-audit cause,
    live window, read watermark and coverage tier within the pane's clipped
    prefix. State follows the live window: ``drift`` when a role's latest
    outcome violates, ``verified`` when every role's latest outcome is clean;
    the standing audit (``all_bad``, ``violations``) retains the append-only
    history beside the verdict. Incomplete or unavailable coverage, or no
    eligible event, stays ``unknown``.

    D counts identical stored path strings. Symlink aliases and traversal are
    not eligible, even when their resolved target is in an allowed directory.
    """
    coverage = classify_outcomes(home, EVIDENCE_BINDING_CLAUSE_TIME)
    read_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
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
    home = home.resolve()
    path_counts: dict[str, int] = {}
    for _ts, _sequence, _role, evidence in loaded:
        path = evidence.get("path")
        if isinstance(path, str):
            path_counts[path] = path_counts.get(path, 0) + 1
    checked: list[tuple[datetime, int, str, str, str]] = []
    for ts, sequence, role, evidence in loaded:
        path = evidence.get("path")
        digest = evidence.get("sha256")
        file_path = Path(path) if isinstance(path, str) else None
        candidate = (file_path if file_path is not None and file_path.is_absolute()
                     else home / file_path if file_path is not None else None)
        try:
            resolved = candidate.resolve() if candidate is not None else None
        except (OSError, RuntimeError):
            resolved = None
        classes: list[str] = []
        if not isinstance(path, str) or not eligible_evidence_path(home, path):
            classes.append("A")
        try:
            if resolved is not None and not resolved.exists():
                classes.append("C")
            else:
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
    bad_last_rows = [(role, latest[role][0]) for role, _classes in latest_bad_roles]
    bad_last = ",".join(f"{role}@{ts.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                        for role, ts in bad_last_rows) or "none"
    count_excluded = sum(1 for row in checked if "B" in row[4])
    # Field order is the pane's visible-prefix order, not a listing order: the
    # pane clips a sample to 120 cells, so a verdict whose cause sits past that
    # prefix reaches the reader with every visible field clean. The
    # standing-audit summary and the live window lead the sample, then the read
    # watermark that bounds them, then the coverage tier.
    parts = [f"all_bad={len(violations)} count_excluded={count_excluded} "
             f"latest_bad={len(latest_bad_roles)} latest_bad_roles={named_bad} "
             f"latest_bad_last={bad_last} read_at={read_at} "
             f"bound={len(checked)} latest_roles={len(latest)}"]
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
            "state": "drift" if latest_bad_roles else "unknown" if incomplete else "verified",
            "sample": " ".join(parts), "kind": "read", "read_at": read_at,
            "policy": EVIDENCE_PATH_POLICY,
            "bound": len(checked), "latest_roles": len(latest),
            "latest_bad": len(latest_bad_roles),
            "latest_bad_roles": [f"{role}({classes})" for role, classes in latest_bad_roles],
            "latest_bad_last": [f"{role}@{ts.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                                for role, ts in bad_last_rows],
            "all_bad": len(violations), "count_excluded": count_excluded,
            "coverage_incomplete": len(incomplete), "incomplete": incomplete,
            "event_sequences": [row["sequence"] for row in coverage["events"]],
            "violations": [f"{role}@{ts.strftime('%Y-%m-%dT%H:%M:%SZ')}:{name}({classes})"
                           for ts, _sequence, role, name, classes in violations]}

SPACE_SOURCE = "http://127.0.0.1:8765/node/note3"
#: The space boundary's producer contract, owned by the named adapter so every
#: reader of that boundary checks the same one.
SPACE_PRODUCER = space_text.SPACE_PRODUCER
SPACE_VALIDITY_SECONDS = 30.0
# A null gap that persists across reads for at least this long, with the light
# channel's own sequence unmoving, is a sustained source freeze rather than a
# dropout. The identity is the light channel's (session, sequence): the node
# cursor is not part of it, because two preserved malformed lines advanced the
# cursor 265954 -> 265956 with the light sequence unmoved and split one
# continuous freeze into two namings. The threshold sits in the gap between the
# measured widths: seconds-wide dropouts resolve between reads, a stopped
# producer does not.
SPACE_FROZEN_MIN_SECONDS = 60.0


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
    """Name the last validated sample and its host-receipt age so an UNKNOWN
    reading still says how stale the feed is, not only that it is not current.

    DM 34981 measured a span (consumer_receipt - phone_sample = latency +
    skew), not a skew directly. Skew is bounded above by the minimum observed
    span; the producer's retained-window bound was 23929-26622ms (n=32) at the
    2026-10-13 investigation and is recomputed each render (bound observed live
    at 25739ms, 12:11Z). Age is computed against ``consumer_receipt_epoch_s`` —
    the host-side arrival stamp — not ``phone_sample_epoch_s``. The phone stamp
    is behind by design and would report false staleness on a healthy feed.
    """
    receipt = light.get("consumer_receipt_epoch_s")
    if not _space_number(receipt) or receipt < 0 or receipt > now:
        return None
    return f"last-receipt={_space_stamp(receipt)} age={_space_age(now - receipt)}"


def _space_endpoint(item: dict) -> tuple[float, float]:
    """Validate one light endpoint's shape and causality.

    The span (receipt - phone = latency + skew) has no upper bound: phone clock
    skew behind the host is measured at ~25.5 s and can grow, so a span-based
    validity check would reject healthy samples. Staleness is checked against
    the receipt (host-side arrival) separately, not here.
    """
    phone, receipt = item["phone_sample_epoch_s"], item["consumer_receipt_epoch_s"]
    if (not _space_number(phone) or not _space_number(receipt)
            or type(item["sequence"]) is not int or item["sequence"] < 0
            or not _space_number(item["lux"]) or item["lux"] < 0
            or receipt < phone):
        raise ValueError("invalid endpoint")
    return phone, receipt


def _space_span_negative(item: object) -> bool:
    """Whether an endpoint's host receipt precedes its own phone sample.

    A negative span is impossible on a healthy feed — the host cannot receive a
    sample before the phone stamped it — so it names a clock or provenance
    anomaly rather than an unusable field.
    """
    if not isinstance(item, dict):
        return False
    phone, receipt = item.get("phone_sample_epoch_s"), item.get("consumer_receipt_epoch_s")
    return _space_number(phone) and _space_number(receipt) and receipt < phone


def _space_clock_bound(light: object) -> str | None:
    """The publisher's own clock-skew bound, carried as opaque text.

    The boundary recomputes it each render from retained acquisition spans
    (received-collected = latency + host_wall - phone_wall; latency >= 0 so
    the minimum span bounds the behind-skew). The reader never parses it:
    a bound that will not read as text is absence, not a verified zero.
    """
    from .space_text import nonblank

    if not isinstance(light, dict):
        return None
    return nonblank(light.get("status_reason"))


class _SpaceEventRefused(Exception):
    """A historical transition the reader will not retain, naming its failed predicate.

    The reason travels to the pane verbatim, so the routine class must not read
    like the defects: a transition older than the producer's own validity is
    expected on nearly every read, while an identity, phone-span, receipt-order,
    future-receipt or bucket contradiction means the publisher's referent or
    clock changed. Collapsing them into one string made a regression and routine
    expiry the same reading.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _space_light(data: object, now: float) -> dict:
    """Validate original evidence; publication and polling never renew a sample."""
    row = {"id": "sense.space.light", "kind": "read", "state": "unknown",
           "reason": "malformed-boundary", "sample": "Note3 light UNKNOWN: malformed-boundary",
           "current_lux": None, "event_id": None, "historical_transition": None,
           "clock_uncertainty": "phone/host clock agreement unverified",
           "clock_bound": None}

    def unknown(reason: str, detail: str | None = None) -> dict:
        row.update(state="unknown", reason=reason, current_lux=None, event_id=None,
                   sample=f"Note3 light UNKNOWN: {reason}"
                          + (f" {detail}" if detail else "")
                          + f"; provenance={SPACE_SOURCE}")
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
        if light["units"] != "lux":
            return unknown("invalid-light-units")
        if light["lux"] is None:
            # A published sample without a lux value is a source coverage gap,
            # not a corrupt file: keep it distinguishable so a dropout reads as
            # itself instead of "malformed-boundary". When the producer's own
            # node-state verdict declares it is deliberately not delivering, the
            # absent channel is that declared silence, so the reading names the
            # declaration rather than blaming the light channel for a
            # producer-wide stop it cannot see from here.
            declared = space_text.decode_node_state(node.get("node_state"))
            return unknown(declared or "light-null-gap", _space_light_age(light, now))
        if (light["status"] != "fresh-clock-conditional"
                or light["delayed_at_receipt"] is not False):
            return unknown("light-not-current-or-delayed", _space_light_age(light, now))
        phone, receipt = _space_endpoint(light)
        if node["cursor"] < light["sequence"]:
            return unknown("invalid-sequence")
        collection_age = light["age_ms_at_phone_collection"]
        if not _space_number(collection_age) or not 0 <= collection_age <= validity * 1000:
            return unknown("invalid-or-stale-collection-age")
        generated, poll = _space_utc(data["generated_at_utc"]), _space_utc(node["last_poll_utc"])
        # Phone stamps sit behind the host by design: the measured quantity
        # is a span (receipt - phone = latency + skew), so skew is bounded
        # above by the smallest observed span, never read directly.
        # phone_sample_epoch_s is excluded from staleness: it would report
        # false staleness on a healthy feed. Receipt is the host-side arrival
        # stamp and is the correct validity referent.
        times = (receipt, generated, poll)
        if any(value > now for value in times):
            return unknown("clock-future")
        if any(now - value > validity for value in times):
            return unknown("stale-original-evidence", _space_light_age(light, now))
        row.update(state="verified", reason="fresh-clock-conditional",
                   current_lux=light["lux"], sequence=light["sequence"],
                   expires_epoch_s=min(times) + validity,
                   clock_bound=_space_clock_bound(light),
                   sample=(f"Note3 {light['lux']:g}lux #{light['sequence']} fresh-clock-conditional; "
                           f"phone={_space_stamp(phone)} receipt={_space_stamp(receipt)}; "
                           f"validity={validity:g}s provenance={node['source']} session={node['session']}"))
        event = node.get("last_light_transition")
        if isinstance(event, dict):
            try:
                event_validity = event["validity_seconds"]
                if not _space_number(event_validity) or event_validity <= 0:
                    raise _SpaceEventRefused("event-malformed-shape")
                event_validity = min(validity, event_validity)
                start, end = event["from"], event["to"]
                if any(_space_span_negative(point) for point in (start, end)):
                    raise _SpaceEventRefused("event-receipt-before-phone")
                start_phone, start_receipt = _space_endpoint(start)
                end_phone, end_receipt = _space_endpoint(end)
                if (event["kind"] != "measured_light_bucket_transition"
                        or event["source"] != node["source"] or event["session"] != node["session"]
                        or event["event_id"] != f"note3:{node['session']}:{end['sequence']}"
                        or not start["sequence"] < end["sequence"] <= light["sequence"]):
                    raise _SpaceEventRefused("event-identity-mismatch")
                if not 0 <= end_phone - start_phone <= event_validity:
                    raise _SpaceEventRefused("event-phone-span")
                if end_receipt < start_receipt:
                    raise _SpaceEventRefused("event-receipt-order")
                if any(value > now for value in (start_receipt, end_receipt)):
                    raise _SpaceEventRefused("event-window-future")
                buckets = tuple(math.floor(math.log2(1 + point["lux"])) for point in (start, end))
                if (buckets[0] == buckets[1] or type(event["bucket_from"]) is not int
                        or type(event["bucket_to"]) is not int
                        or buckets != (event["bucket_from"], event["bucket_to"])):
                    raise _SpaceEventRefused("event-bucket-mismatch")
                if any(now - value > event_validity for value in (start_receipt, end_receipt)):
                    raise _SpaceEventRefused("event-window-expired")
                row["event_id"] = event["event_id"]
                row["event_sequence"] = end["sequence"]
                row["event_sample"] = (
                    f"historical measured light change #{start['sequence']} {start['lux']:g}lux"
                    f" -> #{end['sequence']} {end['lux']:g}lux; "
                    f"phone={_space_stamp(start_phone)}->{_space_stamp(end_phone)}; "
                    f"fresh-clock-conditional provenance={node['source']} session={node['session']}")
            except _SpaceEventRefused as refused:
                row["event_reason"] = refused.reason
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                row["event_reason"] = "event-malformed-shape"
        return row
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return unknown("malformed-boundary")


def _space_light_frozen(row: dict, previous: dict | None, now: float) -> bool:
    """Whether a null sample has persisted as a sustained source freeze.

    A seconds-wide dropout resolves between two reads, so the next read of the
    same stream publishes a fresh sample; only a light channel that has stopped
    keeps the same absent sequence across reads. The streak identity is the
    light channel's own ``(session, sequence)``: the node cursor advances on any
    delivered record, including malformed or non-light lines, so it cannot tell
    a live light channel from a stopped one and is not part of the identity. The
    comparison is against the retained origin of the reader's own current null
    streak, never against publication or polling activity, which cannot renew a
    sample.
    """
    if row.get("reason") != "light-null-gap" or not isinstance(previous, dict):
        return False
    if previous.get("reason") != "light-null-gap":
        return False
    nodes = (row.get("boundary") or {}).get("nodes") or [{}]
    node = nodes[0] if nodes else {}
    session = (row.get("provenance") or {}).get("session")
    try:
        interval = now - float(previous.get("read_epoch_s"))
    except (TypeError, ValueError):
        return False
    return (previous.get("session") == session
            and previous.get("sequence") == (node.get("light") or {}).get("sequence")
            and interval >= SPACE_FROZEN_MIN_SECONDS)


def _space_light_stream_record(home: Path) -> dict | None:
    """The reader's own previous null observation, or None.

    A missing or unreadable record means the duration class cannot be judged, so
    the reading keeps the single-read name rather than claiming a freeze.
    """
    try:
        text = (home / "discovery" / "space-light-stream.json").read_text(encoding="utf-8")
    except OSError:
        return None
    return space_text.decode_stream_record(text)


def _space_light_name_freeze(home: Path, row: dict, now: float) -> dict:
    """Name a sustained freeze in place; the age detail of the gap is kept."""
    previous = _space_light_stream_record(home)
    if not _space_light_frozen(row, previous, now):
        return row
    light = row["boundary"]["nodes"][0]["light"]
    detail = _space_light_age(light, now)
    row.update(reason="light-stream-frozen",
               sample="Note3 light UNKNOWN: light-stream-frozen"
                      + (f" {detail}" if detail else "")
                      + f"; provenance={SPACE_SOURCE}")
    _space_light_record_freeze(home, row, previous, now)
    return row


def _space_light_identity(row: dict) -> tuple | None:
    """The streak identity a naming belongs to, or None if the row carries none.

    The identity is the light channel's own ``(session, sequence)`` — the pair
    the retention rule compares — so a null streak whose sequence advanced is a
    new streak rather than a longer freeze, and a second freeze instance re-arms
    the onset record. The node cursor is excluded: it advances on any delivered
    record, so a cursor-only advance during one stopped light channel must not
    split the streak.
    """
    provenance = row.get("provenance") or {}
    nodes = (row.get("boundary") or {}).get("nodes") or [{}]
    node = nodes[0] if nodes else {}
    session = provenance.get("session")
    sequence = (node.get("light") or {}).get("sequence")
    # The retention rule compares the identity with plain equality, so a JSON
    # number sequence must remain recordable; only values that cannot name a
    # freeze at all are rejected.
    if not isinstance(session, str) or not _space_number(sequence):
        return None
    return session, sequence


def _space_light_freeze_record(home: Path) -> dict | None:
    """The reader's own freeze onset record, or None.

    A missing or unreadable record means no onset has been recorded, so the
    naming episode routes as a first onset rather than as a continuation.
    """
    try:
        text = (home / "discovery" / "space-light-freeze.json").read_text(encoding="utf-8")
    except OSError:
        return None
    return space_text.decode_stream_record(text)


def _space_light_append_freeze_event(home: Path, written: dict,
                                     superseded: object) -> None:
    """Append one onset event to the retained freeze log.

    The onset record names the current streak in place, so each new streak
    replaces the previous event's bytes with its own. This log keeps every
    onset: the entry is the onset record at its naming, plus the superseded
    record's last read when a previous freeze it replaces had been observed,
    so an earlier event's span stays visible after its record is gone. One
    line per onset — a continuing streak names again without a second line,
    and the log lives under the same publication lock as the record.

    The log is audit evidence, not a reading input, so a failing append must
    not fail the reading: the onset record and the routed event remain, and a
    later onset appends again.
    """
    entry = dict(written)
    if isinstance(superseded, dict):
        last_read = superseded.get("last_read_epoch_s")
        if _space_number(last_read):
            entry["superseded_last_read_epoch_s"] = last_read
    try:
        path = home / "discovery" / "space-light-freeze-history.jsonl"
        with path.open("a", encoding="utf-8") as log:
            log.write(space_text.encode_stream_record(entry))
    except OSError:
        # Losing an audit line must not lose the reading or the route; the next
        # onset appends again, and the record above is the naming's evidence.
        pass


def _space_light_record_freeze(home: Path, row: dict, previous: dict | None,
                               now: float) -> None:
    """Record a freeze onset once per streak and route it once.

    A sustained freeze is named on every read of the stopped stream, so the
    onset record is keyed by the streak identity. The first naming of a streak
    writes the onset epochs beside the retention record and routes one
    addressed event naming the class and the age token; later namings of the
    same streak only advance the last-read epoch. A recurrence then cannot end
    before the event is routed, and the detection interval is the naming
    cadence rather than the stored-scan cadence, whose gaps are unbounded.
    Each onset is also appended to the append-only
    ``space-light-freeze-history.jsonl`` log, because naming a new streak
    overwrites the previous onset record in place; the log is the retained
    audit trail of every event, one line per onset.

    Deciding, recording and routing share one publication lock: two panes can
    name the same streak in the same second, and the once-per-streak guarantee
    must not depend on which write lands first.
    """
    identity = _space_light_identity(row)
    if identity is None:
        return
    session, sequence = identity
    cursor = row["boundary"]["nodes"][0].get("cursor")
    onset = previous.get("read_epoch_s") if isinstance(previous, dict) else None
    detail = _space_light_age(row["boundary"]["nodes"][0]["light"], now)
    root = home / "discovery"
    with publication_lock(home):
        record = _space_light_freeze_record(home)
        continuing = (isinstance(record, dict)
                      and record.get("class") == "light-stream-frozen"
                      and record.get("session") == session
                      and record.get("sequence") == sequence)
        if continuing:
            record.update(last_read_epoch_s=now, age_token=detail)
            written = record
        else:
            written = {"class": "light-stream-frozen", "session": session,
                       "cursor": cursor, "sequence": sequence,
                       "onset_epoch_s": onset, "named_epoch_s": now,
                       "last_read_epoch_s": now, "age_token": detail}
        try:
            root.mkdir(parents=True, exist_ok=True)
            temporary = root / f".space-light-freeze-{os.getpid()}.tmp"
            temporary.write_text(space_text.encode_stream_record(written), encoding="utf-8")
            os.replace(temporary, root / "space-light-freeze.json")
        except OSError:
            # The reader's own note failing must not fail the reading; the naming
            # is already visible in the stored scan and the pane.
            return
        if continuing:
            return
        _space_light_append_freeze_event(home, written, record)

        def epoch_token(value: object) -> str:
            return _space_stamp(value) if _space_number(value) else "unrecorded"

        from . import wall
        body = (f"Note3 light-stream-frozen onset: session={session} cursor={cursor} "
                f"sequence={sequence}; first read={epoch_token(onset)}; "
                f"named={epoch_token(now)}; age token: {detail or 'unavailable'}. "
                "One event per streak at the naming cadence; the onset record is "
                "discovery/space-light-freeze.json and the class needs the same "
                "identity unmoving for a minute, so a seconds-wide dropout never "
                "routes.")
        try:
            wall.message(home, "discover", "research-methods", body)
        except (OSError, ValueError):
            # Routing failing leaves the onset record as the local evidence; the
            # next naming of a new streak routes again.
            pass


SPACE_CLOCK_NODES = ("note3", "redmi10")


def _space_clock_domains(data: object, now: float) -> list[dict]:
    """Read each node's clock-domain verdict without renewing its age."""
    nodes: dict[str, object] = {}
    names = list(SPACE_CLOCK_NODES)
    envelope_ok = (isinstance(data, dict)
                   and type(data.get("schema_version")) is int
                   and data.get("schema_version") == 1
                   and data.get("producer") == SPACE_PRODUCER
                   and isinstance(data.get("nodes"), list))
    if envelope_ok:
        for node in data["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("node"), str):
                envelope_ok = False
                continue
            name = node["node"]
            if name not in names:
                names.append(name)
            nodes[name] = node if name not in nodes else None

    rows = []
    for name in names:
        identity = f"sense.space.clock-domain.{name}"
        node = nodes.get(name)
        reading = (space_text.decode_clock_domain(node.get("clock_domain"))
                   if isinstance(node, dict) else None)
        state, reason = "unknown", "boundary-absent-or-invalid"
        detail = ""
        if not envelope_ok:
            reason = "boundary-absent-or-invalid"
        elif node is None:
            reason = "missing-or-duplicate-node"
        elif reading is None:
            reason = "malformed-clock-domain"
        elif reading["status"] != "ok":
            reason = reading["reason"] or f"clock-domain-{reading['status']}"
        else:
            nominal = reading["offset_nominal_s"]
            lower = reading["offset_min_s"]
            upper = reading["offset_max_s"]
            observed = reading["observed_at_epoch_s"]
            validity = reading["validity_seconds"]
            samples = reading["samples"]
            if (any(value is None for value in (nominal, lower, upper, observed, validity))
                    or validity <= 0 or samples <= 0):
                reason = "incomplete-clock-domain"
            elif lower > upper or not lower <= nominal <= upper:
                reason = "inconsistent-clock-domain-interval"
            elif observed > now:
                reason = "clock-domain-future"
            elif now - observed > validity:
                reason = "clock-domain-expired"
            else:
                state, reason = "verified", "clock-domain-current"
                detail = (f" offset={nominal:+.3f}s bounds=[{lower:+.3f},{upper:+.3f}]s"
                          f" age={_space_age(now - observed)} n={samples}"
                          f" validity={validity:g}s")
        if not detail:
            if reading is not None:
                observed = reading["observed_at_epoch_s"]
                validity = reading["validity_seconds"]
                if observed is not None:
                    detail += f" observed={_space_stamp(observed)}"
                    if observed <= now:
                        detail += f" age={_space_age(now - observed)}"
                if validity is not None:
                    detail += f" validity={validity:g}s"
                if reading["status"] == "ok":
                    detail += (f" nominal={reading['offset_nominal_s']}"
                               f" bounds=[{reading['offset_min_s']},{reading['offset_max_s']}]"
                               f" n={reading['samples']}")
                if reading["reason"]:
                    detail += f" producer_reason={reading['reason']}"
        rows.append({"id": identity, "kind": "read", "state": state,
                     "reason": reason,
                     "sample": f"{name} clock-domain {state.upper()}: {reason}"
                                f"{detail}; provenance={SPACE_PRODUCER['name']}"})
    return rows


def _space_clock_domains_read(home: Path) -> list[dict]:
    """Read the shared body boundary; absent or corrupt input stays UNKNOWN."""
    path = home / "body" / "space.json"
    text = _read(path, 65536)
    data = space_text.decode_space_boundary(text)
    return _space_clock_domains(data, time.time())


def _space_light_read(home: Path) -> dict:
    """Read the light boundary, naming a null read's duration class.

    A null read is classified against the reader's own retained record of the
    current null streak. The record holds the streak's *first* read, not the
    latest one: the pane renders every few seconds, so refreshing the origin
    on each render would keep the measured interval below the freeze
    threshold and the class could never fire on a live stream. A fresh sample
    leaves the record alone; a later null read with a new sequence starts a new
    record, since an advancing light channel cannot revisit an old identity. The
    node cursor is not part of that identity: it advances on any delivered
    record, so a cursor-only advance must not re-arm the streak's origin.
    """
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
    elif row.get("reason") == "light-null-gap":
        now = time.time()
        row = _space_light_name_freeze(home, row, now)
        node = row["boundary"]["nodes"][0]
        session = (row.get("provenance") or {}).get("session")
        sequence = node["light"].get("sequence")
        previous = _space_light_stream_record(home)
        continuing = (isinstance(previous, dict)
                      and previous.get("session") == session
                      and previous.get("sequence") == sequence)
        if not continuing:
            record = {"session": session, "cursor": node.get("cursor"),
                      "sequence": sequence,
                      "reason": "light-null-gap", "read_epoch_s": now}
            root = home / "discovery"
            try:
                root.mkdir(parents=True, exist_ok=True)
                temporary = root / f".space-light-stream-{os.getpid()}.tmp"
                temporary.write_text(space_text.encode_stream_record(record), encoding="utf-8")
                os.replace(temporary, root / "space-light-stream.json")
            except OSError:
                pass
    return row


def _space_new_event(previous: dict | None, current: dict) -> bool:
    """Baseline, restart and recovery seed history instead of replaying events."""
    return bool(previous and previous.get("state") == current.get("state") == "verified"
                and previous.get("provenance") == current.get("provenance")
                and previous.get("expires_epoch_s", -1) >= time.time()
                and current.get("event_id") and current["event_id"] != previous.get("event_id")
                and current.get("event_sequence", -1) > previous.get("sequence", -1))


def _switch_label(kind: str, verdict: str | None, unreadable: list[str]) -> str:
    """``blocked``/``unblocked``, or ``unread`` for a switch that would not read."""
    if verdict is not None:
        return verdict
    return "unread" if kind in unreadable else "absent"


def _ethernet_read() -> dict:
    """Sample this host's wired Ethernet interface state.

    Consumer: the operator and health, who must know whether the host's
    physical network link is available before diagnosing connectivity. A
    missing interface, an unreadable sysfs value stays UNKNOWN — absence and
    an unread file are not calm. An interface that is administratively down
    or has no carrier is a verified state, and the sample names it.

    ``operstate`` follows carrier: a cable that is plugged in reads ``up``,
    one that is unplugged reads ``down``. ``admin_up`` distinguishes an
    administratively disabled port from a physically disconnected one.
    ``speed`` is the negotiated link rate in Mb/s (e.g. 1000); ``carrier``
    is 1 when the link is up and 0 when it is down. An unreadable speed or
    carrier is a named problem, never a state.
    """
    interfaces = ethernet_interfaces()
    problems: list[str] = []
    if interfaces:
        states = []
        for name in interfaces:
            value = operstate(name)
            if value is None:
                problems.append(f"operstate-unread:{name}")
                states.append(f"{name}=unread")
                continue
            admin = admin_up(name)
            if admin is None:
                problems.append(f"flags-unreadable:{name}")
                states.append(f"{name}=unread")
                continue
            speed = link_speed(name)
            carrier = carrier_state(name)
            speed_text = f"{speed}Mb/s" if speed is not None else "unread"
            carrier_text = str(carrier) if carrier is not None else "unread"
            states.append(f"{name}={value}/admin-{'up' if admin else 'down'}/{speed_text}/carrier-{carrier_text}")
        sample = "ethernet " + ",".join(states)
    else:
        problems.append("no-ethernet-interface")
        sample = "ethernet absent"
    if problems:
        sample = f"ethernet UNKNOWN {','.join(problems)}; " + sample
    return {"id": "sense.net.ethernet", "kind": "read",
            "state": "unknown" if problems else "verified",
            "reason": ",".join(problems) if problems else "ethernet-state",
            "sample": sample,
            "consumer": "operator/network-health",
            "provenance": {"sysfs": [str(NET_ROOT)],
                           "timeout_seconds": COMMAND_TIMEOUT_SECONDS},
            "interfaces": interfaces}

def _tailnet_peer_state() -> tuple[dict | None, list[str]]:
    """Read the tailnet's own status report, or ``None`` with named problems.

    Shared by the peer-reachability sense and the carried-body peer/delivery
    composite, so one command answers both and they cannot disagree about the
    same instant.
    """
    problems: list[str] = []
    if shutil.which("tailscale") is None:
        problems.append("tailscale-absent")
        return None, problems
    try:
        result = subprocess.run(["tailscale", "status", "--json"],
                                capture_output=True, text=True,
                                timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        problems.append(f"tailscale-unreadable:{type(exc).__name__}")
        return None, problems
    if result.returncode != 0:
        problems.append(f"tailscale-exit-{result.returncode}")
        return None, problems
    data = decode_tailnet_status(result.stdout)
    if data is None:
        problems.append("tailscale-unreadable:decode")
        return None, problems
    return data, problems


def _tailnet_peer_read(data: dict | None = None,
                        problems: list[str] | None = None) -> dict:
    """Sample this host's tailnet peer status.

    Consumer: the operator and health, who must know whether tailnet-transported
    devices are reachable. A missing BackendState, a non-Running state, or a
    missing Self is a host-side UNKNOWN — the tailnet is not functional. A
    missing binary, a non-zero exit, or text that does not decode is a named
    problem, never a state.

    Per-peer: online/active/LastSeen. This sense reports tailnet reachability
    only; it does not distinguish device-unreachable from producer-silent.
    That joint relation is ``sense.body.redmi10.peer-delivery``, which crosses
    the peer state with the consumer's delivery cursor.

    ``data``/``problems`` carry a report the caller already read, so one
    command answers both senses and they cannot disagree about the instant.
    """
    if data is None and problems is None:
        data, problems = _tailnet_peer_state()
    problems = list(problems or [])

    if data is None:
        sample = "tailnet UNKNOWN " + ",".join(problems)
        return {"id": "sense.net.tailnet-peer", "kind": "read",
                "state": "unknown",
                "reason": ",".join(problems),
                "sample": sample,
                "consumer": "operator/network-health",
                "provenance": {"command": "tailscale status --json",
                               "timeout_seconds": COMMAND_TIMEOUT_SECONDS},
                "backend_state": None,
                "self_present": False,
                "peers": {}}

    backend_state = data.get("BackendState")
    self_present = data.get("Self") is not None

    if backend_state != "Running":
        problems.append(f"backend-state-{backend_state}")
    if not self_present:
        problems.append("self-missing")

    peers_data = data.get("Peer", {}) or {}
    peers = {}
    for peer_id, peer in peers_data.items():
        hostname = peer.get("HostName", "?")
        online = peer.get("Online", False)
        active = peer.get("Active", False)
        last_seen = peer.get("LastSeen", "")
        peers[hostname] = {"online": online, "active": active, "last_seen": last_seen}

    if problems:
        sample = f"tailnet UNKNOWN {','.join(problems)}"
        state = "unknown"
        reason = ",".join(problems)
    else:
        online_count = sum(1 for p in peers.values() if p["online"])
        offline_count = len(peers) - online_count
        peer_parts = []
        for name in sorted(peers):
            p = peers[name]
            peer_parts.append(f"{name}={'online' if p['online'] else 'offline'}"
                            f"/{'active' if p['active'] else 'inactive'}"
                            f"/{p['last_seen']}")
        sample = f"tailnet backend={backend_state} peers={len(peers)}" \
                 f" online={online_count} offline={offline_count} " \
                 + ",".join(peer_parts)
        state = "verified"
        reason = "tailnet-peer-state"

    return {"id": "sense.net.tailnet-peer", "kind": "read",
            "state": state,
            "reason": reason,
            "sample": sample,
            "consumer": "operator/network-health",
            "provenance": {"command": "tailscale status --json",
                           "timeout_seconds": COMMAND_TIMEOUT_SECONDS},
            "backend_state": backend_state,
            "self_present": self_present,
            "peers": peers}

def _host_boot_read(home: Path, now: float | None = None) -> dict:
    """Report this host's boot identity and whether it rebooted since the last scan.

    A reboot invalidates every since-boot sample — the kernel's boot id, the
    journal windows, the unit lifetimes — and leaves the previous scan
    describing a host that no longer exists. The reading names the current
    boot id and the boot instant, and compares the boot id with the previous
    scan's: a difference means the host rebooted between the two scans.

    The outage length is bounded, not measured. The host was up when the
    previous scan was taken and booted again at the boot instant, so the
    downtime is at most the distance between them; the exact length is not
    observable from a host that was off, and the reading says so instead of
    reporting a number it cannot support.

    The boot instant is ``now - uptime``. It is exact only while the wall
    clock has not stepped since the boot: a backward step makes the published
    bound an understatement rather than a true upper bound, so the reading
    names its derivation instead of presenting the stamp as measured.

    Consumer: operator and health mesh liveness. The absence of a previous
    scan leaves the reboot question UNKNOWN: "no earlier scan" is not "no
    reboot". A previous scan that exists but cannot be read is named apart
    from an absent one, because a wiped discovery directory and a damaged
    receipt are different faults.
    """
    state = host_boot.read_boot_state()
    problems = list(state["problems"])
    boot_id = state["boot_id"]
    uptime_s = state["uptime_s"]
    moment = now if now is not None else time.time()
    previous = host_boot.previous_scan(latest(home))

    boot_epoch = moment - uptime_s if uptime_s is not None else None
    booted_at = (datetime.fromtimestamp(boot_epoch, timezone.utc)
                 .isoformat(timespec="seconds").replace("+00:00", "Z")
                 if boot_epoch is not None else None)

    parts: list[str] = []
    if boot_id:
        parts.append(f"boot={boot_id[:8]}")
    if booted_at is not None:
        parts.append(f"booted={booted_at}")
        parts.append(f"uptime={int(uptime_s)}s")

    reboot: str
    previous_boot_id = previous.get("boot_id") if previous else None
    if previous is None:
        reboot = "unknown(no-prior-scan)"
        if (home / "discovery" / "latest.json").exists():
            problems.append("prior-scan-unreadable")
            reboot = "unknown(prior-scan-unreadable)"
        else:
            problems.append("no-prior-scan")
    elif not previous_boot_id or not boot_id:
        reboot = "unknown(no-comparable-boot-id)"
        problems.append("no-comparable-boot-id")
    elif previous_boot_id != boot_id:
        reboot = "yes"
        if previous.get("created"):
            parts.append(f"last-scan={previous['created']}")
        if (boot_epoch is not None
                and previous.get("acquisition_start_epoch") is not None):
            bound = boot_epoch - float(previous["acquisition_start_epoch"])
            if bound > 0:
                parts.append(f"outage<={int(bound)}s")
    else:
        reboot = "no"
    parts.append(f"reboot={reboot}")

    if problems:
        verdict = "unknown"
        reason = ",".join(problems)
        sample = f"host boot UNKNOWN {reason}; " + " ".join(parts)
    elif reboot == "yes":
        verdict = "drift"
        reason = "host-rebooted"
        sample = " ".join(parts)
    else:
        verdict = "verified"
        reason = "host-boot-state"
        sample = " ".join(parts)

    return {"id": "sense.host.boot", "kind": "read", "state": verdict,
            "reason": reason, "sample": sample,
            "consumer": "operator/host-liveness",
            "boot_id": boot_id, "uptime_s": uptime_s, "booted_at": booted_at,
            "booted_at_derivation": "now-uptime",
            "previous_boot_id": previous_boot_id, "reboot": reboot}


def _host_radio_read() -> dict:
    """Sample this host's BLE/Wi-Fi adapter state for the space programme.

    Consumer: the space-perception programme's host half, which must know whether
    the owned radio pair is present and usable before a bounded experiment. A
    missing adapter, an unreadable sysfs value or an unreadable controller stays
    UNKNOWN — absence, an unread file and a hang are not calm. A radio that is
    ``down`` or rfkill-blocked is a verified state, and the sample names it.

    ``adv-instances`` is BlueZ's advertising instance counter as ``active/total``:
    ``ActiveInstances`` counts registered advertisement clients and
    ``SupportedInstances`` is the number of *available* slots (the BlueZ 5.72
    ``org.bluez.LEAdvertisingManager`` documents "available"; its
    ``src/advertising.c`` computes ``max_ads - clients``), so the denominator is
    the controller's slot capacity and does not drop as clients register.
    Registration works unprivileged over the D-Bus manager route (senses wake
    38406); the raw-HCI path stays EPERM. The count is BlueZ userspace
    accounting, not proof of radiation and not a capability: a client that dies
    while registering leaves it standing with no advertisement object on the bus
    (2026-10-14: 1/4 at 06:28Z and 2/3 at 08:09Z, no live client), so the field
    must never be read as "advertising works" or "the host is radiating".

    A wireless interface reads as ``operstate/admin-state``. ``operstate``
    follows carrier, so a radio that is administratively up but unassociated
    reads ``down/admin-up`` while a radio that is switched off reads
    ``down/admin-down``; before the admin token both rendered ``=down`` and an
    enabled idle radio was indistinguishable from an absent one (discover, wake
    4454). An interface whose flags or operstate will not read is ``=unread``
    with a named problem, never a state.
    """
    controllers = bluetooth_controllers()
    interfaces = wireless_interfaces()
    switches = rfkill_switches()
    unreadable_switches = rfkill_unreadable()
    ble_switch = rfkill_verdict(switches, "bluetooth")
    wlan_switch = rfkill_verdict(switches, "wlan")
    problems: list[str] = []
    if controllers:
        controller = controller_state()
        if controller["error"]:
            problems.append(str(controller["error"]))
            ble = f"BLE {','.join(controllers)} powered=unread"
        else:
            ble = f"BLE {','.join(controllers)} powered={'yes' if controller['powered'] else 'no'}"
            advertising = controller["advertising"]
            active = advertising["active"] if advertising else None
            available = advertising["supported"] if advertising else None
            if active is not None and available is not None:
                ble += f" adv-instances={active}/{active + available}"
            elif controller["powered"]:
                ble += " adv-instances=unread"
    else:
        problems.append("no-bluetooth-controller")
        ble = "BLE absent"
    if ble_switch == "blocked":
        ble += " rfkill-blocked"
    if interfaces:
        states = []
        for name in interfaces:
            value = operstate(name)
            if value is None:
                problems.append(f"operstate-unread:{name}")
                states.append(f"{name}=unread")
                continue
            admin = admin_up(name)
            if admin is None:
                problems.append(f"flags-unreadable:{name}")
                states.append(f"{name}=unread")
                continue
            states.append(f"{name}={value}/admin-{'up' if admin else 'down'}")
        wifi = "wifi " + ",".join(states)
    else:
        problems.append("no-wifi-interface")
        wifi = "wifi absent"
    if wlan_switch == "blocked":
        wifi += " rfkill-blocked"
    if unreadable_switches:
        problems.append("rfkill-unreadable:" + ",".join(sorted(set(unreadable_switches))))
    rfkill = (f"rfkill bt={_switch_label('bluetooth', ble_switch, unreadable_switches)}"
              f" wlan={_switch_label('wlan', wlan_switch, unreadable_switches)}")
    sample = f"{ble}; {wifi}; {rfkill}"
    if problems:
        sample = f"host radio UNKNOWN {','.join(problems)}; " + sample
    return {"id": "sense.space.host-radio", "kind": "read",
            "state": "unknown" if problems else "verified",
            "reason": ",".join(problems) if problems else "host-radio-state",
            "sample": sample,
            "consumer": "space-perception/host-half",
            "provenance": {"sysfs": [str(BLUETOOTH_ROOT), str(RFKILL_ROOT), str(NET_ROOT)],
                           "command": "bluetoothctl show",
                           "timeout_seconds": COMMAND_TIMEOUT_SECONDS},
            "controllers": controllers, "wireless": interfaces}


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
    observed.append(_mind_watch_wait(home))
    observed.append(_mind_treadmill(home))
    observed.append(_mind_docs_treadmill(home))
    observed.append(_mind_loop_stall(home))
    observed.append(_mind_artifact_recurrence(home))
    observed.append(_ledger_delivery_invariant(home))
    observed.append(_ledger_dv_binding(home))
    observed.append(_ledger_evidence_binding(home))
    observed.append(_head_rewinds(home))
    observed.append(_disk_trend(home))
    observed.append(_space_light_read(home))
    observed.append(read_redmi10(home))
    observed.append(read_redmi10_light(home))
    observed.append(read_redmi10_delivery_gaps(home))
    observed.append(read_redmi10_dense_baseline(home))
    # The multi-body coverage reading crosses the bodies the space boundary
    # names, so the Note3-only selector cannot hide a live body from the
    # summary; it is taken after the per-body rows so the scan keeps the
    # per-body detail and adds the joint verdict beside it.
    observed.append(read_multibody_coverage(home))
    # The peer/delivery composite needs the tailnet report the peer sense also
    # reads, so both are taken once and share the same instant.
    peer_state, peer_problems = _tailnet_peer_state()
    observed.append(read_redmi10_peer_delivery(home, peer=peer_state))
    observed.append(_host_radio_read())
    observed.append(_host_boot_read(home))
    observed.append(_ethernet_read())
    observed.append(_tailnet_peer_read(peer_state, peer_problems))

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
    observed.append(_host_session_stability(home))
    observed.append(_host_server_keeper(home))
    observed.append(_host_session_restarts(home))
    roots, _coverage_failure = _service_import_roots(home)
    observed.append(_sensor_coverage(home, roots))
    observed.append(_renderer_coverage(home))
    observed.append(_runtime_drift_across_sites(home))
    observed.append(_runtime_pin_lag(home))
    observed.append(_repo_branch_inventory(home))
    observed.append(_repo_release_retention(home))
    observed.append(_ghost_site_homes(home))
    observed.append(_coord_dm_disposition_age(home))
    observed.append(_coord_authorship_fog(home))
    observed.append(_repo_landing_block(home))
    observed.append(_coord_gate_latency(home))
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
    def refreshed(item: dict[str, object]) -> dict[str, object]:
        if item.get("id") == "sense.space.light" and "boundary" in item:
            now = time.time()
            return _space_light_name_freeze(home, _space_light(item["boundary"], now), now)
        return item
    readings = [refreshed(item) for item in readings]
    available = [str(item["id"]).removeprefix("command.") for item in readings
                 if item["kind"] == "declaration" and item["state"] == "available"]
    unavailable = [str(item["id"]).removeprefix("command.") for item in readings
                   if item["kind"] == "declaration" and item["state"] == "unavailable"]
    verified = [item for item in readings if item["kind"] != "declaration" and item["state"] == "verified"]
    nonverified = [item for item in readings if item["kind"] != "declaration" and item["state"] != "verified"]
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
            elif identifier == "sense.coord.gate-latency":
                # The humanized age in `oldest=<role>:<age>` advances every
                # scan; identity uses the structured stall count and the
                # oldest role name, so an unchanged condition stops
                # re-notifying while a real change still surfaces. The
                # stored sample keeps the humanized age for readers. Slicing
                # only, so discovery stays outside the parsing boundary.
                _oldest = str(item.get("oldest_open", ""))
                _sep = _oldest.find(":")
                _role = _oldest if _sep < 0 else _oldest[:_sep]
                detail = json.dumps((item.get("stall"), _role), sort_keys=True)
            elif identifier == "sense.site.ghost-homes" and "ghosts" in item:
                # The humanized `newest=<name> age=<age>` in the sample and the
                # structured `age_s` advance every scan, so an unchanged ghost
                # re-announced itself as a material change for hours (wake
                # 59621: two consecutive real scans 84 s apart differed only in
                # `age=10h54m` -> `age=10h56m`). Identity uses the ghost set,
                # holders, counts and the newest ghost's mtime stamp, so the
                # drift stays visible in every scan's drift line while a ghost
                # appearing or leaving, or a holder or file count moving, still
                # surfaces. Stored and reader-facing data keep the age.
                _newest = item.get("newest")
                detail = json.dumps({
                    "ghosts": item.get("ghosts"),
                    "files": item.get("files"),
                    "total": item.get("total"),
                    "holding": item.get("holding"),
                    "empty": item.get("empty"),
                    "no_process": item.get("no_process"),
                    "nested": item.get("nested"),
                    "stray": item.get("stray"),
                    "stray_truncated": item.get("stray_truncated"),
                    "symlinked": item.get("symlinked"),
                    "roots_at_least": item.get("roots_at_least"),
                    "files_at_least": item.get("files_at_least"),
                    "newest": {str(name): (value.get("stamp")
                                           if isinstance(value, dict) else value)
                               for name, value in (_newest.items()
                                                   if isinstance(_newest, dict) else [])},
                }, sort_keys=True)
            elif identifier == "sense.ledger.dv-binding" and "records" in item:
                # `records=N bool_dv=M` are coverage tiers over the whole
                # ledger: they advance on every ledger write while the flagged
                # condition — the set of phases whose `delivery_verified=true`
                # records carry no `verification` dict — is unchanged, so a
                # count-only move re-announced the drift on every write (wake
                # 59805: records 101->102 and bool_dv 56->57 each emitted
                # "Material changes" with `unbound=applied` unchanged). Identity
                # slices the tier out of the sample: the displayed unbound phase
                # set (first three phases, matching the sample's own display
                # granularity) and the unreadable-store reason identify the
                # reading; the counts, the state and the reader-facing sample
                # keep their form. The structured `unbound` count prefixes the
                # identity so a 4th+ phase — hidden by the display cap — still
                # notifies (wake 60035: witness residual E). Slicing only, so
                # discovery stays outside the parsing boundary. A reading
                # without the structured tier keeps sample-text identity.
                _tier = f"records={item.get('records')} bool_dv={item.get('bool_dv')}"
                _detail = str(item["sample"]).replace(_tier, "")
                while len(_detail) > 0 and _detail[0] == " ":
                    _detail = _detail[1:]
                while len(_detail) > 0 and _detail[-1] == " ":
                    _detail = _detail[:-1]
                detail = f"unbound_n={item.get('unbound')} " + _detail
            elif (identifier == "sense.ledger.delivery-invariant"
                  and "records" in item and state != "verified"):
                # `records=N bool_dv=M` are coverage tiers over the whole
                # ledger; in the UNKNOWN branch they are partial reads of an
                # unreadable store and advance on every ledger write while the
                # flagged condition — the violation set — is unchanged.
                # Identity slices the tier out of the sample (same shape as
                # dv-binding, wake 60035). The violations and unreadable-store
                # reason identify the reading; the counts, the state and the
                # reader-facing sample keep their form.
                _tier = f"records={item.get('records')} bool_dv={item.get('bool_dv')}"
                _detail = str(item["sample"]).replace(_tier, "")
                while len(_detail) > 0 and _detail[0] == " ":
                    _detail = _detail[1:]
                while len(_detail) > 0 and _detail[-1] == " ":
                    _detail = _detail[:-1]
                detail = _detail
            elif item["kind"] == "declaration" or state != "verified":
                detail = sample_text
                if identifier == "sense.ledger.evidence-binding":
                    # Notification identity ignores the volatile read_at token
                    # anywhere in the sample; the token stays in stored/reader-
                    # facing data. Matched on a token boundary, so a reordered
                    # producer sample cannot drop other fields into the slot.
                    # Boundary-correct, position-independent removal of the
                    # volatile read_at token; keeps it out of identity while
                    # preserving it in stored/reader-facing data. Uses only
                    # slicing so discovery stays outside the parsing boundary.
                    _needle = "read_at="
                    _out = ""
                    _i = 0
                    _n = len(detail)
                    while True:
                        _idx = detail.find(_needle, _i)
                        if _idx < 0:
                            _out += detail[_i:]
                            break
                        _boundary = (_idx == 0 or detail[_idx - 1] == " ")
                        _end = detail.find(" ", _idx)
                        if _end < 0:
                            _end = _n
                        if _boundary and _end > _idx + len(_needle):
                            _start = _idx - 1 if (_idx > 0 and detail[_idx - 1] == " ") else _idx
                            _out += detail[_i:_start]
                            _i = _end
                        else:
                            _out += detail[_i:_idx + len(_needle)]
                            _i = _idx + len(_needle)
                    detail = _out
                    while len(detail) > 0 and detail[0] == " ":
                        detail = detail[1:]
                    while len(detail) > 0 and detail[-1] == " ":
                        detail = detail[:-1]
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
            elif (old_reading.get("reason"), new_reading.get("reason")) in (
                    ("light-null-gap", "light-stream-frozen"),
                    ("light-stream-frozen", "light-null-gap")):
                # The same absent coverage judged for longer is the reader's
                # duration class, not a light change and not a baseline shift.
                change_details.append(f"{name} gap duration class "
                                      f"{old_reading['reason']} -> "
                                      f"{new_reading['reason']}")
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
             "Verified readings: " + ("; ".join(f"{item['id']} = {item['sample']}" for item in verified) or "none") + "."] + [
        f"{state[:1].upper()}{state[1:]} readings: " +
        ("; ".join(f"{item['id']} — {item['sample']}" for item in nonverified
                   if str(item["state"]) == state) or "none") + "."
        for state in sorted({str(item["state"]) for item in nonverified})] + [
             (f"Full sample: {artifact.resolve()}." if artifact.exists()
              else f"Full sample: absent ({artifact.resolve()})."),
             "Next: senses should verify useful unknown or drift readings or record why the source is unavailable; "
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
