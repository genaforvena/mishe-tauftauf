#!/usr/bin/env python3
"""One tick of the frame-label calibration loop (see frame_label.py for the rules).

  1. sweep frames older than 24 h;
  2. apply Ilya's Telegram replies (~/.mesh/tg-replies.jsonl, written by mesh-voice-rx);
  3. for each camera room whose newest signature changed noticeably versus the previous one,
     build a candidate, let frame_label.decide/reserve gate it (cap 3/8 h persisted BEFORE send),
     capture a fresh frame into a private dir, send it with `mesh-tg --file` to Ilya's own chat,
     and record the Telegram message id.

Usage: python tools/frame_label_tick.py [--dry]   (site from MISHE_SEED_HOME / MISHE_TAUFTAUF_HOME)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import frame_label as fl  # noqa: E402

CHANGE_MIN = 0.06
ROOMS = {"living room": (fl.LIVING, "webcam", None), "bedroom": (fl.BEDROOM, "mac", "ilya@192.168.8.214")}
REPLIES = Path.home() / ".mesh" / "tg-replies.jsonl"


def site_home() -> Path:
    if "--site" in sys.argv:
        return Path(sys.argv[sys.argv.index("--site") + 1])
    return Path(os.environ.get("MISHE_SEED_HOME") or os.environ["MISHE_TAUFTAUF_HOME"])


def newest_change(site: Path, room: str):
    rows = []
    try:
        for ln in (site / "spatial/work/household/signatures.jsonl").read_text().splitlines()[-400:]:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("status") == "ok" and r.get("room") == room and r.get("grid") and not r.get("dark_flat"):
                rows.append(r)
    except OSError:
        return None
    if len(rows) < 2:
        return None
    a, b = rows[-2], rows[-1]
    if len(a["grid"]) != len(b["grid"]) or b["captured_at_epoch_s"] - a["captured_at_epoch_s"] > 3 * 3600:
        return None
    ch = sum(abs(x - y) for x, y in zip(a["grid"], b["grid"])) / len(a["grid"]) / 255.0
    return b["captured_at_epoch_s"], ch


def apply_replies(site: Path) -> int:
    off_f = fl._dir(site) / "replies.offset"
    try:
        off = int(off_f.read_text())
    except (OSError, ValueError):
        off = 0
    try:
        data = REPLIES.read_bytes()
    except OSError:
        return 0
    n = 0
    for ln in data[off:].decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if fl.apply_reply(site, r["reply_to"], r.get("text", "")):
            n += 1
    fl._dir(site).mkdir(parents=True, exist_ok=True)
    off_f.write_text(str(len(data)))
    return n


def capture(room_name: str, out: Path) -> bool:
    import camera_signature as cs
    _, source, host = ROOMS[room_name]
    try:
        if source == "webcam":
            cs._capture_webcam(out, "/dev/video0", {})
        else:
            cs._capture_mac(out, host)
    except Exception:
        return False
    return out.is_file() and out.stat().st_size > 0


def send_photo(path: Path, caption: str) -> tuple[int | None, str]:
    p = subprocess.run(["mesh-tg", "--file", str(path), caption], capture_output=True, text=True, timeout=400)
    m = re.search(r"msg_id=(\d+)", p.stdout)
    return (int(m.group(1)) if p.returncode == 0 and m else None), (p.stderr or p.stdout)[-120:]


def tick(site: Path, dry: bool = False, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    out = {"swept": fl.sweep(site, now), "labels": apply_replies(site), "sent": None, "skipped": {}}
    fdir = fl._dir(site) / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    os.chmod(fdir, 0o700)
    for room_name, (room, _, _) in ROOMS.items():
        nc = newest_change(site, room_name)
        if not nc or nc[1] < CHANGE_MIN:
            out["skipped"][room] = "no change" if nc else "no signatures"
            continue
        det = f"{room}-{int(nc[0])}"
        if any(r.get("det") == det for r in fl.read_ledger(site)):
            out["skipped"][room] = "already handled"
            continue
        frame = fdir / f"{det}.jpg"
        cand = {"det": det, "room": room, "camera": ROOMS[room_name][1], "probs": {}, "frame": str(frame)}
        d = fl.decide(site, cand, now)
        if not d["send"]:
            out["skipped"][room] = d["reason"]
            continue
        if dry:
            out["skipped"][room] = "dry: would send"
            continue
        if not fl.reserve(site, cand, now):
            continue
        if not capture(room_name, frame):
            fl.record_send_failed(site, det, "capture failed", now)
            out["skipped"][room] = "capture failed"
            continue
        hhmm = time.strftime("%H:%M", time.gmtime(now))
        mid, why = send_photo(frame, f"Кто это? ({room}, {hhmm}Z) Ответь реплаем: ilya / rozalia / bruno / никого / other")
        if mid is None:
            fl.record_send_failed(site, det, why, now)
            fl._delete_frame(frame)
            out["skipped"][room] = "send failed"
            continue
        fl.record_sent(site, det, mid, now)
        out["sent"] = {"det": det, "msg": mid}
        break
    return out


if __name__ == "__main__":
    print(json.dumps(tick(site_home(), dry="--dry" in sys.argv)))
