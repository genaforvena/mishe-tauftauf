"""Record one short audio window, keep only derived numbers and a hint, delete the audio.

Only these leave the window: a time, a loudness level (RMS, 0-32767) as an
activity proxy, the gate verdict, and - only when the transcript contains the
hint word "mishe" - the text from "mishe" onward. Ordinary conversation is
transcribed in memory and dropped. No audio, no voice, no speaker identity is
written anywhere.

Pipeline (the gates are not bypassed):
  arecord -> RMS -> mesh-whisper-filter --wav (silence gate)
          -> mesh-whisper-run (niced, serialized whisper.cpp entry point)
          -> mesh-whisper-filter --text (hallucination gate)
          -> keep "mishe ..." segment only
  The WAV file is removed in a finally block whatever happens.

"mishe stop listening" or "mishe stop" writes a one-hour suspension file; while
it is present, `window` records nothing and writes a SUSPENDED row.

Usage:
  python tools/audio_hint.py window [--seconds 8] [--device plughw:0,0]
                                    [--room livingroom] [--site SITE_HOME]
  python tools/audio_hint.py suspend [--hours 1] [--site SITE_HOME]
  python tools/audio_hint.py status [--site SITE_HOME]

Each run appends one JSON object per line to
SITE_HOME/spatial/work/household/audio.jsonl and prints it to stdout.
"""
from __future__ import annotations

import argparse
import array
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

RATE = 16000
MODEL = Path.home() / ".mesh" / "whispercpp" / "models" / "ggml-tiny.bin"
WHISPER = "mesh-whisper-run"
FILTER = "mesh-whisper-filter"
HINT_RE = re.compile(r"\bmishe\b[\s,:.!?-]*(.*)", re.IGNORECASE | re.DOTALL)
STOP_RE = re.compile(r"\bmishe\b[\s,:.!?-]*stop\b", re.IGNORECASE)


def site_home(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not raw:
        raise SystemExit("no site home: pass --site or set MISHE_SEED_HOME")
    return Path(raw)


def out_dir(site: Path) -> Path:
    d = site / "spatial" / "work" / "household"
    d.mkdir(parents=True, exist_ok=True)
    return d


def suspend_path(site: Path) -> Path:
    return out_dir(site) / "audio-suspend.json"


def is_suspended(site: Path, now: float) -> float | None:
    p = suspend_path(site)
    if not p.exists():
        return None
    try:
        until = float(json.loads(p.read_text())["until"])
    except (ValueError, KeyError, TypeError):
        # Unreadable suspension: fail closed, do not record.
        return float("inf")
    return until if until > now else None


def write_suspend(site: Path, hours: float, reason: str) -> float:
    until = time.time() + hours * 3600
    suspend_path(site).write_text(json.dumps({"until": until, "reason": reason}) + "\n")
    return until


def rms_of_wav(path: Path) -> tuple[float, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        frames = w.readframes(w.getnframes())
    samples = array.array("h")
    samples.frombytes(frames)
    if not samples:
        return 0.0, 0
    total = sum(s * s for s in samples)
    return round((total / len(samples)) ** 0.5, 1), len(samples)


def record(path: Path, seconds: int, device: str) -> None:
    subprocess.run(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(RATE), "-c", "1",
         "-d", str(seconds), str(path)],
        check=True, timeout=seconds + 20, capture_output=True)


def gate_wav(path: Path) -> str:
    p = subprocess.run([FILTER, "--wav", str(path)], capture_output=True, text=True, timeout=30)
    return p.stdout.strip() or f"rc={p.returncode}"


def transcribe(path: Path) -> str:
    p = subprocess.run(
        ["nice", "-n", "10", WHISPER, "-m", str(MODEL), "-f", str(path), "-np", "-nt"],
        capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"whisper rc={p.returncode}: {p.stderr.strip()[:200]}")
    return " ".join(p.stdout.split())


def text_gate(text: str) -> str:
    p = subprocess.run([FILTER, "--text", text], capture_output=True, text=True, timeout=30)
    return p.stdout.strip() or f"rc={p.returncode}"


def window(args: argparse.Namespace) -> dict:
    site = site_home(args.site)
    now = time.time()
    row: dict = {"t": round(now, 1), "room": args.room, "source": args.device}
    until = is_suspended(site, now)
    if until is not None:
        row["status"] = "SUSPENDED"
        row["suspended_until"] = None if until == float("inf") else round(until, 1)
        return row

    tmp = Path(tempfile.mkdtemp(prefix="audio-hint-"))
    wav = tmp / "window.wav"
    try:
        record(wav, args.seconds, args.device)
        rms, nsamples = rms_of_wav(wav)
        row["seconds"] = args.seconds
        row["rms"] = rms
        row["samples"] = nsamples
        gate = gate_wav(wav)
        row["gate"] = gate
        if not gate.startswith("PASS"):
            row["status"] = "SILENT_OR_DROPPED"
            return row
        text = transcribe(wav)
        if not text or not text_gate(text).startswith("PASS"):
            row["status"] = "NO_SPEECH"
            return row
        m = HINT_RE.search(text)
        if not m:
            # Ordinary conversation: transcript discarded, only the level is kept.
            row["status"] = "NO_HINT"
            return row
        hint = text[m.start():].strip()
        row["status"] = "HINT"
        row["hint"] = hint
        if STOP_RE.search(hint):
            row["suspended_until"] = round(write_suspend(site, 1.0, "mishe stop"), 1)
            row["action"] = "suspended audio 1 h"
        return row
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError, wave.Error) as exc:
        row["status"] = "UNKNOWN"
        row["reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return row
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def emit(site: Path, row: dict) -> None:
    line = json.dumps(row, sort_keys=True)
    with (out_dir(site) / "audio.jsonl").open("a") as fh:
        fh.write(line + "\n")
    print(line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("window")
    w.add_argument("--seconds", type=int, default=8)
    w.add_argument("--device", default="plughw:0,0")
    w.add_argument("--room", default="livingroom")
    w.add_argument("--site")
    s = sub.add_parser("suspend")
    s.add_argument("--hours", type=float, default=1.0)
    s.add_argument("--site")
    st = sub.add_parser("status")
    st.add_argument("--site")
    args = ap.parse_args(argv)
    site = site_home(args.site)
    if args.cmd == "window":
        if not 6 <= args.seconds <= 10:
            print("seconds must be 6-10", file=sys.stderr)
            return 2
        emit(site, window(args))
    elif args.cmd == "suspend":
        until = write_suspend(site, args.hours, "manual")
        print(json.dumps({"suspended_until": round(until, 1)}))
    else:
        print(json.dumps({"suspended_until": is_suspended(site, time.time())}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
