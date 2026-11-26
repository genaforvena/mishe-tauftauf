#!/usr/bin/env python3
"""Live window: how the home network and connection are doing, in plain words.

Reads the rows the `network-probe` duty appends to SITE/network/probe.jsonl and, read-only,
a few facts from the router over ssh (never writes there).  Anything not measured, or too old,
is shown as UNKNOWN.  The network mind improves this file.
"""
import ast
import fnmatch
import hashlib
import json
import os
import select
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_boundary as nb
import network_router  # client_id(), so a live client can be named from the recorded read

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
PROBE = SITE / "network" / "probe.jsonl"
SPARK = "▁▂▃▄▅▆▇█"
STALE_S = 300
ROUTER_EVERY_S = 60
SLOW_WEB_MS = 1500
SPIKE_WEB_MS = 1000  # a single probe over this is a spike worth counting beside the median
SLOW_DNS_MS = 500
SPEED_USUAL_RATIO = 1.10    # within 10% of the earlier nights' median counts as "about usual"
SPEED_BASELINE_NIGHTS = 14  # measured nights the SPEED and UPLOAD lines compare against

# "Was it worse than usual?" — a day baseline built only from probe rows already loaded for the window.
USUAL_MIN_SAMPLES = 120    # minimum web samples for a meaningful baseline
USUAL_RECENT = 10          # "now" is the median of the last ten web samples
USUAL_EXCLUDE = 60         # keep recent samples out of the baseline so a spike cannot inflate it
USUAL_BUCKETS = 40         # width of the trend sparkline
_router = {"at": 0.0, "text": None, "read_at": 0.0}
_wifi = {"at": 0.0, "text": None, "weak": False}
WEAK_WIFI_DBM = -75
WEAK_WIFI_NAMES = 3      # at most this many weak devices are named on the WI-FI line
REPO = Path(__file__).resolve().parent.parent
OWN_FILES = (Path(__file__).resolve(), Path(__file__).resolve().parent / "network_boundary.py")
OWN_GIT_PATHS = ("tools/network_view.py", "tools/network_boundary.py")
WINDOW_STATE = SITE / "network" / "window.json"
STARTED = time.time()
COPY_CHECK_TIMEOUT_S = 20   # a new copy that hangs at import must not freeze the redraw for long
LIVENESS_S = 1.0            # how long a launched copy must stay up before it may replace the pane
FIRST_FRAME_S = 5.0         # how long the launched copy may take to draw its first frame
EXIT_SETTLE_S = 0.5         # a copy that closed its stdout may still be exiting; do not call that a spin
REFUSED_CHARS = 120         # the reason a newer copy was not run, one line in the window
REFUSED = None              # (reason, when): the file changed but the new copy would not start


def own_stamp():
    """Modification stamps of this view's own code; a change means the running copy is stale."""
    try:
        return tuple(p.stat().st_mtime_ns for p in OWN_FILES)
    except OSError:
        return None


def code_fingerprint(paths=None):
    """Digest of this view's own code as it is on disk; a running copy whose digest differs is stale."""
    digest = hashlib.sha256()
    for p in (paths or OWN_FILES):
        digest.update(Path(p).read_bytes())
    return digest.hexdigest()


def code_version():
    """(short sha, commit time) of the last commit touching this view's own files; UNKNOWN when git cannot say."""
    try:
        r = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h %ct", "--", *OWN_GIT_PATHS],
                           capture_output=True, text=True, timeout=10)
        sha, ct = nb.decode_git_version(r.stdout)
        dirty = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", "HEAD", "--", *OWN_GIT_PATHS],
                               capture_output=True, timeout=10).returncode != 0
        return (sha + " + uncommitted edits" if dirty else sha), ct
    except (OSError, ValueError, subprocess.SubprocessError):
        return "UNKNOWN", None


VERSION = code_version()
VERSION_AT = time.time()
STAMP = own_stamp()
# The digest of the code this process really imported, taken once and never recomputed.  An edit the
# reload gate refuses leaves the old copy running (REFUSED) while the disk holds bytes this process is
# not running; re-reading the files in report_window_state() would then record the refused file's own
# digest, and the window check would compare it with that same file and call a stale pane current.
LOADED_FINGERPRINT = code_fingerprint()


def report_window_state():
    """Record when this copy of the view started and which code it runs; the window duty reads it."""
    try:
        WINDOW_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = WINDOW_STATE.with_name("window.json.tmp")
        # "started" is the kernel start time of this pid: an os.execv reload keeps it, so the window check
        # still matches the live process.  "loaded" is when this copy of the code was imported, which is
        # what a reader needs to age a fingerprint mismatch; the fingerprint is that imported code.
        tmp.write_text(json.dumps({"pid": os.getpid(), "started": nb.process_start(os.getpid()),
                                   "loaded": STARTED, "code": VERSION[0],
                                   "fingerprint": LOADED_FINGERPRINT}))
        tmp.replace(WINDOW_STATE)
    except OSError:
        pass


def copy_starts(path=None):
    """Whether the copy of this view on disk can be imported, not merely compiled.

    A file can compile and still die while its top level runs: a name used before it is defined, a
    call that raises, a missing import.  Re-execing into such a copy kills the whole outer window,
    and only the five-minute check notices.  Run the new copy with its main block disabled in a
    short subprocess and keep the running copy when that fails.  Returns (ok, reason)."""
    path = Path(path or OWN_FILES[0])
    probe = "import runpy, sys; runpy.run_path(sys.argv[1], run_name='reload-check')"
    try:
        r = subprocess.run([sys.executable, "-c", probe, str(path)],
                           capture_output=True, text=True, timeout=COPY_CHECK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"did not finish importing within {COPY_CHECK_TIMEOUT_S} s"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if r.returncode == 0:
        return True, ""
    lines = [line for line in (r.stderr or "").splitlines() if line.strip()]
    return False, " ".join((lines[-1] if lines else f"exit {r.returncode}").split())


def first_frame_seen(proc):
    """Whether the launched copy has written its window header within FIRST_FRAME_S.

    Liveness alone cannot tell a view from a truncated loop that spins without drawing: both stay
    up.  The frame line is what a reader sees, so the copy must draw before it may replace the pane.
    """
    deadline = time.time() + FIRST_FRAME_S
    buf = b""
    while True:
        if b"MISHE NETWORK" in buf:
            return True
        if proc.poll() is not None:
            return False
        left = deadline - time.time()
        if left <= 0:
            return False
        ready, _, _ = select.select([proc.stdout], [], [], min(0.05, left))
        if not ready:
            continue
        chunk = os.read(proc.stdout.fileno(), 4096)
        if not chunk:
            return False
        buf += chunk


def last_stderr_line(proc):
    """The copy's own last error line, so a refusal says what failed rather than only its shape."""
    lines = [line.strip() for line in proc.stderr.read().decode("utf-8", "replace").splitlines()
             if line.strip()]
    return lines[-1] if lines else ""


def copy_survives(path=None):
    """Whether the copy, launched as the window launches it, draws a frame and keeps drawing.

    The main-block check above cannot see a save cut *inside* the guard after a complete statement:
    that copy still has the guard, imports cleanly, prints its frame and exits, so the re-exec would
    leave no window (2026-11-18, the same class).  The copy is started the way os.execv starts it —
    no arguments, so it enters its redraw loop — with a throwaway site home so the probe's window
    state cannot overwrite the live pane's.  It must write its first frame and still be running
    LIVENESS_S later; one frame then an exit, a silent spin, or no frame at all keeps the old pane.
    Returns (ok, reason)."""
    path = Path(path or OWN_FILES[0])
    with tempfile.TemporaryDirectory(prefix="mishe-reload-check-") as home:
        env = dict(os.environ, MISHE_TAUFTAUF_HOME=home)
        env.pop("MISHE_SEED_HOME", None)  # the site home comes from one variable, not a stale other one
        try:
            proc = subprocess.Popen([sys.executable, str(path)], cwd=str(path.parent),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        try:
            if not first_frame_seen(proc):
                # A copy that closed its stdout but has not been reaped yet looks alive for a
                # moment; settle that brief exit before calling it a silent spin.
                try:
                    proc.wait(timeout=EXIT_SETTLE_S)
                except subprocess.TimeoutExpired:
                    return False, f"the copy spins without drawing a frame within {FIRST_FRAME_S:g} s"
                reason = "the copy exits at once when it is run instead of drawing"
                tail = last_stderr_line(proc)
                return False, f"{reason}: {tail}" if tail else f"{reason} (exit {proc.returncode})"
            try:
                proc.wait(timeout=LIVENESS_S)
            except subprocess.TimeoutExpired:
                return True, ""
            reason = "the copy draws one frame and exits"
            tail = last_stderr_line(proc)
            return False, f"{reason}: {tail}" if tail else f"{reason} (exit {proc.returncode})"
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()


def main_block_present(tree):
    """Whether the module still ends in an `if __name__ == "__main__":` guard with a body.

    A save cut at any line before that guard (or an emptied file) compiles and imports as a plain
    module, so the import probe alone would let it replace the window with a copy that exits at
    once.  The guard is what the re-exec runs."""
    for node in tree.body:
        if not isinstance(node, ast.If) or not node.body:
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        values = {n.value for n in ast.walk(node.test) if isinstance(n, ast.Constant)}
        if "__name__" in names and "__main__" in values:
            return True
    return False


def maybe_reexec(stamp):
    """Re-run this file in place when its own code changed on disk and the new copy starts.

    A copy that does not compile, does not start, or has lost its main block is left running, the
    reason is shown in the window (REFUSED) and the check is retried on the next redraw.  Returns
    the stamp to compare against next time."""
    global REFUSED
    now = own_stamp()
    if now is None or stamp is None or now == stamp:
        REFUSED = None
        return stamp
    try:
        tree = compile(OWN_FILES[0].read_text(), str(OWN_FILES[0]), "exec", ast.PyCF_ONLY_AST)
    except (OSError, SyntaxError, ValueError) as exc:
        REFUSED = (f"{type(exc).__name__}: {exc}", time.time())
        return stamp
    ok, why = copy_starts()
    if not ok:
        REFUSED = (why, time.time())
        return stamp
    if not main_block_present(tree):
        REFUSED = ("the file stops before its main block, so the copy would exit instead of drawing", time.time())
        return stamp
    ok, why = copy_survives()
    if not ok:
        REFUSED = (why, time.time())
        return stamp
    REFUSED = None
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(OWN_FILES[0]), *sys.argv[1:]])


def refused_line():
    """One line for the window when the copy on disk would not start; '' when it started (or none new)."""
    if not REFUSED:
        return ""
    text = " ".join(str(REFUSED[0]).split())
    if len(text) > REFUSED_CHARS:
        text = text[:REFUSED_CHARS - 1] + "…"
    return f"  EDIT REFUSED   the file on disk does not start ({text}); still running the copy above"


def rows(limit=1440):
    try:
        return nb.decode_rows(PROBE.read_text(), limit)
    except OSError:
        return []


def nb_speed_rows():
    import network_speed
    return network_speed.read_rows(SITE / "network" / network_speed.LOG_NAME)


def nb_speed_up_rows():
    import network_speed
    return network_speed.read_rows(SITE / "network" / network_speed.UP_LOG_NAME)


def age(row):
    return nb.row_age_seconds(row)


def spark(values, width=40):
    values = [v for v in values if v is not None][-width:]
    if not values:
        return "(no data)"
    hi = max(values) or 1
    return "".join(SPARK[min(7, int(v / hi * 7.999))] for v in values)


def web_ms(row, name):
    """Total time of one secure web check in this row, or None when it did not load (or was not measured)."""
    check = ((row or {}).get("internet") or {}).get(name)
    return check.get("total_ms") if isinstance(check, dict) and check.get("ok") else None


