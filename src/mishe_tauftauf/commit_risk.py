"""Commit risk class: a System 0 path rule first, a decision model only for the residual.

Labels: ``routine`` (docs, tests, observation code only), ``delivery-path`` (release,
promotion, pin, delivery, heal, rollback), ``dispatch-path`` (wake admission, scheduling,
locking, authorization, dispatch, failure routing), and ``ordinary`` (product code with no
such effect; a model-only label, never a path rule). Before ``ordinary`` existed, most
ordinary code escaped to ``unclear`` (replay 2026-11-15: 21 of 27 answers). A commit whose paths all fall in one
rule gets that label from code with source ``system0``. A commit that mixes rules or
touches neither is the residual: the decision provider picks one label from the criteria,
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
from . import commit_risk_text as text
from .commit_risk_text import changed_paths, path_label  # changed_paths is re-exported for callers

QUESTION = "commit-risk-class"
THRESHOLD = 0.7
INSTRUCTIONS = ("Classify a git commit by the risk class of the code it changes. Pick exactly one label "
                "from the criteria. The state gives the commit subject and the changed paths only.")
CRITERIA = {
    "routine": "changes only docs, tests or observation code; no effect on dispatch, delivery or locking",
    "delivery-path": "changes release, promotion, pin, delivery, heal or rollback behaviour",
    "dispatch-path": "changes wake admission, scheduling, locking, authorization or failure routing",
    "ordinary": "changes ordinary product code (budget, reporting, text, units, tools) with no effect on dispatch, delivery or locking",
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


def recorded_shas(home: pathlib.Path) -> set[str]:
    """SHAs already answered in the ledger, so a pass never classifies one twice."""
    return text.ledger_shas(pathlib.Path(home) / "decision-log" / "commit-risk.jsonl")


def record_new(repo: pathlib.Path, home: pathlib.Path, provider: dp.Provider | None, *, count: int = 50,
               post=None) -> list[dict]:
    """Advisory pass: classify each recent commit not yet in the ledger and append its row.

    Runs outside delivery and never gates it. A commit that needs the model is left unrecorded
    when no decision service is configured, and a commit the service did not answer (refused, failed)
    is left unrecorded too, so a later pass asks again.
    """
    done = recorded_shas(home)
    rows = []
    for sha in text.recent_shas(repo, count):
        if sha in done:
            continue
        subject, paths = changed_paths(repo, sha)
        if provider is None and system0(paths) is None:
            continue
        result = dict(classify(subject, paths, provider, post=post), subject=subject, n_paths=len(paths))
        if result["confidence"] is None:
            continue  # the service gave no answer (refused, failed or unconfigured): ask again on a later pass
        record(home, sha, result)
        rows.append(dict(result, sha=sha))
    return rows


def unanswered(repo: pathlib.Path, home: pathlib.Path, provider: dp.Provider | None, *, count: int = 50) -> list[str]:
    """Recent commits the ledger still lacks and that no path rule settles (the model's residual)."""
    done = recorded_shas(home)
    pending = []
    for sha in text.recent_shas(repo, count):
        if sha in done:
            continue
        _, paths = changed_paths(repo, sha)
        if system0(paths) is None:
            pending.append(sha)
    return pending


def advisory_duty(repo: pathlib.Path, home: pathlib.Path, *, now: float | None = None) -> tuple[int, str]:
    """The duty's observation: (exit code, one-line reason). GREEN 0, UNKNOWN 3.

    UNKNOWN when the decision provider is not configured or unreadable, or when residual commits stay
    unanswered while the day's cap has room (the service is failing). Unanswered commits with the cap
    spent are GREEN: the cap is the declared limit, not a fault. Advisory only; nothing gates on it.
    """
    import datetime as _dt
    from .budget_producer import DECISION_CONFIG, DECISION_PROVIDER
    try:
        provider = dp.select(dp.load_providers(pathlib.Path(home) / DECISION_CONFIG), DECISION_PROVIDER)
    except Exception as exc:  # no key material in the reason: the class name is enough
        return 3, f"UNKNOWN: decision provider not usable ({type(exc).__name__})"
    try:
        rows = record_new(pathlib.Path(repo), pathlib.Path(home), provider)
        pending = unanswered(pathlib.Path(repo), pathlib.Path(home), provider)
        day = _dt.datetime.fromtimestamp(time.time() if now is None else now, _dt.timezone.utc).date().isoformat()
        calls = dp.calls_today(provider, day)
    except Exception as exc:
        return 3, f"UNKNOWN: advisory pass failed ({type(exc).__name__})"
    if pending and calls < provider.daily_cap:
        return 3, f"UNKNOWN: {len(pending)} residual commits unanswered with {calls} of {provider.daily_cap} calls used"
    held = f", {len(pending)} held by the daily cap" if pending else ""
    return 0, f"GREEN: {len(rows)} classified this pass{held}"


def main(argv=None) -> int:
    args = text.duty_args(argv)
    code, reason = advisory_duty(args.repo, args.home)
    print(f"mishe: commit-risk: {reason}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
