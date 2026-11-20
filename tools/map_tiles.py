#!/usr/bin/env python3
"""Terminal tile map of the apartment: one model for the viewer and the text twin.

Read-only and dependency-free. The inputs are the derived files under
SITE/senses/map: floorplan.json (rooms, doors, anchors, tile_m) and
map-state.json (the markers and best guesses). The phone row comes from the
collector file SITE/spatial/work/household/phones.jsonl, read only for its age.

Drawing rules (Ilya's honesty rules, 2026-11-13):
  - Geometry that is assumed is drawn dashed and dim; nothing solid is drawn
    from nothing. A room with no drawn size is not drawn at all.
  - A door whose position is UNKNOWN is a '?' tile at the wall, never a solid gap.
  - Furniture and anchors have no position yet; their glyph is a dim placeholder
    inside the room and the legend says so.
  - A person is a bright letter only from a fresh measured label. A best guess
    with a confidence number is a normal letter with the number in the legend.
    An UNKNOWN person is a dim '?' in the last known room, with the age.
  - A missing or stale file is UNKNOWN text, never a guessed layout.

Usage:
  map_tiles.py [--once] [--plain]  (see tools/map_view.py)
"""
import datetime
import json
import os
import re
import statistics
import textwrap


TILE_M = 0.5          # the floorplan tile size; one tile is two characters wide, one row high
CHAR_PER_TILE = 2
MAX_WIDTH = 100       # panes are about 170 columns; the map never exceeds this
STALE_MAP_S = 30 * 60
ACTIVITY_FRESH_S = 1800   # bar scale only; the verdict comes from map_render's room_activity state
ASSUMED_NIGHT_UTC = (21, 5)   # mirrors map_render.NIGHT_UTC; a published mismatch fails the check closed
NIGHT_CURVE_MAX_AGE_S = 1800  # the daylight and activity duties renew their curves about every 10 min
NIGHT_MIN_SAMPLES = 30        # hours with fewer samples do not carry a median luma
PHONE_FRESH_S = 900   # the household_phones limit (Redmi10 stream)
PHONE_HISTORY_WINDOW_S = 7 * 24 * 3600   # the trailing window for the reliability line
# The living-room audio hint is a sensor the map otherwise says nothing about.
AUDIO_FRESH_S = 150        # the listener duty's max_age: an older row is not an observation
AUDIO_TAIL_BYTES = 200000  # bounded tail of the append-only ~27 MB log, about 70 min at 7 s/hop
AUDIO_SPEECH = frozenset(("HINT", "NO_HINT"))  # NO_HINT = speech heard, no hint word

GHOST_BELOW = 0.3     # a guess under this number is shown as a ghost, not a letter
PEOPLE = ["ilya", "rozalia", "bruno"]
NAMES = {"ilya": "Ilya", "rozalia": "Rozalia", "bruno": "Bruno"}
LETTER = {"ilya": "I", "rozalia": "R", "bruno": "B"}
BLIND_SPOT_M = 2.2    # living-room left part the camera cannot see (assumed width, not measured)

# furniture given by label: glyph and room. Positions are placeholders (floorplan has none).
FURNITURE = [
    ("couch", "C", "living_room", (-3, 1), "living_room_couch"),
    ("beanbag", "o", "living_room", (-3, -2), "living_room_beanbag"),
    ("mac_desk", "D", "bedroom", (1, 1), "bedroom_at_mac"),
    ("bed", "T", "bedroom", (-2, -2), "bedroom_bed"),
]
ROOM_NAME = {"living_room": "living room", "bedroom": "bedroom"}
# Rooms the renderer knows but the floorplan does not draw. Kept separate from
# ROOM_NAME so changing an undrawn-room label cannot alter drawn-room lines.
UNDRAWN_ROOM_NAME = {"kitchen_stall": "kitchen stall"}

SGR = {
    "wall": "37", "dashed": "2;37", "floor": "2;90", "label": "36", "hatch": "2;31",
    "furniture": "2;33", "door": "1;33", "fresh": "1;92", "guess": "33", "ghost": "2;90",
    "window": "2;36", "legend": "0",
}
CHARS = {
    "uni": {"h": "─", "v": "│", "dh": "┄", "dv": "┆", "tl": "┌", "tr": "┐", "bl": "└",
            "br": "┘", "t": "┬", "b": "┴", "l": "├", "r": "┤", "x": "┼", "floor": "·",
            "hatch": "╱", "door": "?", "blank": " "},
    "plain": {"h": "-", "v": "|", "dh": ":", "dv": ":", "tl": "+", "tr": "+", "bl": "+",
              "br": "+", "t": "+", "b": "+", "l": "+", "r": "+", "x": "+", "floor": ".",
              "hatch": "/", "door": "?", "blank": " "},
}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tiles(m):
    return int(round(m / TILE_M))


