"""Record one short audio window, keep only derived numbers and a hint, delete the audio.

Only these leave the window: a time, a loudness level (RMS, 0-32767) as an
activity proxy, the gate verdict, and - only when the transcript contains the
hint word "mishe" - the text from "mishe" onward. Ordinary conversation is
transcribed in memory and dropped. No audio, no voice, no speaker identity is
written anywhere.

Pipeline (the gates are not bypassed):
  arecord -> RMS -> mesh-whisper-filter --wav (silence gate)
          -> mesh-whisper-run -l ru, then -l en, --prompt VOCAB (niced, serialized; never auto)
          -> mesh-whisper-filter --text (hallucination gate)
          -> keep the segment from the first EXACT hint word onward (ru pass first)
  The WAV file is removed in a finally block whatever happens.

The vocabulary (prompt, hint words, stop words, stop phrases) is a site file,
SITE_HOME/spatial/work/household/hint_vocabulary.json, not code. A missing or
partial file gives UNKNOWN rows. Hint words match exactly. Stop phrases match
exactly; a bare stop word may be one letter off, only as the first word after
the hint word.

A stop (e.g. "mishe stop listening") writes a one-hour suspension file; while
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
# base multilingual: the model the Russian calibration round trip was measured with.
MODEL = Path.home() / ".mesh" / "whispercpp" / "models" / "ggml-base.bin"
WHISPER = "mesh-whisper-run"
FILTER = "mesh-whisper-filter"
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
VOCAB_NAME = "hint_vocabulary.json"
PROMPT_NAME = "whisper-vocab.txt"
VOCAB_KEYS = ("prompt", "hint_words", "stop_words", "stop_phrases")


def load_vocabulary(site: Path) -> dict:
    """Vocabulary lives in the site home, not in code. Missing or malformed -> ValueError (UNKNOWN)."""
    p = out_dir(site) / VOCAB_NAME
    data = json.loads(p.read_text())
    missing = [k for k in VOCAB_KEYS if k not in data]
    if missing:
        raise ValueError(f"{VOCAB_NAME} missing keys: {missing}")
    if not isinstance(data["prompt"], str) or not data["prompt"].strip():
        raise ValueError(f"{VOCAB_NAME} prompt is empty")
    # Operator-maintained recognizer prompt (one line) overrides the JSON prompt when present.
    prompt = data["prompt"]
    prompt_file = out_dir(site) / PROMPT_NAME
    if prompt_file.exists():
        line = prompt_file.read_text(encoding="utf-8").strip().splitlines()
        if not line or not line[0].strip():
            raise ValueError(f"{PROMPT_NAME} is empty")
        prompt = line[0].strip()
    return {
        "prompt": prompt,
        "hint_words": {tok(w) for w in data["hint_words"]},
        "stop_words": [tok(w) for w in data["stop_words"]],
        "stop_phrases": [tuple(tokens(p)) for p in data["stop_phrases"]],
    }


def tokens(text: str) -> list[str]:
    return [m.group(0).casefold() for m in TOKEN_RE.finditer(text)]


def tok(text: str) -> str:
    t = tokens(text)
    if len(t) != 1:
        raise ValueError(f"vocabulary entry must be one word: {text!r}")
    return t[0]


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def find_hint(text: str, vocab: dict):
    """Exact hint-word match (no fuzz). Returns (token index, char offset) or None."""
    matches = list(TOKEN_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group(0).casefold() in vocab["hint_words"]:
            return i, m.start(), [x.group(0).casefold() for x in matches]
    return None


def is_stop(after: list[str], vocab: dict) -> bool:
    """Stop phrases match exactly; a bare stop word may be one letter off, only as the first word after the hint."""
    for phrase in vocab["stop_phrases"]:
        if tuple(after[: len(phrase)]) == phrase:
            return True
    if after:
        return any(edit_distance(after[0], w) <= 1 for w in vocab["stop_words"])
    return False


_CLAUSE_SPLIT = re.compile(r"[,.!?;:]+")
CALL_MAX_WORDS = 8


def is_short_call(hint: str) -> bool:
    """`hint` starts with the wake word. The call is what follows it: one clause, few words.
    'Миша, добрый день' and 'Миша, включи свет' qualify; 'Миша, добрый день, мы смотрим
    футбол' is talk about the room and does not. When unsure, no call."""
    parts = hint.strip().split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    clauses = [c for c in _CLAUSE_SPLIT.split(rest) if c.strip()]
    return len(clauses) <= 1 and len(rest.split()) <= CALL_MAX_WORDS


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


