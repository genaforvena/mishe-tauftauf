#!/usr/bin/env python3
"""Detector for the cloned-voice path: the `voice.clone.ready` reading.

Exit codes (the duty contract): 0 GREEN, 2 RED, 3 cannot tell. A RED result is never
written as calm; a missing or crashed observation ages to UNKNOWN in the runtime.

Checks, in order (each failure is RED with its own reason):
  1. model file present with the expected size (model.pth, 1867929118 bytes)
  2. mesh-voice-clone.service active
  3. the daemon socket accepts a connection
  4. a no-sound synthesis probe: mesh-voice-say writes a temp wav, length > 0, deleted

Bounded self-healing, each step recorded in the heal state file with its result:
  step 1  start the unit: at most 2 starts per 30 min
  step 2  model wrong or missing: resumable download to model.pth.part with curl, one
          download process at a time, at most 3 starts per 2 h; rename only at the exact size
  step 3  after the ladder is spent, one message through mesh-tg per 30 min; the signal stays RED

Usage: voice-clone-ready.py --home SITE_HOME [--state PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
MODEL_DIR = HOME / ".local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
MODEL = MODEL_DIR / "model.pth"
PART = MODEL_DIR / "model.pth.part"
MODEL_SIZE = 1867929118
MODEL_URL = "https://huggingface.co/coqui/XTTS-v2/resolve/main/model.pth"
UNIT = "mesh-voice-clone.service"
SOCK = Path(os.environ.get("MESH_CLONE_SOCK", HOME / ".mesh/voice-clone/xtts.sock"))
VOICE_SAY = HOME / ".local/bin/mesh-voice-say"
MESH_TG = HOME / ".local/bin/mesh-tg"

RESTART_WINDOW = 30 * 60
RESTART_MAX = 2
DOWNLOAD_WINDOW = 2 * 3600
DOWNLOAD_MAX = 3
ALERT_WINDOW = 30 * 60
PROBE_TEXT = "проверка голоса"


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"restarts": [], "downloads": [], "dl_pid": None, "alert_at": 0}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=1))
    os.replace(tmp, path)


def recent(stamps: list, window: float, now: float) -> list:
    return [t for t in stamps if now - t < window]


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def unit_active() -> str:
    p = subprocess.run(["systemctl", "--user", "is-active", UNIT],
                       capture_output=True, text=True, timeout=20)
    return p.stdout.strip() or f"rc={p.returncode}"


def socket_live() -> bool:
    if not SOCK.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect(str(SOCK))
        return True
    except OSError:
        return False
    finally:
        s.close()


def model_ok() -> bool:
    try:
        return MODEL.stat().st_size == MODEL_SIZE
    except OSError:
        return False


def heal_model(state: dict, now: float, steps: list) -> None:
    """Step 2: finish or start a resumable download of the model file."""
    if PART.exists() and PART.stat().st_size == MODEL_SIZE and not pid_alive(state.get("dl_pid")):
        PART.rename(MODEL)
        steps.append("model: renamed verified .part into place")
        return
    if pid_alive(state.get("dl_pid")):
        steps.append("model: download running (pid %s)" % state["dl_pid"])
        return
    state["downloads"] = recent(state["downloads"], DOWNLOAD_WINDOW, now)
    if len(state["downloads"]) >= DOWNLOAD_MAX:
        steps.append("model: download budget spent (3 per 2 h)")
        return
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    log = open(MODEL_DIR / "download.log", "ab")
    proc = subprocess.Popen(["curl", "-fL", "--retry", "3", "-C", "-", "-o", str(PART), MODEL_URL],
                            stdout=log, stderr=log, start_new_session=True)
    state["dl_pid"] = proc.pid
    state["downloads"].append(now)
    steps.append("model: started resumable download to model.pth.part (pid %d)" % proc.pid)


def heal_unit(state: dict, now: float, steps: list, verb: str) -> None:
    """Step 1: start (or restart) the unit, at most twice per 30 min."""
    state["restarts"] = recent(state["restarts"], RESTART_WINDOW, now)
    if len(state["restarts"]) >= RESTART_MAX:
        steps.append("unit: start budget spent (2 per 30 min)")
        return
    subprocess.run(["systemctl", "--user", verb, UNIT], capture_output=True, text=True, timeout=60)
    state["restarts"].append(now)
    steps.append(f"unit: {verb} issued")


def alert(state: dict, now: float, text: str, steps: list) -> None:
    """Step 3: one message to the humans per 30 min, only after the ladder failed."""
    if now - state.get("alert_at", 0) < ALERT_WINDOW or not MESH_TG.exists():
        return
    p = subprocess.run([str(MESH_TG), text], capture_output=True, text=True, timeout=60)
    state["alert_at"] = now
    steps.append(f"alert: mesh-tg rc={p.returncode}")


def probe() -> tuple[bool, str]:
    """Cheap no-sound synthesis: a temp wav, length > 0, deleted."""
    with tempfile.TemporaryDirectory(prefix="voice-probe-") as td:
        out = Path(td) / "probe.wav"
        try:
            p = subprocess.run([str(VOICE_SAY), PROBE_TEXT, str(out)],
                               capture_output=True, text=True, timeout=120)
        except subprocess.SubprocessError as exc:
            return False, f"probe: {type(exc).__name__}"
        if p.returncode != 0:
            return False, f"probe: mesh-voice-say rc={p.returncode} {p.stderr.strip()[:160]}"
        size = out.stat().st_size if out.exists() else 0
        if size <= 44:
            return False, f"probe: empty wav ({size} bytes)"
        return True, f"probe: {size} bytes"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", required=True)
    ap.add_argument("--state", default=None)
    args = ap.parse_args(argv)
    now = time.time()
    state_path = Path(args.state) if args.state else Path(args.home) / "spatial/work/voice-clone/heal.json"
    state = load_state(state_path)
    steps: list[str] = []
    reasons: list[str] = []

    if not model_ok():
        reasons.append(f"model file missing or not {MODEL_SIZE} bytes")
        heal_model(state, now, steps)

    unit = unit_active()
    sock = False
    if unit != "active":
        reasons.append(f"unit {UNIT} is {unit}")
        heal_unit(state, now, steps, "start")
    else:
        sock = socket_live()
        if not sock:
            reasons.append("daemon socket not accepting connections")
            heal_unit(state, now, steps, "restart")

    if unit == "active" and sock and model_ok():
        ok_probe, detail = probe()
        if not ok_probe:
            reasons.append(detail)

    if reasons and len(recent(state["restarts"], RESTART_WINDOW, now)) >= RESTART_MAX:
        alert(state, now, "Mishe voice: clone path RED after restart budget: " + "; ".join(reasons))

    state["last"] = {"t": round(now, 1), "reasons": reasons, "steps": steps}
    save_state(state_path, state)

    result = {"signal": "voice.clone.ready", "t": round(now, 1), "ok": not reasons,
              "reasons": reasons, "steps": steps, "model_bytes": MODEL.stat().st_size if MODEL.exists() else 0}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not reasons else 2


if __name__ == "__main__":
    sys.exit(main())
