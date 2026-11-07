#!/usr/bin/env python3
"""Live window: what Mishe senses in the house.

Samples the two cameras as derived brightness grids only (no frames kept), reads the
phone streams from discovery/latest.json, appends derived samples to
spatial/work/household/signatures.jsonl, and redraws a text/ASCII picture.
Anything not measured or not validated is shown as UNKNOWN, never guessed as fact.
"""
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME")
            or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
LOG = SITE / "spatial/work/household/signatures.jsonl"
LATEST = SITE / "discovery/latest.json"
AUDIO = SITE / "spatial/work/household/audio.jsonl"
NOTE3_GLOB = "spatial/work/household/note3_samples_run*.jsonl"
XTTS_DIR = Path.home() / ".local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
XTTS_SIZE = 1867929118
TRANSCRIPT_WINDOW_S = 600
ROOMS_FILE = SITE / "spatial/work/household/rooms.json"
DRAW_EVERY = 10
SHADES = " .:-=+*#%@"
SPARK = "▁▂▃▄▅▆▇█"

spec = importlib.util.spec_from_file_location("camsig", Path(__file__).with_name("camera_signature.py"))
camsig = importlib.util.module_from_spec(spec)
sys.modules["camsig"] = camsig
spec.loader.exec_module(camsig)

def load_cameras():
    """Rooms come from the site's rooms.json (house data, not in the repo):
    [{"name": "living room", "source": "webcam", "every": 45},
     {"name": "bedroom", "source": "mac", "target": "USER@HOST", "every": 240}]"""
    try:
        rooms = json.loads(ROOMS_FILE.read_text())
    except (OSError, ValueError):
        rooms = []
    return {r["name"]: {"source": r["source"], "target": r.get("target"), "every": r.get("every", 60),
                        "last": 0.0, "hist": [], "dist": [], "reading": None} for r in rooms}


CAMERAS = load_cameras()


def sample(room, cam):
    if time.time() - cam["last"] < cam["every"]:
        return
    cam["last"] = time.time()
    res = camsig.run(cam["source"], cam["target"] if cam["source"] == "mac" else None,
                     "/dev/video0", None)
    cam["reading"] = res
    if res.get("status") != "ok":
        return
    if cam["hist"]:
        cam["dist"].append(round(camsig.grid_distance(cam["hist"][-1]["grid"], res["grid"]), 2))
    cam["hist"].append(res)
    cam["hist"] = cam["hist"][-60:]
    cam["dist"] = cam["dist"][-40:]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(json.dumps({"room": room, **res}, sort_keys=True) + "\n")


def spark(values, scale=20.0):
    if not values:
        return "(no samples yet)"
    return "".join(SPARK[min(7, int(v / scale * 7.999))] for v in values)


