#!/usr/bin/env python3
"""Detectors for the network window, router readings and the network mind's improvement.

  window    the outer tmux window "network" exists and its pane runs tools/network_view.py
  progress  the network mind's wall is fresh and network files were committed recently
  uplink    the WAN receive-drop rate (eth0 rx_dropped) from router readings stays under the bound
  vpn       sing-box is running and tun0 is up in the newest router reading
  wifi      the read-only iwinfo channel/noise sub-reading is present and plausible
  load      the router's own load/free-RAM self-limit is not breached for long

Exit: 0 GREEN, 2 RED (the window is missing/wrong, or the mind has gone quiet), 3 UNKNOWN
(tmux or git cannot be read).  Nothing here holds a signal GREEN by hand.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import network_boundary as nb

WALL_MAX_AGE_S = 6 * 3600
COMMIT_MAX_AGE_S = 48 * 3600
FILES = ("tools/network_view.py", "tools/network_boundary.py", "rust/mishe-network-probe", "checks/network-care.py")


RESPAWN = ("tmux respawn-pane -k -c {repo} -t {session}:network "
           "'env MISHE_TAUFTAUF_HOME=$MISHE_SEED_HOME python3 tools/network_view.py'")
WINDOW_FILES = ("tools/network_view.py", "tools/network_boundary.py")


RELOAD_GRACE_S = 30        # the view checks its own files every 10 s; a younger edit has not had a redraw


def window_copy_stale(home, repo):
    """0 when the view's running copy is the code on disk now, 2 when it differs (a copy from before the
    last change, which the view itself should have reloaded) or it never reported its copy, 3 when the
    files cannot be read.  Compared by content, so a commit of unchanged code is not a false RED.  A
    difference within RELOAD_GRACE_S of the newest file write is GREEN: the view reloads on its next
    redraw, and an edit that races the five-minute check is not a stale window yet."""
    try:
        state = json.loads((home / "network" / "window.json").read_text())
        running = str(state["fingerprint"])
    except (OSError, ValueError, KeyError, TypeError):
        print("network-care: the view has not reported its running copy (window.json missing or pre-fingerprint)",
              file=sys.stderr)
        return 2
    try:
        disk = hashlib.sha256()
        written = 0.0
        for rel in WINDOW_FILES:
            path = repo / rel
            written = max(written, path.stat().st_mtime)
            disk.update(path.read_bytes())
    except OSError as exc:
        print(f"network-care: view files unreadable: {exc}", file=sys.stderr)
        return 3
    if running != disk.hexdigest():
        since_write = time.time() - written
        if 0 <= since_write < RELOAD_GRACE_S:
            return 0
        # the running copy's own load time, not the process start time: an os.execv reload keeps the pid
        loaded = float(state.get("loaded", state.get("started", 0)))
        print("network-care: the window runs a copy of the view that differs from the files on disk "
              f"(copy loaded {time.strftime('%H:%M:%SZ', time.gmtime(loaded))}, "
              f"code changed {since_write:.0f}s ago)",
              file=sys.stderr)
        return 2
    return 0


START_SLACK_S = 5          # clock-tick rounding between process start and the view's own timestamp
MAX_START_LAG_S = 600      # the view stamps "started" during its own startup, well within this


def process_argv(pid):
    """The argument vector of the live process with this pid, as a list (None when unreadable)."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def is_view_argv(argv):
    """True only when the process is an interpreter running tools/network_view.py as its script: the
    first argument that is not an interpreter option must be the script itself, not -c/-m code, and a
    script merely mentioning network_view.py later in argv does not count."""
    if not argv or not Path(argv[0]).name.startswith("python"):
        return False
    for arg in argv[1:]:
        if arg in ("-c", "-m"):
            return False
        if arg.startswith("-"):
            continue
        return Path(arg).name == "network_view.py"
    return False


