"""Continuous hint listener: one capture stream, overlapping segments, no gap while a segment is analysed.

Why this exists: the one-shot `audio_hint.py window` records 8 s every 105 s, so it hears about 8 %
of the day. This process keeps one arecord stream open and cuts it into 8 s segments that advance
by 7 s (1 s overlap). Capture never waits for analysis: segments go into a bounded queue (3); when
the analyser is behind, the OLDEST queued segment is dropped and counted. Dropped and unanalysed
audio is not counted as covered.

Per segment (the gates are not bypassed):
  RMS gate (mesh-whisper-filter --wav) -> tiny model spots the hint word (ru, then en)
  -> base model confirms it on the same language -> text gate -> keep text from the hint word.
Only hint lines keep text. Every row records the language chosen (`lang`) and the models used.

Coverage (the reading that gates RED) = seconds analysed / wall seconds over the last hour,
suspended time excluded. Captured seconds are reported separately. Under 80 % reads RED.

Usage:
  python tools/audio_listener.py listen [--device plughw:0,0] [--room livingroom] [--site SITE_HOME]
  python tools/audio_listener.py status [--site SITE_HOME]

Status: SITE_HOME/spatial/work/household/audio-listener-status.json (rewritten every 30 s).
Rows: SITE_HOME/spatial/work/household/audio.jsonl (same file as the window producer).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import queue
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import audio_hint as ah

SEG_S = 8
HOP_S = 7
BPS = ah.RATE * 2  # 16-bit mono PCM bytes per second
TINY = Path.home() / ".mesh" / "whispercpp" / "models" / "ggml-tiny.bin"
BASE = ah.MODEL
WINDOW_S = 3600
MIN_WALL_S = 600  # coverage reads WARMING until this much wall time has been observed
COVERAGE_RED = 0.8
QUEUE_MAX = 3
STATUS_EVERY = 30.0
STATUS_NAME = "audio-listener-status.json"
STALE_S = 120.0
LAST_HINTS = 5


class Segment:
    __slots__ = ("t", "pcm", "new_s")

    def __init__(self, t: float, pcm: bytes, new_s: float):
        self.t = t
        self.pcm = pcm
        self.new_s = new_s


class Segmenter:
    """Cuts a 1-second chunked PCM stream into SEG_S segments that advance HOP_S seconds.

    The first segment carries SEG_S new seconds; each later one carries HOP_S new seconds and
    repeats the last SEG_S - HOP_S seconds of the previous one. Consecutive new spans are
    contiguous, so the stream has no gap.
    """

    def __init__(self, seg_s: int = SEG_S, hop_s: int = HOP_S):
        self.seg_bytes = seg_s * BPS
        self.hop_bytes = hop_s * BPS
        self.buf = bytearray()
        self.new = 0
        self.started = False

    def feed(self, chunk: bytes, now: float) -> list[Segment]:
        self.buf += chunk
        if len(self.buf) > self.seg_bytes:
            del self.buf[: len(self.buf) - self.seg_bytes]
        self.new += len(chunk)
        need = self.hop_bytes if self.started else self.seg_bytes
        if len(self.buf) < self.seg_bytes or self.new < need:
            return []
        seg = Segment(now, bytes(self.buf), min(self.new, self.seg_bytes) / BPS)
        self.new = 0
        self.started = True
        return [seg]


class Ledger:
    """Thread-safe rolling ledger of wall, captured, analysed seconds, hints, drops and restarts."""

    def __init__(self, window_s: float = WINDOW_S):
        self.window_s = window_s
        self.lock = threading.Lock()
        self.ev: collections.deque = collections.deque()
        self.drops = 0
        self.segments = 0
        self.restarts = 0
        self.started = time.time()

    def add(self, kind: str, value: float, extra=None) -> None:
        with self.lock:
            self.ev.append((time.time(), kind, value, extra))
            self._trim()

    def count(self, attr: str) -> None:
        with self.lock:
            setattr(self, attr, getattr(self, attr) + 1)

    def _trim(self) -> None:
        cut = time.time() - self.window_s
        while self.ev and self.ev[0][0] < cut:
            self.ev.popleft()

    def summary(self) -> dict:
        with self.lock:
            self._trim()
            sums = collections.Counter()
            hints = []
            for t, kind, value, extra in self.ev:
                if kind == "hint":
                    hints.append(extra)
                else:
                    sums[kind] += value
            return {"wall": sums["wall"], "captured": sums["captured"], "analysed": sums["analysed"],
                    "hints": hints, "drops": self.drops, "segments": self.segments,
                    "restarts": self.restarts}


def status_doc(ledger: Ledger, state: str) -> dict:
    s = ledger.summary()
    wall, ana, cap = s["wall"], s["analysed"], s["captured"]
    if wall >= MIN_WALL_S:
        cov = round(ana / wall, 3)
        cov_cap = round(cap / wall, 3)
        coverage_state = "RED" if cov < COVERAGE_RED else "GREEN"
    else:
        cov = cov_cap = None
        coverage_state = "WARMING"
    return {
        "t": round(time.time(), 1), "pid": os.getpid(), "state": state,
        "window_s": WINDOW_S, "wall_s": round(wall, 1), "analysed_s": round(ana, 1),
        "captured_s": round(cap, 1), "coverage": cov, "coverage_captured": cov_cap,
        "coverage_state": coverage_state, "segments": s["segments"], "drops": s["drops"],
        "restarts": s["restarts"], "hints_1h": len(s["hints"]),
        "last_hints": s["hints"][-LAST_HINTS:],
    }


def write_status(site: Path, doc: dict) -> None:
    path = ah.out_dir(site) / STATUS_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def offer(q: queue.Queue, item, ledger: Ledger) -> None:
    """Bounded hand-off: when full, drop the OLDEST queued segment and count it."""
    while True:
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            try:
                q.get_nowait()
                ledger.count("drops")
            except queue.Empty:
                pass


def classify(wav: Path, row: dict, vocab: dict, site: Path, spot: Path, confirm: Path | None) -> dict:
    """Gate, spot with `spot` (ru, then en), confirm the hint with `confirm` on the same language."""
    gate = ah.gate_wav(wav)
    row["gate"] = gate
    if not gate.startswith("PASS"):
        row["status"] = "SILENT_OR_DROPPED"
        row["lang"] = None
        return row
    heard_lang = None
    hit = None
    for lang in ah.LANGS:
        text = ah.transcribe(wav, vocab["prompt"], lang, spot)
        if not text or not ah.text_gate(text).startswith("PASS"):
            continue
        heard_lang = heard_lang or lang
        if ah.find_hint(text, vocab) is not None:
            hit = (lang, text)
            break
    row["spot_model"] = spot.stem
    if hit is None:
        row["lang"] = heard_lang
        row["status"] = "NO_HINT" if heard_lang else "NO_SPEECH"
        return row
    lang, text = hit
    row["lang"] = lang
    if confirm is not None:
        row["confirm_model"] = confirm.stem
        text2 = ah.transcribe(wav, vocab["prompt"], lang, confirm)
        found = ah.find_hint(text2, vocab) if text2 and ah.text_gate(text2).startswith("PASS") else None
        if found is None:
            row["status"] = "SPOT_UNCONFIRMED"
            return row
        text = text2
    idx, offset, toks = ah.find_hint(text, vocab)
    hint = text[offset:].strip()
    row.update(status="HINT", hint=hint, text=hint)
    if ah.is_stop(toks[idx + 1:], vocab):
        row["suspended_until"] = round(ah.write_suspend(site, 1.0, "mishe stop"), 1)
        row["action"] = "suspended audio 1 h"
    return row


def process(seg: Segment, ledger: Ledger, site: Path, vocab: dict, room: str, device: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="audio-listen-"))
    wav = tmp / "segment.wav"
    row = {"t": round(seg.t, 1), "room": room, "source": device, "seconds": SEG_S,
           "new_s": round(seg.new_s, 2), "mode": "listen"}
    try:
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ah.RATE)
            w.writeframes(seg.pcm)
        row["rms"], row["samples"] = ah.rms_of_wav(wav)
        classify(wav, row, vocab, site, TINY, BASE)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, wave.Error) as exc:
        row["status"] = "UNKNOWN"
        row["reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ah.emit(site, row)
    ledger.count("segments")
    if row["status"] != "UNKNOWN":
        ledger.add("analysed", seg.new_s)
    if row["status"] == "HINT":
        ledger.add("hint", 0.0, {"t": row["t"], "lang": row["lang"], "text": row["text"]})


def worker(q: queue.Queue, ledger: Ledger, site: Path, vocab: dict, room: str, device: str) -> None:
    while True:
        seg = q.get()
        if seg is None:
            return
        process(seg, ledger, site, vocab, room, device)


def start_arecord(device: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(ah.RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def read_chunk(proc: subprocess.Popen, n: int = BPS, timeout: float = 5.0) -> bytes | None:
    """Exactly n bytes, or None when the device stalls or closes."""
    buf = bytearray()
    fd = proc.stdout.fileno()
    while len(buf) < n:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        part = os.read(fd, n - len(buf))
        if not part:
            return None
        buf += part
    return bytes(buf)


def stop_proc(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def capture_loop(site: Path, ledger: Ledger, q: queue.Queue, device: str, stop: threading.Event) -> None:
    proc = None
    seg = None
    last = time.monotonic()
    last_status = 0.0
    try:
        while not stop.is_set():
            mono = time.monotonic()
            delta = mono - last
            last = mono
            until = ah.is_suspended(site, time.time())
            if until is not None:
                stop_proc(proc)
                proc = None
                state = "SUSPENDED"
                stop.wait(5)
                last = time.monotonic()
            else:
                state = "LISTENING"
                ledger.add("wall", delta)
                if proc is None:
                    proc = start_arecord(device)
                    seg = Segmenter()
                chunk = read_chunk(proc)
                if chunk is None:
                    stop_proc(proc)
                    proc = None
                    ledger.count("restarts")
                    state = "RESTARTING"
                    stop.wait(2)
                else:
                    ledger.add("captured", 1.0)
                    for item in seg.feed(chunk, time.time()):
                        offer(q, item, ledger)
            if time.monotonic() - last_status >= STATUS_EVERY:
                write_status(site, status_doc(ledger, state))
                last_status = time.monotonic()
    finally:
        stop_proc(proc)


def listen(args: argparse.Namespace) -> int:
    site = ah.site_home(args.site)
    try:
        vocab = ah.load_vocabulary(site)
    except (OSError, ValueError) as exc:
        write_status(site, {"t": round(time.time(), 1), "state": "UNKNOWN",
                            "reason": f"vocabulary: {type(exc).__name__}: {str(exc)[:200]}"})
        return 3
    ledger = Ledger()
    q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
    th = threading.Thread(target=worker, args=(q, ledger, site, vocab, args.room, args.device), daemon=True)
    th.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        capture_loop(site, ledger, q, args.device, stop)
    except KeyboardInterrupt:
        pass
    finally:
        offer(q, None, ledger)
        th.join(timeout=60)
        write_status(site, status_doc(ledger, "STOPPED"))
    return 0


def status(args: argparse.Namespace) -> int:
    site = ah.site_home(args.site)
    path = ah.out_dir(site) / STATUS_NAME
    if not path.exists():
        print(json.dumps({"reading": "UNKNOWN", "reason": "no listener status file"}))
        return 0
    doc = json.loads(path.read_text())
    age = round(time.time() - float(doc.get("t", 0)), 1)
    doc["age_s"] = age
    if age > STALE_S or doc.get("state") in ("UNKNOWN", "STOPPED"):
        doc["reading"] = "UNKNOWN"
    else:
        doc["reading"] = "RED" if doc.get("coverage_state") == "RED" else "OK"
    print(json.dumps(doc, sort_keys=True, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    li = sub.add_parser("listen")
    li.add_argument("--device", default="plughw:0,0")
    li.add_argument("--room", default="livingroom")
    li.add_argument("--site")
    st = sub.add_parser("status")
    st.add_argument("--site")
    args = ap.parse_args(argv)
    if args.cmd == "listen":
        return listen(args)
    return status(args)


if __name__ == "__main__":
    sys.exit(main())
