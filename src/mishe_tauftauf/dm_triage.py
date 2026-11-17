"""DM triage in shadow (P3): one decision-model pass per unread addressed message.

Shadow only: the rows go to ``decision-log/dm-triage.jsonl`` and nothing reads them
for dispatch. A System 0 tag rule decides the machine-addressed classes first; the
decision model picks one label for the residual. A missing or failing decision
service degrades to ``escalate`` rows, never to a raised error.

Derived state only: the state sent to the model carries the message's sequence,
sender role, recipient role, tag class, body length bucket and a bounded first-line
excerpt — never the full body of a private DM.
"""
from __future__ import annotations

import json
import pathlib
import time

from . import decision_provider as dp
from .wall_text import dm_recipient, first_line, message_subject, record_tag, sender_role
LOG_NAME = "dm-triage.jsonl"
QUESTION = "dm-triage-class"
THRESHOLD = 0.7
INSTRUCTIONS = ("Classify one addressed direct message for the receiving mind. Pick exactly one label "
                "from the criteria. The state gives the sender role, recipient role, tag, body length bucket "
                "and a short first-line excerpt only.")
CRITERIA = {
    "action": "asks the recipient to do something concrete (decide, repair, review, answer a question)",
    "fyi": "informs without requiring a reply (status, result, observation, FYI note)",
    "duplicate": "restates or forwards a message the recipient has already received or answered",
}
URGENCY_QUESTION = "dm-triage-urgency"
URGENCY_INSTRUCTIONS = ("Score the urgency of one addressed direct message from 0 to 3. Pick exactly one "
                        "label from the criteria. The state gives sender role, tag, body length bucket and a "
                        "short first-line excerpt only.")
MAX_STATE_CHARS = 2000
MAX_EXCERPT_CHARS = 160
_LEN_BUCKETS = (120, 400, 1200, 4000)
URGENCY_CRITERIA = {
    "0": "routine information, no time pressure",
    "1": "useful soon, can wait hours",
    "2": "time-sensitive within the current day (a due decision, a RED signal, a blocked resolver)",
    "3": "blocking now: a live incident, a due-now action, or a safety/authorization matter",
}
ANSWERED_QUESTION = "dm-triage-answered"
ANSWERED_INSTRUCTIONS = ("Decide whether one addressed direct message needs an answer or acknowledgement from "
                         "the recipient. Pick exactly one label from the criteria.")
ANSWERED_CRITERIA = {
    "answered": "the recipient has already answered or acknowledged this message (or it needs none)",
    "unanswered": "the recipient still owes an answer or acknowledgement",
}


def log_path(home) -> pathlib.Path:
    return pathlib.Path(home) / "decision-log" / LOG_NAME


def _len_bucket(n: int) -> str:
    for bound in _LEN_BUCKETS:
        if n <= bound:
            return f"<={bound}"
    return f">{_LEN_BUCKETS[-1]}"


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


def derived_state(seq: int, source: str, body: str, roles=()) -> dict:
    """The decision-relevant derived state of one message: no full private text."""
    first = first_line(body)
    recipient = dm_recipient(body, source, roles)
    return {
        "seq": int(seq),
        "from": sender_role(source),
        "to": recipient,
        "tag": record_tag(body),
        "len": _len_bucket(len(body)),
        "first_line": first[:MAX_EXCERPT_CHARS],
        "subject": message_subject(body)[:MAX_EXCERPT_CHARS],
    }


def _state_text(state: dict) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"))[:MAX_STATE_CHARS]


def classify(seq: int, source: str, body: str, provider: dp.Provider | None, roles=(),
             *, post=None, threshold: float = THRESHOLD) -> dict:
    """One message's triage. ``source`` is ``system0``, ``system1`` or ``escalate``."""
    state = derived_state(seq, source, body, roles)
    rule = system0_tag(body)
    if rule is not None:
        return {"label": rule, "source": "system0", "confidence": None, "reason": "tag rule",
                "state": state}
    if provider is None:
        return {"label": None, "source": "escalate", "confidence": None, "reason": "no decision provider",
                "state": state}
    decision = dp.decide(provider, QUESTION, INSTRUCTIONS, _state_text(state), CRITERIA,
                         threshold=threshold, post=post)
    if decision.decided:
        return {"label": decision.choice, "source": "system1", "confidence": decision.confidence,
                "reason": decision.reason, "state": state}
    return {"label": None, "source": "escalate", "confidence": decision.confidence,
            "reason": decision.reason, "state": state}


def score_urgency(seq: int, source: str, body: str, provider: dp.Provider | None, roles=(),
                  *, post=None, threshold: float = THRESHOLD) -> dict:
    """Urgency 0-3 for one message; ``source`` follows classify."""
    state = derived_state(seq, source, body, roles)
    if provider is None:
        return {"urgency": None, "source": "escalate", "confidence": None, "reason": "no decision provider"}
    decision = dp.decide(provider, URGENCY_QUESTION, URGENCY_INSTRUCTIONS, _state_text(state),
                         URGENCY_CRITERIA, threshold=threshold, post=post)
    if decision.decided:
        return {"urgency": decision.choice, "source": "system1", "confidence": decision.confidence,
                "reason": decision.reason}
    return {"urgency": None, "source": "escalate", "confidence": decision.confidence,
            "reason": decision.reason}


def answered(seq: int, source: str, body: str, provider: dp.Provider | None, roles=(),
             *, post=None, threshold: float = THRESHOLD) -> dict:
    """Whether the message still needs an answer/ack; ``source`` follows classify."""
    state = derived_state(seq, source, body, roles)
    if provider is None:
        return {"answered": None, "source": "escalate", "confidence": None, "reason": "no decision provider"}
    decision = dp.decide(provider, ANSWERED_QUESTION, ANSWERED_INSTRUCTIONS, _state_text(state),
                         ANSWERED_CRITERIA, threshold=threshold, post=post)
    if decision.decided:
        return {"answered": decision.choice == "answered", "source": "system1",
                "confidence": decision.confidence, "reason": decision.reason}
    return {"answered": None, "source": "escalate", "confidence": decision.confidence,
            "reason": decision.reason}


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


def record(home, seq: int, role: str, result: dict, *, now: float | None = None,
           urgency: dict | None = None, answered_q: dict | None = None) -> None:
    """Append one triage row. ``outcome`` stays null until a later observation fills it."""
    path = log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "\n" if _torn_tail(path) else ""
    row = {
        "seq": int(seq), "role": role, "ts": int(now if now is not None else time.time()),
        "label": result.get("label"), "source": result.get("source"),
        "confidence": result.get("confidence"), "reason": result.get("reason"),
        "urgency": (urgency or {}).get("urgency"),
        "answered": (answered_q or {}).get("answered"),
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


def record_unread(home, role: str, mail, provider: dp.Provider | None, *, roles=(), now: float,
                  post=None) -> list[dict]:
    """Shadow pass over one wake's unread addressed mail. Never raises: shadow data only."""
    rows = []
    try:
        done = recorded_seqs(home)
        for seq, body in mail:
            if seq in done:
                continue
            result = classify(seq, "", body, provider, roles, post=post)
            urg = score_urgency(seq, "", body, provider, roles, post=post)
            ans = answered(seq, "", body, provider, roles, post=post)
            record(home, seq, role, result, now=now, urgency=urg, answered_q=ans)
            rows.append({"seq": seq, "role": role, **result})
    except Exception:  # noqa: BLE001 - shadow logging must not affect the wake
        return rows
    return rows
