#!/usr/bin/env python3
"""Outer window: is Mishe healthy right now, in plain words.

Runs the read-only tending pass (SITE/operator/tend/tend.py) at most once every
REFRESH_S seconds and shows its verdict first, then the facts behind it: guard, units,
wakes, signals, delivery and resources.  Anything the pass cannot measure, or that is
too old, is shown as UNKNOWN.  Standard library only; the window never writes anywhere.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
TEND = SITE / "operator" / "tend" / "tend.py"
REFRESH_S = 300
TIMEOUT_S = 120
WIDTH = 170
WINDOW_ROWS = 41
_cache = {"at": 0.0, "text": None, "error": None}


def run_tend():
    """The tending pass output, cached; (text, error) where error is None when the pass ran."""
    now = time.time()
    if _cache["text"] is not None and now - _cache["at"] < REFRESH_S:
        return _cache["text"], _cache["error"]
    if not TEND.is_file():
        text, error = None, f"tending pass not found at {TEND}"
    else:
        try:
            r = subprocess.run([sys.executable, str(TEND)], capture_output=True, text=True, timeout=TIMEOUT_S)
            # The pass exits 1 when it flags NEEDS ATTENTION; that is a result, not a failure.
            ok = r.stdout.startswith("# Mishe tending pass")
            text, error = (r.stdout, None) if ok else (None, f"tending pass exit {r.returncode}, no report")
        except (OSError, subprocess.SubprocessError) as exc:
            text, error = None, f"tending pass did not run: {exc.__class__.__name__}"
    _cache.update(at=now, text=text, error=error)
    return text, error


def sections(text):
    """Split the pass output into {heading: [lines]} and keep the header line."""
    out, head, cur = {}, "", None
    for line in text.splitlines():
        if line.startswith("# "):
            head = line[2:].strip()
        elif line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur is not None and line.strip():
            out[cur].append(line.strip())
    return head, out


def verdict_of(secs):
    """Plain one-word verdict: RED when a signal is RED, NEEDS ATTENTION when the pass says so, else OK."""
    v = secs.get("verdict", [])
    if not v:
        return "UNKNOWN", "the pass gave no verdict"
    unmeasured = sum(int(m.group(1)) for l in secs.get("signals", []) if (m := re.match(r"UNKNOWN \((\d+)\)", l)))
    note = f"; {unmeasured} signal(s) UNKNOWN (not measured, not GREEN)" if unmeasured else ""
    if any(l.startswith("RED") for l in secs.get("signals", [])):
        return "RED", "a signal is RED; see below" + note
    if v[0].startswith("NEEDS ATTENTION"):
        return "NEEDS ATTENTION", ("; ".join(l.lstrip("- ") for l in v[1:]) or "see the list below") + note
    return "OK", "nothing flagged" + note


def fit(line):
    return line if len(line) <= WIDTH else line[: WIDTH - 1] + "…"


def render(now=None):
    now = now or time.time()
    text, error = run_tend()
    rows = []
    stamp = time.strftime("%H:%MZ", time.gmtime(now))
    rows.append(f"HEALTH OF MISHE   as of {stamp}   (read-only; refreshes every {REFRESH_S // 60} min)")
    rows.append("")
    if text is None:
        rows.append("VERDICT: UNKNOWN")
        rows.append(f"  Why: {error}. Nothing here is measured, so nothing is GREEN.")
        rows.append("  Next: steward checks the tending pass; this window keeps retrying.")
        return [fit(r) for r in rows]
    head, secs = sections(text)
    verdict, why = verdict_of(secs)
    rows.append(f"VERDICT: {verdict}")
    rows.append(f"  Why: {why}")
    rows.append(f"  Pass: {head or 'tending pass'}")
    attention = secs.get("verdict", [])[1:]
    if attention:
        rows.append("")
        rows.append("NEEDS YOU OR STEWARD")
        rows.extend("  " + l for l in attention[:4])
    for name in ("guard, hold, views", "units", "wakes", "signals", "delivery", "resources"):
        if name in secs:
            rows.append("")
            rows.append(name.upper())
            rows.extend("  " + l for l in secs[name][:5])
    return [fit(r) for r in rows[:WINDOW_ROWS]]


def main():
    once = "--once" in sys.argv
    while True:
        lines = render()
        if not once:
            sys.stdout.write("\033[H\033[2J")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        if once:
            return 0
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
