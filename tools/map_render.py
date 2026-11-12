#!/usr/bin/env python3
"""Apartment map: room-level markers with confidence, derived numbers only.

Usage: python3 -I tools/map_render.py --site SITE

Inputs (all read-only, under SITE):
  spatial/work/household/labels.tsv   human labels, "<utc>\t<key>=<value>..." rows
  spatial/work/household/activity.json room activity buckets (camera grid change)
  spatial/work/household/phones.jsonl phone rows; top-level "t" is the collector time

Outputs (overwritten each run, under SITE/senses/map):
  apartment-map.svg
  apartment-map.txt
  map-state.json        (derived numbers, what the markers say and why)

Activity freshness: a bucket needs an absolute "utc_iso" (a bare HH:MM label is
UNKNOWN, never today). A bucket later than now (plus slack) is rejected as future-dated.
A room whose newest usable bucket is older than ACTIVITY_FRESH_S is "historical",
not current, and is never cited as the busiest room now.

Rules:
  - A marker's room comes only from the newest label for that person.
  - Freshness: <= 2 h  -> confidence "medium"; <= 24 h -> "low"; older or missing -> UNKNOWN.
  - Two rooms named in one label -> "low" and the marker sits in the first room named.
  - UNKNOWN markers are drawn as a grey ghost at the last known room (or in the side panel).
  - Best guess per person: room from the newest room label, confidence from its
    freshness (medium 0.8, low 0.5, ghost decays by half-life 12 h). Camera activity
    is cited in the evidence phrase but never raises a person's number.
  - Rozalia: room-level only, no history drawn, until Ilya confirms with her.
  - Floor plan is schematic: room rectangles are not measured.
"""
import argparse
import contextlib
import datetime
import importlib.util
import io
import json
import os
import re
import sys

SITE = ""          # site root, set by configure()
HERE = ""          # output directory, set by configure()
HH = ""            # household input directory, set by configure()
LABELS = ""
ACTIVITY = ""
NOW = datetime.datetime.now(datetime.timezone.utc)
ACTIVITY_FRESH_S = 30 * 60   # newest usable bucket older than this is historical
FUTURE_SLACK_S = 60          # a bucket this far past NOW is future-dated, rejected


def configure(site, now=None):
    """Point the renderer at one site; the site is an argument, never the file location."""
    global SITE, HERE, HH, LABELS, ACTIVITY, NOW
    site = os.path.abspath(site)
    SITE = site
    HH = os.path.join(site, "spatial", "work", "household")
    LABELS = os.path.join(HH, "labels.tsv")
    ACTIVITY = os.path.join(HH, "activity.json")
    HERE = os.path.join(site, "senses", "map")
    NOW = now or datetime.datetime.now(datetime.timezone.utc)

# schematic layout: (x, y, w, h) in map units; hallway in the middle
ROOMS = {
    "living_room": (0, 0, 6, 4),
    "hallway":     (6, 0, 2, 4),
    "bedroom":     (8, 0, 5, 4),
    "balcony":     (0, 4, 4, 2),
    "wardrobe":    (8, 4, 2, 2),
    "kitchen_stall": (0, 0, 2, 2),   # inside the living room, left, outside camera view
}
# Camera sits in the hallway/wardrobe and looks west into the living room (Ilya, 2026-11-11):
# it sees the near/ground part and the open balcony edge, not the left part or the kitchen stall.
CAMERA_ORIGIN = (6, 2)
CAMERA_CONE = [(6, 2), (2.2, 0.2), (2.2, 5.8)]      # seen region, schematic
BLIND_CAP = 0.3                                      # a blind-spot room is never seen, only inferred
BLIND_SPOT = (0, 0, 2.2, 4)                          # living room left part: cannot see
ROOM_LABEL = {
    "living_room": "living room",
    "hallway": "hallway",
    "bedroom": "bedroom",
    "balcony": "balcony (no sensor)",
    "wardrobe": "wardrobe",
    "kitchen_stall": "kitchen stall (cannot see)",
}
PEOPLE = ["ilya", "rozalia", "bruno"]
KEY_VALUE = re.compile(r"(\w+)=([^\t\s]+)")
TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")


