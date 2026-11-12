#!/usr/bin/env python3
"""Room fingerprint from the Redmi10 Wi-Fi RSSI: nearest labelled room, honest numbers.

Usage: python3 -I tools/rssi_fingerprint.py --site SITE [--now ISO]

Inputs (read-only, under SITE/senses/map):
  label-rssi-vectors.json  labelled moments with phone_to_ap.rssi_median_dbm
  rssi-table.json          live vector; its redmi10 rssi and generated_at

Output (overwritten each run): senses/map/rssi-fingerprint.json, also printed.

Rules:
  - One feature only: the Redmi10 phone-to-AP RSSI. The router-station RSSI is
    UNKNOWN for every labelled moment, so it cannot be fitted yet.
  - Only moments naming a room count (living_room, hallway, bedroom, balcony,
    wardrobe, kitchen_stall). "unknown" and "nobody" are not training rooms.
  - Live vector is UNKNOWN when generated_at is older than STALE_S, or when the
    redmi10 age is older than STALE_S. Stale is never a guess.
  - Nearest = smallest |dBm difference|. A tie across rooms is reported as a tie.
  - Leave-one-out accuracy: each labelled moment is predicted from the others.
    Compared with the majority-room baseline, because n is small.
  - Confidence is the LOO accuracy, capped by n. It is never raised above that.
"""
import datetime
import importlib.util
import json
import os
import sys
from pathlib import Path

# Parsing lives in the named adapter tools/rssi_text.py (listed in parsing-policy.json).
_spec = importlib.util.spec_from_file_location("rssi_text", Path(__file__).with_name("rssi_text.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)
parse_iso = rt.parse_iso

ROOMS = {"living_room", "hallway", "bedroom", "balcony", "wardrobe", "kitchen_stall"}
STALE_S = 180


def load_moments(path):
    doc = rt.read_json(path)
    out = []
    for m in doc.get("moments", []):
        room = m.get("room")
        dbm = (m.get("phone_to_ap") or {}).get("rssi_median_dbm")
        if room not in ROOMS or not isinstance(dbm, (int, float)) or isinstance(dbm, bool):
            continue
        out.append({"who": m.get("who"), "room": room, "dbm": float(dbm),
                    "label_utc": m.get("label_utc")})
    return out


def live_vector(path, now):
    unknown = {"state": "UNKNOWN", "dbm": None}
    try:
        doc = rt.read_json(path)
    except (OSError, ValueError):
        return dict(unknown, reason="rssi-table.json missing or unreadable")
    gen = doc.get("generated_at")
    try:
        gen_t = parse_iso(gen)
    except (TypeError, ValueError):
        return dict(unknown, reason="rssi-table.json has no usable generated_at")
    age_gen = (now - gen_t).total_seconds()
    phone = next((v for v in doc.get("vantages", []) if v.get("vantage") == "redmi10"), None)
    if phone is None or not isinstance(phone.get("rssi"), (int, float)):
        return dict(unknown, reason="no redmi10 rssi in rssi-table.json")
    age_rssi = phone.get("rssi_age_s")
    if age_gen > STALE_S or age_gen < -60 or not isinstance(age_rssi, (int, float)) or age_rssi > STALE_S:
        return dict(unknown, reason="stale: table %.0f s old, rssi %s s old (limit %d s)"
                    % (age_gen, age_rssi, STALE_S), dbm=float(phone["rssi"]))
    return {"state": "fresh", "dbm": float(phone["rssi"]), "generated_at": gen,
            "rssi_age_s": age_rssi}


def nearest(dbm, train):
    """Return (room or None for a tie, best distance, rooms tied at that distance)."""
    best = min(abs(dbm - t["dbm"]) for t in train)
    rooms = sorted({t["room"] for t in train if abs(dbm - t["dbm"]) == best})
    return (rooms[0] if len(rooms) == 1 else None), best, rooms


def loo(moments):
    hits = 0
    majority_hits = 0
    rows = []
    for i, m in enumerate(moments):
        rest = moments[:i] + moments[i + 1:]
        if not rest:
            continue
        pred, dist, tied = nearest(m["dbm"], rest)
        correct = pred == m["room"]
        hits += int(correct)
        counts = {}
        for r in rest:
            counts[r["room"]] = counts.get(r["room"], 0) + 1
        maj = max(sorted(counts), key=lambda r: counts[r])
        majority_hits += int(maj == m["room"])
        rows.append({"who": m["who"], "room": m["room"], "dbm": m["dbm"],
                     "predicted": pred, "tied": tied if pred is None else None,
                     "correct": correct})
    n = len(rows)
    return {"n": n, "correct": hits,
            "accuracy": round(hits / n, 2) if n else None,
            "majority_baseline_accuracy": round(majority_hits / n, 2) if n else None,
            "rows": rows}


def main(argv=None):
    a = rt.parse_cli(argv, "RSSI room fingerprint for one site.")
    site = os.path.abspath(a.site)
    mapdir = os.path.join(site, "senses", "map")
    now = parse_iso(a.now) if a.now else datetime.datetime.now(datetime.timezone.utc)
    moments = load_moments(os.path.join(mapdir, "label-rssi-vectors.json"))
    cv = loo(moments)
    live = live_vector(os.path.join(mapdir, "rssi-table.json"), now)
    # No labelled moment means nothing to match against: a fresh RSSI is not a
    # room claim, so mark the live vector UNKNOWN instead of reporting a tie.
    if live["state"] == "fresh" and not moments:
        live = dict(live, state="UNKNOWN", reason="no labelled moments to match against")
    if live["state"] == "fresh":
        pred, dist, tied = nearest(live["dbm"], moments)
        est = {"room": pred, "tied_rooms": tied if pred is None else [pred],
               "distance_dbm": round(dist, 1)}
        confidence = cv["accuracy"] if cv["accuracy"] is not None else 0.0
        if cv["n"] < 10 or cv["accuracy"] is None or cv["accuracy"] <= (cv["majority_baseline_accuracy"] or 0):
            confidence = "UNKNOWN"
    else:
        est = {"room": None, "tied_rooms": [], "distance_dbm": None}
        confidence = "UNKNOWN"
    out = {"generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "live": live,
           "estimate": est, "confidence": confidence,
           "loo": cv,
           "note": "one feature, %d labelled moments; accuracy is not a claim of room discrimination"
                   % cv["n"]}
    os.makedirs(mapdir, exist_ok=True)
    with open(os.path.join(mapdir, "rssi-fingerprint.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
