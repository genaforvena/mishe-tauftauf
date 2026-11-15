"""Shadow log of wake starts: what a wake gate would have seen, before any gate exists.

One ``start`` row per wake, written before the engine runs, and one ``end`` row once the
engine has settled, in ``decision-log/wake-gate.jsonl``. Nothing here decides anything:
``wake._execute`` calls these functions and ignores their failures, so a broken log can
never stop, delay or change a wake.

The dossier hash covers derived inputs only (owed reasons, signal revisions, mail sequence
numbers, held and consumed intentions). It holds no message bodies and no timestamps, so
two wakes with the same pending work hash the same.

``same_dossier_as_last`` is the System 0 verdict: this role's previous wake had the same
hash and yielded ``verified`` (nothing changed). Model answers are not written yet
(``decision`` is null); the P1 backtest decides whether a model is worth calling.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import state_files, yielding

LOG_NAME = "wake-gate.jsonl"


def log_path(home) -> Path:
    return Path(home) / "decision-log" / LOG_NAME


def dossier_inputs(snap, mail) -> dict:
    """The decision-relevant inputs of one wake, as plain sorted data (no bodies, no times)."""
    return {
        "reasons": sorted(str(r) for r in snap.reasons),
        "signal_revs": {str(k): int(v) for k, v in sorted(snap.signal_revs.items())},
        "mail": sorted(int(seq) for seq, _ in mail),
        "held": sorted(str(h) for h in snap.held),
        "consumed": sorted(str(c) for c in snap.consumed),
    }


def dossier_hash(inputs: dict) -> str:
    raw = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _rows(home) -> list:
    return state_files.read_json_lines(log_path(home))


def _append(home, row: dict) -> None:
    path = log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def same_dossier_verdict(rows: list, role: str, digest: str) -> bool:
    """True when this role's last start had the same hash and that wake yielded verified."""
    last_start = None
    for row in rows:
        if row.get("kind") == "start" and row.get("role") == role:
            last_start = row
    if last_start is None or last_start.get("dossier_hash") != digest:
        return False
    for row in reversed(rows):
        if row.get("kind") == "end" and row.get("key") == last_start.get("key"):
            return row.get("result") == "verified"
    return False


def record_start(home, role: str, key: str, *, snap, mail, now: float) -> None:
    """Append the start row. Never raises: this is shadow data only."""
    try:
        inputs = dossier_inputs(snap, mail)
        digest = dossier_hash(inputs)
        rows = _rows(home)
        _append(home, {
            "kind": "start", "role": role, "key": key, "ts": int(now),
            "trigger": (snap.reasons[0] if snap.reasons else "none"),
            "reasons": inputs["reasons"], "dossier_hash": digest,
            "same_dossier_as_last": same_dossier_verdict(rows, role, digest),
            "decision": None,
        })
    except Exception:  # noqa: BLE001 - shadow logging must not affect the wake
        return


def record_end(home, role: str, key: str, *, state: str, detail: str) -> None:
    """Append the end row with the yield result when the engine left one. Never raises."""
    try:
        result = None
        receipt = state_files.read_json(yielding.receipt_path(home, key))
        if receipt is not None:
            result = receipt.get("result")
        _append(home, {"kind": "end", "role": role, "key": key, "ts": int(time.time()),
                       "state": state, "result": result, "detail": (detail or "")[:200]})
    except Exception:  # noqa: BLE001 - shadow logging must not affect the wake
        return
