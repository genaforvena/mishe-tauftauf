#!/usr/bin/env python3
"""Household feature store: hourly, house-level, derived numbers only, kept for years.

Reads the append-only household samplers (spatial/work/household/{signatures,phones,audio}.jsonl)
and appends one row per COMPLETE UTC hour to features/house-hourly.jsonl:

  {"v": 1, "hour": "2026-11-21T02:00Z", "t": 1795226400,
   "camera": {ROOM: {"n", "luma_mean", "luma_min", "luma_max", "motion", "pairs", "blind", "unavailable"}},
   "audio":  {ROOM: {"n", "rms_p50", "rms_p90", "rms_max", "status": {STATUS: count}}},
   "phones": {DEVICE: {"n", "ok", "stale", "unknown", "accel_std", "battery_min", "battery_last", "lux_mean"}}}

Rules (the point of the store is later pattern work, so these are not negotiable):
* Units are rooms and devices, never people; no raw frames, audio or text are stored (the camera
  signature grid is reduced to a luma mean and a frame-to-frame motion number).
* A source with no usable rows in an hour has NO key in that row. Absence means UNKNOWN; this tool
  never writes a zero for something it did not observe.
* Only hours strictly before the current one are written, each once. The watermark is the last row
  of the store, so a crash between the append and the state file cannot duplicate an hour.
* Malformed and unusable lines are counted in the state file and the printed detail, never repaired.
* The store is a long-lived record: no retention class may name features/ (see docs/household-features.md).

usage: household_features.py --site SITE [--now EPOCH]
Exit codes (duty runner): 0 fresh and valid, 4 UNKNOWN (no source file readable), 1 crash.
One JSON line is printed on stdout and becomes the duty detail.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

HOUR = 3600
SOURCES = ("signatures", "phones", "audio")
STORE_NAME = "house-hourly.jsonl"
STATE_NAME = "house-hourly.state.json"


def hour_start(t: float) -> int:
    return int(t // HOUR) * HOUR


def iso_hour(h: int) -> str:
    return time.strftime("%Y-%m-%dT%H:00Z", time.gmtime(h))


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def read_rows(path: Path, offset: int, cutoff_hour: int):
    """Rows of complete hours from `offset`. Returns (rows, new_offset, malformed, unusable).

    Stops at the first row of an hour >= cutoff_hour (the open hour) and leaves the offset at
    the start of that line so the next run reads it again once the hour is complete."""
    rows, malformed, unusable = [], 0, 0
    size = path.stat().st_size
    if offset > size:
        offset = 0                                  # truncated or rotated: re-read; the watermark prevents duplicates
    new_offset = offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        while True:
            start = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):             # a line still being written
                break
            try:
                row = json.loads(raw)
            except ValueError:
                malformed += 1
                new_offset = fh.tell()
                continue
            t = row.get("t", row.get("captured_at_epoch_s")) if isinstance(row, dict) else None
            if not finite(t):
                unusable += 1
                new_offset = fh.tell()
                continue
            if hour_start(t) >= cutoff_hour:
                new_offset = start
                break
            rows.append((t, row))
            new_offset = fh.tell()
    return rows, new_offset, malformed, unusable


def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 2)


def camera_features(rows):
    by_room = {}
    for t, row in rows:
        room = row.get("room")
        if isinstance(room, str):
            by_room.setdefault(room, []).append((t, row))
    out = {}
    for room, items in by_room.items():
        items.sort(key=lambda x: x[0])
        ok = [(t, r) for t, r in items if r.get("status") == "ok" and finite(r.get("mean_luma"))]
        feat = {"n": len(items), "blind": sum(1 for _, r in items if r.get("status") == "blind"),
                "unavailable": sum(1 for _, r in items if r.get("status") == "unavailable")}
        if ok:
            lumas = [r["mean_luma"] for _, r in ok]
            feat.update(luma_mean=round(statistics.fmean(lumas), 2), luma_min=min(lumas), luma_max=max(lumas))
            diffs = []
            for (t0, a), (t1, b) in zip(ok, ok[1:]):
                ga, gb = a.get("grid"), b.get("grid")
                if t1 - t0 <= 300 and isinstance(ga, list) and isinstance(gb, list) and len(ga) == len(gb) and ga:
                    if all(finite(x) for x in ga) and all(finite(x) for x in gb):
                        diffs.append(sum(abs(x - y) for x, y in zip(ga, gb)) / len(ga) / 255.0)
            if diffs:
                feat.update(motion=round(statistics.fmean(diffs), 5), pairs=len(diffs))
        out[room] = feat
    return out


def audio_features(rows):
    by_room = {}
    for _, row in rows:
        room = row.get("room")
        if isinstance(room, str):
            by_room.setdefault(room, []).append(row)
    out = {}
    for room, items in by_room.items():
        status = {}
        for r in items:
            s = r.get("status") if isinstance(r.get("status"), str) else "UNKNOWN"
            status[s] = status.get(s, 0) + 1
        rms = [r["rms"] for r in items if finite(r.get("rms"))]
        feat = {"n": len(items), "status": dict(sorted(status.items()))}
        if rms:
            feat.update(rms_p50=quantile(rms, 0.5), rms_p90=quantile(rms, 0.9), rms_max=max(rms))
        out[room] = feat
    return out


def phone_features(rows):
    devices = {}
    for _, row in rows:
        for dev, v in row.items():
            if isinstance(v, dict) and isinstance(v.get("status"), str):
                devices.setdefault(dev, []).append(v)
    out = {}
    for dev, items in devices.items():
        feat = {"n": len(items)}
        for s in ("ok", "stale", "unknown"):
            feat[s] = sum(1 for v in items if v["status"] == s)
        good = [v for v in items if v["status"] == "ok"]
        mags = []
        for v in good:
            a = v.get("accel")
            if isinstance(a, list) and len(a) == 3 and all(finite(x) for x in a):
                mags.append(math.sqrt(sum(x * x for x in a)))
        if len(mags) > 1:
            feat["accel_std"] = round(statistics.pstdev(mags), 4)
        batt = [v["battery_pct"] for v in good if finite(v.get("battery_pct"))]
        if batt:
            feat.update(battery_min=min(batt), battery_last=batt[-1])
        lux = [v["lux"] for v in good if finite(v.get("lux"))]
        if lux:
            feat["lux_mean"] = round(statistics.fmean(lux), 2)
        out[dev] = feat
    return out


FEATURES = {"signatures": ("camera", camera_features), "phones": ("phones", phone_features),
            "audio": ("audio", audio_features)}


def last_store_hour(store: Path):
    """The hour of the last complete row in the store, or None. Reads only the tail."""
    if not store.is_file() or store.stat().st_size == 0:
        return None
    with open(store, "rb") as fh:
        fh.seek(max(0, store.stat().st_size - 65536))
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    for raw in reversed(lines):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and finite(row.get("t")):
            return int(row["t"])
    return None


def load_state(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text())
        if isinstance(doc, dict) and isinstance(doc.get("offsets"), dict):
            return doc
    except (OSError, ValueError):
        pass
    return {"v": 1, "offsets": {}, "malformed": {}, "unusable": {}}


def run(site: Path, now: float) -> tuple[int, dict]:
    src = site / "spatial" / "work" / "household"
    paths = {name: src / f"{name}.jsonl" for name in SOURCES}
    present = {name: p for name, p in paths.items() if p.is_file()}
    detail = {"written": 0, "sources": sorted(present), "missing": sorted(set(SOURCES) - set(present))}
    if not present:
        detail["reason"] = f"no household source file under {src}"
        return 4, detail
    fdir = site / "features"
    fdir.mkdir(parents=True, exist_ok=True)
    store, state_path = fdir / STORE_NAME, fdir / STATE_NAME
    state = load_state(state_path)
    cutoff = hour_start(now)
    done = last_store_hour(store)                    # hours <= done are already in the store
    by_hour = {}
    for name, path in present.items():
        rows, offset, bad, unusable = read_rows(path, int(state["offsets"].get(name, 0)), cutoff)
        state["offsets"][name] = offset
        state["malformed"][name] = state["malformed"].get(name, 0) + bad
        state["unusable"][name] = state["unusable"].get(name, 0) + unusable
        for t, row in rows:
            h = hour_start(t)
            if done is None or h > done:
                by_hour.setdefault(h, {}).setdefault(name, []).append((t, row))
    lines = []
    for h in sorted(by_hour):
        row = {"v": 1, "hour": iso_hour(h), "t": h}
        for name, rows in by_hour[h].items():
            key, fn = FEATURES[name]
            feat = fn(rows)
            if feat:
                row[key] = feat
        if len(row) > 3:
            lines.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    if lines:
        with open(store, "a") as fh:
            fh.write("\n".join(lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.replace(tmp, state_path)
    detail["written"] = len(lines)
    detail["last_hour"] = iso_hour(max(by_hour)) if lines else (iso_hour(done) if done else None)
    detail["malformed"] = dict(state["malformed"])
    detail["unusable"] = dict(state["unusable"])
    return 0, detail


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--now", type=float, default=None, help="epoch seconds (tests)")
    args = parser.parse_args(argv)
    code, detail = run(args.site, args.now if args.now is not None else time.time())
    print(json.dumps(detail, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
