#!/usr/bin/env python3
"""Calibration by Ilya's own labels: the deterministic core.

A camera detection may be sent as ONE photo to Ilya's own Telegram chat; he replies
(Telegram reply-to) with who it was. The reply is a ground-truth label. This module
holds only the rules that must not depend on anyone's intention:

  * a persisted ledger (site spatial/work/household/frame_labels/ledger.jsonl) with
    a slot reserved BEFORE any send, so a crash cannot exceed the cap (hard max 3
    sends per rolling 8 h, plus a minimum spacing);
  * consent from consent.json (living room may go to Ilya's chat only while
    Rozalia has not said no; bedroom needs BOTH Ilya and Rozalia explicit yes);
  * active learning: only ambiguous candidates qualify (identity margin small, a
    room with no label yet, or hypotheses disagreeing);
  * retention: frame deleted on label or after 24 h; the ledger keeps only label,
    time, room, camera and model scores, never pixels;
  * a deterministic reply parser (ru/en) that answers UNKNOWN otherwise;
  * scoring of the earlier guess against the label.

Sending and reply capture are hooks (message id -> detection id is recorded by
`record_sent`; `apply_reply` takes the replied-to message id and the text).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

CAP = 3
WINDOW_S = 8 * 3600
MIN_GAP_S = 45 * 60
RETENTION_S = 24 * 3600
THRESHOLD = 0.5
LIVING = "livingroom"
BEDROOM = "bedroom"
LABELS = ("ilya", "rozalia", "bruno", "nobody", "other")

_WORDS = {
    "ilya": ("ilya", "ilia", "илья", "илья", "я", "это я", "me", "i"),
    "rozalia": ("rozalia", "розалия", "розалия", "роз"),
    "bruno": ("bruno", "бруно", "dog", "пёс", "пес", "собака"),
    "nobody": ("nobody", "no one", "noone", "никто", "никого", "пусто", "никого нет"),
    "other": ("other", "someone else", "guest", "stranger", "другой", "другая", "гость", "чужой", "кто-то другой"),
}


def parse_reply(text: str) -> str:
    """ilya|rozalia|bruno|nobody|other, else 'unknown'. Exactly one label must match."""
    t = re.sub(r"[^\w\s-]", " ", (text or "").lower().replace("ё", "е")).strip()
    t = re.sub(r"\s+", " ", t)
    hits = set()
    for label, words in _WORDS.items():
        for w in words:
            w = w.replace("ё", "е")
            if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", t):
                hits.add(label)
    return hits.pop() if len(hits) == 1 else "unknown"


def _dir(site) -> Path:
    return Path(site) / "spatial/work/household/frame_labels"


def read_ledger(site) -> list[dict]:
    p = _dir(site) / "ledger.jsonl"
    rows = []
    try:
        for line in p.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return rows


def _append(site, row: dict) -> None:
    d = _dir(site)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "ledger.jsonl").open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def read_consent(site) -> dict:
    try:
        return json.loads((_dir(site) / "consent.json").read_text())
    except (OSError, ValueError):
        return {}


def consent_ok(consent: dict, room: str) -> tuple[bool, str]:
    roz, ilya = consent.get("rozalia_frames"), consent.get("ilya_frames", "yes")
    if ilya != "yes":
        return False, "ilya_frames not yes"
    if roz == "no":
        return False, "rozalia said no to frames"
    if room == LIVING:
        return True, "living room: Ilya's own chat, Rozalia has not said no"
    if room == BEDROOM:
        if consent.get("ilya_bedroom") == "yes" and consent.get("rozalia_bedroom") == "yes":
            return True, "bedroom: both explicit yes"
        return False, "bedroom needs explicit yes from both"
    return False, "room not allowed"


def reserved(rows: list[dict], now: float) -> list[dict]:
    """Slots in the window; a reserved-then-failed send is released by a 'send_failed' row."""
    failed = {r["det"] for r in rows if r.get("ev") == "send_failed"}
    return [r for r in rows if r.get("ev") == "reserve" and r["det"] not in failed
            and now - r["t"] < WINDOW_S]


def ambiguity(c: dict, rows: list[dict]) -> float:
    p = sorted((c.get("probs") or {}).values(), reverse=True)
    margin = (p[0] - p[1]) if len(p) >= 2 else 1.0
    s = 1.0 - margin
    labelled_rooms = {r["room"] for r in rows if r.get("ev") == "label" and r.get("label") != "unknown"}
    if c["room"] not in labelled_rooms:
        s += 0.5
    if c.get("hypotheses_disagree"):
        s += 0.5
    return round(s, 3)


def decide(site, cand: dict, now: float | None = None) -> dict:
    """Pure decision; no side effects. cand: det, room, camera, probs, hypotheses_disagree, frame."""
    now = time.time() if now is None else now
    rows = read_ledger(site)
    ok, why = consent_ok(read_consent(site), cand["room"])
    if not ok:
        return {"send": False, "reason": why}
    live = reserved(rows, now)
    if len(live) >= CAP:
        return {"send": False, "reason": f"cap {CAP}/8h reached"}
    if live and now - max(r["t"] for r in live) < MIN_GAP_S:
        return {"send": False, "reason": "spacing"}
    s = ambiguity(cand, rows)
    if s < THRESHOLD:
        return {"send": False, "reason": f"not ambiguous enough ({s})", "score": s}
    return {"send": True, "reason": why, "score": s}


def reserve(site, cand: dict, now: float | None = None) -> bool:
    """Persist the slot BEFORE sending. Re-checks the cap; returns False if refused."""
    now = time.time() if now is None else now
    d = decide(site, cand, now)
    if not d["send"]:
        return False
    _append(site, {"ev": "reserve", "t": now, "det": cand["det"], "room": cand["room"],
                   "camera": cand.get("camera"), "probs": cand.get("probs"), "score": d["score"],
                   "frame": cand.get("frame")})
    return True


def record_sent(site, det: str, message_id: int, now: float | None = None) -> None:
    _append(site, {"ev": "sent", "t": time.time() if now is None else now, "det": det, "msg": message_id})


def record_send_failed(site, det: str, why: str, now: float | None = None) -> None:
    _append(site, {"ev": "send_failed", "t": time.time() if now is None else now, "det": det, "why": why[:120]})


def _delete_frame(path) -> bool:
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False


def apply_reply(site, reply_to_msg: int, text: str, now: float | None = None) -> dict | None:
    """Reply to a sent photo -> label event (scored vs the earlier guess); frame deleted."""
    now = time.time() if now is None else now
    rows = read_ledger(site)
    sent = next((r for r in rows if r.get("ev") == "sent" and r["msg"] == reply_to_msg), None)
    if sent is None or any(r.get("ev") == "label" and r["det"] == sent["det"] for r in rows):
        return None
    res = next(r for r in rows if r.get("ev") == "reserve" and r["det"] == sent["det"])
    label = parse_reply(text)
    probs = res.get("probs") or {}
    guess = max(probs, key=probs.get) if probs else None
    ev = {"ev": "label", "t": now, "det": sent["det"], "room": res["room"], "camera": res.get("camera"),
          "label": label, "guess": guess, "probs": probs,
          "guess_correct": None if label == "unknown" or guess is None else guess == label}
    _append(site, ev)
    if res.get("frame"):
        _delete_frame(res["frame"])
    return ev


def sweep(site, now: float | None = None) -> int:
    """Delete frames older than 24 h that never got a label. Returns number deleted."""
    now = time.time() if now is None else now
    rows = read_ledger(site)
    done = {r["det"] for r in rows if r.get("ev") in ("label", "frame_deleted")}
    n = 0
    for r in rows:
        if r.get("ev") == "reserve" and r["det"] not in done and now - r["t"] >= RETENTION_S:
            if r.get("frame"):
                _delete_frame(r["frame"])
            _append(site, {"ev": "frame_deleted", "t": now, "det": r["det"], "why": "24h"})
            n += 1
    return n


def accuracy(site) -> dict:
    ls = [r for r in read_ledger(site) if r.get("ev") == "label" and r.get("guess_correct") is not None]
    return {"labels": len(ls), "correct": sum(1 for r in ls if r["guess_correct"])}