def window_process(home, pane_pid, pane_cmd):
    """0 when the process running in the pane right now is the recorded view, 2 when it is not (a pane
    that was respawned, a process other than the view, a window state that names another pid, or a
    pid that was reused after the view started), 3 when the process cannot be read.  pane_start_command
    alone is not enough: it names what the pane was started with, not what runs in it now."""
    if not pane_pid.isdigit():
        print("network-care: tmux did not report the pane's process; cannot tell", file=sys.stderr)
        return 3
    argv = process_argv(pane_pid)
    if argv is None:
        print(f"network-care: pane process {pane_pid} is not readable; cannot tell", file=sys.stderr)
        return 3
    if not is_view_argv(argv):
        print(f"network-care: pane process {pane_pid} runs {' '.join(argv)!r}, not the network view", file=sys.stderr)
        return 2
    try:
        state = json.loads((home / "network" / "window.json").read_text())
        recorded = str(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0  # no state yet: window_copy_stale reports that case
    if recorded != pane_pid:
        print(f"network-care: the pane runs pid {pane_pid} but the view recorded pid {recorded} "
              "(its fingerprint describes another process)", file=sys.stderr)
        return 2
    started = nb.process_start(pane_pid)
    try:
        recorded_start = float(state["started"])
    except (KeyError, ValueError, TypeError):
        print("network-care: the view recorded no start time; cannot tell whether its pid was reused", file=sys.stderr)
        return 3
    if started is None:
        print(f"network-care: pane process {pane_pid} start time is not readable; cannot tell", file=sys.stderr)
        return 3
    lag = recorded_start - started
    if not (-START_SLACK_S <= lag <= MAX_START_LAG_S):
        print(f"network-care: pid {pane_pid} started {lag:+.0f}s from the view's recorded start; "
              "the pid was reused or the state is stale", file=sys.stderr)
        return 2
    return 0


def window(session, home, repo):
    try:
        r = subprocess.run(["tmux", "list-panes", "-t", f"{session}:network", "-F",
                            "#{pane_dead} #{pane_pid} #{pane_start_command}"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"network-care: tmux unreadable: {exc}", file=sys.stderr)
        return 3
    if r.returncode != 0:
        if "no server" in r.stderr or "error connecting" in r.stderr:
            print("network-care: tmux server unreachable; cannot tell", file=sys.stderr)
            return 3
        print(f"network-care: no window {session}:network", file=sys.stderr)
        return 2
    fields = r.stdout.split(maxsplit=2)
    if len(fields) < 3 or "network_view.py" not in fields[2] or fields[0] != "0":
        print("network-care: window exists but is dead or not running network_view.py", file=sys.stderr)
        return 2
    identity = window_process(home, fields[1], fields[2])
    if identity != 0:
        return identity
    stale = window_copy_stale(home, repo)
    if stale == 2:
        print("network-care: respawn it with: " + RESPAWN.format(session=session, repo=repo.resolve()), file=sys.stderr)
        return 2
    if stale == 3:
        return 3
    print("network-care: window GREEN")
    return 0


def progress(home, repo):
    wall = home / "walls" / "network.md"
    try:
        age = time.time() - wall.stat().st_mtime
    except OSError:
        print("network-care: the network mind has no wall yet", file=sys.stderr)
        return 2
    if age > WALL_MAX_AGE_S:
        print(f"network-care: wall is {age / 3600:.1f}h old (bound {WALL_MAX_AGE_S // 3600}h)", file=sys.stderr)
        return 2
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", *FILES],
                           capture_output=True, text=True, timeout=30)
        stamp = r.stdout.strip()
        if r.returncode != 0 or not stamp.isdigit():
            raise ValueError(r.stderr)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"network-care: git history unreadable: {exc}", file=sys.stderr)
        return 3
    since = time.time() - int(stamp)
    if since > COMMIT_MAX_AGE_S:
        print(f"network-care: no network improvement committed for {since / 3600:.0f}h", file=sys.stderr)
        return 2
    print(f"network-care: progress GREEN wall_age={age:.0f}s last_commit={since / 3600:.1f}h ago")
    return 0


UPLINK_WINDOW_S = 2 * 3600     # the rate is judged over the last two hours of router readings
UPLINK_MIN_SPAN_S = 1.8 * 3600  # a RED rate needs the readings to actually cover that span
UPLINK_MAX_AGE_S = 900         # the newest router reading must be this fresh, else UNKNOWN
UPLINK_RED_PER_HOUR = 400      # rx_dropped per hour above this for the full span is RED


