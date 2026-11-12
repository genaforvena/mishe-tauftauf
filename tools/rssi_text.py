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


def parse_cli(argv, description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--site", required=True)
    p.add_argument("--now", help="ISO UTC time (testing); default host clock")
    return p.parse_args(argv)