def parse_ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def room_of(value):
    """Return the first known room named in a label value, or None."""
    text = value.lower()
    hits = []
    for room in ROOMS:
        key = room if room != "living_room" else "living"
        pos = text.find(key)
        if pos >= 0:
            hits.append((pos, room))
    if not hits:
        return None, 0
    hits.sort()
    return hits[0][1], len(hits)


def newest_labels():
    """Per person: (newest label of any kind, newest label that names a room)."""
    any_ = {p: None for p in PEOPLE}
    room = {p: None for p in PEOPLE}
    with open(LABELS, encoding="utf-8") as fh:
        for line in fh:
            m = TS.match(line)
            if not m:
                continue
            ts = parse_ts(m.group(1))
            for key, value in KEY_VALUE.findall(line):
                if key not in PEOPLE:
                    continue
                r, n = room_of(value)
                entry = (ts, value, r, n)
                if any_[key] is None or ts > any_[key][0]:
                    any_[key] = entry
                if r is not None and (room[key] is None or ts > room[key][0]):
                    room[key] = entry
    return any_, room


def freshness(ts):
    age_h = (NOW - ts).total_seconds() / 3600.0
    if age_h <= 2:
        return "medium", age_h
    if age_h <= 24:
        return "low", age_h
    return "UNKNOWN", age_h


def parse_iso(s):
    """Absolute timestamp with an offset, in UTC; anything else is None (no date, no reading)."""
    if not isinstance(s, str):
        return None
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    return t.astimezone(datetime.timezone.utc)


def room_activity():
    """Newest usable activity per room, with freshness (derived numbers only).

    state: "current" (newest dated bucket within ACTIVITY_FRESH_S), "historical" (older),
    or "UNKNOWN" (no dated usable bucket). A bare HH:MM label is never given a date.
    """
    out = {}
    try:
        data = json.load(open(ACTIVITY, encoding="utf-8"))
    except (OSError, ValueError):
        return {"_error": "activity.json missing or unreadable"}
    for room, v in data.get("rooms", {}).items():
        # activity.json says "living room"; ROOMS and the SVG use "living_room"
        room = room.replace(" ", "_")
        dated, undated, future = [], 0, 0
        for b in v.get("buckets", []):
            if b.get("activity") is None:
                continue
            when = parse_iso(b.get("utc_iso"))
            if when is None:
                undated += 1
            elif (when - NOW).total_seconds() > FUTURE_SLACK_S:
                future += 1
            else:
                dated.append((when, b))
        reasons = []
        if undated:
            reasons.append("%d bucket(s) without an absolute date" % undated)
        if future:
            reasons.append("%d future-dated bucket(s) rejected" % future)
        if dated:
            when, last = max(dated, key=lambda x: x[0])
            age = (NOW - when).total_seconds()
            state = "current" if age <= ACTIVITY_FRESH_S else "historical"
            if state == "historical":
                reasons.append("newest usable bucket %d min old" % round(age / 60))
            entry = {"last_usable_utc": when.isoformat(timespec="seconds"),
                     "activity": last["activity"], "mean_luma": last["mean_luma"], "age_s": round(age, 1)}
        else:
            state = "UNKNOWN"
            reasons.append("no dated usable bucket")
            entry = {"last_usable_utc": None, "activity": None, "mean_luma": None, "age_s": None}
        entry.update(state=state, reason="; ".join(reasons), usable_buckets=v.get("usable_buckets"))
        out[room] = entry
    out["_generated_utc"] = data.get("generated_utc")
    return out


def markers(any_, room):
    rows = {}
    for person in PEOPLE:
        item = any_.get(person)
        last_room = room.get(person)
        if item is None:
            rows[person] = {"room": None, "confidence": "UNKNOWN", "why": "no label ever recorded"}
            continue
        ts, value, r, n = item
        if r is None:
            # newest label names no room: ghost at the last room-naming label, UNKNOWN
            if last_room is None:
                rows[person] = {"room": None, "confidence": "UNKNOWN",
                                "why": "no room-level label ever; newest: %s" % value}
                continue
            lts, lvalue, lroom, _ = last_room
            _, age_h = freshness(lts)
            rows[person] = {"room": lroom, "confidence": "UNKNOWN",
                            "why": "ghost: last room label %s (%.0f h old); newest label %s names no room"
                                   % (lvalue, age_h, value),
                            "label_utc": lts.strftime("%Y-%m-%dT%H:%MZ"), "age_h": round(age_h, 1)}
            continue
        conf, age_h = freshness(ts)
        if n > 1 and conf != "UNKNOWN":
            conf = "low"
        why = ("label: %s" % value) if conf != "UNKNOWN" else \
              "ghost: label %s is %.0f h old" % (value, age_h)
        rows[person] = {"room": r, "confidence": conf, "why": why,
                        "label_utc": ts.strftime("%Y-%m-%dT%H:%MZ"), "age_h": round(age_h, 1)}
    return rows


