#!/usr/bin/env python3
"""Live window: how the home network and connection are doing, in plain words.

Reads the rows the `network-probe` duty appends to SITE/network/probe.jsonl and, read-only,
a few facts from the router over ssh (never writes there).  Anything not measured, or too old,
is shown as UNKNOWN.  The network mind improves this file.
"""
import ast
import hashlib
import json
import os
import select
import statistics
import subprocess
import sys
import tempfile
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
SLOW_DNS_MS = 500
SPEED_USUAL_RATIO = 1.10    # within 10% of the earlier nights' median counts as "about usual"
SPEED_BASELINE_NIGHTS = 14  # measured nights the SPEED and UPLOAD lines compare against

# "Was it worse than usual?" — a day baseline built only from probe rows already loaded for the window.
USUAL_MIN_SAMPLES = 120    # minimum web samples for a meaningful baseline
USUAL_RECENT = 10          # "now" is the median of the last ten web samples
USUAL_EXCLUDE = 60         # keep recent samples out of the baseline so a spike cannot inflate it
USUAL_BUCKETS = 40         # width of the trend sparkline
_router = {"at": 0.0, "text": None}
_wifi = {"at": 0.0, "text": None, "weak": False}
WEAK_WIFI_DBM = -75
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


def outage_lines(data):
    """The outage list for the window: when the home internet was DOWN in the probes we hold."""
    seen = [r for r in data if "internet" in r and r.get("at")]
    if not seen:
        return ["  UNKNOWN (no secure-web probes to read outages from)"]
    first, last = seen[0]["at"], seen[-1]["at"]
    begin, finish = nb.utc_epoch(first), nb.utc_epoch(last)
    stretch = (f" in {covered(finish - begin)}"
               if begin is not None and finish is not None and finish >= begin else "")
    span = f"over the last {len(seen)} probes{stretch} ({at_short(first)} to {at_short(last)})"
    episodes = outage_episodes(data)
    if not episodes:
        return [f"  none {span}"]
    out = []
    for start, end in episodes:
        if end is None:
            out.append(f"  DOWN since {at_short(start)}, still down at the last probe")
        else:
            same_day = at_short(start)[:5] == at_short(end)[:5]
            when = (f"{start[11:16]}Z to {end[11:16]}Z" if same_day
                    else f"{at_short(start)} to {at_short(end)}")
            mins = (nb.utc_epoch(end) - nb.utc_epoch(start)) / 60
            out.append(f"  DOWN {when} ({mins:.0f} min)")
    return out + [f"  ({span})"]


def router_facts():
    if time.time() - _router["at"] < ROUTER_EVERY_S:
        return _router["text"]
    _router["at"] = time.time()
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router",
                            "cut -d' ' -f1 /proc/uptime; cut -d' ' -f1-3 /proc/loadavg; "
                            "ubus call network.interface.wan status | grep -E '\"up\"|\"uptime\"' | tr -d ' \\n'"],
                           capture_output=True, text=True, timeout=12)
        days, load, wan = nb.decode_router(r.stdout)
        _router["text"] = f"router up {days:.1f} days, load {load}, WAN {'up' if wan else 'UNKNOWN/down'}"
    except (OSError, ValueError, subprocess.SubprocessError):
        _router["text"] = None
    return _router["text"]


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
    """(text, weak) for the home Wi-Fi from a read-only router listing; (None, False) when it cannot be read."""
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
    for band in sorted(bands):
        clients = bands[band]
        parts.append(f"{band} GHz {len(clients)}")
        if clients:
            cid, dbm = min(clients, key=lambda client: client[1])
            if weakest is None or dbm < weakest:
                weakest, weakest_band, weakest_name = dbm, band, names.get(cid)
    total = sum(len(v) for v in bands.values())
    weak = weakest is not None and weakest < WEAK_WIFI_DBM
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