def speaking_until(site: Path, now: float) -> float | None:
    """The household responder's speaking.lock holds the until-time of its line and follow-up."""
    p = out_dir(site) / "speaking.lock"
    if not p.exists():
        return None
    try:
        until = float(p.read_text().strip())
    except ValueError:
        return float("inf")  # unreadable lock: fail closed, do not record
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
    p = subprocess.run([FILTER, "--wav", str(path)], capture_output=True, text=True, errors="replace", timeout=30)
    return p.stdout.strip() or f"rc={p.returncode}"


LANGS = ("ru", "en")  # pinned, never "auto": base on real English collapsed to Chinese (98.9% error)


def transcribe(path: Path, prompt: str, lang: str, model: Path = MODEL) -> str:
    if lang not in LANGS:
        raise ValueError(f"language must be one of {LANGS}, got {lang!r}")
    p = subprocess.run(
        ["nice", "-n", "10", WHISPER, "-m", str(model), "-f", str(path), "-l", lang,
         "--prompt", prompt, "-np", "-nt"],
        capture_output=True, text=True, errors="replace", timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"whisper rc={p.returncode}: {p.stderr.strip()[:200]}")
    return " ".join(p.stdout.split())


def text_gate(text: str) -> str:
    p = subprocess.run([FILTER, "--text", text], capture_output=True, text=True, errors="replace", timeout=30)
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
    # Do not record while Mishe's own line is in flight or its follow-up window is open.
    speaking = speaking_until(site, now)
    if speaking is not None:
        row["status"] = "SPEAKING"
        row["speaking_until"] = round(speaking, 1)
        return row

    try:
        vocab = load_vocabulary(site)
    except (OSError, ValueError) as exc:
        row["status"] = "UNKNOWN"
        row["reason"] = f"vocabulary: {type(exc).__name__}: {str(exc)[:200]}"
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
        # Each language is pinned and run on the same window; the first pass that carries the
        # hint word (ru before en) is kept. Transcripts without the hint are dropped unseen.
        hit = None
        heard = False
        for lang in LANGS:
            text = transcribe(wav, vocab["prompt"], lang)
            if not text or not text_gate(text).startswith("PASS"):
                continue
            heard = True
            found = find_hint(text, vocab)
            # Collision guard: a call opens the utterance. A wake word inside ordinary talk
            # ("... и Миша сказал") is not a call, and its text is not kept.
            if found is not None and found[0] == 0:
                hit = (lang, text, found)
                break
        if hit is None:
            row["status"] = "NO_HINT" if heard else "NO_SPEECH"
            return row
        lang, text, (idx, offset, toks) = hit
        hint = text[offset:].strip()
        if is_stop(toks[idx + 1:], vocab):
            row["status"] = "HINT"
            row["lang"] = lang
            row["hint"] = hint
            row["text"] = hint
            row["suspended_until"] = round(write_suspend(site, 1.0, "mishe stop"), 1)
            row["action"] = "suspended audio 1 h"
            return row
        if not is_short_call(hint):
            # Retention: a greeting followed by talk is not a call. Its text is not kept.
            row["status"] = "NO_HINT"
            row["reason"] = "wake word not followed by one short clause: text not kept"
            return row
        row["status"] = "HINT"
        row["lang"] = lang
        row["hint"] = hint
        row["text"] = hint
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


def chain_call_watcher(site: Path) -> None:
    """After each window, start one call-watcher pass (household_call_watcher.py once) so a
    call is answered within one window. The pass is detached: the window never waits on a
    spoken line (speak can take minutes), and nothing it does can raise into the listener.
    Its output goes to call-watcher.log; the watcher's own lock stops overlapping passes."""
    try:
        log = out_dir(site) / "call-watcher.log"
        with log.open("ab") as fh:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve().parent / "household_call_watcher.py"),
                 "once", "--site", str(site)],
                stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True, close_fds=True)
    except (OSError, ValueError) as exc:
        print(f"call-watcher not started: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)


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
        row = window(args)
        emit(site, row)
        chain_call_watcher(site)
        # Duty contract: 0 = observed (any non-failed status), 3 = cannot tell (UNKNOWN ages the signal).
        return 3 if row.get("status") == "UNKNOWN" else 0
    elif args.cmd == "suspend":
        until = write_suspend(site, args.hours, "manual")
        print(json.dumps({"suspended_until": round(until, 1)}))
    else:
        print(json.dumps({"suspended_until": is_suspended(site, time.time())}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
