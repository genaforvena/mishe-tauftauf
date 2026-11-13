#!/usr/bin/env python3
"""Outer window: the apartment as a terminal tile map, best guesses only.

Read-only, dependency-free. Draws the tile model from tools/map_tiles.py over
the derived files under SITE/senses/map (floorplan.json, map-state.json) and
redraws every 30 seconds. It does not sample cameras, phones or labels itself.
Run `python3 -I tools/map_render.py --site SITE` to refresh the derived files.

Usage:
  map_view.py            loop, redraw every 30 s until interrupted
  map_view.py --once     print one frame and exit
  map_view.py --plain    ASCII, no colour (also used when NO_COLOR is set or
                         stdout is not a terminal)

Missing, unreadable or stale files are shown as UNKNOWN, never as a guess.
"""
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import map_tiles  # noqa: E402

SITE = os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf"
REDRAW_EVERY = 30  # seconds


def colour_wanted(plain_flag, stream):
    if plain_flag or os.environ.get("NO_COLOR") is not None:
        return False
    return stream.isatty()


def frame(now=None, color=False):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    model = map_tiles.model_from_site(SITE, now)
    return map_tiles.frame(model, now, color=color)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    plain = "--plain" in argv
    color = colour_wanted(plain, sys.stdout)
    if "--once" in argv:
        sys.stdout.write(frame(color=color))
        sys.stdout.flush()
        return 0
    try:
        while True:
            sys.stdout.write("\x1b[2J\x1b[H" + frame(color=color))
            sys.stdout.flush()
            time.sleep(REDRAW_EVERY)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
