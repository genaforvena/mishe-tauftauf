"""The WAKES section of the tending pass: one line per declared mind, never omitted.

The roster is ``roles.declared(home)`` (the charters the site has), the single source of
truth for which minds exist. A mind with no dispatch record is shown as UNKNOWN, not dropped.
Pure: reads dispatch records and yield files, writes nothing.
"""
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mishe_tauftauf import roles  # noqa: E402

STUCK_AFTER_S = 2400


def ago(seconds):
    s = int(seconds)
    return f"{s}s" if s < 120 else f"{s // 60}min" if s < 7200 else f"{s / 3600:.1f}h"


def wakes(home, now, names=None):
    """[(role, line, stuck)] for every declared mind, in roster order."""
    home = str(home)
    names = sorted(roles.declared(home) if names is None else names)
    out = []
    for role in names:
        record_path = os.path.join(home, "dispatch", f"{role}.json")
        try:
            with open(record_path) as fh:
                record = json.load(fh)
            when = os.path.getmtime(record_path)
        except (OSError, ValueError):
            out.append((role, f"{role:13s} UNKNOWN   no dispatch record", False))
            continue
        state = str(record.get("state") or "UNKNOWN")
        stuck = state == "started" and now - when > STUCK_AFTER_S
        yields = sorted(glob.glob(os.path.join(home, "wakes", f"wake-{role}-*.yield.json")), key=os.path.getmtime)
        last = ago(now - os.path.getmtime(yields[-1])) if yields else "never"
        flag = "  <-- STUCK > 40 min" if stuck else ""
        out.append((role, f"{role:13s} {state:10s} since {ago(now - when)} ago; last yield {last} ago{flag}", stuck))
    return out
