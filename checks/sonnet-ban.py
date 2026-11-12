#!/usr/bin/env python3
"""Detector: the Sonnet ban holds while any live routing path names a Sonnet model.

Human order (Ilya, 2026-11-10): NO Sonnet anywhere until the Claude weekly limit
resets (~2026-11-11T03:00Z).  This reads the live engine role configs, the router
routes and the routing ledger, and goes RED when any of them names a Sonnet model
while the ban is in force, so the resolver removes the reference before a wake
selects it.  Historical evidence (backups, ledger rows inside the lookback that
predate the ban) does not count; only what a wake could select or did select now.

Exit: 0 GREEN (no live Sonnet reference), 2 RED (a live config or a recent
selection names a Sonnet model), 3 UNKNOWN (files unreadable).
"""
import argparse
import calendar
import json
import os
import re
import sys
import time
from pathlib import Path

SONNET = re.compile(r"sonnet", re.IGNORECASE)
BAN_UNTIL_DEFAULT = ""  # 2026-11-12 ban (standing approvals): open-ended until Ilya lifts it
LOOKBACK_S = 6 * 3600  # ledger selections newer than this are "recent"


def ts(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--until", default=BAN_UNTIL_DEFAULT, help="ban end, UTC; empty means no end")
    a = ap.parse_args()
    now = time.time()
    if a.until and now >= ts(a.until):
        print(f"sonnet-ban: ban expired at {a.until}; retire this duty")
        return 0

    hits = []
    roles = a.home / "engines"
    try:
        for cfg in sorted(roles.glob("*.json")):
            if cfg.name.endswith((".bak",)) or ".haiku-fallback" in cfg.name:
                continue
            data = json.loads(cfg.read_text())
            model = str(data.get("model", ""))
            if SONNET.search(model):
                hits.append(f"engines/{cfg.name} model {model}")
        routes = json.loads((a.home / "engine" / "routes.json").read_text()) if (a.home / "engine" / "routes.json").is_file() else {}
        for role, cands in routes.get("roles", {}).items():
            for cand in cands:
                if SONNET.search(str(cand.get("model", ""))):
                    hits.append(f"routes.json {role} -> {cand.get('model')}")
    except (OSError, ValueError) as exc:
        print(f"sonnet-ban: config unreadable: {exc}", file=sys.stderr)
        return 3

    ledger = a.home / "engine" / "routing-ledger.jsonl"
    if ledger.is_file():
        try:
            for line in ledger.read_text().splitlines()[-2000:]:
                try:
                    ev = json.loads(line)
                    at = calendar.timegm(time.strptime(ev["at"], "%Y-%m-%dT%H:%M:%SZ"))
                except (ValueError, KeyError, TypeError):
                    continue
                if ev.get("event") == "select" and now - at <= LOOKBACK_S and SONNET.search(str(ev.get("key", ""))):
                    hits.append(f"ledger {ev['at']} {ev.get('role')} selected {ev.get('key')}")
        except OSError as exc:
            print(f"sonnet-ban: ledger unreadable: {exc}", file=sys.stderr)
            return 3
    else:
        print("sonnet-ban: no routing ledger", file=sys.stderr)
        return 3

    if hits:
        print("sonnet-ban: SONNET WHILE BAN HOLDS -> " + "; ".join(hits), file=sys.stderr)
        return 2
    print("sonnet-ban: GREEN no live config or recent selection names a Sonnet model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
