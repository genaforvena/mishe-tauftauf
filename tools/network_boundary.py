"""Boundary for the network window: decodes probe rows, timestamps and the router's read-only output.

All text interpretation for tools/network_view.py lives here; the view acts on decoded values.
The speed reading's own log is decoded here too, so tools/network_speed.py reads decoded
values and never parses text itself.
"""
import json
import math
import os
import re
import time
from pathlib import Path


def decode_rows(text, limit=1440):
    out = []
    for line in text.splitlines()[-limit:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def decode_json_lines(text, limit=None):
    """Decoded rows of a JSON-lines log, oldest first. Malformed lines are skipped.

    Same shape as :func:`decode_rows` without the age filter, for the speed log.
    """
    lines = text.splitlines()
    if limit is not None:
        lines = lines[-limit:]
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def doc_summary(doc):
    """First line of a module docstring, used as the argparse description."""
    return doc.splitlines()[0]


def decode_git_version(stdout):
    """(short sha, commit time) from `git log --format='%h %ct'` output; ValueError when malformed."""
    sha, ct = stdout.split()
    return sha, int(ct)


def parse_argv(parser, argv):
    """The parsed command-line arguments for a parser built by the caller."""
    return parser.parse_args(argv)


def row_age_seconds(row):
    try:
        t = time.mktime(time.strptime(row["at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (KeyError, ValueError, TypeError):
        return None
    return time.time() - t


NEIGHBOUR_STATES = frozenset({"REACHABLE", "STALE", "DELAY", "PROBE", "FAILED", "INCOMPLETE", "NOARP", "PERMANENT"})


def decode_neighbour(stdout):
    """The kernel's state word from `ip -4 neigh show <addr>` output; UNKNOWN when unreadable."""
    fields = stdout.split()
    state = fields[-1] if fields else ""
    return state if state in NEIGHBOUR_STATES else "UNKNOWN"


def decode_router(stdout):
    """(uptime_days, load, wan_up) from the three read-only lines; ValueError when malformed."""
    lines = stdout.splitlines()
    if len(lines) < 3:
        raise ValueError("short router output")
    return float(lines[0]) / 86400, lines[1], '"up":true' in lines[2]


def band_name(freq):
    """'2.472' (the channel frequency in GHz the router reports) -> '2.4'; '5.200' -> '5'."""
    try:
        ghz = float(freq)
    except ValueError:
        return freq
    if 2.4 <= ghz < 2.5:
        return "2.4"
    if 4.9 <= ghz < 5.9:
        return "5"
    if 5.9 <= ghz < 7.2:
        return "6"
    return freq


def decode_wifi_ids(stdout, client_id):
    """Client ids and signal strengths per band from the router's read-only Wi-Fi listing.

    Input: one "BAND <ghz>" line per radio, then one line per associated client
    ("<MAC> <dBm> dBm ..."). Returns {band: [(id, dBm), ...]}. Each MAC is mapped through
    `client_id`, the same short non-reversible hash the router row stores, so a caller can
    look the device's name up in a recorded row without holding an address. ValueError when
    no radio was listed.
    """
    bands, band = {}, None
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "BAND":
            band = band_name(parts[1])
            bands.setdefault(band, [])
        elif band is not None and len(parts) >= 3 and parts[2] == "dBm":
            try:
                bands[band].append((client_id(parts[0]), int(parts[1])))
            except (ValueError, TypeError):
                continue
    if not bands:
        raise ValueError("no radio in router output")
    return bands


def decode_wifi(stdout):
    """Client signal strengths per band from the router's read-only Wi-Fi listing.

    Counts and dBm only, keyed by band; the ids decode_wifi_ids would build are dropped here, so
    no device identity leaves this function. ValueError when no radio was listed.
    """
    return {band: [dbm for _id, dbm in clients]
            for band, clients in decode_wifi_ids(stdout, lambda mac: mac).items()}


def decode_wifi_radio(lines):
    """Channel, frequency and noise floor from one radio's read-only ``iwinfo ... info`` lines.

    Missing or unknown fields stay None (UNKNOWN in the window); no SSID, MAC or signal is kept.
    """
    info = {"channel": None, "freq_ghz": None, "noise_dbm": None}
    for line in lines:
        m = CHANNEL_LINE.search(line)
        if m:
            info["channel"] = int(m.group(1))
            info["freq_ghz"] = float(m.group(2))
        m = NOISE_LINE.search(line)
        if m:
            info["noise_dbm"] = int(m.group(1))
    return info if any(v is not None for v in info.values()) else None


def utc_epoch(stamp):
    """'2026-11-10T09:49:30Z' -> epoch seconds."""
    import calendar
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def is_down(verdict_text):
    return verdict_text.startswith("DOWN")


# Router self-limit: above this 5-minute load, or below this much free RAM, the router is busy and the
# probe holds off (see network_router.backoff_reason) and the window marks it over the limit. The gate
# reads the 5-minute average, not the 1-minute one: a single 1-minute burst (the router sits near 0.1
# and spikes to 1 or 2 for a sample) held the probe off for a whole cooldown and blinded the duty.
LOAD_LIMIT = 1.0
MEM_FLOOR_MB = 120.0


def router_over_limit(rl):
    """True when a router_load dict is over the self-limit, False when within it, None when unmeasured.

    The 5-minute average decides; a row that predates load_5m falls back to the 1-minute value.
    """
    if not isinstance(rl, dict):
        return None
    load = rl.get("load_5m")
    if load is None:
        load = rl.get("load_1m")
    mem = rl.get("mem_available_mb")
    if load is None or mem is None:
        return None
    return load > LOAD_LIMIT or mem < MEM_FLOOR_MB


# A breach is judged from the last six hours of reads. A longer gap than LOAD_RUN_GAP_S between load
# samples ends the run, because a throttled producer writes skip rows without numbers and two
# breaches hours apart are not one sustained breach. LOAD_ALARM_SPAN_S is how long a run must last
# before the network-router-load duty turns RED; the window prints the same span against it.
LOAD_RUN_WINDOW_S = 6 * 3600
LOAD_RUN_GAP_S = 1200
LOAD_ALARM_SPAN_S = 1800
LOAD_RUN_FUTURE_S = 5       # a row may be a few seconds ahead of the caller's clock


def load_run(rows, now):
    """The newest router load sample in the window, and how long its over-limit run has lasted.

    Returns {"ts", "ago_s", "over", "span_s", "load", "mem_mb"}, or None when no row of the last
    ``LOAD_RUN_WINDOW_S`` carries readable load/free-RAM numbers. Rows without numbers (throttled
    reads) are skipped rather than read as calm, and ``span_s`` is zero unless the newest sample
    is over the limit. One walk feeds both the duty and the window, so they cannot disagree about
    how long a breach has lasted.
    """
    samples = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rl = row.get("router_load")
        over = router_over_limit(rl)
        if over is None:
            continue
        try:
            ts = float(row["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= now - ts <= LOAD_RUN_WINDOW_S and ts <= now + LOAD_RUN_FUTURE_S:
            samples.append((ts, over, rl))
    if not samples:
        return None
    samples.sort(key=lambda item: item[0])
    last_ts, last_over, last_rl = samples[-1]
    span = 0.0
    if last_over:
        start = prev = last_ts
        for ts, over, _ in reversed(samples[:-1]):
            if not over or prev - ts > LOAD_RUN_GAP_S:
                break
            start = prev = ts
        span = last_ts - start
    load = last_rl.get("load_5m")
    if load is None:
        load = last_rl.get("load_1m")
    return {"ts": last_ts, "ago_s": now - last_ts, "over": last_over, "span_s": span,
            "load": load, "mem_mb": last_rl.get("mem_available_mb")}


STATION_LINE = re.compile(r"^([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(-?\d+)\s+dBm")
RATE_LINE = re.compile(r"^\s*(RX|TX):\s*([\d.]+)\s*MBit/s")
CHANNEL_LINE = re.compile(r"Channel:\s*(\d+)\s*\(([\d.]+)\s*GHz\)")
NOISE_LINE = re.compile(r"Noise:\s*(-?\d+)\s*dBm")
RADIO_STAT_LINES = {
    "tx_ok": re.compile(r"^\s*Tx success\s*=\s*(\d+)"),
    "tx_fail": re.compile(r"^\s*Tx fail count\s*=\s*(\d+)"),
    "rx_ok": re.compile(r"^\s*Rx success\s*=\s*(\d+)"),
    "rx_crc": re.compile(r"^\s*Rx with CRC\s*=\s*(\d+)"),
    "temp_c": re.compile(r"^\s*CurrentTemperature\s*=\s*(-?\d+)"),
}
# The driver prints its own polling-error rate after the same fail counter: "Tx fail count = 11221647, PER=3.3%".
# It counts lost frames, not failed attempts, so it is kept apart from the counter arithmetic.
RADIO_PERCENT_LINES = {
    "tx_per": re.compile(r"^\s*Tx fail count\s*=\s*\d+,\s*PER=([\d.]+)%"),
}
# The home router has one access point per band: ra0 on 2.4 GHz, rax0 on 5 GHz. The probe's remote read
# asks for exactly these, and a good row carries both; a row that carries only one band means that radio
# (or its iwinfo/iwpriv pass) went silent, not that the house has one band. The probe builds its radio
# map and its remote loop from this, so a request and an expectation cannot drift apart.
RADIO_BANDS = (("ra0", "2.4"), ("rax0", "5"))
EXPECTED_BANDS = tuple(band for _name, band in RADIO_BANDS)


def decode_radio_stats(lines):
    """Transmit/receive counters and temperature from one radio's read-only ``iwpriv ... stat`` lines.

    All counters are cumulative since the router booted, so they are useful only as a difference
    between two reads. A counter the driver did not print stays out of the dict (None in the
    window), and a radio that printed none of them yields None, never a clean radio. ``tx_per`` is
    the driver's own frame-error rate in percent, kept as a float; older reads do not carry it.
    """
    stats = {}
    for line in lines:
        for key, pattern in RADIO_STAT_LINES.items():
            m = pattern.match(line)
            if m:
                stats[key] = int(m.group(1))
        for key, pattern in RADIO_PERCENT_LINES.items():
            m = pattern.match(line)
            if m:
                stats[key] = float(m.group(1))
    return stats or None


def decode_router_output(stdout, bands, client_id):
    """Row body from the router's combined read-only output; ValueError when it is not what we asked for.

    ``bands`` maps radio name to GHz; ``client_id`` maps a MAC to its short hash. The MAC itself is
    never kept in the returned body.
    """
    sections = {}
    current = None
    uptime_line = None
    for line in stdout.splitlines():
        if line.startswith("UPTIME "):
            uptime_line = line.split(None, 1)[1]
            current = None
        elif line in ("LEASES", "WAN", "LOAD", "LINKS", "VPN", "PHY") or line.startswith("RADIO "):
            current = line
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    if uptime_line is None:
        raise ValueError("no uptime line from the router")
    uptime_s = float(uptime_line)

    names = {}
    for line in sections.get("LEASES", []):
        parts = line.split()
        if len(parts) >= 4:
            host = parts[3] if parts[3] != "*" else ""
            names[parts[1].lower()] = host or "unnamed device"

    radios = []
    clients = []
    for radio, band in bands.items():
        key = "RADIO " + radio
        lines = sections.get(key, [])
        info = decode_wifi_radio(lines)
        if info is None:
            # iwinfo printed no channel or noise (or the remote read never reached this radio): keep the
            # band in the row with every field None, so a silent radio is evidence a reader can see, never
            # a band that quietly vanished from the read and left a calm one-band row behind.
            info = {"channel": None, "freq_ghz": None, "noise_dbm": None}
        radios.append({"band": band, **info, "stats": decode_radio_stats(lines)})
        current_station = None
        for line in lines:
            m = STATION_LINE.match(line.strip())
            if m:
                mac = m.group(1)
                current_station = {"id": client_id(mac), "name": names.get(mac.lower(), "unnamed device"),
                                   "band": band, "dbm": int(m.group(2)), "rx": None, "tx": None}
                clients.append(current_station)
                continue
            r = RATE_LINE.match(line)
            if r and current_station is not None:
                current_station["rx" if r.group(1) == "RX" else "tx"] = float(r.group(2))

    wan = None
    wan_device = None
    wan_text = "\n".join(sections.get("WAN", [])).strip()
    if wan_text:
        try:
            status = json.loads(wan_text)
            wan = {"up": bool(status.get("up")), "uptime_s": int(status.get("uptime", 0))}
            wan_device = status.get("l3_device") or status.get("device") or None
        except (ValueError, TypeError, AttributeError):
            wan = None
    uplink = None
    if wan_device and isinstance(wan_device, str):
        uplink = decode_link_counters(sections.get("LINKS", []), wan_device)
    return {"uptime_s": round(uptime_s), "wan": wan, "uplink": uplink, "clients": clients,
            "radios": radios,
            "router_load": decode_router_load(sections.get("LOAD", [])),
            "vpn": decode_router_vpn(sections.get("VPN", [])),
            "wan_link": decode_wan_link(sections.get("PHY", []))}


def decode_wan_link(lines):
    """Physical WAN link from the router's ``/sys/class/net/<device>`` reads, or None when absent.

    ``device`` is the default route's device. ``speed_mbps``, ``duplex``, ``carrier_up`` and
    ``carrier_down`` are the kernel's own values; a field the kernel did not report stays None, never
    zero, so a driver without a speed file cannot look like a negotiated link.
    """
    fields = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            fields[parts[0]] = parts[1]
    device = fields.get("device")
    if not device:
        return None

    def number(key):
        value = fields.get(key)
        return int(value) if value is not None and value.isdigit() else None

    return {"device": device, "speed_mbps": number("speed"), "duplex": fields.get("duplex"),
            "carrier_up": number("carrier_up_count"), "carrier_down": number("carrier_down_count")}


def decode_router_vpn(lines):
    """Router VPN state: {"ps_ok", "sing_box", "tun0", "tun0_rx_bytes", "tun0_tx_bytes"}. Read-only;
    the process command line is never kept.

    ``ps_ok`` False means the process list could not be read, so ``sing_box`` is None (unknown, not
    "not running"). ``tun0`` is "up", "down" or "absent"; a missing section gives ``ps_ok`` False and
    ``tun0`` None, never a calm reading. The byte counters are the kernel's own, for judging whether
    the tunnel carries traffic; a counter that was not read is None, never zero, so a missing read
    cannot look like an idle tunnel.
    """
    if not lines:
        return {"ps_ok": False, "sing_box": None, "tun0": None,
                "tun0_rx_bytes": None, "tun0_tx_bytes": None}
    ps_ok = any(line.strip() == "PS_OK" for line in lines)
    sing_box = None
    if ps_ok:
        sing_box = any("sing-box" in line for line in lines)
    tun0 = "absent"
    counters = {}
    for line in lines:
        parts = line.split()
        if parts and parts[0] == "tun0":
            flags = parts[2].strip("<>").split(",") if len(parts) > 2 else []
            tun0 = "up" if "UP" in flags and parts[1] != "DOWN" else "down"
        elif len(parts) == 3 and parts[0] == "TUN0_STAT" and parts[2].isdigit():
            counters[parts[1]] = int(parts[2])
    return {"ps_ok": ps_ok, "sing_box": sing_box, "tun0": tun0,
            "tun0_rx_bytes": counters.get("rx_bytes"), "tun0_tx_bytes": counters.get("tx_bytes")}


def decode_router_load(lines):
    """Router self-limit numbers from /proc/loadavg and MemAvailable: {"load_1m", "load_5m", "mem_available_mb"}.

    A missing or unreadable field is None, never zero, so a dead read cannot look like an idle router.
    """
    load_1m = None
    load_5m = None
    mem_mb = None
    for line in lines:
        parts = line.split()
        if load_1m is None and len(parts) >= 3 and parts[0].replace(".", "", 1).isdigit():
            load_1m = float(parts[0])
            if len(parts) >= 2 and parts[1].replace(".", "", 1).isdigit():
                load_5m = float(parts[1])
        elif len(parts) >= 2 and parts[0] == "MemAvailable:" and parts[1].isdigit():
            mem_mb = round(int(parts[1]) / 1024, 1)
    if load_1m is None and mem_mb is None:
        return None
    return {"load_1m": load_1m, "load_5m": load_5m, "mem_available_mb": mem_mb}


def decode_link_counters(lines, device):
    """RX/TX errors and drops of one interface from ``ip -s link show`` output, or None when absent.

    Cumulative since the router booted (kernel counters). An interface block is a header
    "N: name[@peer]: <flags>", then "RX:" and "TX:" column titles, each followed by one numbers line
    (RX: bytes packets errors dropped missed mcast; TX: bytes packets errors dropped carrier collsns).
    """
    counters = {}
    current = None
    for i, line in enumerate(lines):
        head = line.split()
        if line[:1].isdigit() and len(head) >= 2 and head[1].endswith(":"):
            current = head[1].rstrip(":").split("@")[0]
            continue
        tag = line.strip()[:3]
        if current != device or tag not in ("RX:", "TX:") or i + 1 >= len(lines):
            continue
        nums = lines[i + 1].split()
        if len(nums) >= 4 and all(n.isdigit() for n in nums[:4]):
            side = tag[:2].lower()
            counters[f"{side}_errors"] = int(nums[2])
            counters[f"{side}_dropped"] = int(nums[3])
    if len(counters) != 4:
        return None
    return {"device": device, **counters}


def counter_steps(values):
    """(total, restarted): what a cumulative counter went *up by* across these readings, and whether it
    ever went backwards.

    A kernel counter restarts at a router reboot or a driver reset.  Comparing only the endpoints
    misses a restart that sits inside the window: 1, 5, 2, 4 reads as 3 instead of 8.  A step
    backwards is therefore a restart: the value seen since it counts, and ``restarted`` says the
    total is a lower bound (whatever happened between the last read before the restart and the
    restart itself was never observed)."""
    total = 0
    restarted = False
    prev = None
    for value in values:
        if prev is not None:
            if value >= prev:
                total += value - prev
            else:
                total += value
                restarted = True
        prev = value
    return total, restarted


def decode_router_rows(text):
    """Router log rows that carry a numeric ``ts``, oldest first. Malformed lines are skipped."""
    return [row for row in decode_json_lines(text) if isinstance(row.get("ts"), (int, float))]


DEVICE_PING_MAX_AGE_S = 15 * 60
DEVICE_GAP_S = 900  # device pings run about every 3.5 min; a silence this long is unmeasured, not silent


def read_device_ping_rows(path):
    """Expose an unreadable final row even when the log contains older usable samples."""
    text = path.read_text(encoding="utf-8").rstrip()
    if not text:
        return []
    history, _, last = text.rpartition("\n")
    newest = decode_json_lines(last)
    if not newest:
        raise ValueError("newest per-device ping row is unreadable")
    return decode_json_lines(history) + newest


def _nonnegative_number(value):
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


def device_ping_sample(rows, now):
    """Return (devices, age in seconds, problem) for the newest timestamped sample."""
    if not rows:
        return None, None, "no per-device ping reading yet"
    last = rows[-1]
    if not isinstance(last, dict) or not _nonnegative_number(last.get("ts")):
        return None, None, "newest per-device ping timestamp is unreadable"
    timed = [row for row in rows if isinstance(row, dict) and _nonnegative_number(row.get("ts"))]
    newest = max(timed, key=lambda row: row["ts"])
    age = now - newest["ts"]
    if age > DEVICE_PING_MAX_AGE_S:
        return None, age, f"newest per-device ping reading is {age / 60:.0f} min old (STALE)"
    if age < -5:
        return None, age, "per-device ping reading is from the future"
    devices = newest.get("devices")
    if not isinstance(devices, dict) or not devices:
        return None, age, "newest per-device ping row has no device results"
    return devices, max(age, 0), None


def device_outage_phrase(rows, name, now, horizon=86400.0, paused=None):
    """Silent-ping runs for one device in the last horizon seconds, in plain words ("" when none).

    The newest row can be GREEN while the device was silent for hours before it; this keeps that
    history visible without turning an answering device into a failure. A run is consecutive failed
    samples; its length is the span between its first and last failed sample. A gap of more than
    DEVICE_GAP_S between two samples was not measured, so it never extends a run: the run ends at
    its last failed sample before the gap.
    """
    runs = []
    start = end = None
    prev = None
    for row in rows:
        if not isinstance(row, dict) or not _nonnegative_number(row.get("ts")):
            continue
        ts = row["ts"]
        if ts > now or now - ts > horizon:
            continue
        if prev is not None and ts - prev > DEVICE_GAP_S and start is not None:
            runs.append((start, end))
            start = None
        prev = ts
        result = (row.get("devices") or {}).get(name) if isinstance(row.get("devices"), dict) else None
        if not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            if start is None:
                start = ts
            end = ts
        elif start is not None:
            runs.append((start, end))
            start = None
    if start is not None:
        runs.append((start, end))
    if not runs:
        return ""
    longest_h = max(e - s for s, e in runs) / 3600
    last = time.strftime("%H:%MZ", time.gmtime(runs[-1][1]))
    noun = "run" if len(runs) == 1 else "runs"
    # A run with no containment pause logged across it is an unexplained silence, never a calm line.
    unexplained = sum(1 for s, e in runs if not (paused is not None and paused(s, e)))
    why = (f"; {unexplained} with no containment trip logged, UNKNOWN not calm" if unexplained else
           "; each inside a containment pause")
    return (f"{name} silent in {len(runs)} {noun} over the last 24 h (longest {longest_h:.1f} h, "
            f"last ended {last}{why})")


def device_ping_plain_problem(result):
    """The reply problem in plain words for the window and the detector alike.

    When the kernel's neighbour state says ARP got no answer, the device is silent on the LAN (asleep,
    off, or firewalled), not a fault on our side, so say that instead of echoing "no reply (no reply)".
    """
    problem = device_ping_reply_problem(result)
    if problem is None:
        return None
    neigh = result.get("neigh") if isinstance(result, dict) else None
    if neigh in ("FAILED", "INCOMPLETE"):
        return "not answering the LAN (no ARP answer)"
    if neigh == "PROBE":
        # The kernel is still actively probing for it: not a verdict yet, and not "no reply (no reply)".
        return "not answering the LAN yet (ARP still probing)"
    return problem


def device_ping_reply_problem(result):
    """Describe a failed or malformed reply, or return None for a usable measurement."""
    if not isinstance(result, dict):
        return "unreadable ping result"
    if result.get("ok") is False:
        why = result.get("why")
        if isinstance(why, str) and why and why != "no reply":
            return f"no reply ({why})"
        return "no reply"
    avg = result.get("avg_ms")
    loss = result.get("loss_pct")
    if result.get("ok") is not True:
        return "unreadable ping result"
    if not _nonnegative_number(avg) or not _nonnegative_number(loss) or loss >= 100:
        return "unreadable ping result"
    return None


def stderr_summary(stderr, limit=120):
    """First ``limit`` characters of a failed probe's stderr, stripped."""
    return (stderr or "").strip()[:limit]


def process_start(pid):
    """Epoch seconds at which the live process with this pid started (None when unreadable).  The kernel
    keeps this across os.execv, so a view that reloads itself in place keeps its start time."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat.rsplit(")", 1)[1].split()
        ticks = int(fields[19])  # field 22 (starttime), counted after the parenthesised command name
        btime = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                     if line.startswith("btime "))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


PING_RTT_LINE = re.compile(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)")
PING_LOSS_LINE = re.compile(r"(\d+(?:\.\d+)?)% packet loss")


def decode_ping(stdout, returncode):
    """{"ok", "loss_pct", "avg_ms", "max_ms"} from ping output, or {"ok": False, "why": ...}.

    A reply that carries no round-trip figures is not a pass: it is reported as not ok.
    """
    loss = PING_LOSS_LINE.search(stdout)
    rtt = PING_RTT_LINE.search(stdout)
    if loss is None:
        return {"ok": False, "why": "ping output unreadable"}
    loss_pct = float(loss.group(1))
    if rtt is None or loss_pct >= 100.0:
        return {"ok": False, "loss_pct": loss_pct, "why": "no reply"}
    return {
        "ok": True,
        "loss_pct": loss_pct,
        "avg_ms": float(rtt.group(2)),
        "max_ms": float(rtt.group(3)),
    }
