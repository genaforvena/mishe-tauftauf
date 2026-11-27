"""Parsing boundary for tools/rssi_fingerprint.py: ISO time, JSON reads, command line.

Kept separate so the fingerprint arithmetic stays free of parsing sites; listed in
parsing-policy.json as a named adapter (see tools/check_parsing.py).
"""
import argparse
import datetime
import json


def parse_iso(s):
    t = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    return t.replace(tzinfo=datetime.timezone.utc)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parse_json_line(line):
    """One JSON-lines row (router log) -> the decoded object; raises ValueError when malformed."""
    return json.loads(line)


def parse_cli(argv, description, extra=None):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--site", required=True)
    p.add_argument("--now", help="ISO UTC time (testing); default host clock")
    p.add_argument("--log", help="Redmi10 observations log (default: the source named by rssi-table.json)")
    if extra is not None:
        extra(p)
    return p.parse_args(argv)


def parse_iso_offset(s):
    """Camera bucket start such as 2026-11-06T22:30:00+00:00 -> aware datetime."""
    return datetime.datetime.fromisoformat(s)


def parse_obs_line(line):
    """One Redmi10 observations log line -> (epoch, "rssi", dbm) | (epoch, "accel", (x, y, z)) | None.

    Only the two kinds the RSSI motion study reads; every other or malformed line is None.
    """
    if " wifi_link_rssi_dbm=" not in line and " acceleration x=" not in line:
        return None
    parts = line.split()
    try:
        epoch = int(parts[1])
        if parts[2].startswith("wifi_link_rssi_dbm="):
            return epoch, "rssi", int(parts[2].split("=", 1)[1])
        if parts[2] == "acceleration":
            xyz = {k: float(v) for k, v in (kv.split("=", 1) for kv in parts[3:6])}
            return epoch, "accel", (xyz["x"], xyz["y"], xyz["z"])
    except (IndexError, ValueError, KeyError):
        return None
    return None
