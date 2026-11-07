"""Calibrate hint-word recognition: synthesize known phrases, transcribe, compare.

Machinery only; the phrases, the voice and the models are passed in, none of the
household's words or data live here. Needs piper (text to speech), ffmpeg and a
whisper.cpp wrapper that accepts `-m MODEL -f WAV -l auto --prompt TEXT -nt -np -otxt -of OUT`
(for example mesh-whisper-run). Intermediate audio is written to a private temp
directory and deleted before returning.

    python tools/hint_calibration.py PIPER VOICE.onnx WHISPER_RUN MODEL.bin VOCAB_PROMPT PHRASE [PHRASE ...]

Prints one JSON object per phrase: said, heard, hint_ok (the first heard word is one of the first three
words of VOCAB_PROMPT, exactly; fuzz is only for stop words, see stop_ok), word_match
(share of said words found in heard, case and punctuation ignored).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def words(text: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [w for w in cleaned.split() if w]


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def word_match(said: str, heard: str) -> float:
    want = words(said)
    got = words(heard)
    if not want:
        return 0.0
    hit = sum(1 for w in want if any(edit_distance(w, g) <= (1 if len(w) > 3 else 0) for g in got))
    return round(hit / len(want), 2)


def hint_ok(heard: str, variants: list[str]) -> bool:
    """The hint word must match a listed variant exactly: one letter of fuzz would accept
    common names (a Russian "Маша" is one letter from "миша")."""
    got = words(heard)
    return bool(got) and got[0] in [v.lower() for v in variants]


def stop_ok(heard: str, stops: list[str]) -> bool:
    """Stop words are short and easy to mishear ("стоп" came back as "стоб"), and a missed
    stop is the costly error, so allow one letter of fuzz on words of four letters or more."""
    got = words(heard)
    return any(len(w) >= 4 and edit_distance(g, w.lower()) <= 1 for g in got for w in stops)


def run_phrase(piper: str, voice: str, whisper: str, model: str, prompt: str, phrase: str,
               variants: list[str]) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="hintcal-"))
    try:
        raw, wav, out = tmp / "p.wav", tmp / "s.wav", tmp / "t"
        subprocess.run([piper, "-m", voice, "-f", str(raw)], input=phrase.encode(),
                       capture_output=True, timeout=120, check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ar", "16000",
                        "-ac", "1", str(wav)], capture_output=True, timeout=60, check=True,
                       stdin=subprocess.DEVNULL)
        subprocess.run([whisper, "-m", model, "-f", str(wav), "-l", "auto", "--prompt", prompt,
                        "-nt", "-np", "-otxt", "-of", str(out)], capture_output=True, timeout=300,
                       stdin=subprocess.DEVNULL)
        heard = out.with_suffix(".txt").read_text().strip() if out.with_suffix(".txt").exists() else ""
        return {"said": phrase, "heard": heard, "hint_ok": hint_ok(heard, variants),
                "word_match": word_match(phrase, heard)}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"said": phrase, "status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) < 6:
        print(json.dumps({"status": "unavailable", "reason": "usage: see module docstring"}))
        return 2
    piper, voice, whisper, model, prompt = argv[:5]
    variants = words(prompt)[:1] + [w for w in words(prompt)[1:4]]
    failed = 0
    for phrase in argv[5:]:
        row = run_phrase(piper, voice, whisper, model, prompt, phrase, variants)
        failed += "status" in row
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
