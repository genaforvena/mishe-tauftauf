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
    # Blocks in display order: (title, lines, protected). WAKES is the pulse of the plant:
    # every mind gets its line and nothing else is allowed to push it out of the window.
    blocks = []
    attention = secs.get("verdict", [])[1:]
    if attention:
        blocks.append(("NEEDS YOU OR STEWARD", ["  " + l for l in attention[:4]], False))
    for name in ("guard, hold, views", "units", "wakes", "signals", "delivery", "resources"):
        if name in secs:
            protected = name == "wakes"
            body = secs[name] if protected else secs[name][:5]
            blocks.append((name.upper(), ["  " + l for l in body], protected))
    budget = WINDOW_ROWS - len(rows)
    budget -= sum(2 + len(body) for _, body, protected in blocks if protected)
    keep = {}
    for i, (title, body, protected) in enumerate(blocks):
        if protected:
            keep[i] = body
            continue
        need = 2 + len(body)                       # blank line, heading, body
        if need <= budget:
            keep[i], budget = body, budget - need
        elif budget > 2:                           # trim this block to what still fits
            keep[i], budget = body[: budget - 2], 0
    for i, (title, _, _) in enumerate(blocks):
        if i in keep:
            rows.append("")
            rows.append(title)
            rows.extend(keep[i])
    return [fit(r) for r in rows]


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
