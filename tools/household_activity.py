#!/usr/bin/env python3
"""Household activity reading: camera change, lights, quiet blocks, rhythm and sound.

Input : SITE/spatial/work/household/signatures.jsonl (ok rows: room, captured_at_epoch_s,
        grid, mean_luma; no frames) and SITE/spatial/work/household/audio.jsonl
        (rows: t, room, rms).
Output: SITE/spatial/work/household/activity.json, replaced atomically each run:
        rooms          15-min buckets per room: median frame-pair change (0-1) and mean luma
        summary        lights events, settled-for-night run, longest quiet block, rhythm
        sound_by_hour  per room, per UTC hour of day: samples and median audio rms
Activity is a derived change number, not presence. Every threshold is provisional and
uncalibrated, and the output says so. No usable rows exit 1, which the duty reads as
UNKNOWN; a room without rows is absent, never a guessed value.
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOMS = ("living room", "bedroom")
HOUSEHOLD = Path("spatial/work/household")
PAIR_MAX_GAP = 300.0
ACT_WINDOW = 600.0
BUCKET = 900
QUIET = 2.0          # 0-255 grid change units, per frame record
LUMA_STEP = 20.0
EVENT_GAP = 900.0
SETTLED_LUMA = 70.0
SETTLED_MIN_S = 1800.0
QUIET_BLOCK_MIN_S = 600.0
NIGHT_MIN_S = 4 * 3600.0   # a dark run must be at least this long to count as the night
PROVISIONAL = "PROVISIONAL, NOT VALIDATED: compare with the household's own bed and wake times before use"


def hhmm(t):
    return time.strftime("%H:%M", time.gmtime(t)) + "Z" if t is not None else None


def grid_distance(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def load_frames(rows: list[dict]) -> dict[str, list[dict]]:
    by_room: dict[str, list[dict]] = {room: [] for room in ROOMS}
    for row in rows:
        if row.get("status") != "ok" or row.get("room") not in by_room:
            continue
        if not isinstance(row.get("captured_at_epoch_s"), (int, float)) \
                or not isinstance(row.get("mean_luma"), (int, float)) or not row.get("grid"):
            continue
        by_room[row["room"]].append(row)
    for frames in by_room.values():
        frames.sort(key=lambda r: r["captured_at_epoch_s"])
    return by_room


def frame_records(frames: list[dict]) -> tuple[list[dict], list[float]]:
    """Per frame: change from the previous frame and trailing 600 s activity (0-255 units)."""
    recs = []
    for i, row in enumerate(frames):
        t = float(row["captured_at_epoch_s"])
        change = None
        if i > 0:
            prev = frames[i - 1]
            if t - float(prev["captured_at_epoch_s"]) <= PAIR_MAX_GAP and len(prev["grid"]) == len(row["grid"]):
                change = grid_distance(prev["grid"], row["grid"])
        recs.append({"t": t, "luma": float(row["mean_luma"]), "change": change, "activity": None})
    times = [r["t"] for r in recs]
    for j, rec in enumerate(recs):
        lo = bisect.bisect_left(times, rec["t"] - ACT_WINDOW)
        win = [r["change"] for r in recs[lo:j + 1] if r["change"] is not None]
        rec["activity"] = round(sum(win) / len(win), 2) if len(win) >= 3 else None
    return recs, times


def _span(times: list[float], lo_excl: float, hi_incl: float) -> tuple[int, int]:
    return bisect.bisect_right(times, lo_excl), bisect.bisect_right(times, hi_incl)


def unobserved_gaps(times: list[float]) -> list[tuple[float, float]]:
    """Spans between consecutive usable frames longer than PAIR_MAX_GAP: UNKNOWN, not dark or quiet."""
    return [(a, b) for a, b in zip(times, times[1:]) if b - a > PAIR_MAX_GAP]


def has_gap(times: list[float], lo: float, hi: float) -> bool:
    a, b = _span(times, lo, hi)
    seg = times[max(a - 1, 0):b + 1]
    return any(y - x > PAIR_MAX_GAP for x, y in zip(seg, seg[1:]))


def luma_events(recs: list[dict], times: list[float]) -> list[dict]:
    """Lamp events: a step of >= LUMA_STEP that holds for the next 600 s, one per EVENT_GAP."""
    events, last = [], None
    for rec in recs:
        t = rec["t"]
        if has_gap(times, t - 780, t + 600):
            continue
        a, b = _span(times, t - 180, t)
        now = [r["luma"] for r in recs[a:b]]
        a, b = _span(times, t - 780, t - 300)
        before = [r["luma"] for r in recs[a:b]]
        a, b = _span(times, t, t + 600)
        ahead = [r["luma"] for r in recs[a:b]]
        if len(now) < 2 or len(before) < 3 or len(ahead) < 4:
            continue
        step = statistics.median(now) - statistics.median(before)
        held = statistics.median(ahead) - statistics.median(before)
        if abs(step) >= LUMA_STEP and abs(held) >= LUMA_STEP and (step > 0) == (held > 0) \
                and (last is None or t - last >= EVENT_GAP):
            events.append({"t": t, "kind": "on" if step > 0 else "off", "step": round(step, 1)})
            last = t
    return events


def runs(recs: list[dict], pred, min_s: float) -> list[tuple[float, float]]:
    """Sustained runs where pred(rec) holds for at least min_s seconds."""
    out, start, prev = [], None, 0.0
    for rec in recs:
        ok = pred(rec)
        if start is not None and rec["t"] - prev > PAIR_MAX_GAP:
            # An unobserved gap ends the run: silence is UNKNOWN, never a sustained quiet block.
            if prev - start >= min_s:
                out.append((start, prev))
            start = None
        if ok and start is None:
            start = rec["t"]
        if not ok and start is not None:
            if prev - start >= min_s:
                out.append((start, prev))
            start = None
        prev = rec["t"]
    if start is not None and prev - start >= min_s:
        out.append((start, prev))
    return out


def operator_blind(base: Path) -> list[dict]:
    """Blind spans stated by the household (not inferred): {room, from_utc|null, to_utc, reason}."""
    try:
        rows = json.loads((base / "operator_blind_intervals.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("room") in ROOMS and isinstance(row.get("to_utc"), str):
            out.append({"room": row["room"], "from_utc": row.get("from_utc") or "UNKNOWN",
                        "to_utc": row["to_utc"], "minutes": None, "reason": str(row.get("reason", ""))})
    return out


def _interval(a: float, b: float) -> dict:
    return {"from_utc": hhmm(a), "to_utc": hhmm(b), "minutes": round((b - a) / 60),
            "reason": "no usable frame for over 300 s: camera unreachable or blind, not dark"}


def night_lights_out(lights: list[dict], data_end: float | None = None) -> float | None:
    """Start of the latest night: the first lamp-off of a dark run lasting at least NIGHT_MIN_S.
    A dark run starts at the first lamp-off after the previous lamp-on (or at the first lamp-off of
    the data) and ends at the next lamp-on; a run still open at data_end counts up to data_end. The
    last lamp-off of the day is not the night: it is a daytime switch. None (UNKNOWN) when no run
    qualifies."""
    night = None
    run_start = None
    for e in sorted(lights, key=lambda ev: ev["t"]):
        if e["kind"] == "off" and run_start is None:
            run_start = e["t"]
        elif e["kind"] == "on" and run_start is not None:
            if e["t"] - run_start >= NIGHT_MIN_S:
                night = run_start
            run_start = None
    if run_start is not None and data_end is not None and data_end - run_start >= NIGHT_MIN_S:
        night = run_start
    return night


def summarize(living: tuple[list[dict], list[float]], bedroom: tuple[list[dict], list[float]],
              blind: list[dict] = ()) -> dict:
    lrecs, ltimes = living
    brecs, btimes = bedroom
    lights = luma_events(lrecs, ltimes) if lrecs else []
    settled = runs(brecs, lambda r: r["activity"] is not None and r["activity"] < QUIET
                   and r["luma"] <= SETTLED_LUMA, SETTLED_MIN_S)
    lights_out = night_lights_out(lights, lrecs[-1]["t"] if lrecs else None)
    first_active = None
    if lights_out is not None:
        # The 600 s trailing window still holds the lamp's own change for 600 s; skip it.
        for rec in lrecs:
            if rec["t"] > lights_out + ACT_WINDOW and rec["activity"] is not None and rec["activity"] >= QUIET:
                first_active = rec["t"]
                break
    quiet = runs(lrecs, lambda r: r["activity"] is not None and r["activity"] < QUIET, QUIET_BLOCK_MIN_S)
    longest = max(quiet, key=lambda b: b[1] - b[0]) if quiet else None
    return {
        "status": "provisional",
        "unknown_intervals": {
            "living room": [_interval(a, b) for a, b in unobserved_gaps(ltimes)]
            + [b for b in blind if b["room"] == "living room"],
            "bedroom": [_interval(a, b) for a, b in unobserved_gaps(btimes)]
            + [b for b in blind if b["room"] == "bedroom"]},
        "lights_events": [{"utc": hhmm(e["t"]), "kind": e["kind"], "step": e["step"]} for e in lights],
        "lights_out_utc": hhmm(lights_out),
        "first_activity_after_lights_out_utc": hhmm(first_active),
        "settled_for_night": [{"from_utc": hhmm(a), "to_utc": hhmm(b), "minutes": round((b - a) / 60)}
                              for a, b in settled],
        "longest_quiet_block_living_room": (
            {"from_utc": hhmm(longest[0]), "to_utc": hhmm(longest[1]),
             "minutes": round((longest[1] - longest[0]) / 60)} if longest else None),
        "thresholds": {"pair_max_gap_s": PAIR_MAX_GAP, "activity_window_s": ACT_WINDOW, "quiet": QUIET,
                       "luma_step": LUMA_STEP, "event_gap_s": EVENT_GAP,
                       "settled_bedroom_luma_max": SETTLED_LUMA, "settled_min_s": SETTLED_MIN_S},
        "validation": PROVISIONAL,
    }


def rhythm_line(summary: dict) -> str:
    """One plain line for the household window; missing parts read UNKNOWN."""
    quiet = summary.get("longest_quiet_block_living_room")
    quiet_text = (f"{quiet['from_utc']} to {quiet['to_utc']} ({quiet['minutes']} min)" if quiet else "UNKNOWN")
    return ("RHYTHM (provisional): lights out " + (summary.get("lights_out_utc") or "UNKNOWN")
            + "; first living-room activity after that "
            + (summary.get("first_activity_after_lights_out_utc") or "UNKNOWN")
            + "; longest quiet block " + quiet_text + ". " + gap_text(summary)
            + "Not checked against the household yet.")


def gap_text(summary: dict) -> str:
    unknown = summary.get("unknown_intervals") or {}
    parts = [f"{room} {len(spans)} UNKNOWN gap(s)" for room, spans in unknown.items() if spans]
    return ("UNKNOWN (camera unreachable or blind, not dark): " + "; ".join(parts) + ". ") if parts else ""


def buckets(frames: list[dict]) -> list[dict]:
    """15-min buckets: median consecutive-frame change (0-1, per /255) and mean luma."""
    cells: dict[int, dict[str, list[float]]] = {}
    for a, b in zip(frames, frames[1:]):
        ta, tb = float(a["captured_at_epoch_s"]), float(b["captured_at_epoch_s"])
        if tb - ta > PAIR_MAX_GAP or len(a["grid"]) != len(b["grid"]):
            continue
        k = int(tb // BUCKET) * BUCKET
        cells.setdefault(k, {"diffs": [], "luma": []})["diffs"].append(grid_distance(a["grid"], b["grid"]) / 255.0)
    for row in frames:
        k = int(float(row["captured_at_epoch_s"]) // BUCKET) * BUCKET
        cells.setdefault(k, {"diffs": [], "luma": []})["luma"].append(float(row["mean_luma"]))
    series, prev_luma = [], None
    for k in sorted(cells):
        cell = cells[k]
        luma = statistics.mean(cell["luma"]) if cell["luma"] else None
        act = statistics.median(cell["diffs"]) if len(cell["diffs"]) >= 3 else None
        series.append({
            "utc": hhmm(k),
            "frame_pairs": len(cell["diffs"]),
            "activity": None if act is None else round(act, 4),
            "mean_luma": None if luma is None else round(luma, 1),
            "lights_flag_luma_jump": None if luma is None or prev_luma is None else round(luma - prev_luma, 1),
        })
        if luma is not None:
            prev_luma = luma
    return series


def quantiles(series: list[dict]) -> dict | None:
    acts = sorted(s["activity"] for s in series if s["activity"] is not None)
    if not acts:
        return None
    return {"p10": acts[len(acts) // 10], "p50": round(statistics.median(acts), 4),
            "p90": acts[min(len(acts) - 1, int(len(acts) * 0.9))]}


def sound_by_hour(audio_rows: list[dict]) -> dict:
    bins: dict[str, dict[str, list[float]]] = {}
    for row in audio_rows:
        t, rms, room = row.get("t"), row.get("rms"), row.get("room")
        if not isinstance(t, (int, float)) or not isinstance(rms, (int, float)):
            continue
        hour = f"{time.gmtime(t).tm_hour:02d}"
        bins.setdefault(str(room), {}).setdefault(hour, []).append(float(rms))
    return {room: {hour: {"samples": len(vals), "median_rms": round(statistics.median(vals), 1)}
                   for hour, vals in sorted(hours.items())}
            for room, hours in sorted(bins.items())}


def build(signature_rows: list[dict], audio_rows: list[dict], now: float,
          blind: list[dict] | None = None) -> dict | None:
    frames = load_frames(signature_rows)
    if not any(frames.values()):
        return None
    rooms = {}
    for room in ROOMS:
        if frames[room]:
            series = buckets(frames[room])
            rooms[room] = {"ok_samples": len(frames[room]), "buckets": series,
                           "activity_quantiles": quantiles(series),
                           "usable_buckets": sum(1 for s in series if s["activity"] is not None)}
    living = frame_records(frames["living room"]) if frames["living room"] else ([], [])
    bedroom = frame_records(frames["bedroom"]) if frames["bedroom"] else ([], [])
    return {"version": 1, "generated_utc": datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
            .isoformat(timespec="seconds"),
            "method": "rooms: median consecutive-frame change /255 over gaps <= 300 s, 15-min buckets; "
                      "summary: 600 s trailing change, lamp steps, settled and quiet runs",
            "rooms": rooms, "summary": summarize(living, bedroom, blind or []),
            "sound_by_hour": sound_by_hour(audio_rows)}


def write_atomic(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.site.is_dir():
        raise SystemExit(f"site does not exist: {args.site}")
    base = args.site / HOUSEHOLD
    report = build(read_jsonl(base / "signatures.jsonl"), read_jsonl(base / "audio.jsonl"), time.time(),
                   operator_blind(base))
    if report is None:
        print("activity: no ok signature rows for any room: UNKNOWN", file=sys.stderr)
        return 1
    write_atomic(base / "activity.json", report)
    print(rhythm_line(report["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