def verdict(row, a):
    if row is None or a is None or a > STALE_S:
        return "UNKNOWN (no fresh probe)"
    if "internet" not in row:
        return "UNKNOWN (probe is from before the secure web check)"
    gw = row.get("gateway")
    if gw is None or gw["loss_pct"] >= 50:
        return "DOWN inside the home (router/LAN not answering)"
    if not any(web_ms(row, n) is not None for n in row["internet"]):
        return "DOWN: router answers, but no secure website does"
    if gw["loss_pct"] > 0:
        return "DEGRADED: loss to the router"
    best = min(v for v in (web_ms(row, n) for n in row["internet"]) if v is not None)
    if best > SLOW_WEB_MS or (row.get("dns_ms") or 0) > SLOW_DNS_MS:
        return "DEGRADED: slow " + ("DNS" if (row.get("dns_ms") or 0) > SLOW_DNS_MS else "web pages")
    return "OK"


def outage_episodes(data):
    """(start, end) of each run of DOWN probes, oldest first; end is None while still down.

    A probe row without the secure-web section (from before that check) is skipped, not counted as calm.
    """
    episodes, open_start = [], None
    for r in data:
        if "internet" not in r or not r.get("at"):
            continue
        down = nb.is_down(verdict(r, 0))
        if down and open_start is None:
            open_start = r["at"]
        elif not down and open_start is not None:
            episodes.append((open_start, r["at"]))
            open_start = None
    if open_start is not None:
        episodes.append((open_start, None))
    return episodes


def at_short(at):
    """`11-14 16:54Z`: month, day and time in UTC, so a row days old is not read as hours old."""
    text = str(at or "")
    if len(text) >= 16 and text[4] == "-" and text[10] == "T":
        return f"{text[5:10]} {text[11:16]}Z"
    return text


def covered(seconds):
    """A span in the unit a reader would use for it: `45 min`, `9.1 h`, `3.4 days`."""
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def outage_lines(data, paused=None):
    """The outage list for the window: when the home internet was DOWN in the probes we hold.

    `paused(a, b)` names a containment pause that explains a silence (see containment_pause).
    """
    seen = [r for r in data if "internet" in r and r.get("at")]
    if not seen:
        return ["  UNKNOWN (no secure-web probes to read outages from)"]
    first, last = seen[0]["at"], seen[-1]["at"]
    begin, finish = nb.utc_epoch(first), nb.utc_epoch(last)
    stretch = (f" in {covered(finish - begin)}"
               if begin is not None and finish is not None and finish >= begin else "")
    span = f"over the last {len(seen)} probes{stretch} ({at_short(first)} to {at_short(last)})"
    episodes = outage_episodes(data)
    gaps = probe_gaps(seen, paused)
    if not episodes and not gaps:
        return [f"  none {span}"]
    timed = []
    for start, end in episodes:
        if end is None:
            timed.append((nb.utc_epoch(start), f"  DOWN since {at_short(start)}, still down at the last probe"))
        else:
            # always dated: a same-day clock range without the date reads as today's outage
            same_day = at_short(start)[:5] == at_short(end)[:5]
            when = (f"{at_short(start)} to {end[11:16]}Z" if same_day
                    else f"{at_short(start)} to {at_short(end)}")
            mins = (nb.utc_epoch(end) - nb.utc_epoch(start)) / 60
            timed.append((nb.utc_epoch(start), f"  DOWN {when} ({mins:.0f} min)"))
    timed.extend(gaps)
    out = [line for _, line in sorted(timed, key=lambda item: item[0] or 0)]
    return out + [f"  ({span})"]


PROBE_GAP_S = 900  # the probe runs every 3 min; a silence this long is a gap, never a calm stretch


def probe_gaps(seen, paused=None):
    """(epoch, line) for each silence of more than PROBE_GAP_S between two secure-web probe rows.

    Probes that never ran say nothing about the internet, so an outage list that only names DOWN
    runs would read a multi-hour silence as calm. Each gap is named as UNKNOWN instead. When
    `paused(a, b)` returns (start_epoch, end_epoch) of a host containment pause inside the gap, the
    line names that pause: the producers were held back by the guard, not by the router or probe.
    """
    gaps = []
    for before, after in zip(seen, seen[1:]):
        try:
            a, b = nb.utc_epoch(before["at"]), nb.utc_epoch(after["at"])
        except ValueError:
            continue
        if a is not None and b is not None and b - a > PROBE_GAP_S:
            span = f"{at_short(before['at'])} to {at_short(after['at'])} ({(b - a) / 60:.0f} min)"
            pause = paused(a, b) if paused is not None else None
            if pause:
                gaps.append((a, f"  NO PROBES {span}: host paused by containment trip "
                                f"{hhmm(pause[0])}Z to {hhmm(pause[1])}Z, UNKNOWN not calm"))
            else:
                gaps.append((a, f"  NO PROBES {span}: UNKNOWN, not calm"))
    return gaps


def hhmm(epoch):
    return time.strftime("%H:%M", time.gmtime(epoch))


CONTAINMENT_SKIP = "ConditionPathExists=!/var/lib/mishe-containment/tripped"
PROBE_UNITS = ("mishe-producer-network-probe", "mishe-producer-network-devices")
_pause_cache = {}


def containment_pause(a, b):
    """(start, end) epochs of the containment skips of the network producers inside [a, b], or None.

    Read from the journal, where systemd logs each producer skipped by its unmet condition check,
    not from the trip file (the trip file can be gone by the time the gap is drawn). A failed or
    timed-out read is None, so the gap stays plain UNKNOWN rather than being explained away.
    """
    key = (int(a), int(b))
    if key in _pause_cache:
        return _pause_cache[key]
    found = None
    try:
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(a - 60))
        until = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(b + 60))
        run = subprocess.run(["nice", "-n", "10", "journalctl", "--since", since, "--until", until,
                              "--no-pager", "-o", "short-iso"],
                             capture_output=True, text=True, timeout=8)
        if run.returncode == 0:
            for line in run.stdout.splitlines():
                if CONTAINMENT_SKIP not in line or not any(u in line for u in PROBE_UNITS):
                    continue
                stamp = nb.utc_epoch(line[:25].replace("+00:00", "Z")) if len(line) > 25 else None
                if stamp is None or not (a <= stamp <= b):
                    continue
                found = (stamp, stamp) if found is None else (found[0], stamp)
    except (OSError, subprocess.SubprocessError, ValueError):
        found = None
    _pause_cache[key] = found
    return found


def router_facts():
    """The live router line with the read's age, or UNKNOWN with its age when the read failed.

    Cached for ROUTER_EVERY_S, including a failed read so a dead SSH path is not retried every
    redraw. The age is computed at call time, so a cached line does not freeze at "0 s ago".
    """
    now = time.time()
    if now - _router["at"] >= ROUTER_EVERY_S:
        _router["at"] = now
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router",
                                "cut -d' ' -f1 /proc/uptime; cut -d' ' -f1-3 /proc/loadavg; "
                                "ubus call network.interface.wan status | grep -E '\"up\"|\"uptime\"' | tr -d ' \\n'"],
                               capture_output=True, text=True, timeout=12)
            days, load, wan = nb.decode_router(r.stdout)
            loads = load.split()
            if len(loads) not in (3, 5):  # the live read cuts fields 1-3; accept a full loadavg line too
                raise ValueError("malformed loadavg")
            _router["text"] = (f"router up {days:.1f} days, load {' '.join(loads[:3])} (1/5/15 min), "
                               f"WAN {'up' if wan else 'UNKNOWN/down'}")
        except (OSError, ValueError, subprocess.SubprocessError):
            _router["text"] = None
        _router["read_at"] = time.time()
    text = _router["text"]
    age_txt = f"read {time.time() - _router.get('read_at', 0.0):.0f} s ago"
    return f"{text}; {age_txt}" if text else f"UNKNOWN (ssh read failed); {age_txt}"


def client_names():
    """id -> the name the household knows, from the recorded router reads (newest read wins)."""
    names = {}
    for row in reversed(router_rows()):
        for client in row.get("clients") or []:
            cid, name = client.get("id"), client.get("name")
            if cid and name and cid not in names:
                names[cid] = name
    return names


def wifi_facts():
    """(text, weak) for the home Wi-Fi from a read-only router listing; (None, False) when it cannot be read.

    The healthy line names only the weakest device. When any device is below WEAK_WIFI_DBM, the line
    names every weak device with its band (capped at WEAK_WIFI_NAMES), so a second weak client on the
    other band is not hidden behind the global minimum. Names come from the recorded router reads;
    the live listing supplies signal values and client counts.
    """
    if time.time() - _wifi["at"] < ROUTER_EVERY_S:
        return _wifi["text"], _wifi["weak"]
    _wifi["at"] = time.time()
    _wifi["text"], _wifi["weak"] = None, False
    cmd = ("for d in $(iwinfo | awk '/ESSID: \"/{print $1}'); do "
           "echo \"BAND $(iwinfo $d info | grep -o '[0-9.]* GHz' | head -1 | cut -d' ' -f1)\"; "
           "iwinfo $d assoclist; done")
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router", cmd],
                           capture_output=True, text=True, timeout=12)
        bands = nb.decode_wifi_ids(r.stdout, network_router.client_id)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, False
    names = client_names()
    parts, weakest, weakest_band, weakest_name = [], None, None, None
    weak_clients = []
    for band in sorted(set(bands) | set(nb.EXPECTED_BANDS)):
        clients = bands.get(band)
        if clients is None:  # the router has this band, the live listing did not show its radio
            parts.append(f"{band} GHz UNKNOWN")
            continue
        parts.append(f"{band} GHz {len(clients)}")
        if clients:
            cid, dbm = min(clients, key=lambda client: client[1])
            if weakest is None or dbm < weakest:
                weakest, weakest_band, weakest_name = dbm, band, names.get(cid)
            for cid, dbm in clients:
                if dbm < WEAK_WIFI_DBM:
                    weak_clients.append((dbm, band, names.get(cid)))
    total = sum(len(v) for v in bands.values())
    weak = bool(weak_clients)
    if weak:
        # "who is weak?" is plural: name every weak device, on its own band, not just the global
        # minimum. Cap the line so a suddenly crowded band cannot push the readings off the pane.
        weak_clients.sort(key=lambda item: item[0])
        listed = ", ".join(f"{(nm + ' ') if nm else ''}{dbm} dBm on {band} GHz"
                           for dbm, band, nm in weak_clients[:WEAK_WIFI_NAMES])
        if len(weak_clients) > WEAK_WIFI_NAMES:
            listed += f" (+{len(weak_clients) - WEAK_WIFI_NAMES} more)"
        tail = f", weak devices: {listed}"
    else:
        who = f"{weakest_name} " if weakest_name else ""
        tail = f", weakest {who}{weakest} dBm on {weakest_band} GHz" if weakest is not None else ""
    _wifi["text"] = f"{total} clients ({'; '.join(parts)}){tail}" + ("  WEAK SIGNAL" if weak else "")
    _wifi["weak"] = weak
    return _wifi["text"], weak


def wifi_line():
    """The WI-FI line: the live listing with its age (it is cached for ROUTER_EVERY_S), or UNKNOWN."""
    text, _weak = wifi_facts()
    if text is None:
        return "UNKNOWN (router Wi-Fi listing could not be read)"
    return f"{text}; read {time.time() - _wifi['at']:.0f} s ago"


def wifi_pane_rows(text, note, width=172):
    """Lay the WI-FI listing on pane rows; overflow continues on an indented row.

    Splitting on the listing's own commas keeps device names and "2.4 GHz" intact. A single clause
    wider than the first row falls back to a word wrap. The note reserves width on the first row.
    """
    first, indent = "  WI-FI         ", " " * 16
    budget = width - len(note)
    rows, current = [], first
    for clause in text.split(", "):
        piece = clause if current == first else ", " + clause
        if len(current) + len(piece) <= (budget if not rows else width):
            current += piece
        elif current == first:
            wrapped = textwrap.wrap(clause, width=budget - len(first), break_on_hyphens=False)
            rows.append(first + wrapped[0])
            current = indent + " ".join(wrapped[1:])
        else:
            rows.append(current + ",")
            current = indent + clause
    rows.append(current)
    rows[0] += note
    return rows


