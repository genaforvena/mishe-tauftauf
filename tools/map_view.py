#!/usr/bin/env python3
"""Outer window: the apartment map as a best guess, in plain words.

Read-only. Prints the schematic ASCII map and one line per person with the best
guess, its confidence and the evidence in short phrases. Reads only the derived
files that senses/map/render_map.py writes; it does not sample cameras, phones or
labels itself. Run `python3 -I senses/map/render_map.py` first to refresh them.

Missing or unreadable files are printed as UNKNOWN, never as a guess.
"""
import json
import os
import sys
from pathlib import Path

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME")
            or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
MAP_DIR = SITE / "senses" / "map"
PEOPLE = ["ilya", "rozalia", "bruno"]
NAMES = {"ilya": "Ilya", "rozalia": "Rozalia", "bruno": "Bruno"}


def room_words(room):
    return (room or "none").replace("_", " ")


def read_map_text():
    try:
        return (MAP_DIR / "apartment-map.txt").read_text(encoding="utf-8").rstrip()
    except OSError:
        return "Map UNKNOWN: senses/map/apartment-map.txt is missing or unreadable."


def read_state():
    try:
        return json.loads((MAP_DIR / "map-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def guess_line(person, marker):
    guess = marker.get("guess") or {}
    conf = guess.get("confidence")
    room = guess.get("room")
    if room is None:
        return "%s: no best guess. %s" % (NAMES[person], guess.get("evidence", "UNKNOWN"))
    if isinstance(conf, (int, float)):
        head = "%s: %s ~%.2f" % (NAMES[person], room_words(room), conf)
    else:
        head = "%s: %s (UNKNOWN number)" % (NAMES[person], room_words(room))
    return "%s  [%s]" % (head, guess.get("evidence", ""))


def main():
    state = read_state()
    print(read_map_text())
    print()
    if state is None:
        print("Best guess UNKNOWN: map-state.json is missing or unreadable.")
        return 0
    print("Best guess (generated %s, a guess that may be wrong):" % state.get("generated_utc", "?"))
    markers = state.get("markers", {})
    for person in PEOPLE:
        marker = markers.get(person)
        if marker is None:
            print("%s: UNKNOWN, no marker recorded." % NAMES[person])
            continue
        print(guess_line(person, marker))
    return 0


if __name__ == "__main__":
    sys.exit(main())