def _room_geometry(room):
    """(x0, y0, x1, y1) in tiles from a floorplan room, or None when the drawing is UNKNOWN."""
    pos = room.get("position_m") if isinstance(room.get("position_m"), dict) else {}
    w = (room.get("width_m") or {}).get("value") if isinstance(room.get("width_m"), dict) else None
    d = (room.get("depth_m") or {}).get("value") if isinstance(room.get("depth_m"), dict) else None
    x, y = pos.get("x"), pos.get("y")
    if not all(_num(v) for v in (x, y, w, d)):
        return None
    x0, y0 = _tiles(x), _tiles(y)
    return x0, y0, x0 + _tiles(w), y0 + _tiles(d)


def _assumed(room):
    prov = [(room.get(k) or {}).get("provenance") for k in ("width_m", "depth_m", "position_m")
            if isinstance(room.get(k), dict)]
    return "measured" not in prov


def read_phone_row(path):
    """Newest phones.jsonl row as {'age_s': seconds now, 'status': ...}, or None."""
    try:
        with open(path, encoding="utf-8") as fh:
            last = None
            for line in fh:
                if line.strip():
                    last = line
        row = json.loads(last)
        r = row["redmi10"]
        return {"age_s": r.get("age_s"), "status": r.get("status"),
                "collector_t": row.get("t")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def read_phone_history(path):
    """All collector rows, newest last, or None when missing or malformed."""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        return None
    if not rows or not all(isinstance(r, dict) for r in rows):
        return None
    return rows


def phone_stability(rows, now, window_s=None):
    """(drop onsets, fresh rows, total rows) for redmi10 over the trailing window.

    A drop onset is an ok row followed by unknown or stale; the fresh fraction
    is ok rows over all rows in the window. These are the collector's rows, not
    the phone's body log, so this is a different population from delivery-gaps.
    Missing, malformed or empty history is None (UNKNOWN), never a zero.
    """
    if not rows:
        return None
    window_s = PHONE_HISTORY_WINDOW_S if window_s is None else window_s
    now_s = now.timestamp() if hasattr(now, "timestamp") else now
    window = [r for r in rows
              if isinstance(r.get("t"), (int, float)) and r["t"] >= now_s - window_s]
    if not window:
        return None
    statuses = [(r.get("redmi10") or {}).get("status") for r in window]
    if any(not isinstance(s, str) for s in statuses):
        return None
    drops = sum(1 for prev, cur in zip(statuses, statuses[1:])
                if prev == "ok" and cur in ("unknown", "stale"))
    fresh = sum(1 for s in statuses if s == "ok")
    return drops, fresh, len(statuses)

def phone_and_history(path, now):
    """(newest phone row, 7d stability) from one collector file; both fail closed."""
    rows = read_phone_history(path)
    if rows is None:
        return read_phone_row(path), None
    last = rows[-1]
    r = last.get("redmi10") or {}
    phone = {"age_s": r.get("age_s"), "status": r.get("status"),
             "collector_t": last.get("t")}
    return phone, phone_stability(rows, now)

def _age_s_at(now, generated_utc, age_s):
    """Age of a value now, given its age at the moment the state was written."""
    gen = _parse(generated_utc)
    if gen is None or not _num(age_s):
        return None
    return age_s + max(0.0, (now - gen).total_seconds())


def _camera_age(activity, now, generated_utc):
    """Age now of a room's newest usable camera bucket.

    age_s is measured when map-state.json was rendered, so the time since then is added
    (the same adjustment the phone path makes from its collector time). Without a render
    time an age_s is UNKNOWN rather than an understated number. Without age_s the age is
    counted from last_usable_utc.
    """
    if _num(activity.get("age_s")):
        return _age_s_at(now, generated_utc, activity["age_s"])
    try:
        when = datetime.datetime.fromisoformat(str(activity.get("last_usable_utc")))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (now - when).total_seconds())


def _frame_age(act, now):
    """Age now of the newest camera frame recorded by the activity producer.

    activity.json publishes one frame time for both rooms. A missing, undated or
    malformed stamp is UNKNOWN, never an assumed fresh camera.
    """
    raw = (act or {}).get("_newest_frame_utc")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        when = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (now - when).total_seconds())