# Priors that never go stale. Host clock is UTC; the apartment's local night is
# ASSUMED to be 21:00-05:00 UTC until Ilya confirms it (asked on Telegram).
NIGHT_UTC = (21, 5)
NIGHT_BEDROOM = 0.4   # night prior for the two humans, bedroom only
FLOOR = 0.15          # last known room never decays below this
PHONE_MAX_AGE_S = 120


def in_night(hour):
    start, end = NIGHT_UTC
    return hour >= start or hour < end


def phone_home():
    """Ilya's Redmi10 on home Wi-Fi: newest phones row, status ok and fresh by its own time.

    age_s is measured at the row's collector time "t"; the effective age is age_s plus
    the time elapsed since t. A stale, failed, undated or future-dated row is UNKNOWN,
    never "away": absence of a fresh row is not evidence of absence.
    """
    unknown = "phone presence UNKNOWN"
    try:
        with open(os.path.join(HH, "phones.jsonl"), encoding="utf-8") as fh:
            last = None
            for line in fh:
                if line.strip():
                    last = line
        doc = json.loads(last)
        row = doc["redmi10"]
        t = doc.get("t")
        if not isinstance(t, (int, float)) or isinstance(t, bool):
            return None, unknown + " (phone row has no collector time)"
        elapsed = NOW.timestamp() - float(t)
        if elapsed < -FUTURE_SLACK_S:
            return None, unknown + " (phone row is future-dated)"
        age = row.get("age_s")
        if row.get("status") != "ok" or not isinstance(age, (int, float)) or isinstance(age, bool):
            return None, unknown + " (phone status %s)" % row.get("status")
        effective = float(age) + max(elapsed, 0.0)
        if effective <= PHONE_MAX_AGE_S:
            return True, "phone home (Redmi10 on Wi-Fi, effective age %.0f s)" % effective
        return None, unknown + " (last phone row %.0f s old)" % effective
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, unknown


