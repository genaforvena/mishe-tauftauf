"""Boundary for the network window: decodes probe rows, timestamps and the router's read-only output.

All text interpretation for tools/network_view.py lives here; the view acts on decoded values.
The speed reading's own log is decoded here too, so tools/network_speed.py reads decoded
values and never parses text itself.
"""
import json
import time


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
