"""Boundary for the network window: decodes probe rows, timestamps and the router's read-only output.

All text interpretation for tools/network_view.py lives here; the view acts on decoded values.
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
