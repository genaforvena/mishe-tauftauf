#!/usr/bin/env python3
"""Learned map priors from the hourly feature store, scored on held-out days before use.

Reads SITE/features/house-hourly.jsonl (tools/household_features.py) and, for each room with
bedroom camera motion, learns the probability that the room's "presence proxy" is active at each
UTC hour of day. The proxy is deliberately crude and is named as such: an hour is active when the
room's camera motion is above the median of all its observed hours. It is a stand-in for occupancy,
not occupancy; no labels and no people are involved.

Training: hours before the cut (the last HOLDOUT_DAYS days are held out). Only hours whose
bedroom camera frames were available enter the series — an hour with every frame unavailable
carries no motion reading even when the producer writes a number. A learned hour needs at
least MIN_N training samples, otherwise it stays null and the renderer falls back.

Scoring: per-hour Brier score on held-out hours (every UTC hour, not just night), learned per-hour
prior against the hand-set constant, recorded per hour in priors.json so the published priors carry
their own held-out evidence. The learned prior is ADOPTED only on the aggregate night-hours window
(the same window the renderer's hand-set night prior covers): it must beat the hand-set constant on
held-out night hours and have enough held-out samples; otherwise map_render keeps its hand-set value.
Adoption is recorded in priors.json, never assumed.

Writes SITE/senses/map/priors.json (derived numbers only; never committed).

usage: python3 -I tools/map_priors.py --site SITE [--now EPOCH]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

HOLDOUT_DAYS = 6
MIN_N = 3
MIN_HELDOUT = 20
STALE_HOURS = 48
HAND_NIGHT = 0.4
NIGHT_UTC = (21, 5)  # same assumed window as tools/map_render.py


def in_night(hour):
    start, end = NIGHT_UTC
    return hour >= start or hour < end


def load_rows(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("t"), (int, float)):
                    rows.append(r)
    except OSError:
        return None
    return rows


def _quartiles(xs):
    xs = sorted(xs)
    if len(xs) < 2:
        return xs[0], xs[0]
    q = statistics.quantiles(xs, n=4)
    return q[0], q[2]


def describe(rows):
    """Per hour-of-day median and interquartile spread of motion, light and sound, with sample counts.

    Descriptive only: nothing here feeds the renderer. Hour-of-week needs more weeks than the store
    holds (about 16 days, so about two samples per cell); it is deferred to 2026-12-12 when three
    weeks of history exist. Sound comes from the living room's derived rms_p50 (no raw audio).
    """
    fields = {
        "bedroom_motion": ("camera", "bedroom", "motion"),
        "bedroom_luma": ("camera", "bedroom", "luma_mean"),
        "livingroom_luma": ("camera", "living room", "luma_mean"),
        "livingroom_motion": ("camera", "living room", "motion"),
        "livingroom_sound_rms_p50": ("audio", "livingroom", "rms_p50"),
    }
    acc = {k: {h: [] for h in range(24)} for k in fields}
    for r in rows:
        h = dt.datetime.fromtimestamp(r["t"], dt.timezone.utc).hour
        for key, (sec, room, field) in fields.items():
            v = r.get(sec, {}).get(room, {})
            if isinstance(v, dict) and isinstance(v.get(field), (int, float)):
                acc[key][h].append(float(v[field]))
    out = {}
    for key, per_hour in acc.items():
        cells = {}
        for h in range(24):
            xs = per_hour[h]
            if not xs:
                cells[str(h)] = {"n": 0, "median": None, "iqr": None}
                continue
            lo, hi = _quartiles(xs)
            cells[str(h)] = {"n": len(xs), "median": round(statistics.median(xs), 4),
                             "iqr": [round(lo, 4), round(hi, 4)]}
        out[key] = cells
    return out


def build(site, now):
    path = os.path.join(site, "features", "house-hourly.jsonl")
    rows = load_rows(path)
    out = {"v": 1, "generated_utc": now.strftime("%Y-%m-%dT%H:%MZ"), "source": "features/house-hourly.jsonl",
           "proxy": "camera motion above the room's median hour (crude presence proxy, not occupancy)",
           "rooms": {}, "night_bedroom": {"adopted": False, "reason": "not evaluated"}}
    if rows is None or not rows:
        out["night_bedroom"]["reason"] = "feature store missing or empty: UNKNOWN"
        return out
    series = []
    hours_total = hours_usable = 0
    for r in rows:
        cam = r.get("camera", {}).get("bedroom", {})
        if not isinstance(cam, dict):
            continue
        if isinstance(cam.get("motion"), (int, float)):
            hours_total += 1
            # An hour whose frames were all unavailable carries no motion reading; the
            # producer still writes a number there, so availability — not the number's
            # presence — decides whether the hour is usable evidence.
            if cam.get("unavailable", 0) == 0:
                hours_usable += 1
                t = dt.datetime.fromtimestamp(r["t"], dt.timezone.utc)
                series.append((t, float(cam["motion"])))
    series.sort()
    if len(series) < 2:
        out["night_bedroom"]["reason"] = ("too few camera-available bedroom hours (%d of %d): UNKNOWN"
                                           % (len(series), hours_total))
        out["bedroom_hours"] = {"total": hours_total, "camera_available": hours_usable}
        return out
    out["bedroom_hours"] = {"total": hours_total, "camera_available": hours_usable}
    # The cut is relative to the newest row, so a stalled feature store would otherwise
    # "hold out" weeks-old data and adopt a prior with no freshness signal. Refuse when stale.
    age_h = (now - series[-1][0]).total_seconds() / 3600
    out["bedroom_hours"]["newest_age_hours"] = round(age_h, 1)
    if age_h > STALE_HOURS:
        out["night_bedroom"]["reason"] = ("newest camera-available hour is %.0f h old (limit %d h): "
                                          "feature store stale, hand-set %.1f kept" % (age_h, STALE_HOURS, HAND_NIGHT))
        return out
    median = statistics.median(m for _, m in series)
    cut = series[-1][0] - dt.timedelta(days=HOLDOUT_DAYS)
    train = [(t, 1 if m > median else 0) for t, m in series if t < cut]
    test = [(t, 1 if m > median else 0) for t, m in series if t >= cut]

    counts = {h: [0, 0] for h in range(24)}
    for t, y in train:
        counts[t.hour][0] += y
        counts[t.hour][1] += 1
    learned = {h: (round(c[0] / c[1], 3) if c[1] >= MIN_N else None) for h, c in counts.items()}
    out["rooms"]["bedroom"] = {
        "median_motion": round(median, 5),
        "train_hours": len(train),
        "by_hour_utc": {str(h): {"p_active": learned[h], "n": counts[h][1]} for h in range(24)},
    }

    out["descriptive"] = describe(rows)

    # Per-hour held-out evidence: for every UTC hour, the Brier of the learned prior (or the
    # hand-set fallback when the hour has too few training samples) against the hand-set constant
    # on the held-out hours at that hour. Recorded per hour so the published priors carry their own
    # evidence; the adoption gate below stays the aggregate night-hours one.
    fallback = HAND_NIGHT
    held_by_hour = {}
    for t, y in test:
        held_by_hour.setdefault(t.hour, []).append(y)
    for h in range(24):
        cell = out["rooms"]["bedroom"]["by_hour_utc"][str(h)]
        ys = held_by_hour.get(h)
        if not ys:
            cell["heldout_n"] = 0
            cell["brier_learned"] = None
            cell["brier_hand_set"] = None
            cell["beats_hand_set"] = None
            continue
        pred = learned[h] if learned[h] is not None else fallback
        b_learn = sum((pred - y) ** 2 for y in ys) / len(ys)
        b_hand = sum((fallback - y) ** 2 for y in ys) / len(ys)
        cell["heldout_n"] = len(ys)
        cell["brier_learned"] = round(b_learn, 4)
        cell["brier_hand_set"] = round(b_hand, 4)
        cell["beats_hand_set"] = b_learn < b_hand

    held = [(t.hour, y) for t, y in test if in_night(t.hour)]
    n = len(held)
    if n < MIN_HELDOUT:
        out["night_bedroom"]["reason"] = "held-out night hours %d < %d: hand-set %.1f kept" % (n, MIN_HELDOUT, HAND_NIGHT)
        out["night_bedroom"]["heldout_n"] = n
        return out
    b_hand = sum((fallback - y) ** 2 for _, y in held) / n
    b_learn = sum(((learned[h] if learned[h] is not None else fallback) - y) ** 2 for h, y in held) / n
    adopted = b_learn < b_hand
    out["night_bedroom"] = {
        "adopted": adopted,
        "heldout_n": n,
        "brier_learned": round(b_learn, 4),
        "brier_hand_set": round(b_hand, 4),
        "reason": ("learned hour prior beats hand-set %.1f on held-out night hours" % HAND_NIGHT) if adopted
        else ("learned hour prior does not beat hand-set %.1f on held-out night hours; hand-set kept" % HAND_NIGHT),
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Learn map priors from the hourly store; score on held-out days.")
    ap.add_argument("--site", required=True)
    ap.add_argument("--now", type=float, default=None)
    args = ap.parse_args(argv)
    site = os.path.abspath(args.site)
    now = dt.datetime.fromtimestamp(args.now, dt.timezone.utc) if args.now else dt.datetime.now(dt.timezone.utc)
    doc = build(site, now)
    target_dir = os.path.join(site, "senses", "map")
    os.makedirs(target_dir, exist_ok=True)
    tmp = os.path.join(target_dir, "priors.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, os.path.join(target_dir, "priors.json"))
    nb = doc["night_bedroom"]
    print(json.dumps({"adopted": nb.get("adopted", False), "reason": nb.get("reason")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
