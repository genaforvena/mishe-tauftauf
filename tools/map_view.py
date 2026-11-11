#!/usr/bin/env python3
"""Outer window: the apartment map as a best guess, in plain words.

Read-only, dependency-free. Re-reads the derived files that
senses/map/render_map.py writes and redraws every 30 seconds (like
household_view.py). It does not sample cameras, phones or labels itself.
Run `python3 -I senses/map/render_map.py` to refresh them.

Usage:
  map_view.py          loop, redraw every 30 s until interrupted
  map_view.py --once   print one frame and exit

Missing, unreadable or stale files are shown as UNKNOWN, never as a guess.
"""
import datetime
import json
import os
import sys
import textwrap
import time
from pathlib import Path

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME")
            or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
MAP_DIR = SITE / "senses" / "map"
PEOPLE = ["ilya", "rozalia", "bruno"]
NAMES = {"ilya": "Ilya", "rozalia": "Rozalia", "bruno": "Bruno"}
REDRAW_EVERY = 30          # seconds
WIDTH = 100                # fits a 172-column pane with room to spare
STALE_AFTER = 30 * 60      # map-state older than this is flagged, not trusted


def room_words(room):
    return (room or "none").replace("_", " ")


def read_map_text():
    """The schematic only: the lines before the first person line.

    The per-person lines in apartment-map.txt are replaced by the wrapped
    guesses printed below, so long evidence strings never overflow the pane.
    """
    try:
        lines = (MAP_DIR / "apartment-map.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "Map UNKNOWN: senses/map/apartment-map.txt is missing or unreadable."
    schematic = []
    for line in lines:
        if line[:1].isalpha():
            break
        schematic.append(line.rstrip())
    while schematic and not schematic[-1]:
        schematic.pop()
    return "\n".join(schematic) or "Map UNKNOWN: apartment-map.txt has no schematic."


def read_state():
    try:
        return json.loads((MAP_DIR / "map-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parse_utc(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def age_note(state, now):
    """Return (text, stale) describing how old the rendered guess is."""
    generated = parse_utc((state or {}).get("generated_utc"))
    if generated is None:
        return "generated time UNKNOWN", True
    age = (now - generated).total_seconds()
    stamp = generated.strftime("%H:%MZ")
    if age > STALE_AFTER:
        return ("rendered %s, %d min ago: STALE, the renderer may be stopped"
                % (stamp, age // 60)), True
    return "rendered %s, %d min ago" % (stamp, max(0, age) // 60), False


def guess_line(person, marker):
    guess = marker.get("guess") or {}
    conf = guess.get("confidence")
    room = guess.get("room")
    if room is None:
        head = "%s: no best guess." % NAMES[person]
    elif isinstance(conf, (int, float)):
        head = "%s: %s, about %d%% sure." % (NAMES[person], room_words(room), round(conf * 100))
    else:
        head = "%s: %s (confidence UNKNOWN)." % (NAMES[person], room_words(room))
    evidence = guess.get("evidence") or "UNKNOWN"
    return textwrap.fill(head + " Why: " + evidence, WIDTH, subsequent_indent="    ")


def frame(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    state = read_state()
    out = [read_map_text(), ""]
    if state is None:
        out.append("Best guess UNKNOWN: map-state.json is missing or unreadable.")
        out.append("")
    else:
        note, stale = age_note(state, now)
        out.append("Best guess (a guess that may be wrong; %s)." % note)
        if stale:
            out.append("WARNING: the guesses below may be out of date.")
        for person in PEOPLE:
            marker = (state.get("markers") or {}).get(person)
            if marker is None:
                out.append("%s: UNKNOWN, no marker recorded." % NAMES[person])
                continue
            out.append(guess_line(person, marker))
        out.append("")
    out.append("Updated %s (every %d s). Ctrl-C to stop." % (
        now.strftime("%H:%M:%SZ"), REDRAW_EVERY))
    return "\n".join(out) + "\n"


def main():
    if "--once" in sys.argv:
        sys.stdout.write(frame())
        sys.stdout.flush()
        return 0
    try:
        while True:
            sys.stdout.write("\x1b[2J\x1b[H" + frame())
            sys.stdout.flush()
            time.sleep(REDRAW_EVERY)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
