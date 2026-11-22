#!/usr/bin/env python3
"""House window: predictions about the house, scored against what actually happened.

Read-only terminal view over features/predictions-score.json (tools/house_predict.py) and the hourly
feature store (features/house-hourly.jsonl, tools/household_features.py). Plain words first, numbers
second, UNKNOWN when something was not measured. At most 41 rows by 170 columns.

  last-value  assumes the next hour looks like the last complete one
  same-hour   the usual value for that hour of the week (of the day until three weeks of history exist)

A net earns attention only by beating both on forecasts made BEFORE the hour; the replay is a
retrospective walk-forward over stored history and is shown separately, never mixed into the live board.
The window command loops this tool; it prints once and exits (3 = no score yet).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import textwrap
import time
from pathlib import Path

DEFAULT_HOME = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
HOUR = 3600
SPARK = "▁▂▃▄▅▆▇█"
STALE_S = 2700
MAX_ROWS, MAX_COLS = 41, 170
SECTIONS = {"lr_light": ("camera", "living room", "luma_mean"), "lr_motion": ("camera", "living room", "motion"),
            "br_light": ("camera", "bedroom", "luma_mean"), "br_motion": ("camera", "bedroom", "motion"),
            "lr_sound": ("audio", "livingroom", "rms_p50"), "lr_loud": ("audio", "livingroom", "rms_p90"),
            "lr_quiet": ("audio", "livingroom", "silent_frac")}


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def fmt(v) -> str:
    if not finite(v):
        return "UNKNOWN"
    return f"{v:.0f}" if abs(v) >= 1000 else f"{v:.3g}"


def hhmm(t) -> str:
    return time.strftime("%m-%d %H:%MZ", time.gmtime(t)) if finite(t) else "UNKNOWN"


def ago(now, t) -> str:
    if not finite(t):
        return "UNKNOWN"
    s = max(0, now - t)
    return f"{int(s // 60)} min ago" if s < 5400 else f"{s / 3600:.1f} h ago"


def read_json(path: Path):
    try:
        doc = json.loads(path.read_text())
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def store_tail(path: Path, rows: int = 30) -> dict:
    """Last `rows` store rows by hour (bounded read: the tail of the file only)."""
    out = {}
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 400_000))
            for raw in fh.read().splitlines()[-rows:]:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(row, dict) and finite(row.get("t")):
                    out[int(row["t"])] = row
    except OSError:
        pass
    return out


def value(row, slug):
    section, room, field = SECTIONS[slug]
    block = row.get(section) if row else None
    feat = block.get(room) if isinstance(block, dict) else None
    if not isinstance(feat, dict):
        return None
    if field == "silent_frac":
        st, n = feat.get("status"), feat.get("n")
        return st.get("SILENT_OR_DROPPED", 0) / n if isinstance(st, dict) and finite(n) and n > 0 else None
    v = feat.get(field)
    return float(v) if finite(v) else None


def spark(values) -> str:
    known = [v for v in values if v is not None]
    if not known:
        return "·" * len(values)
    lo, hi = min(known), max(known)
    return "".join("·" if v is None else SPARK[0 if hi == lo else int((v - lo) / (hi - lo) * 7)] for v in values)


def table(title, rows, labels) -> list:
    # Each horizon's baselines sit on the row; the damped net gets its own continuation line,
    # so the h=24 cell stays at column 96 and inside the MAX_COLS pane width.
    out = [title, f"  {'target':<26}{'next hour (h=1)':<70}{'same hour tomorrow (h=24)'}"]
    for slug, label in labels.items():
        cells, damps = [], []
        for h in (1, 24):
            r = rows.get(f"{slug}|{h}")
            if not r or not r.get("n"):
                cells.append("none yet")
                damps.append(None)
                continue
            m = r["mae"]
            word = {"same-hour beats last-value": "same-hour wins", "last-value beats same-hour": "last-value wins"}.get(r["verdict"], r["verdict"])
            cells.append(f"n={r['n']:<4} last-value {fmt(m['persist']):>6}  same-hour {fmt(m['how']):>6}  {word}")
            d = r.get("damp") or {}
            damps.append(f"damped {fmt((d.get('mae') or {}).get('damp')):>6} n={d.get('n', 0)} {d.get('verdict', '')}"
                         if d.get("n") else None)
        out.append(f"  {label:<26}{cells[0]:<70}{cells[1]}")
        # one continuation line per horizon: the damped verdict can be long, so it never shares a row
        for h, damp in zip((1, 24), damps):
            if damp:
                out.append(f"  {'':<26}h={h:<3}{damp}")
    return out


def render(home: Path, now: float, score: dict, tail: dict, *, max_rows: int = MAX_ROWS) -> str:
    labels = score.get("targets") or {}
    c = int(now // HOUR) * HOUR
    counts, chain = score.get("counts") or {}, score.get("chain") or {}
    live, replay = score.get("live") or {}, score.get("replay") or {}
    lines = [f"HOUSE  forecasts about the house, written before the hour and scored after            now {hhmm(now)}  score {ago(now, score.get('generated'))}"]
    if not finite(score.get("generated")) or now - score["generated"] > STALE_S:
        lines.append("  STALE: the scoreboard is old; the house-predictions duty should refresh it every 15 minutes (UNKNOWN until it does)")
    store = score.get("store") or {}
    lines.append(f"  store: {store.get('hours', 'UNKNOWN')} hourly rows {hhmm(store.get('first'))} .. {hhmm(store.get('last'))} (newest hour ended {ago(now, store.get('last') + HOUR if finite(store.get('last')) else None)})"
                 + ("   STORE IS STALE: no forecast is made from old data" if score.get("stale") else ""))
    scored = counts.get("scored", 0)
    lines.append(f"  log: {counts.get('forecasts', 0)} forecasts, {scored} scored, {counts.get('unobserved', 0)} unobserved (hour not measured: never counted as zero); "
                 f"hash chain {chain.get('state', 'UNKNOWN').upper()}" + (f" at line {chain.get('bad_line')}" if chain.get("state") == "broken" else "")
                 + f"; live since {hhmm(score.get('first_live'))}")
    day = [r for k, r in replay.items() if k.endswith("|24") and r.get("n")]
    hr = [r for k, r in replay.items() if k.endswith("|1") and r.get("n")]
    wins24 = sum(1 for r in day if r["verdict"].startswith("same-hour"))
    wins1 = sum(1 for r in hr if r["verdict"].startswith("last-value"))
    lines.append("")
    reading = ("Reading: " + ("no live forecast has been scored yet; the first land within the hour. " if not scored else
                             f"{scored} live forecasts scored so far. ")
               + (f"On replayed history the usual hour beats 'nothing changes' for tomorrow on {wins24} of {len(day)} targets, "
                  f"and for the next hour 'nothing changes' still wins on {wins1} of {len(hr)}." if day or hr else "No replay is available yet.")
               + " A net matters only when it beats BOTH plain guesses on forecasts made in advance; replay is retrospective and is not live skill.")
    lines += textwrap.wrap(reading, width=MAX_COLS - 4, initial_indent="  ", subsequent_indent="    ")
    lines.append("")
    lines += table("LIVE (made before the hour, scored after)", live, labels)
    lines.append("")
    lines.append("LAST 24 HOURS  ('·' = hour not measured)             last value   next hour: same-hour / last-value    tomorrow, same hour: same-hour")
    nxt = score.get("next") or {}
    hours = [c - HOUR * i for i in range(24, 0, -1)]
    for slug, label in labels.items():
        series = [value(tail.get(t), slug) for t in hours]
        n1, n24 = (nxt.get(slug) or {}).get("1", {}), (nxt.get(slug) or {}).get("24", {})
        lines.append(f"  {label:<26}{spark(series)}   {fmt(series[-1]):>8}      {fmt(n1.get('how')):>8} / {fmt(n1.get('persist')):<8}"
                     f"                  {fmt(n24.get('how')):>8}")
    lines.append("")
    sur = score.get("surprises") or []
    lines.append("SURPRISES (a net with a record missed by 3x its usual error)" + ("" if sur else ": none yet"))
    for s in sur[-3:][::-1]:
        lines.append(f"  {hhmm(s.get('t'))}  {labels.get(s.get('slug'), s.get('slug'))}: saw {fmt(s.get('obs'))}, expected {fmt(s.get('forecast'))}  ({s.get('z')}x)")
    # REPLAY is the most detailed and least urgent section, so it goes last and is the part cut when the pane is short.
    lines.append("")
    head = len(lines)                      # everything before REPLAY, which is what must stay visible
    lines += table("REPLAY (retrospective walk-forward over stored history, last 14 days)", replay, labels)
    lines = [l[:MAX_COLS] for l in lines]
    if len(lines) > max_rows:
        kept = max_rows - 1               # reserve one line for the cut notice
        cut = len(lines) - kept
        if head <= kept:
            note = f"  ... {cut} more lines cut to fit the pane (the REPLAY tail; LIVE, LAST 24 HOURS and SURPRISES are above it)"
        else:
            note = f"  ... {cut} more lines cut to fit the pane; the cut reaches above REPLAY"
        lines = lines[:kept] + [note]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, default=DEFAULT_HOME)
    ap.add_argument("--now", type=float, default=None, help="epoch seconds (tests)")
    a = ap.parse_args(argv)
    score = read_json(a.home / "features" / "predictions-score.json")
    if score is None:
        print("house: UNKNOWN: no prediction score yet (features/predictions-score.json); the house-predictions duty writes it", file=sys.stderr)
        return 3
    now = a.now if a.now is not None else time.time()
    try:
        terminal_rows = os.get_terminal_size().lines
    except OSError:
        terminal_rows = 0
    # The trailing newline needs a row too. A redirected frame uses the default cap.
    max_rows = min(MAX_ROWS, max(1, terminal_rows - 1)) if terminal_rows else MAX_ROWS
    frame = render(a.home, now, score, store_tail(a.home / "features" / "house-hourly.jsonl"),
                   max_rows=max_rows)
    print(frame, end="" if terminal_rows == 1 else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