def uplink_state(rows, now):
    """Judge the WAN receive-drop rate from router readings (dicts with 'ts' and 'uplink').

    Returns (code, message): 0 GREEN, 2 RED, 3 UNKNOWN.  A dead or stale probe is UNKNOWN, never GREEN.
    A counter that went backwards (router reboot or driver reset) is UNKNOWN for this window."""
    fresh = []
    for row in rows:
        try:
            ts = float(row["ts"])
            up = row["uplink"]
            dropped = int(up["rx_dropped"])
            errors = int(up["rx_errors"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - ts <= UPLINK_WINDOW_S and ts <= now + START_SLACK_S:
            fresh.append((ts, dropped, errors))
    if not fresh:
        return 3, "network-care: no router uplink reading in the last two hours"
    fresh.sort()
    last_ts, last_drop, last_err = fresh[-1]
    if now - last_ts > UPLINK_MAX_AGE_S:
        return 3, f"network-care: newest router uplink reading is {(now - last_ts) / 60:.0f} min old"
    if last_err > 0:
        return 2, f"network-care: eth0 has {last_err} receive errors (a link fault, not drops)"
    if len(fresh) < 2:
        return 3, "network-care: only one router uplink reading; no rate yet"
    first_ts, first_drop, _ = fresh[0]
    span = last_ts - first_ts
    if last_drop < first_drop:
        return 3, "network-care: rx_dropped counter went backwards in this window (reset); no rate"
    if span <= 0:
        return 3, "network-care: router uplink readings do not span a time; no rate"
    per_hour = (last_drop - first_drop) * 3600.0 / span
    text = f"rx_dropped {per_hour:.0f}/h over {span / 3600:.1f} h"
    if per_hour > UPLINK_RED_PER_HOUR and span >= UPLINK_MIN_SPAN_S:
        return 2, f"network-care: {text}, above {UPLINK_RED_PER_HOUR}/h for the full span"
    return 0, f"network-care: uplink GREEN ({text})"


def router_readings(home):
    """The tail of the router's JSONL log as decoded rows, or None when it cannot be read."""
    path = home / "network" / "router.jsonl"
    rows = []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            tail = fh.read().decode("utf-8", "replace").splitlines()
        if size > 256 * 1024:
            tail = tail[1:]  # the first line may be cut mid-row
        for line in tail:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError as exc:
        print(f"network-care: router readings unreadable: {exc}", file=sys.stderr)
        return None
    return rows


def uplink(home):
    rows = router_readings(home)
    if rows is None:
        return 3
    code, message = uplink_state(rows, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


VPN_MAX_AGE_S = 900         # the newest VPN reading must be this fresh, else UNKNOWN
VPN_WINDOW_S = 30 * 60      # only readings from this window count


def vpn_state(rows, now):
    """Judge sing-box and tun0 from the newest fresh router VPN reading.

    Row shape: {"ts", "ok", "vpn": {"ps_ok", "sing_box", "tun0"}}. Returns (code, message):
    0 GREEN, 2 RED, 3 UNKNOWN. A missing/stale reading, an unreadable process list or an unknown
    process/link is UNKNOWN, never GREEN; a readable "sing-box NOT running" or a tun0 down/absent
    is RED. Like the window's VPN line, this judges the single newest reading: the tunnel process
    either exists at that instant or it does not.
    """
    fresh = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("ok"):
            continue
        v = row.get("vpn")
        if not isinstance(v, dict):
            continue
        try:
            ts = float(row["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= now - ts <= VPN_WINDOW_S and ts <= now + START_SLACK_S:
            fresh.append((ts, v))
    if not fresh:
        return 3, "network-care: no VPN reading in the last 30 minutes"
    fresh.sort(key=lambda item: item[0])
    ts, v = fresh[-1]
    ago = now - ts
    if ago > VPN_MAX_AGE_S:
        return 3, f"network-care: newest VPN reading is {ago / 60:.0f} min old"
    if not v.get("ps_ok"):
        return 3, "network-care: sing-box UNKNOWN (router process list not read)"
    if v.get("sing_box") is False:
        return 2, "network-care: sing-box NOT running on the router"
    tun = v.get("tun0")
    if tun in ("down", "absent"):
        return 2, f"network-care: tun0 {tun.upper()} on the router"
    if v.get("sing_box") is not True or tun != "up":
        return 3, "network-care: VPN state unreadable (unknown process or link)"
    return 0, "network-care: VPN GREEN (sing-box running, tun0 up)"


def vpn(home):
    rows = router_readings(home)
    if rows is None:
        return 3
    code, message = vpn_state(rows, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


WIFI_MAX_AGE_S = 900         # the newest Wi-Fi radio reading must be this fresh, else UNKNOWN
WIFI_WINDOW_S = 30 * 60      # only readings from this window count


def wifi_state(rows, now):
    """Judge the Wi-Fi channel/noise/counter sub-reading from the router readings.

    Row shape: {"ts", "ok", "radios": [{"band", "channel", "freq_ghz", "noise_dbm", "stats"}, ...]}.
    Returns (code, message): 0 GREEN, 2 RED, 3 UNKNOWN. A missing, stale or incomplete reading
    is UNKNOWN; impossible channel or noise values are RED. This detects a dead sub-probe —
    the whole router read can still succeed while iwinfo or iwpriv silently yields nothing.
    """
    fresh = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("ok"):
            continue
        radios = row.get("radios")
        if not isinstance(radios, list) or not radios:
            continue
        try:
            ts = float(row["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= now - ts <= WIFI_WINDOW_S and ts <= now + START_SLACK_S:
            fresh.append((ts, radios))
    if not fresh:
        return 3, "network-care: no Wi-Fi channel/noise reading in the last 30 minutes"
    fresh.sort(key=lambda item: item[0])
    ts, radios = fresh[-1]
    ago = now - ts
    if ago > WIFI_MAX_AGE_S:
        return 3, f"network-care: newest Wi-Fi radio reading is {ago / 60:.0f} min old"
    parts = []
    missing = []
    for radio in radios:
        if not isinstance(radio, dict):
            return 3, "network-care: Wi-Fi radio reading unreadable"
        band, ch, noise = radio.get("band"), radio.get("channel"), radio.get("noise_dbm")
        if not band or not isinstance(ch, int) or not isinstance(noise, int):
            return 3, "network-care: Wi-Fi radio reading incomplete (channel or noise missing)"
        if ch < 1 or ch > 196 or noise >= 0 or noise < -120:
            return 2, f"network-care: impossible Wi-Fi channel {ch} or noise {noise} dBm on {band} GHz"
        parts.append(f"{band} GHz ch {ch}, noise {noise} dBm")
        stats = radio.get("stats")
        if not (isinstance(stats, dict) and isinstance(stats.get("tx_ok"), int)
                and isinstance(stats.get("tx_fail"), int)):
            missing.append(band)
    if len(missing) == len(radios):
        return 3, "network-care: Wi-Fi transmission counters missing from the newest router read"
    if missing:
        return 3, ("network-care: Wi-Fi transmission counters missing on "
                   + ", ".join(f"{band} GHz" for band in missing) + " in the newest router read")
    return 0, "network-care: Wi-Fi radio GREEN (" + "; ".join(parts) + ")"


def wifi(home):
    rows = router_readings(home)
    if rows is None:
        return 3
    code, message = wifi_state(rows, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


LOAD_MAX_AGE_S = 900         # the newest load reading must be this fresh, else UNKNOWN


def load_state(rows, now):
    """Judge the router's own load/free-RAM self-limit from the read-only readings.

    Row shape: {"ts", "router_load": {"load_5m", "load_1m", "mem_available_mb"}}. Returns
    (code, message): 0 GREEN, 2 RED, 3 UNKNOWN. The limit and the run walk are the same ones the
    window prints (``network_boundary.router_over_limit`` / ``load_run``): a short burst is GREEN
    with the breach named, because the 5-minute average the gate reads can spike for one sample;
    an unbroken run of over-limit samples spanning at least 30 minutes is RED. A skipped row
    carries no ``router_load``, so throttled reads neither raise nor hide the alarm; a gap longer
    than 20 minutes between load readings breaks the run rather than joining two breaches.
    """
    sample = nb.load_run(rows, now)
    if sample is None:
        return 3, "network-care: no router load reading in the last six hours"
    ago = sample["ago_s"]
    if ago > LOAD_MAX_AGE_S:
        return 3, f"network-care: newest router load reading is {ago / 60:.0f} min old"
    text = (f"load {sample['load']:.2f} (5 min), free RAM {sample['mem_mb']:.0f} MB; "
            f"limit load {nb.LOAD_LIMIT:g}, free RAM {nb.MEM_FLOOR_MB:.0f} MB")
    if not sample["over"]:
        return 0, f"network-care: router load GREEN ({text})"
    span = sample["span_s"]
    if span >= nb.LOAD_ALARM_SPAN_S:
        return 2, f"network-care: router over its self-limit for {span / 60:.0f} min ({text})"
    return 0, (f"network-care: router over its self-limit for {span / 60:.0f} min, "
               f"under the {nb.LOAD_ALARM_SPAN_S // 60} min alarm ({text})")


def load(home):
    rows = router_readings(home)
    if rows is None:
        return 3
    code, message = load_state(rows, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", choices=("window", "progress", "uplink", "vpn", "wifi", "load"))
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--session", default="plants")
    a = ap.parse_args()
    if a.what == "uplink":
        return uplink(a.home)
    if a.what == "vpn":
        return vpn(a.home)
    if a.what == "wifi":
        return wifi(a.home)
    if a.what == "load":
        return load(a.home)
    return window(a.session, a.home, a.repo) if a.what == "window" else progress(a.home, a.repo)


if __name__ == "__main__":
    sys.exit(main())