ROUTER_LOG = SITE / "network" / "router.jsonl"
STALE_ROUTER_S = 15 * 60  # the producer runs every 5 min; three missed runs is stale
# "Is the radio worse than usual?" — the last hour's share of failed transmit attempts against the
# median of the completed clock hours in the retained router reads (two days).  A ratio alone would
# call noise on the quiet 5 GHz band an emergency, so a level must also clear a points-above floor.
WIFI_USUAL_HOURS = 24       # completed hours the usual share is taken from
WIFI_USUAL_MIN_HOURS = 3    # fewer complete hours than this and there is no usual yet
WIFI_WORSE_RATIO = 1.5      # 50% above the usual share ...
WIFI_WORSE_PP = 5.0         # ... and at least five points above it
WIFI_MUCH_RATIO = 2.0       # twice the usual share ...
WIFI_MUCH_PP = 10.0         # ... and at least ten points above it


def router_rows():
    """Rows the network-router duty wrote (failed runs included), oldest first."""
    try:
        text = ROUTER_LOG.read_text()
    except OSError:
        return []
    return nb.decode_router_rows(text)


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
    return (f"{len(clients)} clients ({band_txt}){weak_txt}; read {ago:.0f} s ago{stale}\n"
            f"                    clients over the last hour: {trend}")


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


def guard_line(now=None, marker=GUARD_TRIP_MARKER):
    """Whether the host guard's trip marker is present: the usual reason a router read goes STALE.

    Read-only stat. Any error other than "absent" is UNKNOWN, never a calm clear.
    """
    now = time.time() if now is None else now
    try:
        mtime = os.stat(marker).st_mtime
    except FileNotFoundError:
        return "clear (no trip marker; the router and window units may run)"
    except OSError as exc:
        return f"UNKNOWN (trip marker could not be read: {exc.strerror or type(exc).__name__})"
    return (f"TRIPPED {(now - mtime) / 60:.0f} min ago: the guard holds back the router units "
            "and their readings go STALE until it is rearmed")


EGRESS_STATE = SITE / "health" / "egress.json"
EGRESS_STALE_S = 900  # the egress duty runs every 5 minutes; 15 minutes without a write is stale


def exit_line(now=None, path=EGRESS_STATE):
    """EXIT NODE in plain words: each client's tailscale exit-node state from the egress duty's file.

    Read-only. A missing, unreadable or stale file is UNKNOWN, never a calm all-GREEN.
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
    for r in bad[:REASON_CLIENTS]:
        why = first_reason(r)
        if why:
            lines.append(" " * 17 + f"{str(r.get('client', '?'))[:24]} {normalized[id(r)]}: {why}")
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
        line = f"UNKNOWN (last router read failed {ago:.0f} s ago: {last.get('error', 'no reason recorded')})"
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


def vpn_line(now=None):
    """VPN (sing-box on the router) in plain words, from the newest router read that carried it.

    "Running" and "tun0 up" say the tunnel process and link exist; they do not say which clients use it.
    No VPN reading at all, or an unreadable process list, is UNKNOWN, never a calm answer.
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
    stale = "  STALE" if ago > STALE_ROUTER_S else ""
    return f"{proc}, {tun} on the router; read {ago:.0f} s ago{stale}"