def _parse(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None

def build_model(floor, floor_reason, state, state_reason, phone, now, stability=None):
    """Pure model: rooms, grid cells, sprites, legend and status lines. No file I/O."""
    m = {"rooms": {}, "cells": {}, "sprites": [], "legend": [], "status": [], "banner": []}
    rooms = []
    if floor is None:
        m["banner"].append("Map UNKNOWN: floorplan.json %s. No rooms drawn." % (floor_reason or "missing"))
    else:
        for r in floor.get("rooms", []):
            if not isinstance(r, dict) or not isinstance(r.get("id"), str):
                continue
            geo = _room_geometry(r)
            if geo is None:
                m["legend"].append("%s: size or position UNKNOWN, not drawn." % r["id"])
                continue
            rooms.append((r, geo))
    for r, (x0, y0, x1, y1) in rooms:
        m["rooms"][r["id"]] = {"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                               "assumed": _assumed(r), "label": ROOM_NAME.get(r["id"], r["id"])}
    _draw_rooms(m)
    _draw_doors(m, floor or {})
    _draw_furniture(m, floor or {})
    _draw_sprites(m, state, now)
    _status(m, floor, state, state_reason, phone, now, stability)
    return m


def _set(m, cell, ch, style):
    m["cells"][cell] = (ch, style)


def _draw_rooms(m):
    for rid, rm in m["rooms"].items():
        style = "dashed" if rm["assumed"] else "wall"
        x0, y0, x1, y1 = rm["x0"], rm["y0"], rm["x1"], rm["y1"]
        left, right = CHAR_PER_TILE * x0, CHAR_PER_TILE * x1
        walls = set()
        for cx in range(left, right + 1):
            walls.add((cx, y0))
            walls.add((cx, y1))
        for cy in range(y0, y1 + 1):
            walls.add((left, cy))
            walls.add((right, cy))
        for c in walls:
            m.setdefault("wallstyle", {})[c] = style
            m.setdefault("walls", set()).add(c)
        for cy in range(y0 + 1, y1):
            for cx in range(left + 1, right):
                if (cx, cy) not in m["cells"]:
                    _set(m, (cx, cy), "floor", "floor")
        label = " %s " % rm["label"]
        if len(label) + 2 <= right - left:
            for i, ch in enumerate(label):
                m.setdefault("labels", {})[(left + 2 + i, y0)] = ch


def _draw_doors(m, floor):
    for door in floor.get("doors", []):
        if not isinstance(door, dict):
            continue
        between = door.get("between") or []
        if len(between) != 2 or not all(b in m["rooms"] for b in between):
            continue
        a, b = (m["rooms"][between[0]], m["rooms"][between[1]])
        if a["x1"] == b["x0"] or b["x1"] == a["x0"]:
            x = a["x1"] if a["x1"] == b["x0"] else b["x1"]
            ya, yb = max(a["y0"], b["y0"]), min(a["y1"], b["y1"])
            if ya >= yb:
                continue
            cell = (CHAR_PER_TILE * x, (ya + yb) // 2)
            m["walls"].add(cell)
            m.setdefault("doors", []).append(cell)
            m["cells"][cell] = ("door", "door")
            m["legend"].append("? door %s: position UNKNOWN, drawn as a placeholder at the wall" % door.get("id"))


def _local_cell(rm, lx, ly):
    """Character cell for a room-local tile; negative values count from the far side."""
    w = (rm["x1"] - rm["x0"])
    h = (rm["y1"] - rm["y0"])
    lx = lx if lx >= 0 else w + lx
    ly = ly if ly >= 0 else h + ly
    lx = min(max(lx, 0), w - 1)
    ly = min(max(ly, 1), h - 2) if h > 2 else 1
    return CHAR_PER_TILE * rm["x0"] + 1 + 2 * lx, rm["y0"] + ly


def _draw_furniture(m, floor):
    seen = set()
    for label_id, glyph, room_id, (lx, ly), _ in FURNITURE:
        if room_id not in m["rooms"]:
            continue
        cell = _local_cell(m["rooms"][room_id], lx, ly)
        if cell in m["cells"] and m["cells"][cell][1] != "floor":
            continue
        m["cells"][cell] = (glyph, "furniture")
        seen.add(label_id)
    if "living_room" in m["rooms"]:
        rm = m["rooms"]["living_room"]
        width = int(round(BLIND_SPOT_M / TILE_M)) * CHAR_PER_TILE
        left = CHAR_PER_TILE * rm["x0"] + 1
        for cy in range(rm["y0"] + 1, rm["y1"]):
            for cx in range(left, min(left + width, CHAR_PER_TILE * rm["x1"])):
                m["cells"][(cx, cy)] = ("hatch", "hatch")
        m["legend"].append("hatch: blind spot (kitchen stall side, left %.1f m, assumed width) the camera cannot see" % BLIND_SPOT_M)
    if "bedroom" in m["rooms"] and "bedroom_window" in [w.get("id") for w in floor.get("windows", []) if isinstance(w, dict)]:
        rm = m["rooms"]["bedroom"]
        cell = (CHAR_PER_TILE * (rm["x0"] + rm["x1"]) // 2, rm["y0"])
        m["cells"][cell] = ("W", "window")
        m["legend"].append("W bedroom window (given label); position on the wall UNKNOWN, placeholder")
    if seen:
        m["legend"].append("placeholder glyphs: " + ", ".join(
            "%s %s" % (g, lbl) for lbl, g, _, _, _ in FURNITURE if lbl in seen)
            + " (positions UNKNOWN; drawn dim)")


def _place(m, room_id, slot, person, style):
    rm = m["rooms"].get(room_id)
    if rm is None:
        return False
    cell = _local_cell(rm, -4 - 2 * slot, -5)  # room's right side: clear of the blind-spot hatch
    m["sprites"].append((cell, LETTER[person], style, person))
    return True


def _draw_sprites(m, state, now):
    markers = (state or {}).get("markers") or {}
    slots = {}
    for person in PEOPLE:
        mk = markers.get(person) or {}
        conf = mk.get("confidence")
        room = mk.get("room")
        g = mk.get("guess") or {}
        gconf = g.get("confidence")
        if conf in ("medium", "low") and room in m["rooms"]:
            kind, at = "fresh", room
        elif _num(gconf) and gconf >= GHOST_BELOW and g.get("room") in m["rooms"]:
            kind, at = "guess", g["room"]
        elif room in m["rooms"]:
            kind, at = "ghost", room
        else:
            kind, at = None, None
        m.setdefault("person", {})[person] = {"kind": kind, "room": at, "marker": mk}
        if kind is None:
            continue
        slot = slots.get(at, 0)
        slots[at] = slot + 1
        if kind == "ghost":
            m["sprites"].append((_local_cell(m["rooms"][at], -4 - 2 * slot, -5), "?", "ghost", person))
        else:
            _place(m, at, slot, person, kind)


def _bar(fraction, width=10):
    if fraction is None:
        return "UNKNOWN".ljust(width)
    n = int(round(max(0.0, min(1.0, fraction)) * width))
    return "#" * n + "." * (width - n)


def _fresh_fraction(age_s, limit):
    if not _num(age_s):
        return None
    return 1.0 - min(1.0, age_s / float(limit))


def _age_text(age_s):
    if not _num(age_s):
        return "age UNKNOWN"
    if age_s < 120:
        return "%d s" % age_s
    if age_s < 7200:
        return "%d min" % (age_s // 60)
    return "%d h" % (age_s // 3600)


def _status(m, floor, state, state_reason, phone, now, stability=None):
    gen = (state or {}).get("generated_utc")
    if state is None:
        m["banner"].append("Best guess UNKNOWN: map-state.json %s." % (state_reason or "missing"))
    else:
        gen_dt = _parse(gen)
        if gen_dt is None:
            m["banner"].append("Best guess UNKNOWN: map-state.json has no render time.")
        else:
            age = (now - gen_dt).total_seconds()
            if age > STALE_MAP_S:
                m["banner"].append("Best guess STALE: rendered %d min ago; sprites hidden." % (age // 60))
                m["sprites"] = []
    state_ok = state is not None and not any(b.startswith("Best guess") for b in m["banner"])
    if not state_ok:
        m["sprites"] = []
    act_lines = []
    act = (state or {}).get("room_activity") or {}
    for rid in ("living_room", "bedroom"):
        a = act.get(rid) or {}
        age = _camera_age(a, now, gen) if state else None
        frac = _fresh_fraction(age, ACTIVITY_FRESH_S)
        verdict = {"current": " (fresh)", "historical": " (stale)",
                   "UNKNOWN": " (UNKNOWN)"}.get(a.get("state"))
        if verdict is None:
            verdict = "" if age is None else (
                " (fresh)" if age <= ACTIVITY_FRESH_S else " (stale)")
        act_lines.append("activity %-7s [%s] %s%s" % (
            "living" if rid == "living_room" else "bedroom", _bar(frac), _age_text(age), verdict))
    m["status"].extend(act_lines)
    frame_age = _frame_age(act, now)
    if frame_age is None:
        m["status"].append("camera frame UNKNOWN (no dated frame in activity.json)")
    else:
        m["status"].append("camera frame %s old (newest frame in activity.json)" % _age_text(frame_age))
    m["status"].append("activity age = newest usable 15-min bucket; bar is bucket age, not camera health")
    if phone is None:
        m["status"].append("phone Redmi10 [%s] UNKNOWN (no phones row)" % _bar(None))
    else:
        age = None
        if _num(phone.get("collector_t")) and _num(phone.get("age_s")):
            age = (now.timestamp() - phone["collector_t"]) + phone["age_s"]
        frac = _fresh_fraction(age, PHONE_FRESH_S)
        m["status"].append("phone Redmi10 [%s] newest row %s (status %s)" % (
            _bar(frac), _age_text(age), phone.get("status")))
    if stability is None:
        m["status"].append("phone history UNKNOWN (no usable collector rows; bar is newest-row age only)")
    else:
        drops, fresh, total = stability
        m["status"].append("phone history: %d drop onsets in 7d, fresh in %d%% of %d samples; bar is newest-row age only"
                           % (drops, 100 * fresh // total, total))
    rssi = ((state or {}).get("rssi") or {}).get("text") or "RSSI UNKNOWN (no rssi state)"
    m["status"].append("rssi " + rssi.replace("rssi (Redmi10): ", "").replace("rssi ", ""))
    m["status"].insert(0, "time %s" % now.strftime("%Y-%m-%d %H:%M:%SZ"))
    m["confidence"] = _map_confidence(m, floor, state)
    m["status"].append("map confidence %s" % m["confidence"]["level"])
    for line in m["confidence"]["why"]:
        m["status"].append("  " + line)
    for person in PEOPLE:
        m["legend"].append(_person_line(person, m))


def _map_confidence(m, floor, state):
    rooms = (floor or {}).get("rooms") or []
    measured = 0
    for r in rooms:
        if isinstance(r, dict) and all(
                isinstance(r.get(k), dict) and r[k].get("provenance") == "measured"
                for k in ("width_m", "depth_m")):
            measured += 1
    doors = [d for d in (floor or {}).get("doors", []) if isinstance(d, dict)]
    door_known = any(isinstance(d.get("position_m"), dict) and _num(d["position_m"].get("value"))
                     for d in doors)
    fresh = sum(1 for p in PEOPLE if (m.get("person", {}).get(p) or {}).get("kind") == "fresh")
    level = "LOW" if measured == 0 else "MEDIUM"
    why = ["rooms measured %d of %d (sizes are assumed)" % (measured, len(rooms)),
           "door position %s" % ("known" if door_known else "UNKNOWN"),
           "people with a fresh label %d of %d" % (fresh, len(PEOPLE)),
           "to raise it: a fresh label, a measured wall length, a door position"]
    return {"level": level, "why": why}


def _label_parts(mk):
    """(date, age_h) of the newest label, or (None, None) when neither is usable.

    map_render publishes label_utc beside age_h. The date is validated before it
    reaches the legend so a malformed timestamp cannot put a wrong day there.
    """
    age = mk.get("age_h")
    age = age if _num(age) else None
    date = None
    lutc = mk.get("label_utc")
    if isinstance(lutc, str):
        try:
            date = datetime.date.fromisoformat(lutc[:10]).isoformat()
        except ValueError:
            date = None
    return date, age


def _person_line(person, m):
    info = (m.get("person") or {}).get(person) or {}
    kind = info.get("kind")
    mk = info.get("marker") or {}
    g = mk.get("guess") or {}
    name = NAMES[person]
    date, age = _label_parts(mk)
    if date is not None and age is not None:
        dated = guess_when = "on %s, %.1fd ago" % (date, age / 24.0)
    elif age is not None:
        dated = "%s h old" % age
        guess_when = "%.1fh ago" % age
    elif date is not None:
        dated = guess_when = "on %s" % date
    else:
        dated = guess_when = None

    if kind == "fresh":
        return "%s %s: fresh label, %s, %s" % (LETTER[person], name, ROOM_NAME.get(info["room"], info["room"]), mk.get("why", ""))
    if kind == "guess":
        # A guess only appears without a fresh label (_draw_sprites), so its
        # weight is a heuristic, not a validated probability. age_h belongs to
        # the last label, which may be in a different room from the guess.
        label_room = ROOM_NAME.get(mk.get("room"), mk.get("room"))
        if guess_when is None or not label_room:
            last = "; last label UNKNOWN"
        else:
            last = "; last label %s %s" % (label_room, guess_when)
        return "%s %s: guess %s (weight %d%%, not a probability)%s" % (
            LETTER[person], name, ROOM_NAME.get(info["room"], info["room"]),
            round(g.get("confidence", 0) * 100), last)
    if kind == "ghost":
        when = "" if dated is None else " (label %s)" % dated
        return "? %s: UNKNOWN, last known %s%s" % (name, ROOM_NAME.get(info["room"], info["room"]), when)
    # A marker can name a room the floorplan does not draw. It has no sprite,
    # but a place is still recorded: say which one, never "no place recorded".
    room = mk.get("room")
    if isinstance(room, str) and room:
        shown = UNDRAWN_ROOM_NAME.get(room, room)
        if mk.get("confidence") in ("medium", "low"):
            return "%s: fresh label in %s (not drawn on this map)" % (name, shown)
        when = "" if dated is None else "; label %s" % dated
        return "? %s: UNKNOWN, last known %s (not drawn on this map%s)" % (name, shown, when)
    return "%s: no place recorded (UNKNOWN)" % name


def _grid_bounds(m):
    cells = list(m["cells"]) + list(m.get("walls", set())) + [s[0] for s in m["sprites"]]
    if not cells:
        return 0, 0, 0, 0
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    return min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1


def _render_rows(m, plain):
    chars = CHARS["plain" if plain else "uni"]
    walls = m.get("walls", set())
    wallstyle = m.get("wallstyle", {})
    labels = m.get("labels", {})
    sprites = {c: (ch, st, p) for c, ch, st, p in m["sprites"]}
    x0, y0, x1, y1 = _grid_bounds(m)
    rows = []
    for cy in range(y0, y1 + 1):
        row = []
        for cx in range(x0, x1 + 1):
            cell = (cx, cy)
            if cell in sprites:
                ch, style, _ = sprites[cell]
                row.append((ch, style))
            elif cell in labels:
                row.append((labels[cell], "label"))
            elif cell in m["cells"]:
                ch, style = m["cells"][cell]
                if style == "door":
                    row.append((chars["door"], "door"))
                elif style == "window":
                    row.append((ch, "window"))
                elif style == "hatch":
                    row.append((chars["hatch"], "hatch"))
                elif style == "floor":
                    row.append((chars["floor"], "floor"))
                elif style == "furniture":
                    row.append((ch, "furniture"))
                else:
                    row.append((ch, style))
            elif cell in walls:
                row.append((_wall_char(walls, cell, chars, wallstyle.get(cell, "wall")),
                            wallstyle.get(cell, "wall")))
            else:
                row.append((chars["blank"], "blank"))
        rows.append(row)
    return rows


def _wall_char(walls, cell, chars, style):
    cx, cy = cell
    up = (cx, cy - 1) in walls
    down = (cx, cy + 1) in walls
    left = (cx - 1, cy) in walls
    right = (cx + 1, cy) in walls
    h, v = left or right, up or down
    if h and not v:
        return chars["dh"] if style == "dashed" else chars["h"]
    if v and not h:
        return chars["dv"] if style == "dashed" else chars["v"]
    if not h and not v:
        return chars["h"]
    if up and down and left and right:
        return chars["x"]
    if down and right and not up and not left:
        return chars["tl"]
    if down and left and not up and not right:
        return chars["tr"]
    if up and right and not down and not left:
        return chars["bl"]
    if up and left and not down and not right:
        return chars["br"]
    if up and down and right:
        return chars["l"]
    if up and down and left:
        return chars["r"]
    if left and right and down:
        return chars["t"]
    return chars["b"]


def _paint(ch, style, color):
    if not color or style in ("blank",):
        return ch
    code = SGR.get(style)
    if not code or code == "0":
        return ch
    return "\x1b[%sm%s\x1b[0m" % (code, ch)


def map_rows(model, color=False):
    """Map rows only (no legend), as strings of at most the map width."""
    rows = _render_rows(model, plain=not color)
    out = []
    for row in rows:
        text = "".join(_paint(ch, st, color) for ch, st in row).rstrip()
        out.append(text)
    return out


def frame(model, now, color=False):
    """The whole frame: banner, map, legend and status strip, every line at most MAX_WIDTH."""
    lines = ["SENSES MAP  apartment, best guess (%s)" % now.strftime("%H:%MZ")]
    lines.extend(model.get("banner") or [])
    lines.append("")
    lines.extend(map_rows(model, color))
    lines.append("")
    lines.append("+-- status " + "-" * 60)
    for s in model["status"]:
        lines.extend(textwrap.wrap(s, MAX_WIDTH - 4, subsequent_indent="    ") or [""])
    lines.append("+-- legend " + "-" * 60)
    lines.append("solid = measured, dashed = assumed geometry; ? = unknown")
    for s in model["legend"]:
        lines.extend(textwrap.wrap(s, MAX_WIDTH, subsequent_indent="  ") or [""])
    out = []
    for line in lines:
        out.append(line if len(line) <= MAX_WIDTH or color else line[:MAX_WIDTH])
    return "\n".join(out).rstrip() + "\n"


def _curve_at(site, name, now):
    """Read a site-home curve and its file age; missing or malformed is UNKNOWN."""
    path = os.path.join(site, "spatial", "work", "household", name)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data, max(0.0, now.timestamp() - os.path.getmtime(path))
    except (OSError, ValueError):
        return None, None


def _luma_by_hour(daylight):
    rooms = daylight.get("rooms") if isinstance(daylight, dict) else None
    info = rooms.get("living room") if isinstance(rooms, dict) else None
    hours = info.get("hours") if isinstance(info, dict) else None
    if not isinstance(hours, dict):
        return {}
    out = {}
    for key, entry in hours.items():
        if not isinstance(key, str) or not key.isdigit() or not isinstance(entry, dict):
            continue
        hour, luma, samples = int(key), entry.get("median_luma"), entry.get("samples")
        if (0 <= hour <= 23 and isinstance(luma, (int, float)) and isinstance(samples, (int, float))
                and samples >= NIGHT_MIN_SAMPLES):
            out[hour] = luma
    return out


def _activity_by_hour(activity):
    rooms = activity.get("rooms") if isinstance(activity, dict) else None
    info = rooms.get("living room") if isinstance(rooms, dict) else None
    buckets = info.get("buckets") if isinstance(info, dict) else None
    if not isinstance(buckets, list):
        return {}
    by_hour = {}
    for b in buckets:
        if not isinstance(b, dict):
            continue
        utc, value = b.get("utc"), b.get("activity")
        if not isinstance(utc, str) or len(utc) < 2 or not isinstance(value, (int, float)):
            continue
        try:
            hour = int(utc[:2])
        except ValueError:
            continue
        if 0 <= hour <= 23:
            by_hour.setdefault(hour, []).append(value)
    return {h: statistics.median(vals) for h, vals in by_hour.items()}


def _night_window_published(state):
    """The window the renderer put in a marker's evidence, or None when none carries one."""
    markers = state.get("markers") if isinstance(state, dict) else None
    if not isinstance(markers, dict):
        return None
    for info in markers.values():
        guess = info.get("guess") if isinstance(info, dict) else None
        evidence = guess.get("evidence") if isinstance(guess, dict) else None
        if isinstance(evidence, str):
            found = re.search(r"night prior (\d{2})-(\d{2}) UTC", evidence)
            if found:
                return (int(found.group(1)), int(found.group(2)))
    return None


def _night_prior_text(daylight, daylight_age, activity, activity_age, state):
    start, _end = ASSUMED_NIGHT_UTC
    prefix = "night prior %02d-%02dZ" % (start, _end)
    published = _night_window_published(state)
    if published is not None and published != ASSUMED_NIGHT_UTC:
        return prefix + ": UNKNOWN (renderer publishes a different window)"
    if (not isinstance(daylight_age, (int, float)) or not isinstance(activity_age, (int, float))
            or daylight_age > NIGHT_CURVE_MAX_AGE_S or activity_age > NIGHT_CURVE_MAX_AGE_S):
        return prefix + ": timing UNKNOWN (no fresh daylight and activity curve)"
    lumas = _luma_by_hour(daylight)
    acts = _activity_by_hour(activity)
    if not lumas or not acts or start not in lumas or start not in acts:
        return prefix + ": timing UNKNOWN (a needed hour is missing from the curves)"
    dark_h, quiet_h = min(lumas, key=lumas.get), min(acts, key=acts.get)
    if dark_h == start and quiet_h == start:
        return (prefix + " matches the darkest and quietest hour (%02dZ, luma %.0f, activity %.3f)"
                % (start, lumas[start], acts[start]))
    return (prefix + " is unvalidated: at %02dZ living-room luma %.0f (darkest %02dZ %.0f), camera activity %.3f (quietest %02dZ %.3f)"
            % (start, lumas[start], dark_h, lumas[dark_h], acts[start], quiet_h, acts[quiet_h]))


def night_prior_line(site, state, now):
    """One derived status line; missing, stale or conflicting curves stay UNKNOWN."""
    daylight, daylight_age = _curve_at(site, "daylight.json", now)
    activity, activity_age = _curve_at(site, "activity.json", now)
    return _night_prior_text(daylight, daylight_age, activity, activity_age, state)


# The camera's "busiest room" feeds every best guess, but it may be nearly
# constant. Measure it from the retained 15-min buckets before a reader treats
# it as evidence that locates a person.
CAMERA_BIAS_MIN_BUCKETS = 24       # fewer shared buckets than a day is too thin to read
CAMERA_BIAS_MIN_HOUR_N = 6         # hours with fewer buckets do not carry a share


def _room_activity_series(activity):
    """Room -> {timestamp: activity} from dated, numeric camera buckets."""
    rooms = activity.get("rooms") if isinstance(activity, dict) else None
    if not isinstance(rooms, dict):
        return {}, {}
    out = {}
    for name in ("bedroom", "living room"):
        info = rooms.get(name)
        buckets = info.get("buckets") if isinstance(info, dict) else None
        series = {}
        if isinstance(buckets, list):
            for b in buckets:
                if not isinstance(b, dict):
                    continue
                value = b.get("activity")
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                try:
                    when = datetime.datetime.fromisoformat(str(b.get("utc_iso")))
                except (TypeError, ValueError):
                    continue
                series[when] = value
        out[name] = series
    return out["bedroom"], out["living room"]


def camera_room_bias_line(site, now):
    """One derived status line: how often the camera's busiest room is actually bedroom.

    Missing, stale or thin activity data stays UNKNOWN; a room-by-hour prior is
    not published unless it can beat this constant on held-out days.
    """
    prefix = "camera room bias"
    activity, age = _curve_at(site, "activity.json", now)
    if activity is None or not isinstance(age, (int, float)) or age > NIGHT_CURVE_MAX_AGE_S:
        return prefix + " UNKNOWN (activity curve missing or stale)"
    bed, liv = _room_activity_series(activity)
    shared = sorted(set(bed) & set(liv))
    if len(shared) < CAMERA_BIAS_MIN_BUCKETS:
        return prefix + " UNKNOWN (only %d shared activity buckets)" % len(shared)
    wins = sum(1 for t in shared if bed[t] > liv[t])
    by_hour = {}
    for t in shared:
        n, k = by_hour.get(t.hour, (0, 0))
        by_hour[t.hour] = (n + 1, k + (bed[t] > liv[t]))
    flips = sorted(h for h, (n, k) in by_hour.items()
                   if n >= CAMERA_BIAS_MIN_HOUR_N and k * 2 <= n)
    pct = 100 * wins // len(shared)
    if flips:
        return "%s: bedroom busier in %d%% of %d shared buckets; living room leads at %s — not a person signal" % (
            prefix, pct, len(shared), ", ".join("%02dZ" % h for h in flips))
    return "%s: bedroom busier in %d%% of %d shared buckets and in all %d hours — near-constant, not a person signal" % (
        prefix, pct, len(shared), len(by_hour))


def audio_room_line(site, now):
    """Newest living-room speech and listener row as derived ages, or UNKNOWN; no transcript text."""
    path = os.path.join(site, "spatial", "work", "household", "audio.jsonl")
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - AUDIO_TAIL_BYTES)
            fh.seek(start)
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return "audio livingroom UNKNOWN (no audio log)"
    lines = raw.splitlines()
    if start and not raw.startswith("\n"):
        lines = lines[1:]       # a partial first row is never a reading
    newest = oldest = speech = None
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict) or row.get("room") != "livingroom":
            continue
        t = row.get("t")
        if not _num(t):
            continue
        if newest is None or t > newest:
            newest = t
        if oldest is None or t < oldest:
            oldest = t
        if row.get("status") in AUDIO_SPEECH and (speech is None or t > speech):
            speech = t
    age = None if newest is None else now.timestamp() - newest
    if age is None or age < -60 or age > AUDIO_FRESH_S:
        return "audio livingroom UNKNOWN (no fresh listener row)"
    if oldest is None:
        return "audio livingroom UNKNOWN (no dated listener row)"
    if speech is None:
        return "audio livingroom: no speech in last %s; listener row %s old" % (
            _age_text(max(0.0, now.timestamp() - oldest)), _age_text(age))
    return "audio livingroom: speech %s ago; listener row %s old (not a person claim)" % (
        _age_text(max(0.0, now.timestamp() - speech)), _age_text(age))


def derived_status_lines(site, state, now):
    """The derived status lines both the saved map twin and the terminal view draw."""
    return [
        night_prior_line(site, state, now),
        camera_room_bias_line(site, now),
        audio_room_line(site, now),
    ]


def model_from_site(site, now, phone_path=None):
    """Load the derived files under SITE and build the model. Missing files are UNKNOWN."""
    mapdir = os.path.join(site, "senses", "map")
    floor, floor_reason = None, "missing"
    try:
        with open(os.path.join(mapdir, "floorplan.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("rooms"), list):
            floor, floor_reason = doc, None
        else:
            floor_reason = "has no rooms list"
    except (OSError, ValueError) as exc:
        floor_reason = "unreadable (%s)" % type(exc).__name__
    state, state_reason = None, "missing or unreadable"
    try:
        with open(os.path.join(mapdir, "map-state.json"), encoding="utf-8") as fh:
            st = json.load(fh)
        if isinstance(st, dict):
            state, state_reason = st, None
    except (OSError, ValueError):
        pass
    phone_file = phone_path or os.path.join(site, "spatial", "work", "household", "phones.jsonl")
    phone, stability = phone_and_history(phone_file, now)
    model = build_model(floor, floor_reason, state, state_reason, phone, now, stability)
    model["status"].extend(derived_status_lines(site, state, now))
    return model
