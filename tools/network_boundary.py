"""Boundary for the network window: decodes probe rows, timestamps and the router's read-only output.

All text interpretation for tools/network_view.py lives here; the view acts on decoded values.
The speed reading's own log is decoded here too, so tools/network_speed.py reads decoded
values and never parses text itself.
"""
import json
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


def decode_router(stdout):
    """(uptime_days, load, wan_up) from the three read-only lines; ValueError when malformed."""
    lines = stdout.splitlines()
    if len(lines) < 3:
        raise ValueError("short router output")
    return float(lines[0]) / 86400, lines[1], '"up":true' in lines[2]


def decode_wifi(stdout):
    """Client signal strengths per band from the router's read-only Wi-Fi listing.

    Input: one "BAND <ghz>" line per radio, then one line per associated client
    ("<MAC> <dBm> dBm ..."). Returns {band: [dBm, ...]}; MAC addresses are dropped here,
    so no device identity leaves this function. ValueError when no radio was listed.
    """
    bands, band = {}, None
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "BAND":
            band = parts[1]
            bands.setdefault(band, [])
        elif band is not None and len(parts) >= 3 and parts[2] == "dBm":
            try:
                bands[band].append(int(parts[1]))
            except ValueError:
                continue
    if not bands:
        raise ValueError("no radio in router output")
    return bands


def utc_epoch(stamp):
    """'2026-11-10T09:49:30Z' -> epoch seconds."""
    import calendar
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def is_down(verdict_text):
    return verdict_text.startswith("DOWN")


# Router self-limit: above this 1-minute load, or below this much free RAM, the router is busy and the
# probe holds off (see network_router.backoff_reason) and the window marks it over the limit.
LOAD_LIMIT_1M = 1.0
MEM_FLOOR_MB = 120.0


STATION_LINE = re.compile(r"^([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(-?\d+)\s+dBm")
RATE_LINE = re.compile(r"^\s*(RX|TX):\s*([\d.]+)\s*MBit/s")


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
        elif line in ("LEASES", "WAN", "LOAD", "LINKS") or line.startswith("RADIO "):
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

    clients = []
    for key in [k for k in sections if k.startswith("RADIO ")]:
        radio = key.split()[1]
        if radio not in bands:
            continue
        current_station = None
        for line in sections[key]:
            m = STATION_LINE.match(line.strip())
            if m:
                mac = m.group(1)
                current_station = {"id": client_id(mac), "name": names.get(mac.lower(), "unnamed device"),
                                   "band": bands[radio], "dbm": int(m.group(2)), "rx": None, "tx": None}
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
            "router_load": decode_router_load(sections.get("LOAD", []))}


def decode_router_load(lines):
    """Router self-limit numbers from /proc/loadavg and MemAvailable: {"load_1m", "mem_available_mb"}.

    A missing or unreadable field is None, never zero, so a dead read cannot look like an idle router.
    """
    load_1m = None
    mem_mb = None
    for line in lines:
        parts = line.split()
        if load_1m is None and len(parts) >= 3 and parts[0].replace(".", "", 1).isdigit():
            load_1m = float(parts[0])
        elif len(parts) >= 2 and parts[0] == "MemAvailable:" and parts[1].isdigit():
            mem_mb = round(int(parts[1]) / 1024, 1)
    if load_1m is None and mem_mb is None:
        return None
    return {"load_1m": load_1m, "mem_available_mb": mem_mb}


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


def decode_router_rows(text):
    """Router log rows that carry a numeric ``ts``, oldest first. Malformed lines are skipped."""
    return [row for row in decode_json_lines(text) if isinstance(row.get("ts"), (int, float))]


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
