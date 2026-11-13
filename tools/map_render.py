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
import html
import importlib.util
import io
import json
import os
import re
import sys
import textwrap

SITE = ""          # site root, set by configure()
HERE = ""          # output directory, set by configure()
HH = ""            # household input directory, set by configure()
LABELS = ""
ACTIVITY = ""
FLOORPLAN = ""     # SITE/senses/map/floorplan.json, set by configure()
RSSI_HISTORY = ""  # SITE/senses/map/rssi-history.jsonl, set by configure()
NOW = datetime.datetime.now(datetime.timezone.utc)
ACTIVITY_FRESH_S = 30 * 60   # newest usable bucket older than this is historical
FUTURE_SLACK_S = 60          # a bucket this far past NOW is future-dated, rejected


def configure(site, now=None):
    """Point the renderer at one site; the site is an argument, never the file location."""
    global SITE, HERE, HH, LABELS, ACTIVITY, FLOORPLAN, RSSI_HISTORY, NOW
    site = os.path.abspath(site)
    SITE = site
    HH = os.path.join(site, "spatial", "work", "household")
    LABELS = os.path.join(HH, "labels.tsv")
    ACTIVITY = os.path.join(HH, "activity.json")
    HERE = os.path.join(site, "senses", "map")
    FLOORPLAN = os.path.join(HERE, "floorplan.json")
    RSSI_HISTORY = os.path.join(HERE, "rssi-history.jsonl")
    NOW = now or datetime.datetime.now(datetime.timezone.utc)

# Room vocabulary for label text. Geometry is NOT here: the drawing comes from
# SITE/senses/map/floorplan.json (Ilya said 2 rooms; the others are not carried).
ROOMS = ["living_room", "hallway", "bedroom", "balcony", "wardrobe", "kitchen_stall"]
BLIND_CAP = 0.3                                      # a blind-spot room is never seen, only inferred
# Living room left part, metres from the living room corner (Ilya, 11 Nov: camera cannot see
# the left part or the kitchen stall). Inferred; drawn dashed red.
BLIND_SPOT_M = (0.0, 0.0, 2.2, 5.0)
PLAN_SCALE = 50    # pixels per metre
PLAN_PAD = 20
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


def load_floorplan():
    """SITE floorplan.json as (doc, None), or (None, reason). A missing plan is UNKNOWN, never a guessed one."""
    try:
        with open(FLOORPLAN, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "floorplan.json unreadable (%s)" % type(exc).__name__
    if not isinstance(doc, dict) or not isinstance(doc.get("rooms"), list):
        return None, "floorplan.json has no rooms list"
    return doc, None


def _field(d):
    """(value, provenance) of one floorplan field; absent is (None, 'unknown')."""
    if not isinstance(d, dict):
        return None, "unknown"
    return d.get("value"), d.get("provenance", "unknown")


def plan_rooms(doc):
    """Floorplan rooms in metres with per-field provenance. geo is False when any drawing number is missing."""
    out = []
    for r in doc.get("rooms", []):
        if not isinstance(r, dict) or not r.get("id"):
            continue
        w, wp = _field(r.get("width_m"))
        d, dp = _field(r.get("depth_m"))
        a, ap = _field(r.get("area_m2"))
        pos = r.get("position_m") if isinstance(r.get("position_m"), dict) else {}
        x, y, pp = pos.get("x"), pos.get("y"), pos.get("provenance", "unknown")
        out.append({"id": r["id"], "label": ROOM_LABEL.get(r["id"], r.get("label") or r["id"]),
                    "x": x, "y": y, "w": w, "d": d, "area": a,
                    "wp": wp, "dp": dp, "ap": ap, "pp": pp,
                    "geo": all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y, w, d))})
    return out


def _tag(prov):
    return "assumed" if prov == "assumed" else str(prov)


def _esc(text):
    return html.escape(str(text), quote=True)


