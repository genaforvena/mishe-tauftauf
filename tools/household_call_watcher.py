#!/usr/bin/env python3
"""Household call watcher: answer a spoken call to Mishe, and only that.

Reads new HINT rows from SITE/spatial/work/household/audio.jsonl (written by
audio_hint.py), keeps only calls that are recent, not yet handled, and that ask
for something the silent responder can answer (time, where people are,
greeting, small talk). Everything else is recorded as handled and left alone:
ordinary conversation and media are never answered.

The responder's own gates still apply inside household_responder.speak():
quiet flags (quiet.flag, speech-quiet.json), the speaking lock, and the Note3
playback. This watcher adds no bypass.

Subcommands:
  once   [--site SITE] [--dry-run]     one pass over new rows
  loop   [--site SITE] [--interval 5]  repeat `once` (for a user service)

State: SITE/spatial/work/household/call-watcher.json holds the handled row
times (bounded). A row is retried only while it is younger than CALL_MAX_AGE_S
and only when the speaking lock was busy; otherwise it is marked handled.

Every decision is printed as one JSON line on stdout.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if "household_responder" in sys.modules:  # already loaded (tests, or a host that imported it)
    hr = sys.modules["household_responder"]
else:
    _spec = importlib.util.spec_from_file_location("household_responder", _HERE / "household_responder.py")
    hr = importlib.util.module_from_spec(_spec)
    sys.modules["household_responder"] = hr
    _spec.loader.exec_module(hr)
ht = hr.ht

WORK = hr.WORK
AUDIO = "audio.jsonl"
STATE = "call-watcher.json"
CALL_MAX_AGE_S = 120
HANDLED_KEEP = 300

# The hint word that opens the transcript. The listener already keeps text from the hint word on.
HINT_WORDS = ("mishe", "миша", "мише", "мишка", "misha", "meshie", "mishi")
_LEAD = re.compile(r"^\s*([^\s,.!?;:]+)[\s,.!?;:]*", re.UNICODE)


def strip_hint(text: str) -> tuple[str, bool]:
    """Remove the leading hint word (and any repeats). Returns (rest, had_hint)."""
    rest = text.strip()
    had = False
    while True:
        m = _LEAD.match(rest)
        if not m:
            break
        word = m.group(1).lower()
        if word in HINT_WORDS:
            had = True
            rest = rest[m.end():]
            continue
        break
    return rest.strip(), had


def intent(rest: str) -> str | None:
    """Only these are answered. Anything else is not addressed to Mishe in a way it can answer."""
    t = ht.tidy(rest)
    if not t:
        return None
    if ht.asks_time(t):
        return "time"
    if ht.asks_where(t):
        return "where"
    if ht.greets(t):
        return "greet"
    if ht.small_talk(t):
        return "small_talk"
    return None


def load_state(site: Path) -> dict:
    p = site / WORK / STATE
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return {"handled": [float(x) for x in doc.get("handled", [])]}
    except (OSError, ValueError, TypeError):
        return {"handled": []}


def save_state(site: Path, state: dict) -> None:
    p = site / WORK / STATE
    state["handled"] = sorted(state["handled"])[-HANDLED_KEEP:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state) + "\n", encoding="utf-8")
    tmp.replace(p)


def rows(site: Path) -> list[dict]:
    p = site / WORK / AUDIO
    if not p.exists():
        return []
    out = []
    for ln in ht.nonblank_lines(p.read_text(encoding="utf-8", errors="replace")):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def once(site: Path, dry_run: bool = False, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    state = load_state(site)
    handled = set(state["handled"])
    decisions: list[dict] = []
    for row in rows(site):
        if row.get("status") != "HINT":
            continue
        t = row.get("t")
        if not isinstance(t, (int, float)) or t in handled:
            continue
        if now - t > CALL_MAX_AGE_S:
            handled.add(t)
            continue
        raw = row.get("hint") or row.get("text") or ""
        rest, had = strip_hint(raw)
        lang = row.get("lang") if row.get("lang") in ("ru", "en") else "ru"
        kind = intent(rest) if had else None
        if kind is None:
            handled.add(t)
            decisions.append({"t": t, "action": "skip", "reason": "not an answerable call"})
            continue
        answer = hr.reply(rest, lang, site, now)
        if dry_run:
            decisions.append({"t": t, "action": "dry-run", "intent": kind, "reply": answer})
            continue
        rc = hr.speak(answer, lang, site)
        decisions.append({"t": t, "action": "spoke" if rc == 0 else "refused", "intent": kind,
                          "reply": answer, "rc": rc})
        if rc == 4:
            # a line was in flight: keep the call for a retry while it is still fresh
            continue
        handled.add(t)
    if not dry_run:
        state["handled"] = list(handled)
        save_state(site, state)
    for d in decisions:
        print(json.dumps(d, ensure_ascii=False))
    return decisions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("once", "loop"):
        sp = sub.add_parser(name)
        sp.add_argument("--site")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--interval", type=float, default=5.0)
    a = p.parse_args(argv)
    site = hr.site_root(a.site)
    if a.cmd == "once":
        once(site, dry_run=a.dry_run)
        return 0
    while True:
        once(site, dry_run=a.dry_run)
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
