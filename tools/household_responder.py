#!/usr/bin/env python3
"""Household responder: turn a hint transcript into a short spoken reply, quietly by default.

Silent core (v1). Input is a transcript from audio_hint's HINT row (text from
the hint word onward) plus its language. Output is reply text composed only
from facts Mishe already holds about the house: the current time, where the
last labelled people were (labels.tsv), and the latest activity reading. No
private habit data is used unless asked for explicitly.

Gates, in order, before any spoken line:
  1. quiet flag: SITE/spatial/work/household/quiet.flag present -> never speak.
  2. speaking lock: an in-flight line blocks a second one.
  3. the listener is suspended for the speaking window (speaking.lock holds
     the until-time; audio_hint must not record while it is present).

Subcommands:
  reply  --text "..." --lang ru|en [--site SITE]     print reply text only (silent)
  test   [--site SITE]                               run stored phrases, no audio
  speak  --text "..." --lang ru|en [--site SITE]     synth in the clone voice, play via Note3
                                                     (refused while quiet.flag exists)

Every exchange is appended to SITE/spatial/work/household/responder.jsonl and
pruned to 24 hours. The transcript stored there is the same text audio_hint
already keeps; no audio is written anywhere.

Usage:
  python tools/household_responder.py test
  python tools/household_responder.py reply --text "Миша, который час?" --lang ru
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

WORK = Path("spatial/work/household")
QUIET_FLAG = "quiet.flag"
SPEAK_LOCK = "speaking.lock"
EXCHANGES = "responder.jsonl"
FOLLOWUP_S = 20
KEEP_S = 24 * 3600
LABEL_MAX_AGE_S = 2 * 3600
ACTIVITY_MAX_AGE_S = 15 * 60
VOICE_SAY = Path.home() / ".local/bin/mesh-voice-say"
NOTE3_PUSH = Path.home() / ".local/bin/mesh-note3-say"  # speaks piper only; see speak()

PEOPLE = {
    "ilya": ["ilya", "илья", "илюша"],
    "rozalia": ["rozalia", "розалия"],
    "bruno": ["bruno", "бруно"],
}
ASK_TIME = re.compile(r"который час|сколько (времени|часов)|what time|time is it", re.I)
ASK_WHERE = re.compile(r"где (ты|ilya|илья|розалия|rozalia|бруно|bruno)|where (are|is)|кто (дома|здесь)|who('s| is) (home|here)", re.I)
GREET = re.compile(r"^\W*(привет|здравствуй|hi|hello|hey)\b", re.I)
SMALL_TALK = re.compile(r"как дела|how are you", re.I)


def site_root(arg: str | None) -> Path:
    raw = arg or os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not raw:
        raise SystemExit("no site: pass --site or set MISHE_SEED_HOME")
    return Path(raw)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def quiet(site: Path) -> bool:
    return (site / WORK / QUIET_FLAG).exists()


def latest_locations(site: Path, now: float) -> dict:
    """Last labelled row per person from labels.tsv. Missing or old -> UNKNOWN."""
    path = site / WORK / "labels.tsv"
    if not path.exists():
        return {}
    rows = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    if not rows:
        return {}
    stamp, _, rest = rows[-1].partition("\t")
    try:
        t = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return {}
    if now - t > LABEL_MAX_AGE_S:
        return {"_stale": True, "_at": stamp}
    out = {"_at": stamp}
    for field in rest.split("\t"):
        key, _, value = field.partition("=")
        if key.strip().lower().replace(" ", "_") in PEOPLE:
            out[key.strip().lower().replace(" ", "_")] = value
    return out


def latest_activity(site: Path, now: float) -> dict | None:
    path = site / WORK / "activity.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    t = doc.get("generated_utc")
    try:
        gen = dt.datetime.fromisoformat(t).timestamp()
    except (TypeError, ValueError):
        return None
    if now - gen > ACTIVITY_MAX_AGE_S:
        return None
    return doc


def _where_line(lang: str, locs: dict) -> str:
    if not locs or locs.get("_stale"):
        return "Не знаю точно, я давно никого не видела." if lang == "ru" else "I don't know for sure; I haven't seen anyone recently."
    parts = []
    for name in ("ilya", "rozalia", "bruno"):
        if name in locs:
            where = locs[name].replace("_", " ")
            if lang == "ru":
                who = {"ilya": "Илья", "rozalia": "Розалия", "bruno": "Бруно"}[name]
            else:
                who = name.capitalize()
            parts.append(f"{who}: {where}")
    if not parts:
        return "Не знаю, кто где." if lang == "ru" else "I don't know who is where."
    return "; ".join(parts) + "."


def reply(text: str, lang: str, site: Path, now: float | None = None) -> str:
    """Compose a short reply from house facts. Deterministic; no model call."""
    now = time.time() if now is None else now
    t = text.strip()
    if ASK_TIME.search(t):
        u = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%H:%M")
        return f"Сейчас {u} по UTC." if lang == "ru" else f"It is {u} UTC."
    if ASK_WHERE.search(t):
        return _where_line(lang, latest_locations(site, now))
    if GREET.search(t):
        return "Привет. Я здесь." if lang == "ru" else "Hi. I'm here."
    if SMALL_TALK.search(t):
        return "Всё тихо и в порядке." if lang == "ru" else "All quiet and fine."
    return "Повтори, пожалуйста, я не поняла." if lang == "ru" else "Sorry, please say that again."


def _prune(path: Path, now: float) -> None:
    if not path.exists():
        return
    keep = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            if now - float(json.loads(ln)["t"]) <= KEEP_S:
                keep.append(ln)
        except (ValueError, KeyError):
            continue
    path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def log_exchange(site: Path, row: dict, now: float) -> None:
    path = site / WORK / EXCHANGES
    _prune(path, now)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def speak(text: str, lang: str, site: Path) -> int:
    """Quiet check, lock, synth in the clone voice, play. Returns a process rc."""
    if quiet(site):
        print("refused: quiet.flag present", file=sys.stderr)
        return 3
    lock = site / WORK / SPEAK_LOCK
    now = time.time()
    if lock.exists():
        try:
            if float(lock.read_text().strip()) > now:
                print("refused: a line is already in flight", file=sys.stderr)
                return 4
        except ValueError:
            pass
    # suspend the listener for the speaking window plus the follow-up window
    lock.write_text(str(now + 60 + FOLLOWUP_S))
    try:
        wav = subprocess.run([str(VOICE_SAY), text], capture_output=True, text=True, timeout=200)
        if wav.returncode != 0:
            print(f"clone synth rc={wav.returncode}: {wav.stderr.strip()[:200]}", file=sys.stderr)
            return 5
        # Playback path is not yet wired for the clone wav; mesh-note3-say is piper-only.
        print(f"synth ok: {wav.stdout.strip()} (playback not wired in v1)", file=sys.stderr)
        return 6
    finally:
        # the follow-up window stays open for FOLLOWUP_S after the line
        lock.write_text(str(time.time() + FOLLOWUP_S))
        log_exchange(site, {"t": round(now, 1), "lang": lang, "reply": text, "kind": "spoken"}, now)


def test(site: Path) -> int:
    cases = [
        ("Миша, который час?", "ru", "UTC"),
        ("Mishe, what time is it?", "en", "UTC"),
        ("Миша, где Илья?", "ru", None),
        ("Привет, Миша", "ru", "Привет"),
        ("бла бла непонятно", "ru", "Повтори"),
    ]
    failed = 0
    for text, lang, want in cases:
        out = reply(text, lang, site)
        ok = want is None or want in out
        failed += 0 if ok else 1
        print(("PASS" if ok else "FAIL"), repr(text), "->", repr(out))
    print("quiet flag present:", quiet(site))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("reply", "speak", "test"):
        sp = sub.add_parser(name)
        sp.add_argument("--site")
        if name != "test":
            sp.add_argument("--text", required=True)
            sp.add_argument("--lang", choices=["ru", "en"], default="ru")
    a = p.parse_args(argv)
    site = site_root(a.site)
    if a.cmd == "test":
        return test(site)
    if a.cmd == "reply":
        out = reply(a.text, a.lang, site)
        print(out)
        return 0
    return speak(reply(a.text, a.lang, site), a.lang, site)


if __name__ == "__main__":
    raise SystemExit(main())