def fingerprint_footer():
    """RSSI footer facts: leave-one-out and the live vector, read from the fingerprint written this render."""
    try:
        doc = json.load(open(os.path.join(HERE, "rssi-fingerprint.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return "Leave-one-out: UNKNOWN (rssi-fingerprint.json missing)"
    loo = doc.get("loo") or {}
    live = doc.get("live") or {}
    age = live.get("rssi_age_s")
    return ("Leave-one-out: %s/%s correct, majority baseline %s; one feature, not a room claim. "
            "Live Redmi10 vector age %s s."
            % (loo.get("correct"), loo.get("n"), loo.get("majority_baseline_accuracy"),
               "%.0f" % age if isinstance(age, (int, float)) else "UNKNOWN"))


def rssi_history_footer():
    """Rows in rssi-history.jsonl and the age of the newest one; unreadable is UNKNOWN."""
    try:
        with open(RSSI_HISTORY, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError) as exc:
        return "RSSI history: UNKNOWN (%s)" % type(exc).__name__
    if not rows:
        return "RSSI history: 0 rows"
    at = parse_iso(rows[-1].get("at"))
    age = "%.0f s" % (NOW - at).total_seconds() if at else "UNKNOWN"
    return "RSSI history: %d rows, newest %s old" % (len(rows), age)


def activity_footer(activity):
    """Camera room activity as one footer line: current, historical (named as not current) or UNKNOWN."""
    parts = []
    for room in ("living_room", "bedroom"):
        act = activity.get(room) or {}
        state = act.get("state", "UNKNOWN")
        name = ROOM_LABEL[room]
        if state == "current":
            parts.append("%s activity %.3f (%s)" % (name, act["activity"], act["last_usable_utc"]))
        elif state == "historical":
            parts.append("%s historical %.3f (%s), not current" % (name, act["activity"], act["last_usable_utc"]))
        else:
            parts.append("%s activity UNKNOWN" % name)
    return "Camera activity: " + "; ".join(parts)


def svg(rows, activity, rssi, fp_doc, fp_error):
    """Floorplan picture: rooms from floorplan.json, tokens with uncertainty halos, evidence footer."""
    rooms = plan_rooms(fp_doc) if fp_doc else []
    geo = {r["id"]: r for r in rooms if r["geo"]}
    s, pad = PLAN_SCALE, PLAN_PAD
    plan_w = max([r["x"] + r["w"] for r in geo.values()] or [6])
    plan_h = max([r["y"] + r["d"] for r in geo.values()] or [5])
    def X(m):
        return pad + m * s
    def Y(m):
        return pad + m * s

    colours = {"ilya": "#f2c14e", "rozalia": "#e8788c", "bruno": "#7ec8e3"}
    out = []
    plan_bottom = Y(plan_h)
    footer = []
    # plan rooms, drawn from floorplan.json; assumed geometry is dashed and tagged
    for r in geo.values():
        dash = ' stroke-dasharray="8 5"' if "assumed" in (r["wp"], r["dp"], r["ap"], r["pp"]) else ""
        out.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#2a3140" stroke="#7a8599" '
                   'stroke-width="2"%s/>' % (X(r["x"]), Y(r["y"]), r["w"] * s, r["d"] * s, dash))
        out.append('<text x="%g" y="%g" fill="#c9d1e0">%s  %g m² [%s]</text>' %
                   (X(r["x"]) + 6, Y(r["y"]) + 16, _esc(r["label"]), r["area"] if isinstance(r["area"], (int, float)) else 0,
                    _esc(_tag(r["ap"]))))
        dims_tag = "assumed" if "assumed" in (r["wp"], r["dp"]) else _tag(r["wp"])
        out.append('<text x="%g" y="%g" fill="#7f8aa3" font-size="10">%.1f × %.1f m [%s]</text>' %
                   (X(r["x"]) + 6, Y(r["y"]) + 32, r["w"], r["d"], _esc(dims_tag)))
    if fp_doc is None:
        out.append('<text x="%d" y="%d" fill="#e08a87">NO FLOORPLAN: %s</text>' % (X(0), Y(1), _esc(fp_error)))
    elif not geo:
        out.append('<text x="%d" y="%d" fill="#e08a87">floorplan has no drawable room geometry</text>' % (X(0), Y(1)))
    # blind spot: inferred, living room left part, relative to the living room corner
    lr = geo.get("living_room")
    if lr:
        bx, by, bw, bh = BLIND_SPOT_M
        out.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#000" fill-opacity="0.35" '
                   'stroke="#c0504d" stroke-dasharray="4 3"/>' % (X(lr["x"] + bx), Y(lr["y"] + by), bw * s, bh * s))
        out.append('<text x="%g" y="%g" fill="#e08a87" font-size="10">BLIND SPOT (inferred): camera cannot see</text>' %
                   (X(lr["x"] + bx) + 4, Y(lr["y"] + bh) - 6))
    # tokens: halo radius shows uncertainty; UNKNOWN is hollow
    slot, unplaced = {}, []
    for person in PEOPLE:
        r = rows[person]
        g = r["guess"]
        conf = g["confidence"]
        unknown = r["confidence"] == "UNKNOWN"
        if g["room"] is None or not conf:
            unplaced.append("%s: no position, %s" % (person, r["why"]))
            continue
        room = geo.get(g["room"])
        if room is None:
            unplaced.append("%s: best guess %s is not in floorplan (unplaced)" % (person, ROOM_LABEL[g["room"]]))
            continue
        k = slot.get(room["id"], 0)
        slot[room["id"]] = k + 1
        cx = X(room["x"] + 1.2 + 1.4 * k)
        cy = Y(room["y"] + 2.0)
        halo = 12 + 45 * (1 - conf)
        colour = colours[person]
        out.append('<circle cx="%g" cy="%g" r="%g" fill="%s" fill-opacity="0.12" stroke="%s" '
                   'stroke-opacity="0.5" stroke-dasharray="2 3"/>' % (cx, cy, halo, colour, colour))
        if unknown:
            out.append('<circle cx="%g" cy="%g" r="10" fill="none" stroke="%s" stroke-width="2" '
                       'stroke-dasharray="3 2"/>' % (cx, cy, colour))
        else:
            out.append('<circle cx="%g" cy="%g" r="10" fill="%s" fill-opacity="0.85"/>' % (cx, cy, colour))
        # labels stack under the room name, so neighbouring halos cannot hide them
        out.append('<text x="%g" y="%g" fill="%s" font-size="10">%s best guess %s %.2f%s</text>' %
                   (X(room["x"]) + 6, Y(room["y"]) + 50 + 14 * k, colour, person, ROOM_LABEL[room["id"]],
                    conf, " UNKNOWN" if unknown else ""))
    # footer: what the picture says and what it does not
    ly = plan_bottom + 30
    lines = []
    if fp_doc:
        total = fp_doc.get("total_area_m2") if isinstance(fp_doc.get("total_area_m2"), dict) else {}
        lines.append("Plan: schematic, from floorplan.json (written %s). Total %s m2 [%s]. "
                     "Tagged assumed = placeholder, not measured."
                     % (fp_doc.get("written_utc", "?"), total.get("value", "UNKNOWN"), _tag(total.get("provenance", "unknown"))))
    else:
        lines.append("Plan: UNKNOWN, %s; no rooms drawn." % fp_error)
    for person in PEOPLE:
        r = rows[person]
        place = ROOM_LABEL[r["room"]] if r["room"] else "UNKNOWN"
        lines.append("%s: %s, marker confidence %s (%s)" % (person, place, r["confidence"], r["why"]))
    lines.append(rssi["text"])
    lines.append(activity_footer(activity))
    lines.append(rssi_history_footer())
    lines.append(fingerprint_footer())
    lines.append("Camera coverage: not drawn; floorplan has no camera pose (cam_livingroom in living room, "
                 "cam_bedroom in bedroom).")
    lines.append("Blind spot: living room left 2.2 m, inferred (Ilya 11 Nov); kitchen stall room not stated in floorplan.")
    lines.extend(unplaced)
    wrapped = []
    for line in lines:
        parts = textwrap.wrap(line, 118) or [""]
        wrapped.append(parts[0])
        wrapped.extend("    " + part for part in parts[1:])
    lines = wrapped
    width = int(max(plan_w * s, 860) + 2 * pad)
    height = int(ly + 4 + len(lines) * 18 + pad)
    head = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="monospace" font-size="12">'
            % (width, height), '<rect width="100%" height="100%" fill="#1b1d24"/>']
    out = head + out
    out.append('<text x="%d" y="%g" fill="#c9d1e0">Ilya / Rozalia / Bruno: best guess, room level. Halo radius = uncertainty; hollow = UNKNOWN.</text>'
               % (pad, ly - 16))
    for i, line in enumerate(lines):
        colour = colours.get(line.split(":", 1)[0], "#c9d1e0")
        out.append('<text x="%d" y="%g" fill="%s">%s</text>' % (pad, ly + 4 + 18 * i, colour, _esc(line)))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_png(svg_path, png_path):
    """PNG beside the SVG via cairosvg. A missing tool or a failed conversion is UNKNOWN, and any old PNG is removed."""
    try:
        import cairosvg
    except ImportError:
        cairosvg = None
    if cairosvg is None:
        _drop(png_path)
        return {"state": "UNKNOWN", "reason": "cairosvg not importable from this python"}
    try:
        cairosvg.svg2png(url=svg_path, write_to=png_path)
    except Exception as exc:  # any conversion failure is UNKNOWN, not a stale picture
        _drop(png_path)
        return {"state": "UNKNOWN", "reason": "cairosvg failed (%s)" % type(exc).__name__}
    return {"state": "ok", "path": png_path}


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


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
    fp_doc, fp_error = load_floorplan()
    os.makedirs(HERE, exist_ok=True)
    svg_path = os.path.join(HERE, "apartment-map.svg")
    png_path = os.path.join(HERE, "apartment-map.png")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg(rows, rooms_only, rssi, fp_doc, fp_error))
    png = write_png(svg_path, png_path)
    with open(os.path.join(HERE, "apartment-map.txt"), "w", encoding="utf-8") as fh:
        fh.write(ascii_map(rows, rssi))
    state = {"generated_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "markers": rows,
             "room_activity": activity, "rssi": rssi,
             "floorplan": {"state": "ok" if fp_doc else "UNKNOWN", "reason": fp_error},
             "png": png}
    with open(os.path.join(HERE, "map-state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, ensure_ascii=False)
    print(json.dumps({p: {"room": rows[p]["room"], "confidence": rows[p]["confidence"]} for p in PEOPLE}))
    print(svg_path)
    print(png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
