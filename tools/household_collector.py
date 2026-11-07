#!/usr/bin/env python3
"""One-shot household samplers, run by duties (not by a terminal window).

  camera --room NAME --source webcam|mac [--target HOST] --site SITE
      Captures one camera frame through tools/camera_signature.py, keeps only the
      derived grid, classifies a blind camera (lens covered, dark or flat image)
      and appends one row to spatial/work/household/signatures.jsonl.

  phones --site SITE
      Reads the newest Note3 sensor sample and the Redmi10 stream from the
      discovery snapshot, appends one row to spatial/work/household/phones.jsonl.

Exit codes follow the duty runner: 0 = fresh and valid (GREEN), 1 = missing,
stale, blind or failed (UNKNOWN; the signal ages to UNKNOWN). Nothing here reports
RED. Every run prints one JSON line on stdout, which becomes the duty detail.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTE3_MAX_AGE_S = 300.0
REDMI_MAX_AGE_S = 900.0
BLIND_FLAT_STDEV = 1.5          # a lens covered by cloth or a door: nearly no structure
BLIND_DARK_LUMA = 4.0           # near-black ...
BLIND_DARK_STDEV = 3.0          # ... with almost no structure


def load_camsig():
    spec = importlib.util.spec_from_file_location("camsig", HERE / "camera_signature.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["camsig"] = mod
    spec.loader.exec_module(mod)
    return mod


def classify_frame(grid: list[int], mean_luma: float) -> tuple[bool, str]:
    """Return (blind, reason). Pure: a blind frame must never reach the lights-out
    detector or the daylight curve, so the caller stores it as status 'blind'."""
    sd = statistics.pstdev(grid) if len(grid) > 1 else 0.0
    if sd < BLIND_FLAT_STDEV:
        return True, f"camera blind: flat grid (stdev {sd:.2f} < {BLIND_FLAT_STDEV})"
    if mean_luma < BLIND_DARK_LUMA and sd < BLIND_DARK_STDEV:
        return True, f"camera blind: near-black (mean luma {mean_luma:.1f}, stdev {sd:.2f})"
    return False, ""


def site_path(site: str, rel: str) -> Path:
    return Path(site) / rel


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def room_target(site: str, name: str):
    try:
        rooms = json.loads(site_path(site, "spatial/work/household/rooms.json").read_text())
    except (OSError, ValueError):
        return None
    for r in rooms:
        if r.get("name") == name:
            return r
    return None


def cmd_camera(args) -> int:
    camsig = load_camsig()
    room = room_target(args.site, args.room)
    if room is None or room.get("source") != args.source:
        row = {"room": args.room, "source": args.source, "status": "unavailable",
               "reason": "room or source not in rooms.json", "captured_at_epoch_s": round(time.time(), 1)}
    else:
        target = room.get("target") if args.source == "mac" else None
        row = {"room": args.room, **camsig.run(args.source, target, "/dev/video0", None)}
        if row.get("status") == "ok":
            blind, reason = classify_frame(row["grid"], row["mean_luma"])
            if blind:
                row = {k: v for k, v in row.items() if k not in ("grid",)}
                row.update(status="blind", reason=reason)
    append(site_path(args.site, "spatial/work/household/signatures.jsonl"), row)
    print(json.dumps({k: v for k, v in row.items() if k != "grid"}, sort_keys=True))
    return 0 if row.get("status") == "ok" else 1


def newest_note3(site: str):
    best = None
    for f in glob.glob(str(site_path(site, "spatial/work/household/note3_samples_run*.jsonl"))):
        try:
            lines = Path(f).read_text().splitlines()[-5:]
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            t = row.get("wall_ts")
            if isinstance(t, (int, float)) and (best is None or t > best["wall_ts"]):
                best = row
    return best


def note3_summary(site: str, now: float) -> dict:
    row = newest_note3(site)
    if row is None:
        return {"status": "unavailable", "reason": "no Note3 sample"}
    age = now - row["wall_ts"]
    sensors = row.get("sensors", {})
    light = sensors.get("light", {})
    accel = sensors.get("accelerometer", {})
    out = {"status": "ok" if age <= NOTE3_MAX_AGE_S else "stale", "age_s": round(age, 1),
           "light_raw": light.get("values") if light.get("status") == "ok" else None,
           "light_note": "raw channels; lux channel not verified",
           "accel": accel.get("values") if accel.get("status") == "ok" else None,
           "battery_pct": (row.get("battery") or {}).get("level_pct")}
    if out["status"] == "stale":
        out["reason"] = f"Note3 sample {age:.0f}s old (limit {NOTE3_MAX_AGE_S:.0f}s): sampler stopped"
    elif row.get("status") == "unknown":
        # an explicit adb-failure row is fresh by age but carries no sensor values
        out["status"] = "unknown"
        out["reason"] = f"Note3 sampler row unknown: {row.get('reason', 'no reason given')}"
    elif light.get("status") != "ok" or accel.get("status") != "ok":
        # a fresh row whose light or accelerometer is not active/ok is not a valid reading
        out["status"] = "unknown"
        out["reason"] = (f"Note3 light {light.get('reason', light.get('status', 'missing'))}; "
                         f"accel {accel.get('reason', accel.get('status', 'missing'))}")
    return out


def redmi_summary(site: str, now: float) -> dict:
    try:
        path = site_path(site, "discovery/latest.json")
        data = json.loads(path.read_text())
        file_age = now - path.stat().st_mtime
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": f"discovery snapshot: {type(exc).__name__}"}
    for row in data.get("observations", []):
        rid = str(row.get("id", ""))
        if rid.endswith(".stream") and "redmi10" in rid:
            v = row.get("values", {})
            age = file_age + float(row.get("row_age_s") or 0)
            out = {"status": "ok" if age <= REDMI_MAX_AGE_S else "stale", "age_s": round(age, 1),
                   "lux": v.get("lux", {}).get("value"),
                   "battery_raw": v.get("battery", {}).get("level")}
            if out["status"] == "stale":
                out["reason"] = f"Redmi10 receipt {age:.0f}s old (limit {REDMI_MAX_AGE_S:.0f}s)"
            return out
    return {"status": "unavailable", "reason": "no Redmi10 stream row"}


def cmd_phones(args) -> int:
    now = time.time()
    phones = {"note3": note3_summary(args.site, now), "redmi10": redmi_summary(args.site, now)}
    row = {"t": round(now, 1), **phones}
    append(site_path(args.site, "spatial/work/household/phones.jsonl"), row)
    print(json.dumps(row, sort_keys=True))
    return 0 if all(p["status"] == "ok" for p in phones.values()) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cam = sub.add_parser("camera")
    cam.add_argument("--room", required=True)
    cam.add_argument("--source", required=True, choices=("webcam", "mac"))
    cam.add_argument("--site", required=True)
    cam.set_defaults(func=cmd_camera)
    ph = sub.add_parser("phones")
    ph.add_argument("--site", required=True)
    ph.set_defaults(func=cmd_phones)
    args = parser.parse_args(argv)
    if not args.site.strip():
        parser.error("--site must name the site home; an empty value would write into the working directory")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