def wifi_radio_line(now=None):
    """Wi-Fi channel and noise floor from the newest recorded router read.

    A read with no radio info is UNKNOWN, never a quiet channel; an old read is STALE.
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

    A hole (the producer was not running) or an hour too short to bracket is left out, never filled
    with an invented rate: the caller needs enough real hours before it may call anything "usual".
    """
    base = int(now // 3600) * 3600
    rates = []
    for hour in range(1, hours + 1):
        hi = base - (hour - 1) * 3600
        got = _radio_fail_window(reads, band, hi - 3600, hi)
        if got is not None:
            fails, sends, _span = got
            rates.append(100.0 * fails / sends)
    return rates


def wifi_usuality(rate, earlier):
    """Plain words for a band's last-hour failed-attempt share against its own completed hours.

    The rate counts retries, so this is a symptom, not a diagnosis: it says the radio is working
    harder than it usually does, which is what a person notices as a slow connection.
    """
    if len(earlier) < WIFI_USUAL_MIN_HOURS:
        return ", no usual yet"
    usual = statistics.median(earlier)
    if rate >= usual * WIFI_MUCH_RATIO and rate - usual >= WIFI_MUCH_PP:
        return f", much worse than usual (usual {usual:.1f}%)"
    if rate >= usual * WIFI_WORSE_RATIO and rate - usual >= WIFI_WORSE_PP:
        return f", worse than usual (usual {usual:.1f}%)"
    return ", about usual"


def wifi_quality_line(now=None):
    """Share of Wi-Fi transmissions that failed, and the driver's own frame loss, from the recorded
    per-radio ``iwpriv ... stat`` counters.

    Read from the recorded per-radio counters, one line per band so each fits the window. A read
    without the counters, or a stale newest read, is UNKNOWN; a band whose counters are missing from
    the newest read is named as UNKNOWN rather than dropped; a rate is shown only when two reads an
    hour or less apart bracket it. The driver's own PER is shown when the newest read carries it
    (older rows do not): the attempts figure counts retries, so a person needs the driver's own
    frame-loss number next to it to tell a retrying radio from a broken one.
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
    for radio in last["radios"]:
        if not isinstance(radio, dict):
            continue
        band = radio.get("band", "?")
        stats = radio.get("stats")
        total = _radio_sends(stats) if isinstance(stats, dict) else None
        if not total:
            parts.append(f"{band} GHz transmission counters UNKNOWN (missing from the newest read)")
            continue
        text = f"{band} GHz {100.0 * stats['tx_fail'] / total:.1f}% of attempts failed since boot"
        got = _radio_fail_window(reads, band, now - 3600, now)
        if got is None:
            text += " (last hour UNKNOWN)"
        else:
            fails, sends, span = got
            rate = 100.0 * fails / sends
            text += f", {rate:.1f}% last {span / 60:.0f} min"
            text += wifi_usuality(rate, _hour_attempt_rates(reads, band, now))
        per = stats.get("tx_per")
        if isinstance(per, (int, float)):
            text += f"; driver's frame loss {per:.1f}%"
        parts.append(text)
    if not parts:
        return "UNKNOWN (the router read carried no transmission counters)"
    temps = [(r.get("stats") or {}).get("temp_c") for r in last["radios"] if isinstance(r, dict)]
    temps = [t for t in temps if isinstance(t, int)]
    tail = f"; radio {max(temps)} C" if temps else "; radio temperature UNKNOWN"
    return "\n".join(parts) + f"{tail}; read {ago:.0f} s ago"


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
    """Receive drops on the WAN link over the last hour, from recorded counters, with a trend clause."""
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
    return (f"{drops} receive drops in the last {span / 60:.0f} min "
            f"(about {drops * 3600 / span:.0f} per hour), {uplink_trend_phrase(reads, now)}")


def gateway_line(data):
    cur = (data[-1].get("gateway") if data else None) or {}
    vals = [((r.get("gateway") or {}).get("avg_ms")) for r in data]
    loss = [((r.get("gateway") or {}).get("loss_pct")) for r in data]
    now = f"{cur['avg_ms']:.1f} ms" if "avg_ms" in cur else "UNKNOWN"
    lossnow = f"{cur['loss_pct']:.0f}% loss" if "loss_pct" in cur else ""
    jitnow = f"jitter {cur['jitter_ms']:.2f} ms" if "jitter_ms" in cur else "jitter UNKNOWN"
    worst = max([v for v in loss if v is not None], default=None)
    return (f"  {'router (LAN)':<22}{now:>10}  {lossnow:<9} {jitnow:<17} {spark(vals)}  "
            f"worst loss {('%.0f%%' % worst) if worst is not None else 'UNKNOWN'}")


def web_line(label, name, data):
    vals = [web_ms(r, name) for r in data]
    cur = vals[-1] if vals else None
    now = f"{cur:.0f} ms" if cur is not None else "UNKNOWN"
    failed = sum(1 for v in vals[-40:] if v is None)
    return f"  {label:<22}{now:>10}  {spark(vals)}  failed in last {min(len(vals), 40)}: {failed}"


def web_sample(row):
    """Mean total time of the secure web checks in one row, or None when none answered."""
    vals = [v for v in (web_ms(row, name) for name in ("gstatic", "cloudflare")) if v is not None]
    return sum(vals) / len(vals) if vals else None


SECURE_HOSTS = ("gstatic", "cloudflare")
NIGHT_HOURS = range(0, 6)      # the household is asleep; the quiet baseline for a handshake
# The busy evening, 18:00-20:59 clock time: the only hours where the handshake median is elevated in
# BOTH the 8-day file and the 3.4-day window the view loads (18/19/20 = 462/526/408 ms and 469/459/423 ms;
# 17:00 and 21:00 sit near the daytime baseline in both). The household asks "why are pages slow at 7pm?",
# so these hours get named; the cause (household traffic or the ISP's evening peak) is not asserted.
EVENING_HOURS = range(18, 21)
PHASE_BASELINE_MIN = 60        # rows needed before a night/evening median means anything


def phase_ms(row, name, field):
    """One phase (connect_ms or tls_ms) of a secure web check, or None when it did not load or was not measured."""
    check = ((row or {}).get("internet") or {}).get(name)
    return check.get(field) if isinstance(check, dict) and check.get("ok") else None


def _sample_mean(row, field):
    """Mean of one check phase across the secure hosts in a row, or None when none measured it."""
    vals = [phase_ms(row, name, field) for name in SECURE_HOSTS]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


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


def phase_line(data, now=None):
    """Where a page load's time goes: reaching the site vs the secure handshake, with the age of the reading.

    The INTERNET lines above show the whole page load and already flag "slower than usual"; this says
    which half moved. The two phases fail differently: a slow connect points at the path (router, WAN,
    ISP), a slow handshake at the encrypted setup. Stating both lets a household tell "the line is bad"
    from "the secure setup is slow tonight" without reading raw probes.
    """
    now = time.time() if now is None else now
    last = data[-1] if data else None
    if last is None:
        return "UNKNOWN (no probe row)"
    a = age(last)
    if a is None:
        return "UNKNOWN (last probe has no readable time)"
    if a > STALE_S:
        return f"UNKNOWN (last probe {a / 60:.0f} min old)"
    conn, tls = _sample_mean(last, "connect_ms"), _sample_mean(last, "tls_ms")
    if conn is None or tls is None:
        return f"UNKNOWN (the probe {a:.0f} s ago did not measure both phases)"
    usual = _hour_median(data, "tls_ms", range(24))
    night = _hour_median(data, "tls_ms", NIGHT_HOURS)
    evening = _hour_median(data, "tls_ms", EVENING_HOURS)
    sent = f"reaching the site {conn:.0f} ms, secure handshake {tls:.0f} ms"
    if usual is None:
        return f"{sent}; handshake usually UNKNOWN (too few probes for a baseline); last probe {a:.0f} s ago"
    if night is None or evening is None:
        return f"{sent}; handshake usually {usual:.0f} ms; last probe {a:.0f} s ago"
    return (f"{sent}; handshake usually {usual:.0f} ms"
            f" (nights ~{night:.0f}, evenings ~{evening:.0f}); last probe {a:.0f} s ago")


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
    return spark(means)


def usuality_line(data, now=None):
    """'Was it worse than usual?': web latency now vs the last day's typical, with a trend sparkline.
    Built only from probe rows already loaded for the window; UNKNOWN when too few samples or stale."""
    now = time.time() if now is None else now
    recent = []
    for row in data:
        stamp = row.get("at") if isinstance(row, dict) else None
        if not isinstance(stamp, str):
            continue
        try:
            t = nb.utc_epoch(stamp)
        except (ValueError, TypeError):
            continue
        if now - t <= 86400:
            recent.append(row)
    samples = [web_sample(r) for r in recent]
    good = [v for v in samples if v is not None]
    if len(good) < USUAL_MIN_SAMPLES:
        return f"UNKNOWN (only {len(good)} web samples in the last day; need {USUAL_MIN_SAMPLES})"
    cur = statistics.median(good[-USUAL_RECENT:])
    base_pool = good[:-USUAL_EXCLUDE] if len(good) >= USUAL_EXCLUDE + USUAL_RECENT else good
    base = statistics.median(base_pool)
    ratio = cur / base if base else 1.0
    if ratio < 1.25:
        compare = "about usual"
    elif ratio < 2:
        compare = "slower than usual"
    else:
        compare = "much slower than usual"
    stamps = [nb.utc_epoch(r["at"]) for r in recent]
    span_h = (max(stamps) - min(stamps)) / 3600
    span = f"{span_h:.0f} h" if span_h < 48 else f"{span_h / 24:.1f} d"
    stale = ""
    if data:
        a = age(data[-1])
        if a is not None and a > STALE_S:
            stale = f" (STALE: last probe {a:.0f}s ago)"
    return (f"  {'USUAL':<22}now {cur:.0f} ms vs typical {base:.0f} ms ({compare}, over {span})"
            f"  trend: {day_spark(samples)}{stale}")


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
    """The 'last probe' header line; the cadence beside the age is measured, never a fixed claim."""
    if a is None and data:
        a = age(data[-1])
    return (f"  last probe: {'%ds ago' % a if a is not None else 'never'}   "
            f"({probe_cadence_phrase(data)}; sparklines = last {n} probes)")


def draw():
    data = rows()
    last = data[-1] if data else None
    a = age(last) if last else None
    out = [f"MISHE NETWORK  {time.strftime('%H:%M:%SZ', time.gmtime())}   derived numbers only; UNKNOWN when not measured", ""]
    sha, committed = VERSION
    committed_txt = f", committed {(time.time() - committed) / 3600:.1f} h ago" if committed else ""
    out.append(f"  view code {sha}{committed_txt}; this copy loaded {time.time() - STARTED:.0f} s ago")
    refused = refused_line()
    if refused:
        out.append(refused)
    out.append(f"  HOME INTERNET: {verdict(last, a)}")
    out.append(probe_age_line(data, a, min(len(data), 40)))
    out.append("")
    out.append("  ROUTER AND LOCAL LINK (ping, average of 5)")
    out.append(gateway_line(data))
    out.append("")
    out.append("  INTERNET (secure web pages, full time to finish)")
    out.append(web_line("gstatic.com", "gstatic", data))
    out.append(web_line("cloudflare.com", "cloudflare", data))
    dns = [r.get("dns_ms") for r in data]
    out.append(f"  DNS lookup            {('%.0f ms' % dns[-1]) if dns and dns[-1] is not None else 'UNKNOWN':>10}  {spark(dns)}")
    out.append("  WHERE TIME GOES  " + phase_line(data))
    out.append(usuality_line(data))
    out.append("")
    out.append("  SPEED         " + speed_line(nb_speed_rows()))
    out.append("  UPLOAD        " + speed_up_line(nb_speed_up_rows()))
    out.append("")
    out.append("  OUTAGES (home internet DOWN, from the probes)")
    out.extend(outage_lines(data))
    out.append("  WI-FI         " + wifi_line() + "   (read-only, live read; name from the recorded read)")
    out.append("  WI-FI RADIO   " + wifi_radio_line() + "   (read-only iwinfo on the router)")
    quality = wifi_quality_line().split("\n")
    out.append("  WI-FI QUALITY " + quality[0])
    out.extend(" " * 16 + extra for extra in quality[1:])
    out[-1] += "   (read-only router counters)"
    out.append("  RECORDED 5-MIN " + recorded_clients_line())
    rf = router_facts()
    out.append("  ROUTER        " + (rf or "UNKNOWN (ssh read failed)") + "   (read-only)")
    out.append("  UPLINK        " + uplink_line() + "   (read-only)")
    out.append("  WAN LINK      " + wan_link_line() + "   (read-only, speed and carrier drops)")
    out.append("  UPLINK RATE   " + uplink_rate_line())
    out.append("  ROUTER LOAD   " + router_health_line() + "   (read-only, one read per 5 min)")
    out.append("  VPN           " + vpn_line() + "   (read-only, router process list and tun0)")
    out.append("  HOST GUARD    " + guard_line() + "   (read-only, marker file only)")
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