ROUTER_LOG = SITE / "network" / "router.jsonl"
STALE_ROUTER_S = 15 * 60  # the producer runs every 5 min; three missed runs is stale
# "Is the radio worse than usual?" — the last hour's share of failed transmit attempts against the
# median of the last 24 completed clock hours in the two-day retained reads.  A ratio alone would
# call noise on the quiet 5 GHz band an emergency, so a level must also clear a points-above floor.
WIFI_USUAL_HOURS = 24       # completed hours the usual share is taken from
WIFI_USUAL_MIN_HOURS = 3    # fewer complete hours than this and there is no usual yet
WIFI_WORSE_RATIO = 1.5      # 50% above the usual share ...
WIFI_WORSE_PP = 5.0         # ... and at least five points above it
WIFI_MUCH_RATIO = 2.0       # twice the usual share ...
WIFI_MUCH_PP = 10.0         # ... and at least ten points above it
WIFI_BASELINE_RUN_MIN = 4    # a run this long can capture a 24 h median; re-base on the earlier hours


def router_rows():
    """Rows the network-router duty wrote (failed runs included), oldest first."""
    try:
        text = ROUTER_LOG.read_text()
    except OSError:
        return []
    return nb.decode_router_rows(text)


FROZEN_READS = 6  # a client identical across this many good reads is worth a look


def frozen_clients(rows_, reads=FROZEN_READS):
    """Names listed by the router whose rx, tx and signal did not change across the last `reads`
    good reads, while at least one other client did. A stable link can look like this, so it is an
    observation for the wall, not a verdict; an idle or dead client shows the same numbers."""
    good = [r for r in rows_ if r.get("ok") and r.get("clients")][-reads:]
    if len(good) < reads:
        return []
    def key(c):
        return (c.get("rx"), c.get("tx"), c.get("dbm"))
    seen = {}
    for r in good:
        for c in r["clients"]:
            seen.setdefault(c.get("name", "?"), []).append(key(c))
    same = [n for n, ks in seen.items() if len(ks) == reads and len(set(ks)) == 1]
    moving = [n for n, ks in seen.items() if len(set(ks)) > 1]
    return sorted(same) if moving else []


def recorded_clients_line(now=None):
    """Client count, weakest device and last-hour trend from the recorded router reads."""
    now = time.time() if now is None else now
    rows_ = router_rows()
    if not rows_:
        return "UNKNOWN (no router reading recorded yet)"
    last = rows_[-1]
    ago = now - last["ts"]
    if not last.get("ok"):
        return f"UNKNOWN (last router read failed {ago:.0f} s ago: {last.get('error', 'no reason recorded')})"
    clients = last.get("clients", [])
    bands = {}
    for c in clients:
        bands[c.get("band", "?")] = bands.get(c.get("band", "?"), 0) + 1
    band_txt = "; ".join(f"{b} GHz {n}" for b, n in sorted(bands.items()))
    weakest = min(clients, key=lambda c: c.get("dbm", 0)) if clients else None
    weak_txt = f", weakest {weakest.get('name', '?')} {weakest.get('dbm')} dBm" if weakest else ""
    hour = [r for r in rows_ if now - r["ts"] <= 3600 and r.get("ok")]
    trend = spark([len(r.get("clients", [])) for r in hour]) if hour else "(no data)"
    stale = "  STALE" if ago > STALE_ROUTER_S else ""
    frozen = frozen_clients(rows_)
    frozen_txt = (f"\n                    unchanged over the last {FROZEN_READS} reads (rates and signal): "
                  + ", ".join(frozen)) if frozen else ""
    return (f"{len(clients)} clients ({band_txt}){weak_txt}; read {ago:.0f} s ago{stale}\n"
            f"                    clients over the last hour: {trend}{frozen_txt}")


def uplink_line(now=None):
    """WAN link errors and drops since the router booted, from the last recorded router read."""
    now = time.time() if now is None else now
    rows_ = router_rows()
    if not rows_:
        return "UNKNOWN (no router reading recorded yet)"
    last = rows_[-1]
    ago = now - last["ts"]
    if not last.get("ok"):
        return f"UNKNOWN (last router read failed {ago:.0f} s ago: {last.get('error', 'no reason recorded')})"
    up = last.get("uplink")
    if not up:
        return f"UNKNOWN (WAN counters not in the read {ago:.0f} s ago)"
    stale = "  STALE" if ago > STALE_ROUTER_S else ""
    return (f"{up['device']}: rx errors {up['rx_errors']}, dropped {up['rx_dropped']}; "
            f"tx errors {up['tx_errors']}, dropped {up['tx_dropped']} "
            f"(since router boot; read {ago:.0f} s ago{stale})")


def wan_link_line(now=None):
    """Physical WAN link from the last recorded router read: negotiated speed/duplex and carrier drops.

    The drop counter is the kernel's own since the router booted. The last-hour clause sums the counter's
    steps between the readings inside the hour, so a cable that dropped is visible even when the boot
    is old; a step backwards means the router rebooted (or the driver reset), and the drops since it
    are counted as a lower bound, never as "none".
    """
    now = time.time() if now is None else now
    rows_ = router_rows()
    if not rows_:
        return "UNKNOWN (no router reading recorded yet)"
    last = rows_[-1]
    ago = now - last["ts"]
    if not last.get("ok"):
        return f"UNKNOWN (last router read failed {ago:.0f} s ago: {last.get('error', 'no reason recorded')})"
    stale = "  STALE" if ago > STALE_ROUTER_S else ""
    link = last.get("wan_link")
    if not link:
        return f"UNKNOWN (the read {ago:.0f} s ago has no physical WAN link state{stale})"
    device = link.get("device") or "?"
    speed = link.get("speed_mbps")
    duplex = link.get("duplex")
    if speed is None:
        return f"UNKNOWN ({device} link speed not reported by the router; read {ago:.0f} s ago{stale})"
    link_txt = f"{speed} Mb/s" + (f" {duplex} duplex" if duplex in ("full", "half") else "")
    down = link.get("carrier_down")
    if not isinstance(down, int):
        return f"{device}: {link_txt}; carrier drops UNKNOWN (not in the read); read {ago:.0f} s ago{stale}"
    since_boot = f"link down {down} time{'s' if down != 1 else ''} since boot"
    uptime = last.get("uptime_s")
    if isinstance(uptime, (int, float)) and uptime > 0:
        since_boot += f" ({uptime / 86400:.1f} days)"
    hour = [r for r in rows_ if now - r["ts"] <= 3600 and r.get("ok")
            and isinstance((r.get("wan_link") or {}).get("carrier_down"), int)]
    if len(hour) >= 2:
        counts = [r["wan_link"]["carrier_down"] for r in hour]
        in_hour, restarted = nb.counter_steps(counts)
        if restarted and in_hour:
            hour_txt = f", at least {in_hour} in the last hour (counters reset by a reboot)"
        elif restarted:
            hour_txt = ", counters reset in the last hour (router reboot)"
        elif in_hour:
            hour_txt = f", {in_hour} in the last hour"
        else:
            hour_txt = ", none in the last hour"
    else:
        hour_txt = ", last-hour drops UNKNOWN"
    return f"{device}: {link_txt}; {since_boot}{hour_txt}; read {ago:.0f} s ago{stale}"


GUARD_TRIP_MARKER = "/var/lib/mishe-containment/tripped"


GUARD_TRIP_RECORD_GLOB = "tripped-*.json"  # the guard now writes one record per trip, not a bare marker
RECENT_TRIP_S = 6 * 3600  # a record older than this does not explain a current gap


def router_read_age_s(now=None):
    """Seconds since the newest router read was recorded, or None when no read is recorded."""
    rows_ = router_rows()
    if not rows_:
        return None
    return (time.time() if now is None else now) - rows_[-1]["ts"]


def guard_line(now=None, marker=GUARD_TRIP_MARKER, router_read_age=None):
    """Whether the host guard tripped recently: the usual reason a router read goes STALE.

    Two signs are read, both as plain stats: the bare marker file (older guard), and the newest
    `tripped-*.json` trip record beside it (current guard). A recent record is named as a trip even
    though the latch itself is not read here. When the router was read fresh since that record, the
    line says so: the units are running again, so the record is history, not a current hold.
    Any read error is UNKNOWN, never a calm clear.
    """
    now = time.time() if now is None else now
    try:
        mtime = os.stat(marker).st_mtime
        return (f"TRIPPED {(now - mtime) / 60:.0f} min ago: the guard holds back the router units "
                "and their readings go STALE until it is rearmed")
    except FileNotFoundError:
        pass
    except OSError as exc:
        return f"UNKNOWN (trip marker could not be read: {exc.strerror or type(exc).__name__})"
    # os.scandir, not Path.glob: on CPython 3.12 glob silently yields nothing when the directory
    # cannot be listed (PermissionError), which would read as a calm clear.
    try:
        newest = None
        with os.scandir(Path(marker).parent) as entries:
            for entry in entries:
                if not fnmatch.fnmatch(entry.name, GUARD_TRIP_RECORD_GLOB):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except FileNotFoundError:  # rotated away between listing and stat
                    continue
                newest = mtime if newest is None else max(newest, mtime)
    except OSError as exc:
        return f"UNKNOWN (trip records could not be read: {exc.strerror or type(exc).__name__})"
    if newest is None:
        return "clear (no trip marker or trip record; the router and window units may run)"
    age = now - newest
    if age < RECENT_TRIP_S:
        if router_read_age is not None and router_read_age < STALE_ROUTER_S:
            return (f"TRIP RECORD {age / 60:.0f} min ago, but the router was read "
                    f"{router_read_age:.0f} s ago, so the router units are running again "
                    "(the latch itself is not read here)")
        return (f"TRIP RECORD {age / 60:.0f} min ago (latch not read here): the guard may still hold "
                "back the router units, and their readings go STALE until it is rearmed")
    return f"clear (no trip marker; last trip record {age / 3600:.1f} h ago)"


EGRESS_STATE = SITE / "health" / "egress.json"
EGRESS_STALE_S = 900  # the egress duty runs every 5 minutes; 15 minutes without a write is stale


def plain_name(name):
    """A name with case, spaces and punctuation removed, for matching a duty's id to a router's name."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def wifi_presence(now=None, path=ROUTER_LOG):
    """plain_name -> dBm for each client in the newest recorded router read; {} when there is none.

    Only a fresh, successful read counts: a stale listing must not be used to explain why a client
    does not answer. A MAC is never involved; the router's own hostname is matched to the egress
    duty's client id, so `imac-rozalia` finds `iMac-Rozalia`.
    """
    now = time.time() if now is None else now
    try:
        rows_ = nb.decode_router_rows(path.read_text())
    except OSError:
        return {}
    if not rows_:
        return {}
    last = rows_[-1]
    if not last.get("ok") or not 0 <= now - last["ts"] <= STALE_ROUTER_S:
        return {}
    out = {}
    for client in last.get("clients") or []:
        name, dbm = client.get("name"), client.get("dbm")
        if isinstance(name, str) and isinstance(dbm, int):
            out.setdefault(plain_name(name), dbm)
    return out


def exit_line(now=None, path=EGRESS_STATE, presence=None):
    """EXIT NODE in plain words: each client's tailscale exit-node state from the egress duty's file.

    Read-only. A missing, unreadable or stale file is UNKNOWN, never a calm all-GREEN. A client that
    is still in the Wi-Fi listing while it does not answer is explained that way: the duty's ssh
    detail (`No route to host`) reads like a fault, but an associated client that is simply asleep
    looks the same, and the household sees its name in the Wi-Fi line right above.
    """
    now = time.time() if now is None else now
    try:
        mtime = os.stat(path).st_mtime
        with open(path, encoding="utf-8") as fh:
            results = json.load(fh).get("results") or []
    except FileNotFoundError:
        return "UNKNOWN (no exit-node reading yet: the egress duty has not written its file)"
    except (OSError, ValueError, AttributeError) as exc:
        return f"UNKNOWN (exit-node file could not be read: {type(exc).__name__})"
    age_min = (now - mtime) / 60
    if now - mtime > EGRESS_STALE_S:
        return f"UNKNOWN (last exit-node reading is {age_min:.0f} min old)"
    if not results:
        return f"UNKNOWN (exit-node reading is {age_min:.0f} min old and lists no clients)"
    states = []
    normalized = {}
    for r in results:
        name = str(r.get("client", "?"))[:24]
        state = str(r.get("state", "UNKNOWN"))
        if state not in ("GREEN", "RED"):
            state = "UNKNOWN"
        normalized[id(r)] = state
        states.append(f"{name} {state}")
    bad = [r for r in results if r.get("state") != "GREEN"]
    head = "all GREEN" if not bad else f"{len(bad)} of {len(results)} not GREEN"
    lines = [f"{head}: " + ", ".join(states) + f"; read {age_min:.0f} min ago"]
    wifi = (wifi_presence(now) if presence is None else presence) if bad else {}
    for r in bad[:REASON_CLIENTS]:
        name = str(r.get("client", "?"))[:24]
        dbm = wifi.get(plain_name(name))
        if dbm is not None:
            lines.append(" " * 17 + f"{name} {normalized[id(r)]}: still on Wi-Fi at {dbm} dBm; "
                                    "not answering ssh (it may be asleep)")
            continue
        why = first_reason(r)
        if why:
            lines.append(" " * 17 + f"{name} {normalized[id(r)]}: {why}")
    return "\n".join(lines)


REASON_CLIENTS = 3       # at most this many non-GREEN clients get their cause spelled out
REASON_CHARS = 70        # one cause per line, so the window keeps its width


def first_reason(result):
    """The producer's own words for a non-GREEN client, one line, bounded; '' when it gave none."""
    for reason in result.get("reasons") or []:
        text = " ".join(str(reason).split())
        if len(text) > REASON_CHARS:
            cut = text[:REASON_CHARS - 1]
            space = cut.rfind(" ")
            text = (cut[:space] if space > 0 else cut) + "…"
        if text:
            return text
    return ""