def ascii_grid(grid, w, h):
    rows = []
    for y in range(h):
        rows.append("".join(SHADES[min(9, grid[y * w + x] * 10 // 256)] * 2 for x in range(w)))
    return rows


def phones():
    out = {}
    try:
        data = json.loads(LATEST.read_text())
        age = time.time() - LATEST.stat().st_mtime
        for row in data.get("observations", []):
            rid = str(row.get("id", ""))
            if rid.endswith(".stream") and ("redmi10" in rid or "note3" in rid):
                out[rid.split(".")[2]] = (row, age)
    except (OSError, ValueError) as exc:
        out["error"] = (str(exc)[:80], 0)
    return out


def read_rows(path, limit=400):
    try:
        lines = path.read_text().splitlines()[-limit:]
    except OSError:
        return None
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def rms_spark(values):
    if not values:
        return ""
    top = max(max(values), 1.0)
    return "".join(SPARK[min(7, int(v / top * 7.999))] for v in values)


def sound_lines(now):
    rows = read_rows(AUDIO)
    if rows is None:
        return ["  SOUND: UNKNOWN (no audio log at all)"]
    if not rows:
        return ["  SOUND: UNKNOWN (audio log empty)"]
    out = []
    mics = {}
    for r in rows:
        mics.setdefault(r.get("room", "?") + " / " + str(r.get("source", "?")), []).append(r)
    for mic, mrows in mics.items():
        last = mrows[-1]
        age = int(now - last.get("t", 0))
        state = last.get("status", "?")
        fresh = "" if age < 300 else "  STALE: no sampling for %d min (UNKNOWN now)" % (age // 60)
        out.append("  %s  rms %.0f  %s  %ds ago%s" % (mic, last.get("rms", 0), state, age, fresh))
        out.append("    level " + rms_spark([r.get("rms", 0) for r in mrows[-40:]]))
    heard = [r for r in rows if r.get("text") and now - r.get("t", 0) < TRANSCRIPT_WINDOW_S]
    out.append("  heard in the last %d min (hint lines and calibration text only):" % (TRANSCRIPT_WINDOW_S // 60))
    if not heard:
        out.append("    (nothing kept: windows without the hint word are not transcribed or are discarded)")
    for r in heard[-5:]:
        out.append("    %s %s: %s" % (time.strftime("%H:%M:%S", time.gmtime(r["t"])), r.get("room", "?"),
                                      str(r["text"])[:90]))
    return out


def voice_lines():
    size = None
    try:
        size = (XTTS_DIR / "model.pth").stat().st_size
    except OSError:
        pass
    model = "model ok" if size == XTTS_SIZE else ("model MISSING" if size is None else "model WRONG SIZE")
    sock = XTTS_DIR.parent.parent.parent.parent / ".mesh/voice-clone/xtts.sock"
    try:
        live = sock.is_socket()
    except OSError:
        live = False
    unit = "?"
    try:
        import subprocess
        unit = subprocess.run(["systemctl", "--user", "is-active", "mesh-voice-clone.service"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        pass
    state = "OK" if (size == XTTS_SIZE and unit == "active" and live) else "RED"
    return ["  VOICE clone: %s  (%s, unit %s, socket %s)  synth probe not wired yet" %
            (state, model, unit, "present" if live else "absent")]


def note3_lines(now):
    newest = None
    for f in sorted(SITE.glob(NOTE3_GLOB))[-3:]:
        rows = read_rows(f, 5) or []
        if rows:
            newest = rows[-1]
    if newest is None:
        return ["  NOTE3 sensor feed: UNKNOWN (no samples)"]
    t = newest.get("t") or newest.get("epoch") or newest.get("time")
    age = int(now - t) if isinstance(t, (int, float)) else None
    return ["  NOTE3 sensor feed: last sample %s ago%s" % (
        "?" if age is None else "%ds" % age, "  STALE (UNKNOWN)" if age is None or age > 300 else "")]


def box(title, lines, width):
    top = "┌─ " + title + " " + "─" * max(0, width - len(title) - 5) + "┐"
    body = ["│ " + l.ljust(width - 4)[:width - 4] + " │" for l in lines]
    return [top] + body + ["└" + "─" * (width - 2) + "┘"]


def room_box(name, cam, width):
    lines = []
    res = cam["reading"]
    if res is None:
        lines.append("camera: waiting for first sample")
    elif res.get("status") != "ok":
        lines.append("camera: UNKNOWN - " + str(res.get("reason", "?"))[:width - 20])
    else:
        age = int(time.time() - res["captured_at_epoch_s"])
        d = cam["dist"][-1] if cam["dist"] else None
        state = "?" if d is None else ("● motion" if d > 6 else ("◐ small change" if d > 2 else "· quiet"))
        lines.append(f"camera {age}s old  luma {res['mean_luma']:.0f}  {state}")
        lines += ["  " + r for r in ascii_grid(res["grid"], res["grid_w"], res["grid_h"])]
        lines.append("change " + spark(cam["dist"][-width + 12:]))
    lines.append("presence: UNKNOWN (not validated)")
    return box(name.upper(), lines, width)


def side_by_side(a, b, gap=2):
    n = max(len(a), len(b))
    wa = len(a[0]) if a else 0
    wb = len(b[0]) if b else 0
    a = a + [" " * wa] * (n - len(a))
    b = b + [" " * wb] * (n - len(b))
    return [x + " " * gap + y for x, y in zip(a, b)]


def draw():
    cols = shutil.get_terminal_size((120, 40)).columns
    half = max(34, min(46, (cols - 4) // 2))
    out = [f"MISHE SENSES  {time.strftime('%H:%M:%S', time.gmtime())}Z   household brief boundaries: "
           "derived numbers only, frames deleted, UNKNOWN when unvalidated", ""]
    boxes = [room_box(name + " (" + cam["source"] + ")", cam, half) for name, cam in CAMERAS.items()]
    if not boxes:
        out.append("  no rooms configured: write " + str(ROOMS_FILE))
    while boxes:
        pair, boxes = boxes[:2], boxes[2:]
        out += side_by_side(pair[0], pair[1]) if len(pair) == 2 else pair[0]
    out.append("")
    ph = phones()
    out.append("PHONES")
    for name in ("redmi10", "note3"):
        item = ph.get(name)
        if not item or not isinstance(item[0], dict):
            out.append(f"  {name:8s} UNKNOWN (no stream row)")
            continue
        row, age = item
        v = row.get("values", {})
        lux = v.get("lux", {}).get("value")
        bat = v.get("battery", {})
        out.append(f"  {name:8s} {row.get('state', '?'):9s} row_age~{int((row.get('row_age_s') or 0) + age)}s  "
                   f"light={lux}lux  battery={bat.get('level')} charging={bat.get('charging')}  "
                   f"(from discover scan {int(age)}s old)")
    out.append("  position of phones: only as the household states it")
    out.append("")
    out.append("")
    out.append("SOUND, VOICE, SENSOR FEEDS")
    now = time.time()
    out += sound_lines(now)
    out += voice_lines()
    out += note3_lines(now)
    out.append("")
    out.append("WHERE IS ILYA: UNKNOWN. Not validated against labels yet.")
    best = None
    for room, cam in CAMERAS.items():
        if cam["dist"]:
            if best is None or cam["dist"][-1] > best[1]:
                best = (room, cam["dist"][-1])
    if best and best[1] > 6:
        out.append(f"  hint only: most recent motion in the {best[0]} camera (index {best[1]}), "
                   "which does not say who")
    out.append("PATTERNS: collecting (signatures.jsonl); none claimed yet.  BRUNO: no observation yet.")
    sys.stdout.write("\x1b[2J\x1b[H" + "\n".join(out) + "\n")
    sys.stdout.flush()


def main():
    while True:
        for room, cam in CAMERAS.items():
            try:
                sample(room, cam)
            except Exception as exc:  # a failing sensor must show as UNKNOWN, not stop the view
                cam["reading"] = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"[:120]}
        draw()
        time.sleep(DRAW_EVERY)


if __name__ == "__main__":
    main()
