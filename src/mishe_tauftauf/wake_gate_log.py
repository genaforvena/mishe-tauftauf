"""Shadow log of wake starts: what a wake gate would have seen, before any gate exists.

One ``start`` row per wake, written before the engine runs, and one ``end`` row once the
engine has settled, in ``decision-log/wake-gate.jsonl``. Nothing here decides anything:
``wake._execute`` calls these functions and ignores their failures, so a broken log can
never stop, delay or change a wake.

The dossier hash covers derived inputs only (owed reasons, signal revisions, mail sequence
numbers, held and consumed intentions). It holds no message bodies and no timestamps, so
two wakes with the same pending work hash the same.

``same_dossier_as_last`` is the System 0 verdict: this role's previous wake had the same
hash and yielded ``verified`` (nothing changed).

``decision`` is the System One shadow answer for the same wake: one choice (owed work or repeat)
from the decision provider, over derived facts only (role label, trigger, owed reasons, mail count).
The model never sees the System 0 verdict, so the two can be compared later. A missing or failing
provider leaves ``choice`` null with the reason; the wake is never delayed past the provider timeout
and never stopped.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import decision_provider as dp
from . import state_files, yielding

LOG_NAME = "wake-gate.jsonl"
QUESTION = "wake-worth-running"
INSTRUCTIONS = ("Decide whether one wake of a role adds new work. Pick exactly one label from the criteria. "
                "The state gives derived facts only: the role label, the trigger, the owed reasons and a count of mail items.")
CRITERIA = {
    "owed-work": "a new signal, message, due intention or change makes this wake necessary",
    "repeat": "nothing new is owed, so this wake would add nothing",
}
THRESHOLD = 0.7


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


def _torn_tail(path: Path) -> bool:
    """True when the file is non-empty and its last byte is not a newline (a torn write)."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                return False
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"
    except FileNotFoundError:
        return False


def _append(home, row: dict) -> None:
    path = log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A torn earlier write leaves a fragment with no newline; close it first so the new
    # row starts on its own line instead of being concatenated onto the fragment.
    prefix = "\n" if _torn_tail(path) else ""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix + json.dumps(row, sort_keys=True) + "\n")


def same_dossier_verdict(rows: list, role: str, digest: str) -> bool:
    """True when this role's last start had the same hash and that wake yielded verified.

    A verified receipt alone is not enough: an engine can write its receipt and then raise,
    and that end row carries state ``unknown`` with result ``verified``. Only a wake whose
    engine settled normally (state ``yielded``) counts.
    """
    last_start = None
    for row in rows:
        if row.get("kind") == "start" and row.get("role") == role:
            last_start = row
    if last_start is None or last_start.get("dossier_hash") != digest:
        return False
    for row in reversed(rows):
        if row.get("kind") == "end" and row.get("key") == last_start.get("key"):
            return row.get("state") == "yielded" and row.get("result") == "verified"
    return False


def system1_answer(home, role: str, reasons: list, trigger: str, mail_count: int) -> dict | None:
    """The shadow System One answer for one wake start. Never raises; None when there is no provider."""
    try:
        from .budget_producer import DECISION_CONFIG, DECISION_PROVIDER
        provider = dp.select(dp.load_providers(Path(home) / DECISION_CONFIG), DECISION_PROVIDER)
        state = (f"role: {role}\ntrigger: {trigger}\nowed reasons: {', '.join(reasons) or 'none'}\n"
                 f"mail items: {mail_count}")
        decision = dp.decide(provider, QUESTION, INSTRUCTIONS, state, CRITERIA, threshold=THRESHOLD)
        # Shadow only: a below-threshold answer is still logged as its argmax, marked escalated,
        # so the threshold can later be calibrated against real outcomes instead of guessed.
        escalated = decision.choice is None and bool(decision.probabilities)
        choice = max(decision.probabilities, key=decision.probabilities.get) if escalated else decision.choice
        return {"choice": choice, "escalated": escalated, "probabilities": decision.probabilities,
                "confidence": decision.confidence, "reason": decision.reason}
    except Exception as exc:  # noqa: BLE001 - shadow answer: a failure is an unanswered start, not an error
        return {"choice": None, "escalated": False, "probabilities": {}, "confidence": None,
                "reason": f"decision provider not usable ({type(exc).__name__})"}


def record_start(home, role: str, key: str, *, snap, mail, now: float) -> None:
    """Append the start row. Never raises: this is shadow data only."""
    try:
        inputs = dossier_inputs(snap, mail)
        digest = dossier_hash(inputs)
        rows = _rows(home)
        trigger = (snap.reasons[0] if snap.reasons else "none")
        _append(home, {
            "kind": "start", "role": role, "key": key, "ts": int(now),
            "trigger": trigger,
            "reasons": inputs["reasons"], "dossier_hash": digest,
            "same_dossier_as_last": same_dossier_verdict(rows, role, digest),
            "decision": system1_answer(home, role, inputs["reasons"], trigger, len(inputs["mail"])),
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