LOAD_LIMIT = nb.LOAD_LIMIT
MEM_FLOOR_MB = nb.MEM_FLOOR_MB


def router_health_line(now=None):
    """Router load and free RAM from the newest recorded read, with the self-limit it is held to and,
    when it is over that limit, how long the breach has lasted against the 30-minute alarm."""
    now = time.time() if now is None else now
    rows_ = router_rows()
    if not rows_:
        return "UNKNOWN (no router reading recorded yet)"
    last = rows_[-1]
    ago = now - last["ts"]
    sample = nb.load_run(rows_, now)
    if not last.get("ok"):
        reason = str(last.get("error", "no reason recorded"))
        if reason.startswith("skipped:") and sample is not None:
            # A busy-router hold is not a failed probe: say when the next read is due, not just "failed".
            due = time.strftime("%H:%M", time.gmtime(now - sample["ago_s"] + network_router.COOLDOWN_S))
            line = f"UNKNOWN (router busy, no read held until about {due} UTC: {reason})"
        else:
            line = f"UNKNOWN (last router read failed {ago:.0f} s ago: {reason})"
        if sample is not None:
            line += (f"; last real load {sample['load']:.2f} (5 min), free RAM {sample['mem_mb']:.0f} MB, "
                     f"read {sample['ago_s'] / 60:.0f} min ago")
            if sample["over"]:
                line += f", over the self-limit for {sample['span_s'] / 60:.0f} min"
        return line
    if sample is None:
        return f"UNKNOWN (load or free RAM not in the read {ago:.0f} s ago)"
    age = sample["ago_s"]
    limit_txt = "within the self-limit"
    if sample["over"]:
        limit_txt = f"over the self-limit for {sample['span_s'] / 60:.0f} min"
        if sample["span_s"] < nb.LOAD_ALARM_SPAN_S:
            limit_txt += f", alarm at {nb.LOAD_ALARM_SPAN_S // 60} min"
    stale = "  STALE" if age > STALE_ROUTER_S else ""
    return (f"load {sample['load']:.2f} (5 min), free RAM {sample['mem_mb']:.0f} MB, {limit_txt} "
            f"(limit: load {LOAD_LIMIT:g}, free RAM {MEM_FLOOR_MB:.0f} MB); read {age:.0f} s ago{stale}")


DEVICES_LOG = SITE / "network" / "devices.jsonl"


def devices_line(now=None):
    """Show the newest per-device average, loss and age, or why the reading is UNKNOWN."""
    now = time.time() if now is None else now
    try:
        rows = nb.read_device_ping_rows(DEVICES_LOG)
    except FileNotFoundError:
        return "UNKNOWN (no per-device ping reading yet)"
    except (OSError, ValueError):
        return "UNKNOWN (per-device ping log is unreadable)"
    devices, age, problem = nb.device_ping_sample(rows, now)
    if problem:
        return f"UNKNOWN ({problem})"

    parts = []
    unknown = False
    try:
        frozen = set(frozen_clients(router_rows()))
    except Exception:  # the router log is optional here; a missing read just adds no link
        frozen = set()
    for name, result in sorted(devices.items()):
        problem = nb.device_ping_plain_problem(result)
        if problem:
            if isinstance(result, dict) and result.get("wifi") == "ASSOCIATED":
                problem += ", still on Wi-Fi"
                if name in frozen:
                    problem += (f", router counters unchanged over the last {FROZEN_READS} reads"
                                " (associated but not answering)")
            parts.append(f"{name} {problem}")
            unknown = True
        else:
            parts.append(f"{name} {result['avg_ms']:.0f} ms avg, {result['loss_pct']:.0f}% loss")
    status = "UNKNOWN: " if unknown else ""
    history = [h for name in sorted(devices)
               if (h := nb.device_outage_phrase(rows, name, now, paused=containment_pause))]
    hist_txt = f"; {'; '.join(history)}" if history else ""
    return status + "; ".join(parts) + hist_txt + f"; read {age / 60:.0f} min ago"


def _data_size(n):
    """Bytes in plain words (B/KB/MB/GB), for traffic amounts a person can judge at a glance."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


def tunnel_traffic(reads, last):
    """Bytes the tunnel moved since the previous read that carried counters, in plain words.

    Process and link state cannot tell a carrying tunnel from one that routes nothing, so the kernel's
    own tun0 byte counters are differenced. Missing counters, or no earlier counter reading, is
    UNKNOWN, never "no traffic"; a negative delta is a recreated tun0, not a rate.
    """
    v = last.get("vpn") or {}
    rx, tx = v.get("tun0_rx_bytes"), v.get("tun0_tx_bytes")
    if not isinstance(rx, int) or not isinstance(tx, int):
        return "tun0 traffic UNKNOWN (counters not in the read)"
    prev = None
    for row in reversed(reads[:-1]):
        p = row.get("vpn") or {}
        if isinstance(p.get("tun0_rx_bytes"), int) and isinstance(p.get("tun0_tx_bytes"), int):
            prev = row
            break
    if prev is None:
        return "tun0 traffic UNKNOWN (no earlier counter read)"
    span = last["ts"] - prev["ts"]
    if span <= 0:
        return "tun0 traffic UNKNOWN (the two reads share a time)"
    p = prev["vpn"]
    moved = (rx - p["tun0_rx_bytes"]) + (tx - p["tun0_tx_bytes"])
    if moved < 0:
        return "tun0 counters restarted (tun0 recreated since the previous read)"
    when = f"{span:.0f} s" if span < 90 else f"{span / 60:.0f} min"
    if moved == 0:
        return f"nothing through tun0 in {when}"
    return f"{_data_size(moved)} through tun0 in {when}"


def vpn_line(now=None):
    """VPN (sing-box on the router) in plain words, from the newest router read that carried it.

    "Running" and "tun0 up" say the tunnel process and link exist; the byte counters say whether it
    carried anything since the previous read. Neither says which clients use it. No VPN reading at
    all, or an unreadable process list, is UNKNOWN, never a calm answer.
    """
    now = time.time() if now is None else now
    reads = [r for r in router_rows() if r.get("ok") and isinstance(r.get("vpn"), dict)]
    if not reads:
        return "UNKNOWN (no VPN reading recorded yet)"
    last = reads[-1]
    ago = now - last["ts"]
    v = last["vpn"]
    if not v.get("ps_ok"):
        proc = "sing-box UNKNOWN (process list not read)"
    else:
        proc = "sing-box running" if v.get("sing_box") else "sing-box NOT running"
    tun = {"up": "tun0 up", "down": "tun0 DOWN", "absent": "tun0 absent"}.get(v.get("tun0"), "tun0 UNKNOWN")
    moved = tunnel_traffic(reads, last) if v.get("tun0") == "up" else None
    traffic = f"; {moved}" if moved else ""
    stale = "  STALE" if ago > STALE_ROUTER_S else ""
    return f"{proc}, {tun} on the router{traffic}; read {ago:.0f} s ago{stale}"


def wifi_radio_line(now=None):
    """Wi-Fi channel and noise floor from the newest recorded router read.

    A read with no radio info is UNKNOWN, never a quiet channel; an old read is STALE. A band the
    router has (``nb.EXPECTED_BANDS``) but the newest read does not carry is named UNKNOWN, so a
    silent radio never looks like a house with one band.
    """
    now = time.time() if now is None else now
    rows_ = router_rows()
    if not rows_:
        return "UNKNOWN (no router reading recorded yet)"
    reads = [r for r in rows_ if r.get("ok") and r.get("radios")]
    if not reads:
        return "UNKNOWN (Wi-Fi channel/noise not in the router reads)"
    last = reads[-1]
    ago = now - last["ts"]
    parts = []
    for radio in last["radios"]:
        if not isinstance(radio, dict):
            parts.append("UNKNOWN")
            continue
        band = radio.get("band", "?")
        ch, freq, noise = radio.get("channel"), radio.get("freq_ghz"), radio.get("noise_dbm")
        if ch is None or freq is None:
            parts.append(f"{band} GHz channel UNKNOWN")
            continue
        text = f"{band} GHz ch {ch} ({freq:g} GHz)"
        text += f", noise {noise} dBm" if noise is not None else ", noise UNKNOWN"
        parts.append(text)
    named = {radio.get("band") for radio in last["radios"] if isinstance(radio, dict)}
    for band in nb.EXPECTED_BANDS:
        if band not in named:  # the row was written before a silent radio was kept; name the gap anyway
            parts.append(f"{band} GHz UNKNOWN (missing from the newest read)")
    stale = "  STALE" if ago > STALE_ROUTER_S else ""
    return f"{'; '.join(parts)}; read {ago:.0f} s ago{stale}"


def _radio_stats(row, band):
    for radio in row.get("radios") or []:
        if isinstance(radio, dict) and radio.get("band") == band and isinstance(radio.get("stats"), dict):
            return radio["stats"]
    return None


def _radio_sends(stats):
    """Transmissions attempted, from the driver's success and fail counters; None when one is missing."""
    ok, fail = stats.get("tx_ok"), stats.get("tx_fail")
    if ok is None or fail is None:
        return None
    return ok + fail


def _radio_fail_window(reads, band, lo, hi):
    """Failed transmissions between the first and last radio read in [lo, hi]; a reboot restarts the span.

    Returns (fails, total_sends, span_seconds), or None when the window holds less than 10 min of
    counters or the counters went backwards without a reboot.
    """
    sel = [r for r in reads if lo <= r["ts"] <= hi and _radio_stats(r, band) is not None]
    if len(sel) < 2:
        return None
    start = sel[0]
    for prev, cur in zip(sel, sel[1:]):
        if cur["uptime_s"] < prev["uptime_s"]:  # the router rebooted: counters restarted from zero
            start = cur
    end = sel[-1]
    span = end["ts"] - start["ts"]
    if span < 600:
        return None
    a, b = _radio_stats(start, band), _radio_stats(end, band)
    fails, fails_before = b.get("tx_fail"), a.get("tx_fail")
    tot_a, tot_b = _radio_sends(a), _radio_sends(b)
    if fails is None or fails_before is None or tot_a is None or tot_b is None:
        return None
    delta_fail, delta_tot = fails - fails_before, tot_b - tot_a
    if delta_fail < 0 or delta_tot <= 0:
        return None
    return delta_fail, delta_tot, span