def rssi_evidence():
    """Redmi10 Wi-Fi RSSI fingerprint for this render, as one evidence line (derived numbers only).

    Recomputed each render by tools/rssi_fingerprint.py so the line is never older than the
    render. It is evidence, not a placement: it never changes a marker's room or confidence,
    because leave-one-out accuracy is what decides that, and it sits on Ilya's marker only
    (the Redmi10 is his phone).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        spec = importlib.util.spec_from_file_location("rssi_fingerprint", os.path.join(here, "rssi_fingerprint.py"))
        fp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fp)
        with contextlib.redirect_stdout(io.StringIO()):
            fp.main(["--site", SITE])
        doc = json.load(open(os.path.join(HERE, "rssi-fingerprint.json"), encoding="utf-8"))
    except (Exception, SystemExit) as exc:  # a failed helper is UNKNOWN, never a guess
        return {"state": "UNKNOWN", "text": "rssi (Redmi10): UNKNOWN, fingerprint failed (%s)" % type(exc).__name__}
    loo = doc.get("loo") or {}
    loo_txt = "leave-one-out %s/%s correct, majority baseline %s" % (
        loo.get("correct"), loo.get("n"), loo.get("majority_baseline_accuracy"))
    live = doc.get("live") or {}
    if live.get("state") != "fresh":
        return {"state": "UNKNOWN", "text": "rssi (Redmi10): UNKNOWN, %s; %s"
                % (live.get("reason", "no live vector"), loo_txt)}
    est = doc.get("estimate") or {}
    if est.get("room") is None:
        where = "tie between %s" % " and ".join(est.get("tied_rooms") or ["?"])
    else:
        where = "nearest %s" % est["room"]
    return {"state": "fresh", "text": "rssi (Redmi10): %.0f dBm, %s at %s dB; %s; not a room claim"
            % (live["dbm"], where, est.get("distance_dbm"), loo_txt)}


def add_guesses(rows, activity):
    """Best guess per person: a room, a confidence number and one evidence phrase.

    Sources, combined by taking the highest number for a room:
      - the newest room label, by freshness (medium 0.8, low 0.5);
      - a ghost of the last room label, decaying by half-life 12 h but floored at 0.15;
      - a night prior: bedroom 0.4 for Ilya and Rozalia during the assumed night.
    Phone presence and camera activity are cited in the phrase only; they never
    raise a number, because the phone is not tied to a room and the camera
    names a room, not a person.
    """
    busiest = [(v["activity"], k, v["last_usable_utc"]) for k, v in activity.items()
               if isinstance(v, dict) and v.get("state") == "current" and v.get("activity") is not None]
    if busiest:
        act, room, when = max(busiest)
        cam = "camera busiest room %s at %s (current, not person-specific)" % (room, when)
    elif any(isinstance(v, dict) and v.get("state") == "historical" for v in activity.values()):
        cam = "camera room activity historical only, not current"
    else:
        cam = "camera room activity UNKNOWN"
    home, home_note = phone_home()
    night = in_night(NOW.hour)
    for person in PEOPLE:
        r = rows[person]
        base_room, base_conf, base_why = None, 0.0, "no room label ever"
        if r.get("room") is not None:
            if r["confidence"] == "medium":
                base_conf = 0.8
            elif r["confidence"] == "low":
                base_conf = 0.5
            else:
                base_conf = max(FLOOR, 0.6 * 0.5 ** (r["age_h"] / 12.0))
            base_room, base_why = r["room"], r["why"]
        best_room, best_conf, why = base_room, base_conf, base_why
        if night and person in ("ilya", "rozalia") and NIGHT_BEDROOM > best_conf:
            best_room, best_conf = "bedroom", NIGHT_BEDROOM
            why = "night prior %02d-%02d UTC (assumed local night); last label: %s" % (
                NIGHT_UTC[0], NIGHT_UTC[1], base_why)
        if person == "ilya":
            why = "%s; %s" % (why, home_note)
        if best_room == "kitchen_stall" and best_conf > BLIND_CAP:
            best_conf = BLIND_CAP
            why += " [blind spot: inference only, cannot see]"
        if best_room is None:
            r["guess"] = {"room": None, "confidence": 0.0,
                          "evidence": "%s; %s" % (why, cam)}
            continue
        r["guess"] = {"room": best_room, "confidence": round(best_conf, 2),
                      "evidence": "%s; %s" % (why, cam)}


def svg(rows, activity, rssi):
    scale, pad = 50, 20
    width = 13 * scale + 2 * pad
    height = 6 * scale + 2 * pad + 120
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'font-family="monospace" font-size="12">' % (width, height)]
    out.append('<rect width="100%" height="100%" fill="#1b1d24"/>')
    for room, (x, y, w, h) in ROOMS.items():
        act = activity.get(room, {})
        state = act.get("state", "UNKNOWN")
        tint = "#2f3f3a" if state == "current" else "#2a3140"
        out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#7a8599" '
                   'stroke-width="2"/>' % (pad + x * scale, pad + y * scale, w * scale, h * scale, tint))
        out.append('<text x="%d" y="%d" fill="#c9d1e0">%s</text>' %
                   (pad + x * scale + 6, pad + y * scale + 16, ROOM_LABEL[room]))
        if state == "current":
            note = "activity %.3f (%s)" % (act["activity"], act["last_usable_utc"])
        elif state == "historical":
            note = "historical %.3f (%s), not current" % (act["activity"], act["last_usable_utc"])
        else:
            note = "activity UNKNOWN"
        out.append('<text x="%d" y="%d" fill="#7f8aa3" font-size="10">%s</text>' %
                   (pad + x * scale + 6, pad + y * scale + h * scale - 8, note))
    bx, by, bw, bh = BLIND_SPOT
    out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="#000" fill-opacity="0.45" '
               'stroke="#c0504d" stroke-dasharray="4 3"/>' % (pad + bx * scale, pad + by * scale, bw * scale, bh * scale))
    out.append('<text x="%d" y="%d" fill="#e08a87" font-size="10">BLIND SPOT: camera cannot see</text>' %
               (pad + bx * scale + 4, pad + (by + bh) * scale - 26))
    out.append('<polygon points="%s" fill="#f2c14e" fill-opacity="0.12" stroke="#f2c14e" stroke-opacity="0.5"/>' %
               " ".join("%d,%d" % (pad + px * scale, pad + py * scale) for px, py in CAMERA_CONE))
    out.append('<circle cx="%d" cy="%d" r="4" fill="#f2c14e"/>' % (pad + CAMERA_ORIGIN[0] * scale, pad + CAMERA_ORIGIN[1] * scale))
    colours = {"ilya": "#f2c14e", "rozalia": "#e8788c", "bruno": "#7ec8e3"}
    slot = {}
    for person in PEOPLE:
        r = rows[person]
        if r["room"] is None:
            continue
        x, y, w, h = ROOMS[r["room"]]
        k = slot.get(r["room"], 0)
        slot[r["room"]] = k + 1
        cx = pad + x * scale + 30 + k * 60
        cy = pad + y * scale + 50
        colour = colours[person]
        if r["confidence"] == "UNKNOWN":
            colour = "#555b68"
        out.append('<circle cx="%d" cy="%d" r="12" fill="%s" fill-opacity="0.85"/>' % (cx, cy, colour))
        out.append('<text x="%d" y="%d" fill="#fff" font-size="10">%s %s</text>' %
                   (cx - 14, cy + 26, person, r["confidence"]))
    ly = pad + 6 * scale + 30
    out.append('<text x="%d" y="%d" fill="#c9d1e0">Ilya / Rozalia / Bruno — best guess, room level. Schematic plan, not measured.</text>' % (pad, ly))
    for i, person in enumerate(PEOPLE):
        r = rows[person]
        place = ROOM_LABEL[r["room"]] if r["room"] else "UNKNOWN"
        line = "%s: %s, confidence %s (%s)" % (person, place, r["confidence"], r["why"])
        out.append('<text x="%d" y="%d" fill="%s">%s</text>' % (pad, ly + 20 + i * 18, colours[person], line))
    out.append('<text x="%d" y="%d" fill="%s">%s</text>' % (pad, ly + 20 + len(PEOPLE) * 18, colours["ilya"], rssi["text"]))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def ascii_map(rows, rssi):
    lines = ["+" + "-" * 62 + "+",
             "|  living room    | hall |        bedroom          |",
             "|                 |      |                          |",
             "|  (sensor: cam)  |      |   (sensor: cam)          |",
             "|  [left + kitchen stall = BLIND SPOT, camera cannot see] |",
             "+-----------------+      +--------------------------+",
             "|  balcony (no sensor)   | wardrobe |               |",
             "+------------------------+----------+---------------+"]
    out = "\n".join(lines) + "\n"
    for person in PEOPLE:
        r = rows[person]
        place = ROOM_LABEL[r["room"]] if r["room"] else "UNKNOWN"
        out += "%-8s %-22s confidence %-7s %s\n" % (person, place, r["confidence"], r["why"])
        g = r["guess"]
        gplace = ROOM_LABEL[g["room"]] if g["room"] else "no guess"
        if g["room"] is None:
            conf = "-"
        else:
            conf = "%.2f" % g["confidence"] if g["confidence"] >= 0.01 else "<0.01"
        out += "         best guess: %s, confidence %s; %s\n" % (gplace, conf, g["evidence"])
    out += rssi["text"] + "\n"
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render the apartment map for one site.")
    parser.add_argument("--site", required=True, help="site root directory")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.site):
        print("site does not exist: %s" % args.site, file=sys.stderr)
        return 2
    configure(args.site)
    any_, room = newest_labels()
    rows = markers(any_, room)
    activity = room_activity()
    rooms_only = {k: v for k, v in activity.items() if not k.startswith("_")}
    add_guesses(rows, rooms_only)
    rssi = rssi_evidence()
    os.makedirs(HERE, exist_ok=True)
    with open(os.path.join(HERE, "apartment-map.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg(rows, rooms_only, rssi))
    with open(os.path.join(HERE, "apartment-map.txt"), "w", encoding="utf-8") as fh:
        fh.write(ascii_map(rows, rssi))
    state = {"generated_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "markers": rows,
             "room_activity": activity, "rssi": rssi}
    with open(os.path.join(HERE, "map-state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, ensure_ascii=False)
    print(json.dumps({p: {"room": rows[p]["room"], "confidence": rows[p]["confidence"]} for p in PEOPLE}))
    print(os.path.join(HERE, "apartment-map.svg"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
