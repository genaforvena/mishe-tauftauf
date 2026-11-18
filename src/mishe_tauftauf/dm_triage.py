"""System 0 triage of machine-addressed mail (P3).

The rows go to ``decision-log/dm-triage.jsonl`` and nothing reads them for dispatch. Only
forms a deterministic rule settles are logged: a bracketed record tag, and the fixed
OnFailure failure-notice sentence. Prose DMs are left to the mind, just as an unavailable
decision service used to leave them. The 2026-11-18 decision-model pass was retired after
two outcome-attribution proxies failed and no consumer of its class rows appeared; the
historical rows stay in the log as evidence.

A row carries the message sequence, the receiving role and the decided class — never the
full body of a private DM.
"""
from __future__ import annotations

import json
import pathlib
import time

from .wall_text import failure_notice, record_tag

LOG_NAME = "dm-triage.jsonl"


def log_path(home) -> pathlib.Path:
    return pathlib.Path(home) / "decision-log" / LOG_NAME


def system0_tag(body: str) -> str | None:
    """The deterministic rule: a bracketed record tag decides the class without a model."""
    tag = record_tag(body)
    if tag is None:
        return None
    if tag in {"permission", "permission-request"}:
        return "action"
    if tag in {"ci", "note", "outcome", "yield", "wall"}:
        return "fyi"
    return None


def system0_class(body: str) -> str | None:
    """The class a deterministic rule settles for this message, or None for prose.

    Two forms are lookups: a bracketed record tag, and the fixed OnFailure sentence
    ``failure.report`` writes ("<unit> failed. Signal <name> is RED..."). A failure
    notice asks its resolver to act, so it is ``action``. ``wall_text.FAILURE_NOTICE``
    is the one template both the reporter and the matcher use, so a wording change
    cannot silently stop classifying notices.
    """
    if failure_notice(body) is not None:
        return "action"
    return system0_tag(body)


def _torn_tail(path: pathlib.Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                return False
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def record(home, seq: int, role: str, result: dict, *, now: float | None = None) -> None:
    """Append one triage row. ``outcome`` stays null until a later observation fills it."""
    path = log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "\n" if _torn_tail(path) else ""
    row = {
        "seq": int(seq), "role": role, "ts": int(now if now is not None else time.time()),
        "label": result.get("label"), "source": result.get("source"),
        "confidence": result.get("confidence"), "reason": result.get("reason"),
        "outcome": None,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix + json.dumps(row, sort_keys=True) + "\n")


def recorded_seqs(home) -> set[int]:
    """Sequences already triaged, so a pass never classifies one message twice."""
    from . import state_files
    try:
        return {int(r["seq"]) for r in state_files.read_json_lines(log_path(home)) if "seq" in r}
    except Exception:
        return set()


def record_unread(home, role: str, mail, *, now: float) -> list[dict]:
    """System 0 pass over one wake's unread addressed mail. Never raises: shadow data only.

    Only machine-addressed forms are decided and logged; a prose DM is left to the mind,
    the same fallback a missing decision service used to produce. No provider is loaded
    and no call is made, so the pass adds no latency at wake start.
    """
    rows = []
    try:
        done = recorded_seqs(home)
        for seq, body in mail:
            if seq in done:
                continue
            label = system0_class(body)
            if label is None:
                continue
            result = {"label": label, "source": "system0", "confidence": None,
                      "reason": "system0 rule"}
            record(home, seq, role, result, now=now)
            rows.append({"seq": seq, "role": role, **result})
    except Exception:  # noqa: BLE001 - shadow logging must not affect the wake
        return rows
    return rows
