"""Commit risk class: a System 0 path rule first, a decision model only for the residual.

Labels: ``routine`` (docs, tests, observation code only), ``delivery-path`` (release,
promotion, pin, delivery, heal, rollback), ``dispatch-path`` (wake admission, scheduling,
locking, authorization, dispatch, failure routing). A commit whose paths all fall in one
rule gets that label from code with source ``system0``. A commit that mixes rules or
touches neither is the residual: the decision provider picks one label from the three,
or the escape option, and the answer is kept only if its confidence clears the threshold.

The class is advisory. It is written to a ledger for later ground truth (``outcome`` is
filled in by whoever later observes a rollback or a delivery problem); it never gates
delivery, review or promotion. A missing or failing decision service yields the label
``None`` with source ``escalate``, so a mind reads the commit instead.
"""
from __future__ import annotations

import json
import pathlib
import time

from . import decision_provider as dp
from .commit_risk_text import changed_paths, path_label  # changed_paths is re-exported for callers

QUESTION = "commit-risk-class"
THRESHOLD = 0.7
INSTRUCTIONS = ("Classify a git commit by the risk class of the code it changes. Pick exactly one label "
                "from the criteria. The state gives the commit subject and the changed paths only.")
CRITERIA = {
    "routine": "changes only docs, tests or observation code; no effect on dispatch, delivery or locking",
    "delivery-path": "changes release, promotion, pin, delivery, heal or rollback behaviour",
    "dispatch-path": "changes wake admission, scheduling, locking, authorization or failure routing",
}
MAX_STATE_CHARS = 2000


def system0(paths: list[str]) -> str | None:
    """The deterministic rule. Every path must fall under one label; a mix or no match is the residual."""
    if not paths:
        return None
    labels = set()
    for path in paths:
        hit = path_label(path)
        if hit is None:
            return None
        labels.add(hit)
    return labels.pop() if len(labels) == 1 else None


def classify(subject: str, paths: list[str], provider: dp.Provider | None, *, post=None,
             threshold: float = THRESHOLD) -> dict:
    """One commit's class. ``source`` is ``system0``, ``system1`` or ``escalate``."""
    label = system0(paths)
    if label is not None:
        return {"label": label, "source": "system0", "confidence": 1.0, "reason": "path rule"}
    if provider is None:
        return {"label": None, "source": "escalate", "confidence": None, "reason": "no decision provider"}
    state = f"commit subject: {subject}\nchanged paths:\n" + "\n".join(paths)
    state = state[:MAX_STATE_CHARS]
    decision = dp.decide(provider, QUESTION, INSTRUCTIONS, state, CRITERIA, threshold=threshold, post=post)
    if decision.decided:
        return {"label": decision.choice, "source": "system1", "confidence": decision.confidence,
                "reason": decision.reason}
    return {"label": None, "source": "escalate", "confidence": decision.confidence, "reason": decision.reason}


def record(home: pathlib.Path, sha: str, result: dict, *, now: float | None = None) -> None:
    """Append one answer to the ledger. ``outcome`` stays null until a later observation fills it."""
    ledger = pathlib.Path(home) / "decision-log" / "commit-risk.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    row = dict(sha=sha, at=time.time() if now is None else now, outcome=None, **result)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