def _hour_attempt_rates(reads, band, now, hours=WIFI_USUAL_HOURS):
    """Share of failed transmit attempts in each completed clock hour, newest first; partial hours skipped.

    Preserve holes as None so an unknown hour cannot join two separate runs. Require coverage
    across most of each hour and no 15-minute hole between reads (the producer runs every five).
    """
    base = int(now // 3600) * 3600
    rates = []
    for hour in range(1, hours + 1):
        hi = base - (hour - 1) * 3600
        got = _radio_fail_window(reads, band, hi - 3600, hi)
        samples = [r for r in reads if hi - 3600 <= r["ts"] <= hi
                   and _radio_stats(r, band) is not None]
        covered = (got is not None and got[2] >= 3000
                   and all(b["ts"] - a["ts"] <= 900 for a, b in zip(samples, samples[1:])))
        if covered:
            fails, sends, _span = got
            rates.append(100.0 * fails / sends)
        else:
            rates.append(None)
    return rates


def _wifi_severity(rate, usual):
    """0 = about usual, 1 = worse, 2 = much worse; a higher severity also clears a lower bar."""
    if rate >= usual * WIFI_MUCH_RATIO and rate - usual >= WIFI_MUCH_PP:
        return 2
    if rate >= usual * WIFI_WORSE_RATIO and rate - usual >= WIFI_WORSE_PP:
        return 1
    return 0


def _wifi_run_baseline(earlier, lifetime):
    """A 'usual' share the newest bad run cannot define, and whether the lifetime share was used.

    A trailing median includes the very hours being flagged: once a bad run fills half the
    window the median becomes the bad rate and the flag quietly vanishes while the radio is
    still busy. Look for the longest newest run whose every hour is worse than the median of
    the hours before it, and take that earlier median as the baseline. When no calm hour is
    left in the window, use the lifetime since-boot share instead: thirteen days of counter
    history cannot be captured by one bad day. Returns (baseline, used_lifetime); a hole breaks the
    reported duration in the caller but must not hide a long bad run behind its own bad baseline.
    """
    known = [hour for hour in earlier if hour is not None]
    if len(known) < WIFI_USUAL_MIN_HOURS:
        return None, False
    # Known hours still inform the baseline; only the duration requires continuity.
    n = len(known)
    for split in range(n, WIFI_BASELINE_RUN_MIN - 1, -1):
        older = known[split:]
        before = lifetime if split == n else statistics.median(older) if older else None
        if before is None or not all(_wifi_severity(h, before) for h in known[:split]):
            continue
        return before, split == n
    return statistics.median(known), False


def wifi_usuality(rate, earlier, lifetime=None):
    """Plain words for a band's last-hour failed-attempt share against its own completed hours.

    The rate counts retries, so this is a symptom, not a diagnosis: it says the radio is working
    harder than it usually does, which is what a person notices as a slow connection. The baseline
    is taken from the hours before a sustained bad run, so a long problem cannot make its own
    "usual" and then disappear; when no calm hour is left, the lifetime since-boot share
    is used and named. When the current hour clears a bar, the phrase also counts the consecutive
    completed hours that cleared the same bar, so a five-hour problem does not read like a fresh
    blip. A hole or a quiet hour stops the count; the rolling hour is never counted as a completed
    one.
    """
    usual, used_lifetime = _wifi_run_baseline(earlier, lifetime)
    if usual is None:
        return ", no usual yet"
    since_boot = " since boot" if used_lifetime else ""
    severity = _wifi_severity(rate, usual)
    if not severity:
        return ", about usual"
    hours = 0
    for past in earlier:  # newest completed hour first
        if past is None or not _wifi_severity(past, usual):  # a hole or calm hour stops the run
            break
        hours += 1
    span = f" for at least {hours} h" if hours else ""
    label = "much worse than usual" if severity == 2 else "worse than usual"
    return f", {label}{span} (usual {usual:.1f}%{since_boot})"


def wifi_quality_line(now=None):
    """Share of Wi-Fi transmissions that failed, and the driver's own error rate, from the recorded
    per-radio ``iwpriv ... stat`` counters.

    Read from the recorded per-radio counters, one line per band so each fits the window. A read
    without the counters, or a stale newest read, is UNKNOWN; a band whose counters are missing from
    the newest read is named as UNKNOWN rather than dropped; a rate is shown only when two reads an
    hour or less apart bracket it. The driver's own PER is shown when the newest read carries it
    (older rows do not): the attempts figure counts retries, so a person needs the driver's own
    frame-error number next to it to tell a retrying radio from a broken one. That PER cannot be
    reproduced from the counters printed beside it (2.4 GHz: 14.5% failed attempts against PER 3.4%;
    receive side 15.7% CRC against PER 0.7%), so its population and averaging window are the driver's
    own and are not measured here: the trailing clause says the window is not measured instead of
    claiming a period. When the newest read carries no PER the clause says UNKNOWN instead of
    vanishing: an absent number must not read as a quiet radio. A band the router has
    (``nb.EXPECTED_BANDS``) but the newest read does not carry is named UNKNOWN for the same reason:
    a silent radio must not leave a calm one-band line behind.
    """
    now = time.time() if now is None else now
    reads = [r for r in router_rows() if r.get("ok") and isinstance(r.get("radios"), list)
             and r.get("uptime_s") is not None
             and any(isinstance(radio, dict) and isinstance(radio.get("stats"), dict)
                     for radio in r["radios"])]
    if not reads:
        return "UNKNOWN (no Wi-Fi transmission counters in the router reads)"
    last = reads[-1]
    ago = now - last["ts"]
    if ago > STALE_ROUTER_S:
        return f"UNKNOWN (newest Wi-Fi transmission counters are {ago / 60:.0f} min old)  STALE"
    parts = []
    per_seen = False
    for radio in last["radios"]:
        if not isinstance(radio, dict):
            continue
        band = radio.get("band", "?")
        stats = radio.get("stats")
        total = _radio_sends(stats) if isinstance(stats, dict) else None
        if not total:
            parts.append(f"{band} GHz transmission counters UNKNOWN (missing from the newest read)")
            continue
        lifetime = 100.0 * stats["tx_fail"] / total
        text = f"{band} GHz {lifetime:.1f}% of attempts failed since boot"
        got = _radio_fail_window(reads, band, now - 3600, now)
        if got is None:
            text += " (last hour UNKNOWN)"
        else:
            fails, sends, span = got
            rate = 100.0 * fails / sends
            text += f", {rate:.1f}% last {span / 60:.0f} min"
            text += wifi_usuality(rate, _hour_attempt_rates(reads, band, now), lifetime)
        per = stats.get("tx_per")
        if isinstance(per, (int, float)):
            text += f"; driver's error rate {per:.1f}%"
            per_seen = True
        else:
            text += "; driver's error rate UNKNOWN"
        parts.append(text)
    named = {radio.get("band") for radio in last["radios"] if isinstance(radio, dict)}
    for band in nb.EXPECTED_BANDS:
        if band not in named:  # a row from before a silent radio was kept: name the gap, never hide it
            parts.append(f"{band} GHz transmission counters UNKNOWN (radio missing from the newest read)")
    if not parts:
        return "UNKNOWN (the router read carried no transmission counters)"
    temps = [(r.get("stats") or {}).get("temp_c") for r in last["radios"] if isinstance(r, dict)]
    temps = [t for t in temps if isinstance(t, int)]
    tail = [f"radio {max(temps)} C" if temps else "radio temperature UNKNOWN"]
    if per_seen:
        tail.append("driver's error rate window not measured")
    tail.append(f"read {ago:.0f} s ago")
    return "\n".join(parts + ["; ".join(tail)])


def _wan_drops(reads, lo, hi):
    """Receive drops between the first and last WAN read in [lo, hi]; a reboot restarts the span.

    Returns (drops, span_seconds), or None when the window holds less than 10 min of counters.
    """
    sel = [r for r in reads if lo <= r["ts"] <= hi]
    if len(sel) < 2:
        return None
    start = sel[0]
    for prev, cur in zip(sel, sel[1:]):
        if cur["uptime_s"] < prev["uptime_s"]:  # the router rebooted: counters restarted from zero
            start = cur
    end = sel[-1]
    span = end["ts"] - start["ts"]
    if span < 600:
        return None
    drops = end["uplink"]["rx_dropped"] - start["uplink"]["rx_dropped"]
    if drops < 0:
        return None
    return drops, span


def uplink_trend_phrase(reads, now):
    """Plain comparison of this hour's drop rate with the six hours before it."""
    earlier = []
    for h in range(1, 7):
        got = _wan_drops(reads, now - (h + 1) * 3600, now - h * 3600)
        if got is not None:
            earlier.append(got[0] * 3600 / got[1])
    if len(earlier) < 3:
        return "no comparison (fewer than 3 earlier hours of counters)"
    earlier.sort()
    med = earlier[len(earlier) // 2]
    cur = _wan_drops(reads, now - 3600, now)
    rate = cur[0] * 3600 / cur[1]
    lo_txt, hi_txt = f"{earlier[0]:.0f}", f"{earlier[-1]:.0f}"
    if rate > 1.25 * med:
        return f"higher than the {len(earlier)} earlier hours (median {med:.0f} per hour)"
    if rate < 0.75 * med:
        return f"lower than the {len(earlier)} earlier hours (median {med:.0f} per hour)"
    return f"about the same as the {len(earlier)} earlier hours ({lo_txt} to {hi_txt} per hour)"


def uplink_rate_line(now=None):
    """Receive drops on the WAN link, from recorded counters, with a trend clause and the read's age.

    The line stays computable for up to an hour after the router producer stops writing, so without
    an age and a STALE mark a dead producer's last hour reads as a calm, current trend beside lines
    that do name their age. The newest read in the window is the one that ages.
    """
    now = time.time() if now is None else now
    reads = [r for r in router_rows() if r.get("ok") and r.get("uplink") and r.get("uptime_s") is not None]
    recent = [r for r in reads if now - r["ts"] <= 3600]
    if len(recent) < 2:
        return "UNKNOWN (fewer than two WAN counter reads in the last hour)"
    got = _wan_drops(reads, now - 3600, now)
    if got is None:
        return ("UNKNOWN (no usable WAN counters in the last hour: under 10 min since the last router "
                "boot, or the counter fell without a reboot)")
    drops, span = got
    ago = now - recent[-1]["ts"]
    stale = "  STALE" if ago > STALE_ROUTER_S else ""
    newest = max(reads, key=lambda r: r["ts"])
    boot_txt = ""
    if newest["uplink"].get("rx_dropped") is not None and newest["uptime_s"] >= 3600:
        since_boot = newest["uplink"]["rx_dropped"] * 3600 / newest["uptime_s"]
        boot_txt = f"; average since the router last booted about {since_boot:.0f} per hour"
    return (f"{drops} receive drops in the last {span / 60:.0f} min "
            f"(about {drops * 3600 / span:.0f} per hour), {uplink_trend_phrase(reads, now)}"
            f"{boot_txt}; read {ago:.0f} s ago{stale}")


def gateway_line(data):
    cur = (data[-1].get("gateway") if data else None) or {}
    vals = [((r.get("gateway") or {}).get("avg_ms")) for r in data]
    loss_rows = [(r.get("at"), (r.get("gateway") or {}).get("loss_pct")) for r in data]
    loss = [v for _, v in loss_rows]
    now = f"{cur['avg_ms']:.1f} ms" if "avg_ms" in cur else "UNKNOWN"
    lossnow = f"{cur['loss_pct']:.0f}% loss" if "loss_pct" in cur else ""
    jitnow = f"jitter {cur['jitter_ms']:.2f} ms" if "jitter_ms" in cur else "jitter UNKNOWN"
    worst = max([v for v in loss if v is not None], default=None)
    # The worst is over every loaded probe, not just the 40 in the sparkline beside it; name
    # that window so an old blip cannot read as a problem in the recent, calm sparkline.
    seen_at = [at for at, v in loss_rows if v is not None and at]
    span = ""
    if len(seen_at) >= 2:
        try:
            begin, finish = nb.utc_epoch(seen_at[0]), nb.utc_epoch(seen_at[-1])
        except (ValueError, TypeError):
            begin = finish = None
        if begin is not None and finish is not None and finish >= begin:
            span = f" (over {covered(finish - begin)})"
    return (f"  {'router (LAN)':<22}{now:>10}  {lossnow:<9} {jitnow:<17} {spark(vals)}  "
            f"worst loss {('%.0f%%' % worst) if worst is not None else 'UNKNOWN'}{span}")


def web_line(label, name, data):
    vals = [web_ms(r, name) for r in data]
    cur = vals[-1] if vals else None
    now = f"{cur:.0f} ms" if cur is not None else "UNKNOWN"
    failed = sum(1 for v in vals[-40:] if v is None)
    return f"  {label:<22}{now:>10}  {spark(vals)}  failed in last {min(len(vals), 40)}: {failed}"


def host_busy_line(data):
    """Whether this mesh-home machine was busy when the newest probe ran, next to its web timings.

    The probe records kernel PSI `some avg10` for CPU and IO (rows from probe 0.2.0 on). A slow TLS
    handshake on a busy host is host time, not uplink time, so a slow page with a busy host is named
    as such. A row without the field (older probe, or PSI unreadable) is UNKNOWN, never "not busy".
    The busy/slow words are a reading, not a verdict on the uplink.
    """
    last = data[-1] if data else None
    if last is None:
        return "  HOST BUSY     UNKNOWN (no probe row yet)"
    a = age(last)
    stamp = f"probe {last.get('at', '?')}, {a:.0f} s old" if a is not None else "probe age UNKNOWN"
    host = last.get("host")
    cpu = host.get("cpu_some10") if isinstance(host, dict) else None
    if cpu is None:
        return f"  HOST BUSY     UNKNOWN (CPU pressure not in that probe; {stamp})"
    io_ = host.get("io_some10")
    io_txt = f", disk/IO waiting {io_:.0f}%" if io_ is not None else ", disk/IO UNKNOWN"
    text = f"CPU waiting {cpu:.0f}% of the time{io_txt}"
    best = min((v for v in (web_ms(last, n) for n in last.get("internet") or {}) if v is not None), default=None)
    if best is not None and best > SLOW_WEB_MS and cpu >= 50:
        text += "; SLOW HERE: busy host, so slow pages are likely this machine, not the uplink"
    return f"  HOST BUSY     {text} ({stamp})"


def dns_line(data):
    """The home resolver's lookup time, with why the connect time below can be smaller.

    The probe's curl `%{time_connect}` (the source of "reaching the site" in `phase_line`) counts name
    lookup as well as the TCP connect, while this is one separate, freshly measured lookup. A cached
    answer makes the connect's lookup far quicker, so the smaller number below is not a contradiction.
    """
    vals = [r.get("dns_ms") for r in data]
    now = f"{vals[-1]:.0f} ms" if vals and vals[-1] is not None else "UNKNOWN"
    return (f"  {'DNS lookup':<22}{now:>10}  {spark(vals)}"
            "  (the connect time below includes a lookup; cached answers are faster)")


def web_sample(row):
    """Mean total time of the secure web checks in one row, or None when none answered."""
    vals = [v for v in (web_ms(row, name) for name in ("gstatic", "cloudflare")) if v is not None]
    return sum(vals) / len(vals) if vals else None


SECURE_HOSTS = ("gstatic", "cloudflare")
NIGHT_HOURS = range(0, 6)      # the household is asleep; the quiet baseline for a handshake
# The busy part of the day, 16:00-20:59 UTC, where the web checks and the handshake are structurally
# elevated. Measured on the 1440 rows loaded at 2026-11-20 17:20Z: web-sample medians 609/671/562 ms
# at 16/17/18h against ~350 ms overnight, and handshake medians 475/520/411 ms at those hours against
# ~210 ms overnight; the peak is 16-18h, not 18-20h. An earlier 3-day sample put it at 18-20h, so the
# busiest hours were judged against the pooled day median and read "slower than usual" every ordinary
# day. The household asks "why are pages slow at 7pm?", so these hours get named; the cause (household
# traffic or the ISP's peak) is not asserted.
EVENING_HOURS = range(16, 21)
PHASE_BASELINE_MIN = 60        # rows needed before a night/evening median means anything
PHASE_SPLIT_RATIO = 2.0      # when one host's handshake is this many times the other's ...
PHASE_SPLIT_MS = 200         # ... and at least this far apart, the mean hides which host was slow


def phase_ms(row, name, field):
    """One cumulative checkpoint of a secure web check in ms, or None when it was not measured.

    curl's `%{time_connect}`, `%{time_appconnect}` and `%{time_total}` are measured from the start
    of the request: appconnect includes connect, total includes appconnect. They are checkpoints,
    not durations; calling appconnect "the handshake" double counts the connect and makes the
    printed phases fail to add up to the page total.
    """
    check = ((row or {}).get("internet") or {}).get(name)
    return check.get(field) if isinstance(check, dict) and check.get("ok") else None


def phase_value(row, name, field):
    """One disjoint phase in ms: the increment between two of curl's cumulative checkpoints.

    Reaching the site is time_connect, the secure handshake is appconnect minus connect, and the
    rest of the page is total minus appconnect. A missing or reversed pair is UNKNOWN, never a
    negative number or one borrowed from another checkpoint.
    """
    if field == "connect_ms":
        return phase_ms(row, name, "connect_ms")
    if field == "handshake_ms":
        later, earlier = phase_ms(row, name, "tls_ms"), phase_ms(row, name, "connect_ms")
    elif field == "body_ms":
        later, earlier = phase_ms(row, name, "total_ms"), phase_ms(row, name, "tls_ms")
    else:
        return None
    if later is None or earlier is None or later < earlier:
        return None
    return later - earlier


def _sample_mean(row, field):
    """Mean of one disjoint phase across the secure hosts in a row, or None when none measured it."""
    vals = [phase_value(row, name, field) for name in SECURE_HOSTS]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _handshake_split(row):
    """An indented second line naming both hosts' handshakes when their mean would mislead.

    `phase_line` prints the mean of the two disjoint handshake durations. On an ordinary probe they
    track each other, but one can stall alone while the other stays fast, and the mean then reads as
    a general secure-setup problem. Only a clear divergence gets the breakdown, so the window stays
    dense; one host failing to answer is not a divergence and gets no second line.
    """
    vals = [phase_value(row, name, "handshake_ms") for name in SECURE_HOSTS]
    if any(v is None or v < 0 for v in vals):
        return ""
    lo, hi = min(vals), max(vals)
    if lo <= 0 or hi < PHASE_SPLIT_RATIO * lo or hi - lo < PHASE_SPLIT_MS:
        return ""
    parts = ", ".join(f"{name} {v:.0f} ms" for name, v in zip(SECURE_HOSTS, vals))
    slower = " and ".join(name for name, v in zip(SECURE_HOSTS, vals) if v == hi)
    return f"\n                handshakes: {parts} ({slower} slower)"


def _hour_median(data, field, hours):
    """Median of per-row phase means over probes taken in `hours` of the UTC clock, or None when too few.

    Per-row means, not pooled per-host values: the INTERNET lines and the USUAL baseline are per-row
    means too, and pooling lets one fast host dilute a slow one, which hides the evening rise.
    """
    vals = []
    for row in data:
        try:
            hour = time.gmtime(nb.utc_epoch(row["at"])).tm_hour
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if hour in hours:
            v = _sample_mean(row, field)
            if v is not None:
                vals.append(v)
    return statistics.median(vals) if len(vals) >= PHASE_BASELINE_MIN else None

CLOCK_PHASES = {"evening": EVENING_HOURS}


def _window_clock_phase(stamps):
    """The named clock phase most of `stamps` fall in, or None when they lie mostly outside it.

    Only the evening is named: the retained rows' night median sits within a few percent of the
    pooled day median, so matching it would add fragility for no honesty gain. The recent window
    spans about half an hour, so it sits inside the phase or outside it; a straddling window
    counts for the side holding the majority, and a tie counts as neither.
    """
    if not stamps:
        return None
    counts = {name: 0 for name in CLOCK_PHASES}
    for t in stamps:
        hour = time.gmtime(t).tm_hour
        for name, hours in CLOCK_PHASES.items():
            if hour in hours:
                counts[name] += 1
    name = max(counts, key=counts.get)
    return name if counts[name] * 2 > len(stamps) else None


def _clock_web_median(data, hours):
    """Median web sample over retained probes taken in `hours` of the UTC clock, with its span.

    The USUAL line's day baseline pools every hour, so during the evening's own clock hours it
    would read "slower than usual" on an ordinary evening, every evening: the retained rows'
    evening median runs about 1.3x the pooled one. This baseline draws on the same retained rows
    `_hour_median` uses, because a single day holds only about three hours of evening samples; a
    multi-day phase dip can pull it for as long as those rows are retained, which is the same
    exposure the handshake hour medians already carry. The newest `USUAL_EXCLUDE` phase samples
    are dropped, so tonight's own slowdown cannot calibrate itself: the pool is earlier evenings.
    """
    samples = []
    for row in data:
        stamp = row.get("at") if isinstance(row, dict) else None
        if not isinstance(stamp, str):
            continue
        try:
            t = nb.utc_epoch(stamp)
            hour = time.gmtime(t).tm_hour
        except (ValueError, TypeError, OverflowError):
            continue
        if hour in hours:
            v = web_sample(row)
            if v is not None:
                samples.append((t, v))
    samples.sort()
    pool = samples[:-USUAL_EXCLUDE]
    if len(pool) < PHASE_BASELINE_MIN:
        return None, len(pool), None
    return statistics.median([v for _, v in pool]), len(pool), _sample_span([t for t, _ in pool])


def phase_line(data, now=None, facts=None):
    """Where a page load's time goes: three disjoint phases whose sum is the page's total time.

    The headline and USUAL line above already flag a sustained slowdown; this says which part moved.
    The probe's curl timings are cumulative checkpoints, so the phases here are
    their differences: reaching the site, the secure handshake, and the rest of the page. A slow
    connect points at the path (router, WAN, ISP), a slow handshake at the encrypted setup, and a
    slow remainder at the page itself. Printing the raw checkpoints as phases would double count the
    connect and leave the three numbers short of the total the INTERNET lines show.
    When a slowdown is flagged, a second indented line names the phase that accounts for it, using
    the same sample window and baseline as the USUAL line below.
    """
    now = time.time() if now is None else now
    facts = facts if facts is not None else usuality_facts(data, now)
    last = data[-1] if data else None
    if last is None:
        return "UNKNOWN (no probe row)"
    a = age(last)
    if a is None:
        return "UNKNOWN (last probe has no readable time)"
    if a > STALE_S:
        return f"UNKNOWN (last probe {a / 60:.0f} min old)"
    conn = _sample_mean(last, "connect_ms")
    hs = _sample_mean(last, "handshake_ms")
    body = _sample_mean(last, "body_ms")
    if None in (conn, hs, body):
        return f"UNKNOWN (the probe {a:.0f} s ago did not measure all three phases)"
    usual = _hour_median(data, "handshake_ms", range(24))
    night = _hour_median(data, "handshake_ms", NIGHT_HOURS)
    evening = _hour_median(data, "handshake_ms", EVENING_HOURS)
    sent = (f"reaching the site {conn:.0f} ms, secure handshake {hs:.0f} ms, "
            f"rest of page {body:.0f} ms")
    split = _handshake_split(last)
    note = _slowdown_note(facts)
    if usual is None:
        return f"{sent}; handshake usually UNKNOWN (too few probes for a baseline); last probe {a:.0f} s ago{split}{note}"
    if night is None or evening is None:
        return f"{sent}; handshake usually {usual:.0f} ms; last probe {a:.0f} s ago{split}{note}"
    return (f"{sent}; handshake usually {usual:.0f} ms"
            f" (nights ~{night:.0f}, evenings ~{evening:.0f}); last probe {a:.0f} s ago{split}{note}")


def day_spark(samples, buckets=USUAL_BUCKETS):
    """Bucket web-latency samples across the loaded window into `buckets` means for a sparkline."""
    if not samples:
        return "(no data)"
    n = len(samples)
    means = []
    for i in range(buckets):
        chunk = samples[i * n // buckets:(i + 1) * n // buckets]
        vals = [v for v in chunk if v is not None]
        means.append(sum(vals) / len(vals) if vals else None)
    known = [m for m in means if m is not None]
    if not known:
        return "(no data)"
    # A bucket with no answered check is drawn as a gap, not skipped: skipping it would make a
    # dead stretch of the day look like continuous data.
    hi = max(known) or 1
    return "".join("·" if m is None else SPARK[min(7, int(m / hi * 7.999))] for m in means)


PHASE_LABELS = (("connect_ms", "reaching the site"),
                ("handshake_ms", "secure handshake"),
                ("body_ms", "rest of page"))


def _phase_complete(row):
    """One row's three disjoint phase means, or None unless one host measured all three.

    Dropping unknown phases silently can turn a single measured probe into a claim about a
    ten-probe window, so attribution is built only from hosts whose three phases are all known.
    Taking whole hosts also keeps the three figures additive: their sum is that row's page total.
    """
    triples = [tuple(phase_value(row, name, field) for field, _ in PHASE_LABELS)
               for name in SECURE_HOSTS]
    triples = [t for t in triples if all(v is not None for v in t)]
    if not triples:
        return None
    return tuple(sum(t[i] for t in triples) / len(triples) for i in range(3))


def _phase_medians(rows):
    """Phase medians, their total's median and support, over rows with all three phases measured."""
    complete = [v for v in (_phase_complete(row) for row in rows) if v is not None]
    if not complete:
        return ({field: None for field, _ in PHASE_LABELS}, 0, None)
    medians = {field: statistics.median(t[i] for t in complete)
               for i, (field, _) in enumerate(PHASE_LABELS)}
    return medians, len(complete), statistics.median(sum(t) for t in complete)


def _slowdown_note(facts):
    """One indented line naming the phase behind a flagged slowdown; '' when it is not measurable.

    The newest probe can look ordinary while the ten-probe median stays slow, so WHERE TIME GOES
    alone cannot say which phase moved. This uses the same samples and baseline as USUAL, and
    speaks only when one phase accounts for at least half the extra time (and at least 20 ms). The
    typical comes from the day's phase-measured rows before the recent window, so the line names
    those rows' own span beside it; the web baseline spans more rows and would overstate coverage.
    With partial support the recent minutes likewise span only the phase-measured rows.
    A median built from too few measured rows is not a ten-probe median: say UNKNOWN with the
    support counts instead of turning one measured probe into a claim about the whole window.
    """
    if facts is None or facts.get("compare") in (None, "about usual") or facts.get("stale"):
        return ""
    recent_n = facts.get("phase_support", 0)
    base_n = facts.get("phase_base_support", 0)
    window_n = facts.get("window_count", 0)
    if recent_n < (window_n + 1) // 2 or base_n < PHASE_BASELINE_MIN:
        return (f"\n                slowdown phase UNKNOWN (phases measured on {recent_n} of "
                f"{window_n} recent probes, {base_n} baseline rows)")
    cur, base = facts.get("phase_cur"), facts.get("phase_base")
    cur_total, base_total = facts.get("phase_total_cur"), facts.get("phase_total_base")
    if not cur or not base or cur_total is None or base_total is None:
        return ""
    extra = cur_total - base_total
    if extra <= 0:
        return ""
    deltas = {f: cur[f] - base[f] for f, _ in PHASE_LABELS
              if cur.get(f) is not None and base.get(f) is not None}
    if not deltas:
        return ""
    field = max(deltas, key=deltas.get)
    if deltas[field] < max(20.0, 0.5 * extra):
        return ""
    label = dict(PHASE_LABELS)[field]
    source = facts["window"] if recent_n == window_n else f"{facts['phase_window']} measured"
    return (f"\n                slowdown is in the {label}: {source} "
            f"{cur[field]:.0f} ms vs typical {base[field]:.0f} ms ({facts['phase_base_span']} of samples)")


def _sample_span(stamps):
    """Label the time a set of samples covers; UNKNOWN below two rows."""
    if len(stamps) < 2:
        return "UNKNOWN"
    span_h = (max(stamps) - min(stamps)) / 3600
    if span_h < 1:
        return f"{span_h * 60:.0f} min"
    return f"{span_h:.0f} h" if span_h < 48 else f"{span_h / 24:.1f} d"


def _recent_minutes(stamps):
    """Whole minutes spanned by the stamps behind a figure, or None when there is no minute to name."""
    if len(stamps) < 2:
        return None
    mins = (stamps[-1] - stamps[0]) / 60
    return f"{mins:.0f} min" if mins >= 1 else None


def _recent_window(count, stamps, measured=None):
    """'last N probes (M min)' from the stamps behind a figure; a measured count names a subset's span."""
    text = f"last {count} probes" if measured is None else f"{measured} of last {count} probes"
    mins = _recent_minutes(stamps)
    return f"{text} ({mins})" if mins else text


def usuality_facts(data, now=None):
    """The last-day web latency comparison, shared by the headline and the USUAL line.

    One computation so both say the same thing about the same samples: a headline that said OK while
    USUAL said "much slower than usual" left a reader unsure which to believe. `compare` is None when
    there are too few samples; `stale` is set when the newest probe is older than STALE_S.
    `span` covers every loaded row and labels the trend; `base_span` covers only the rows that supply
    the typical, which drop the newest `USUAL_EXCLUDE` samples so a spike cannot inflate its own
    baseline. Labelling the typical with `span` claimed 24 h where the baseline spanned about 21 h.
    With sparse phase support the recent minutes also span only the phase-measured rows
    (`phase_window`), not every web-good probe that supplied the web median.
    The typical is clock-phase matched: when the recent window sits inside the evening hours it
    comes from earlier evenings' own retained samples (`clock_base`, `clock_span`), because the
    pooled day median is biased low at the evening's elevated hours and would read "slower than
    usual" on an ordinary evening, every evening. Too few evening samples leaves `clock_base`
    None: the compare falls back to the pooled day median and the label keeps the day span
    ("of samples"), so the line never claims an evening calibration it has no samples for.
    """
    now = time.time() if now is None else now
    recent = []  # (epoch, web sample, row) oldest first; sample is None when the row's checks failed
    for row in data:
        stamp = row.get("at") if isinstance(row, dict) else None
        if not isinstance(stamp, str):
            continue
        try:
            t = nb.utc_epoch(stamp)
        except (ValueError, TypeError):
            continue
        if now - t <= 86400:
            recent.append((t, web_sample(row), row))
    samples = [v for _, v, _ in recent]
    good = [v for v in samples if v is not None]
    if len(good) < USUAL_MIN_SAMPLES:
        # Thin data still has recent web samples: the SPIKES row must count them, not claim there are none.
        return {"compare": None, "good_count": len(good), "recent_good": good[-USUAL_RECENT:]}
    good_stamps = [t for t, v, _ in recent if v is not None]
    cur_stamps = good_stamps[-USUAL_RECENT:]
    cur = statistics.median(good[-USUAL_RECENT:])
    window = _recent_window(len(cur_stamps), cur_stamps)
    mins = _recent_minutes(cur_stamps)
    recent_phrase = f"last {mins}" if mins else window
    good_rows = [row for _, v, row in recent if v is not None]
    exclude = len(good) >= USUAL_EXCLUDE + USUAL_RECENT
    base_pool = good[:-USUAL_EXCLUDE] if exclude else good
    base_rows = good_rows[:-USUAL_EXCLUDE] if exclude else good_rows
    base_stamps = good_stamps[:-USUAL_EXCLUDE] if exclude else good_stamps
    base = statistics.median(base_pool)
    clock_phase = _window_clock_phase(cur_stamps)
    clock_base, clock_support, clock_span = (
        _clock_web_median(data, CLOCK_PHASES[clock_phase]) if clock_phase else (None, 0, None))
    compare_base = clock_base if clock_base is not None else base
    ratio = cur / compare_base if compare_base else 1.0
    phase_cur, phase_support, phase_total_cur = _phase_medians(good_rows[-USUAL_RECENT:])
    phase_base, phase_base_support, phase_total_base = _phase_medians(base_rows)
    phase_base_stamps = [t for t, row in zip(base_stamps, base_rows) if _phase_complete(row) is not None]
    phase_base_span = _sample_span(phase_base_stamps)
    phase_cur_stamps = [t for t, row in zip(cur_stamps, good_rows[-USUAL_RECENT:])
                        if _phase_complete(row) is not None]
    phase_window = _recent_window(len(cur_stamps), phase_cur_stamps, measured=phase_support)
    if ratio < 1.25:
        compare = "about usual"
    elif ratio < 2:
        compare = "slower than usual"
    else:
        compare = "much slower than usual"
    span = _sample_span([t for t, _, _ in recent])
    base_span = _sample_span(base_stamps)
    stale = ""
    if data:
        a = age(data[-1])
        if a is not None and a > STALE_S:
            stale = f" (STALE: last probe {a:.0f}s ago)"
    return {"cur": cur, "base": base, "compare": compare, "window": window,
            "samples": samples, "span": span, "base_span": base_span, "stale": stale,
            "recent_good": good[-USUAL_RECENT:],
            "recent_phrase": recent_phrase, "window_count": len(cur_stamps),
            "clock_phase": clock_phase, "clock_base": clock_base,
            "clock_support": clock_support, "clock_span": clock_span,
            "phase_cur": phase_cur, "phase_base": phase_base, "phase_support": phase_support,
            "phase_base_support": phase_base_support, "phase_total_cur": phase_total_cur,
            "phase_total_base": phase_total_base, "phase_base_span": phase_base_span,
            "phase_window": phase_window}


def usuality_line(data, now=None, facts=None):
    """'Was it worse than usual?': the recent web latency vs the last day's typical, with a trend sparkline.

    The recent figure is the median of the last `USUAL_RECENT` samples, not the newest probe: at the
    current ~204 s cadence that is about half an hour, and the INTERNET lines above already show the
    newest probe. Calling this median "now" made the pane contradict itself (a fast newest probe beside
    a slower half-hour median, both labelled as the present), so the line names the sample count and the
    span those samples actually cover. During the evening hours the typical is the evening's own
    median, and the label's span says "of evenings", because the pooled day median is biased low
    there and would read "slower than usual" on an ordinary evening, every evening. Without
    enough evening samples the line keeps the whole day's typical, labelled "of samples" like
    every other hour: it then claims only the day comparison it actually makes, never an
    evening calibration it has no samples for.
    The typical excludes the last `USUAL_EXCLUDE` samples so a spike cannot inflate its own
    baseline; its label names that baseline's own span, not the loaded window. Built only from
    rows already loaded for the window; UNKNOWN when too few samples; STALE past `STALE_S`.
    The span after the comparison names the loaded sample window, not the slowdown's duration: the
    headline above already names how long the slowdown lasted, and a bare "over 24 h" here read as a
    day-long slowdown that contradicted it.
    """
    facts = facts if facts is not None else usuality_facts(data, now)
    if facts["compare"] is None:
        return (f"UNKNOWN (only {facts['good_count']} web samples in the last day; "
                f"need {USUAL_MIN_SAMPLES})")
    if facts.get("clock_base") is not None:
        typical = (f"typical {facts['clock_base']:.0f} ms "
                   f"({facts['compare']}; {facts['clock_span']} of evenings)")
    else:
        typical = f"typical {facts['base']:.0f} ms ({facts['compare']}; {facts['base_span']} of samples)"
    return (f"  {'USUAL':<22}{facts['window']:<24}median {facts['cur']:.0f} ms vs {typical}"
            f"  trend {facts['span']}: {day_spark(facts['samples'])}{facts['stale']}")


def spike_share_line(facts):
    """Web probes over SPIKE_WEB_MS in the recent median's window, on their own row.

    The median hides intermittent spikes: one probe in three can take over a second while the
    median sits near the typical, so this row names the count the median hides. It is kept off the
    USUAL row, which is already at the pane's width. UNKNOWN when there are no web samples.
    """
    recent = (facts or {}).get("recent_good", [])
    if not recent:
        return "  SPIKES        UNKNOWN (no web samples in the recent window)"
    over = sum(1 for v in recent if v > SPIKE_WEB_MS)
    return (f"  {'SPIKES':<14}{over} of the last {len(recent)} web probes over "
            f"{SPIKE_WEB_MS // 1000} s (the median hides these)")


def headline_state(state, data, now=None, facts=None):
    """Append a sustained relative slowdown to an otherwise-OK headline.

    `verdict` is about reachability and absolute slowness; its OK must not change, or the outage list
    would misclassify probes. A ten-probe median more than 1.25x the typical is a different fact
    and belongs in the same line; during the evening hours the typical is the evening's own, and
    the headline says "for the evening" so it cannot read as a claim against the whole day.
    DOWN/DEGRADED/UNKNOWN and a stale or unmeasurable baseline are left alone so the headline
    never claims a slowdown it has not measured.
    """
    if state != "OK":
        return state
    facts = facts if facts is not None else usuality_facts(data, now)
    if facts["compare"] in (None, "about usual") or facts["stale"]:
        return state
    phase = facts.get("clock_phase") if facts.get("clock_base") is not None else None
    qual = f" for the {phase}" if phase else ""
    return f"{state}, but {facts['compare']}{qual} over the {facts['recent_phrase']}"


def night_values(rows, key):
    """Good readings collapsed to one per UTC night, oldest first.

    The producers append one row a night; collapsing by date means a stray second run that night
    cannot weight itself twice, and a failed row is never a night of its own.
    """
    by_date = {}
    for row in rows:
        if not row.get("ok"):
            continue
        ts, value = row.get("ts"), row.get(key)
        if not isinstance(ts, (int, float)) or not isinstance(value, (int, float)):
            continue
        by_date[time.strftime("%Y-%m-%d", time.gmtime(ts))] = (ts, float(value))
    return [value for _, value in sorted(by_date.values())]


def night_comparison(values):
    """Plain "was tonight usual?" phrase for a speed line; empty only when there is no reading.

    The latest night is compared with the median of the earlier ones in a bounded window. One or
    two earlier nights are named rather than called "typical", and with none the line admits there
    is no baseline yet instead of inventing one.
    """
    if not values:
        return ""
    recent = values[-SPEED_BASELINE_NIGHTS:]
    earlier = recent[:-1]
    if not earlier:
        return "; first night measured, no usual yet"
    if len(earlier) <= 2:
        named = " and ".join(f"{v:g}" for v in earlier)
        word = "night" if len(earlier) == 1 else "nights"
        return f"; earlier {word} {named} Mbps"
    typical = statistics.median(earlier)
    if typical <= 0:
        return ""
    ratio = recent[-1] / typical
    if ratio >= SPEED_USUAL_RATIO:
        compare = "faster than usual"
    elif ratio <= 1 / SPEED_USUAL_RATIO:
        compare = "slower than usual"
    else:
        compare = "about usual"
    return (f"; {compare} vs {round(typical, 1):g} Mbps over {len(earlier)} earlier nights "
            f"({min(earlier):g} to {max(earlier):g})")


def speed_line(speed_rows, now=None):
    """SPEED in plain words: the last good download with its age, or UNKNOWN (never a calm old number)."""
    now = time.time() if now is None else now
    if not speed_rows:
        return "UNKNOWN (no speed reading yet; one 5 MB download a night, 02-05 local)"
    last = speed_rows[-1]
    if not last.get("ok"):
        return f"UNKNOWN (last try {int((now - last['ts']) // 60)} min ago failed: {last.get('error', '?')[:60]})"
    good = [r for r in speed_rows if r.get("ok")]
    best = good[-1]
    hours = (now - best["ts"]) / 3600
    if hours > 36:
        return f"UNKNOWN (last reading is {hours:.0f} h old)"
    usual = night_comparison(night_values(speed_rows, "down_mbps"))
    return f"DOWN {best['down_mbps']:g} Mbps, measured {hours:.1f} h ago (5 MB download, night only){usual}"


def speed_up_line(up_rows, now=None):
    """UPLOAD in plain words: the last good upload with its age, or UNKNOWN (never a calm old number)."""
    now = time.time() if now is None else now
    if not up_rows:
        return "UNKNOWN (no upload reading yet; one 2 MB upload a night, 02-05 local)"
    last = up_rows[-1]
    if not last.get("ok"):
        return f"UNKNOWN (last upload {int((now - last['ts']) // 60)} min ago failed: {last.get('error', '?')[:60]})"
    good = [r for r in up_rows if r.get("ok")]
    best = good[-1]
    hours = (now - best["ts"]) / 3600
    if hours > 36:
        return f"UNKNOWN (last upload is {hours:.0f} h old)"
    usual = night_comparison(night_values(up_rows, "up_mbps"))
    return f"UP {best['up_mbps']:g} Mbps, measured {hours:.1f} h ago (2 MB upload, night only){usual}"


def probe_cadence_phrase(data):
    """How far apart the recent probes are, measured from their own timestamps.

    The duty's period is a deployment detail; deriving the phrase from recorded rows keeps the
    window honest if that period changes or a producer slows to a crawl.
    """
    ts = []
    for row in data[-41:]:
        try:
            ts.append(nb.utc_epoch(row["at"]))
        except (KeyError, TypeError, ValueError):
            continue
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if 0 < b - a)
    if not gaps:
        return "probe cadence UNKNOWN"
    med = gaps[len(gaps) // 2]
    if med < 90:
        return f"probes about {med:.0f} s apart"
    return f"probes about {med / 60:.1f} min apart"


def probe_age_line(data, a=None, n=40):
    """The 'last probe' header line; the cadence and any staleness are both measured.

    The per-site rows, gateway and outage list carry no freshness of their own: after the producer
    stops, their last numbers stay on screen and "failed in last 40: 0" reads calm. This header
    governs all of them, so it says STALE as well as showing an age that a reader must interpret.
    """
    if a is None and data:
        a = age(data[-1])
    age_txt = "%ds ago" % a if a is not None else "never"
    stale = "  STALE" if a is not None and a > STALE_S else ""
    return (f"  last probe: {age_txt}   "
            f"({probe_cadence_phrase(data)}; sparklines = last {n} probes unless labelled){stale}")


def draw():
    data = rows()
    facts = usuality_facts(data)
    last = data[-1] if data else None
    a = age(last) if last else None
    out = [f"MISHE NETWORK  {time.strftime('%H:%M:%SZ', time.gmtime())}   derived numbers only; UNKNOWN when not measured", ""]
    sha, committed = VERSION
    committed_txt = f", committed {(time.time() - committed) / 3600:.1f} h ago" if committed else ""
    out.append(f"  view code {sha}{committed_txt}; this copy loaded {time.time() - STARTED:.0f} s ago")
    refused = refused_line()
    if refused:
        out.append(refused)
    out.append(f"  HOME INTERNET: {headline_state(verdict(last, a), data, facts=facts)}")
    out.append(probe_age_line(data, a, min(len(data), 40)))
    out.append("")
    out.append("  ROUTER AND LOCAL LINK (ping, average of 5)")
    out.append(gateway_line(data))
    out.append("")
    out.append("  INTERNET (secure web pages, full time to finish)")
    out.append(web_line("gstatic.com", "gstatic", data))
    out.append(web_line("cloudflare.com", "cloudflare", data))
    out.append(dns_line(data))
    out.append(host_busy_line(data))
    out.append("  WHERE TIME GOES  " + phase_line(data, facts=facts))
    out.append(usuality_line(data, facts=facts))
    out.append(spike_share_line(facts))
    out.append("")
    out.append("  SPEED         " + speed_line(nb_speed_rows()))
    out.append("  UPLOAD        " + speed_up_line(nb_speed_up_rows()))
    out.append("")
    out.append("  OUTAGES (home internet DOWN, from the probes)")
    out.extend(outage_lines(data, containment_pause))
    # A long device name or several weak clients must not wrap the row and push the frame's header
    # off the pane; wifi_pane_rows moves the overflow to an indented continuation row.
    wifi_note = f"   (read-only live read; weak below {WEAK_WIFI_DBM} dBm; names recorded)"
    out.extend(wifi_pane_rows(wifi_line(), wifi_note))
    out.append("  WI-FI RADIO   " + wifi_radio_line() + "   (read-only iwinfo on the router)")
    quality = wifi_quality_line().split("\n")
    out.append("  WI-FI QUALITY " + quality[0])
    out.extend(" " * 16 + extra for extra in quality[1:])
    out[-1] += "   (read-only router counters)"
    out.append("  RECORDED 5-MIN " + recorded_clients_line())
    out.append("  ROUTER        " + router_facts() + "   (read-only)")
    out.append("  UPLINK        " + uplink_line() + "   (read-only)")
    out.append("  WAN LINK      " + wan_link_line() + "   (read-only, speed and carrier drops)")
    out.append("  UPLINK RATE   " + uplink_rate_line())
    out.append("  ROUTER LOAD   " + router_health_line() + "   (read-only, one read per 5 min)")
    out.append("  DEVICE PING   " + devices_line() + "   (named household devices, read-only pings)")
    out.append("  VPN           " + vpn_line() + "   (read-only, router process list and tun0)")
    out.append("  HOST GUARD    " + guard_line(router_read_age=router_read_age_s()) +
               "   (read-only, trip marker and trip records)")
    exit_lines = exit_line().split("\n")
    out.append("  EXIT NODE     " + exit_lines[0] + "   (read-only, the egress duty's file)")
    out.extend(exit_lines[1:])
    out.append("")
    out.append("  note: pings to public addresses are not used: the router answers them itself,")
    out.append("  so they look fast even when the internet is down. Internet checks are secure page loads.")
    return "\n".join(out)


def render_frame():
    """The frame to print: the window, or a loud error frame when a line raises.

    A redraw that raises used to kill the loop and with it the whole outer window: a partly edited
    view (two files or two edits, not yet consistent) or any bug in one line made the pane vanish,
    and only the five-minute check noticed.  The error frame keeps the window alive, names the
    error, and says plainly that nothing in the pane is a reading until it clears.
    """
    try:
        return draw()
    except Exception as exc:  # noqa: BLE001 - one bad line must not take the window down
        tb = exc.__traceback__
        while tb is not None and tb.tb_next is not None:  # the innermost frame is the guilty line
            tb = tb.tb_next
        line = f" at line {tb.tb_lineno}" if tb is not None and tb.tb_lineno else ""
        return "\n".join([
            f"MISHE NETWORK  {time.strftime('%H:%M:%SZ', time.gmtime())}   the window cannot draw",
            "",
            f"  WINDOW ERROR  {type(exc).__name__}: {exc}{line}",
            f"  view code {VERSION[0]}",
            "",
            "  Nothing in this pane is a network reading until this clears: the whole window is",
            "  UNKNOWN, not calm. The view retries every 10 s and picks up its own file edits.",
        ])


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(draw())
    else:
        report_window_state()
        state_at = time.time()
        while True:
            STAMP = maybe_reexec(STAMP)
            # While an edit is refused the disk holds code this copy is not running; keep the header's
            # code label on the loaded copy, or it would name a commit/edits that never took effect.
            if not REFUSED and time.time() - VERSION_AT > 60:  # commits and edits show within a minute
                VERSION, VERSION_AT = code_version(), time.time()
            # Re-record once a minute: a stray loop run (not --once) overwrites window.json with its own pid,
            # and the window check would then call the live pane's copy stale until this copy writes again.
            if time.time() - state_at > 60:
                report_window_state()
                state_at = time.time()
            sys.stdout.write("\x1b[H\x1b[2J" + render_frame() + "\n")
            sys.stdout.flush()
            time.sleep(10)
