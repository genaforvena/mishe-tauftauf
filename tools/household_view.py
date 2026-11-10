#!/usr/bin/env python3
"""Live window: what Mishe senses in the house.

Samples the two cameras as derived brightness grids only (no frames kept), reads the
phone streams from discovery/latest.json, appends derived samples to
spatial/work/household/signatures.jsonl, and redraws a text/ASCII picture.
Anything not measured or not validated is shown as UNKNOWN, never guessed as fact.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
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
REPO = Path(__file__).resolve().parent.parent
OWN_FILES = (Path(__file__).resolve(),
             Path(__file__).resolve().parent / "camera_signature.py",
             Path(__file__).resolve().parent / "household_activity.py")
OWN_GIT_PATHS = ("tools/household_view.py", "tools/camera_signature.py", "tools/household_activity.py")
WINDOW_STATE = SITE / "spatial" / "work" / "household" / "window.json"
STARTED = time.time()
VERSION = "UNKNOWN"
VERSION_AT = time.time()
STAMP = None
DRAW_EVERY = 10
SHADES = " .:-=+*#%@"
SPARK = "▁▂▃▄▅▆▇█"

spec = importlib.util.spec_from_file_location("camsig", Path(__file__).with_name("camera_signature.py"))
camsig = importlib.util.module_from_spec(spec)
sys.modules["camsig"] = camsig
spec.loader.exec_module(camsig)

hact_spec = importlib.util.spec_from_file_location("household_activity", Path(__file__).with_name("household_activity.py"))
hact = importlib.util.module_from_spec(hact_spec)
sys.modules["household_activity"] = hact
hact_spec.loader.exec_module(hact)
DAYLIGHT_FILE = SITE / "spatial/work/household/daylight.json"
ACTIVITY_FILE = SITE / "spatial/work/household/activity.json"
FEED_MAX_AGE_S = 1500  # the daylight and activity duties' max_age

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


def own_stamp():
    try:
        return tuple(p.stat().st_mtime_ns for p in OWN_FILES)
    except OSError:
        return None


def code_fingerprint(paths=None):
    h = hashlib.sha256()
    for p in (paths or OWN_FILES):
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def code_version():
    try:
        r = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h %ct", "--", *OWN_GIT_PATHS],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0 or not r.stdout.strip():
            return "UNKNOWN", None
        sha, ct = r.stdout.split()
        dirty = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", "HEAD", "--", *OWN_GIT_PATHS],
                               capture_output=True, timeout=5)
        if dirty.returncode != 0:
            sha = sha + "+dirty"
        return sha, int(ct)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "UNKNOWN", None


def report_window_state():
    try:
        WINDOW_STATE.parent.mkdir(parents=True, exist_ok=True)
        WINDOW_STATE.write_text(json.dumps({"started": STARTED, "stamp": list(own_stamp() or ()),
                                           "fingerprint": code_fingerprint(), "version": VERSION[0] if isinstance(VERSION, tuple) else VERSION}))
    except OSError:
        pass


def maybe_reexec(stamp):
    cur = own_stamp()
    if cur is not None and stamp is not None and cur != stamp:
        try:
            os.execv(sys.executable, [sys.executable, str(OWN_FILES[0]), *sys.argv[1:]])
        except OSError:
            pass
    return cur if cur is not None else stamp

STAMP = own_stamp()
VERSION = code_version()
VERSION_AT = time.time()
report_window_state()


def refresh(room, cam):
    """Read only: the household_camera duties append the signatures; this window never captures."""
    mine = [r for r in (read_rows(LOG, limit=600) or []) if r.get("room") == room]
    if not mine:
        cam["reading"] = {"status": "unavailable", "reason": "no duty row yet"}
        return
    cam["reading"] = mine[-1]
    ok = [r for r in mine if r.get("status") == "ok"]
    cam["hist"] = ok[-60:]
    recent = ok[-41:]  # consecutive pairs: each frame against the one before it
    cam["dist"] = [round(camsig.grid_distance(a["grid"], b["grid"]), 2)
                   for a, b in zip(recent, recent[1:])]


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


def read_json_file(path):
    try:
        return json.loads(path.read_text()), path.stat().st_mtime
    except (OSError, ValueError):
        return None, None


def stale_note(mtime, now):
    if mtime is None:
        return ""
    age = now - mtime
    return "" if age <= FEED_MAX_AGE_S else "  STALE: written %d min ago (UNKNOWN now)" % (age // 60)


def hour_spark(values, top):
    """24 UTC hours; a missing hour is a dot, never a guessed level."""
    return "".join("·" if v is None else SPARK[min(7, int(v / top * 7.999))] for v in values)


def daylight_lines(now):
    data, mtime = read_json_file(DAYLIGHT_FILE)
    if not isinstance(data, dict) or not data.get("rooms"):
        return ["  DAYLIGHT: UNKNOWN (no daylight curve yet)"]
    out = []
    for room, info in data["rooms"].items():
        hours = info.get("hours", {})
        vals = [hours.get("%02d" % h, {}).get("median_luma") for h in range(24)]
        known = [h for h, v in enumerate(vals) if v is not None]
        latest = ("latest %02d:00Z luma %.1f" % (known[-1], vals[known[-1]])) if known else "no usable hour yet"
        out.append("  DAYLIGHT %-12s %s  %s" % (room, hour_spark(vals, 255.0), latest))
    out.append("           hours 00Z..23Z, lamp steps removed, scale 0-255 luma" + stale_note(mtime, now))
    return out


def rhythm_lines(now):
    data, mtime = read_json_file(ACTIVITY_FILE)
    if not isinstance(data, dict):
        return ["  RHYTHM: UNKNOWN (no activity reading yet)"]
    out = ["  " + hact.rhythm_line(data.get("summary", {})) + stale_note(mtime, now)]
    sound = data.get("sound_by_hour", {}).get("livingroom", {})
    vals = [sound.get("%02d" % h, {}).get("median_rms") for h in range(24)]
    top = max([v for v in vals if v is not None] or [1.0])
    out.append("  SOUND by UTC hour, living room (median rms): " + hour_spark(vals, max(top, 1.0)))
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
    t = newest.get("wall_ts") or newest.get("t")
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
    out.append("DAYLIGHT, RHYTHM (camera source; one night of data; not validated)")
    out += daylight_lines(now)
    out += rhythm_lines(now)
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
    global STAMP, VERSION, VERSION_AT
    if "--once" in sys.argv:
        print(draw())
        return
    report_window_state()
    while True:
        STAMP = maybe_reexec(STAMP)
        if time.time() - VERSION_AT > 60:
            VERSION, VERSION_AT = code_version(), time.time()
        for room, cam in CAMERAS.items():
            try:
                refresh(room, cam)
            except Exception as exc:
                cam["reading"] = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"[:120]}
        draw()
        time.sleep(DRAW_EVERY)


if __name__ == "__main__":
    main()
