"""System One savings signal (P4): what the wake path costs and what a gate would save.

A System 0 producer. It reads only the ledgers the wake path already keeps and calls no
provider, so a missing decision service cannot stop it:

- ``decision-log/wake-gate.jsonl``: one ``start`` row per wake (trigger class, its ``signal:``
  reasons, the System 0 ``same_dossier_as_last`` verdict) and one ``end`` row (yielded result).
- ``decision-log/wake-gate-live.jsonl``: rows a live gate wrote (``action`` skip or wake).
  Absent means no gate is live, so avoided wakes are 0 and the gate reads ``off``.
- ``decision-log/commit-risk.jsonl``: each commit's deciding source (``system0`` path or
  subject rule, ``system1`` model, ``escalate`` to a mind), for the measured rule saving.
- ``engine/budget.json`` under ``routerai.decision``: calls and spend of the decision budget.

Over the trailing 24 h it reports wake starts per day by trigger class, wakes avoided by a
live gate, the projected skip of same-dossier starts (what a gate would skip, not what it
skipped), the changed-rate of those projected skips against the changed-rate of all wakes,
and the decision calls and spend. Exit 0 measured (GREEN), 3 UNKNOWN when a ledger or
the budget is unusable. The wake-gate ledger needs a readable start row, even when
none of its starts fall inside the window.

It also reports per-signal changed rates (from each start's ``signal:`` reasons) and flags
signals with at least ``MIN_SIGNAL_ENDED`` ended wakes and a changed rate below
``MAX_SIGNAL_CHANGED_RATE`` as ``retirement_candidates``. This is a detector only: the flag
never skips a wake. The volume floor exempts rare-event detectors, whose low changed rate is
their purpose, not waste.

For clean yields whose only reason was one signal, the report also counts recorded artifacts
and attributed commits from the corresponding work receipts. Unknown receipts stay visible.

It also measures what the deterministic rung already took off the model and the mind: the
trailing-window count of commit-risk rows decided by a path or subject rule, against model
answers and escalations. A missing commit-risk ledger reports null counts, not a zero saving.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import state_files, yielding

GREEN, UNKNOWN = 0, 3
WINDOW_S = 24 * 3600
BUDGET_STALE_S = 6 * 3600
WAKE_LOG = "decision-log/wake-gate.jsonl"
LIVE_LOG = "decision-log/wake-gate-live.jsonl"
MIN_SIGNAL_ENDED = 20
MAX_SIGNAL_CHANGED_RATE = 0.15
COMMIT_LOG = "decision-log/commit-risk.jsonl"


def _trigger_class(trigger) -> str:
    """Bucket a trigger by its prefix (signal, intention, inbox, ...) so cardinality stays bounded."""
    text = str(trigger or "none")
    return text.split(":", 1)[0] or "none"


def _signal_names(start) -> set[str]:
    """Signal names a start woke on, from its reasons. A wake co-firing on several
    signals counts once per name; this is a detector, never a gate."""
    names = set()
    reasons = start.get("reasons")
    if isinstance(reasons, list):
        for reason in reasons:
            if isinstance(reason, str) and reason.startswith("signal:"):
                name = reason.split(":", 1)[1]
                if name:
                    names.add(name)
    return names

def _cofired(start, name) -> bool:
    """True when a start carries another wake reason that would wake the role on its own.

    An ``inbox`` (human DM), an ``intention:`` or a second named ``signal:`` reason each
    wakes the role independently, so a changed wake carrying one would have happened
    without this signal. Only the exact own token is excluded: a duplicate of it, a
    malformed empty ``signal:`` token and non-string entries are not independent wake work.
    """
    own = f"signal:{name}"
    reasons = start.get("reasons")
    if not isinstance(reasons, list):
        return False
    for reason in reasons:
        if not isinstance(reason, str) or reason == own:
            continue
        if reason == "inbox" or reason.startswith("intention:") or (
            reason.startswith("signal:") and reason[len("signal:"):]
        ):
            return True
    return False


def _rate(num: int, den: int):
    return None if den == 0 else round(num / den, 4)


def _receipt_output(home: Path, start: dict, result: str) -> tuple[bool, bool] | None:
    """Read factual output from the exact wake receipt. None means evidence is unknown."""
    key = start["key"]
    role = start.get("role")
    if Path(key).name != key or not isinstance(role, str) or not role:
        return None
    path = home / "wakes" / f"{key}.work.json"
    try:
        receipt = state_files.read_json(path) if not path.is_symlink() and path.is_file() else None
    except (OSError, ValueError):
        return None
    if not isinstance(receipt, dict) or (
        receipt.get("version") != 1 or receipt.get("evidence_state") != "KNOWN"
        or receipt.get("wake_key") != key or receipt.get("role") != role
        or receipt.get("claimed_result") != result
    ):
        return None
    commits = receipt.get("commits_attributed")
    artifacts = receipt.get("artifacts")
    if not isinstance(commits, list) or not isinstance(artifacts, list):
        return None
    if any(not isinstance(sha, str) or len(sha) not in (40, 64) for sha in commits):
        return None
    if any(not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not ref["path"]
           or type(ref.get("size")) is not int or ref["size"] <= 0
           or not isinstance(ref.get("sha256"), str) or len(ref["sha256"]) != 64
           for ref in artifacts):
        return None
    return bool(commits), bool(artifacts)


def measure(home, *, now: float) -> dict:
    """The savings numbers for the trailing window. Raises ValueError when a ledger is unusable."""
    home = Path(home)
    wake_path = home / WAKE_LOG
    if not wake_path.exists():
        raise ValueError("wake-gate ledger missing: nothing measured yet")
    rows = state_files.read_json_lines(wake_path)
    since = now - WINDOW_S
    starts = {}
    ends = {}
    saw_start = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row.get("key")
        if not isinstance(key, str) or not key:
            continue
        if row.get("kind") == "start":
            timestamp = row.get("ts")
            if isinstance(timestamp, bool):
                continue
            try:
                started_at = float(timestamp)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(started_at):
                continue
            saw_start = True
            if started_at >= since:
                starts[key] = row
        elif row.get("kind") == "end":
            ends[key] = row
    if not saw_start:
        raise ValueError("wake-gate ledger has no readable start rows")

    by_trigger = Counter(_trigger_class(r.get("trigger")) for r in starts.values())
    ended = [(s, ends[k]) for k, s in starts.items() if k in ends]
    ended_by_trigger = Counter()
    yielded_by_trigger = Counter()
    changed_by_trigger = Counter()
    for start, end in ended:
        trigger = _trigger_class(start.get("trigger"))
        ended_by_trigger[trigger] += 1
        result = end.get("result")
        if end.get("state") == "yielded" and result in yielding.RESULTS:
            yielded_by_trigger[trigger] += 1
            if result == "changed":
                changed_by_trigger[trigger] += 1
    trigger_rates = {
        trigger: {
            "ended": ended_by_trigger[trigger],
            "yielded": yielded_by_trigger[trigger],
            "changed": changed_by_trigger[trigger],
            "changed_rate": _rate(changed_by_trigger[trigger], yielded_by_trigger[trigger]),
        }
        for trigger in sorted(by_trigger)
    }

    signal_ended = Counter()
    signal_yielded = Counter()
    signal_changed = Counter()
    signal_changed_cofired = Counter()
    signal_receipts = defaultdict(Counter)
    for start, end in ended:
        names = _signal_names(start)
        if not names:
            continue
        result = end.get("result")
        clean = end.get("state") == "yielded" and result in yielding.RESULTS
        changed = clean and result == "changed"
        for name in names:
            signal_ended[name] += 1
            if not clean:
                continue
            signal_yielded[name] += 1
            if changed:
                signal_changed[name] += 1
                if _cofired(start, name):
                    signal_changed_cofired[name] += 1
            if start.get("reasons") != [f"signal:{name}"]:
                continue
            output = _receipt_output(home, start, result)
            counts = signal_receipts[name]
            if output is None:
                counts["unknown"] += 1
            else:
                committed, registered = output
                counts["known"] += 1
                counts["with_attributed_commit"] += committed
                counts["with_registered_artifact"] += registered
    signal_rates = {
        name: {
            "ended": signal_ended[name],
            "yielded": signal_yielded[name],
            "changed": signal_changed[name],
            "changed_rate": _rate(signal_changed[name], signal_yielded[name]),
        }
        for name in sorted(signal_ended)
    }
    retirement_candidates = [
        name
        for name, stats in signal_rates.items()
        if stats["ended"] >= MIN_SIGNAL_ENDED
        and stats["changed_rate"] is not None
        and stats["changed_rate"] < MAX_SIGNAL_CHANGED_RATE
    ]
    # For each flagged signal, the co-firing evidence the retirement review needs:
    # a changed wake that also carried a DM/intention/other-signal reason would have
    # happened without this signal, so only such wakes count toward a safe retirement.
    retirement_review = {
        name: {
            "changed": signal_changed[name],
            "changed_cofired": signal_changed_cofired[name],
            "changed_all_cofired": signal_changed[name] > 0
            and signal_changed_cofired[name] == signal_changed[name],
        }
        for name in retirement_candidates
    }
    results = Counter(e.get("result") or "none" for _, e in ended)
    same = [(s, e) for s, e in ended if s.get("same_dossier_as_last") is True]
    changed_all = sum(1 for _, e in ended if e.get("result") == "changed")
    changed_same = sum(1 for _, e in same if e.get("result") == "changed")

    live_path = home / LIVE_LOG
    live_rows = state_files.read_json_lines(live_path) if live_path.exists() else []
    live = [r for r in live_rows if isinstance(r, dict) and float(r.get("ts", 0)) >= since]
    skipped = sum(1 for r in live if r.get("action") == "skip")

    budget = _budget(home, now)
    return {
        "window_h": WINDOW_S // 3600,
        "starts": len(starts),
        "starts_by_trigger": dict(sorted(by_trigger.items())),
        "ended": len(ended),
        "changed_by_trigger": trigger_rates,
        "changed_by_signal": signal_rates,
        "sole_signal_receipts": {
            name: {field: counts[field] for field in (
                "known", "unknown", "with_attributed_commit", "with_registered_artifact",
            )}
            for name, counts in sorted(signal_receipts.items())
        },
        "retirement_candidates": retirement_candidates,
        "retirement_review": retirement_review,
        "results": dict(sorted(results.items())),
        "gate": "live" if live_path.exists() else "off",
        "avoided": skipped,
        "projected_skip": len(same),
        "changed_rate_all": _rate(changed_all, len(ended)),
        "changed_rate_projected_skip": _rate(changed_same, len(same)),
        "rules": _rules(home, now),
        "decision": budget,
    }


def _rules(home: Path, now: float) -> dict:
    """Decisions the deterministic rung took in the trailing window, from the advisory ledger.

    A missing ledger is UNKNOWN (null counts), never an empty success: a saving cannot be
    claimed without its evidence. Torn or non-object rows are counted as malformed; a row
    outside the window, or stamped in the future, is simply not counted. A commit a rule
    answered is one the model and the mind did not take, so ``system0`` is the measured
    saving and ``escalate`` is the residual a mind still reads.
    """
    path = home / COMMIT_LOG
    if not path.exists():
        return {"ledger": "missing", "system0": None, "path_rule": None,
                "subject_rule": None, "system1": None, "escalate": None,
                "rule_share": None, "malformed": None}
    counts = Counter()
    malformed = 0
    since = now - WINDOW_S
    for row in state_files.read_json_lines(path):
        if not isinstance(row, dict):
            malformed += 1
            continue
        at = row.get("at")
        if isinstance(at, bool):
            malformed += 1
            continue
        try:
            at = float(at)
        except (TypeError, ValueError, OverflowError):
            malformed += 1
            continue
        if not math.isfinite(at) or at < since or at > now + 5:
            continue
        source = row.get("source")
        if source not in ("system0", "system1", "escalate"):
            malformed += 1
            continue
        counts[source] += 1
        if source == "system0":
            reason = row.get("reason")
            if reason == "path rule":
                counts["path_rule"] += 1
            elif reason == "subject rule":
                counts["subject_rule"] += 1
    total = sum(counts[source] for source in ("system0", "system1", "escalate"))
    return {
        "ledger": "ok",
        "system0": counts["system0"],
        "path_rule": counts["path_rule"],
        "subject_rule": counts["subject_rule"],
        "system1": counts["system1"],
        "escalate": counts["escalate"],
        "rule_share": _rate(counts["system0"], total),
        "malformed": malformed,
    }


def _budget(home: Path, now: float) -> dict:
    budget = state_files.read_json(home / "engine" / "budget.json")
    if not isinstance(budget, dict):
        raise ValueError("engine/budget.json missing or not an object")
    decision = (budget.get("routerai") or {}).get("decision")
    if not isinstance(decision, dict):
        raise ValueError("routerai.decision missing from engine/budget.json")
    observed = float(decision.get("observed_at") or 0)
    if observed <= 0 or now - observed > BUDGET_STALE_S:
        raise ValueError("decision budget is stale or unobserved")
    return {
        "calls_today": decision.get("calls_today"),
        "daily_cap": decision.get("daily_cap"),
        "spent": decision.get("spent"),
        "spend_limit": decision.get("spend_limit"),
    }


def duty(home, *, now: float | None = None) -> tuple[int, str]:
    """The duty verdict: exit code and one line. Never raises."""
    now = time.time() if now is None else now
    try:
        numbers = measure(home, now=now)
    except (ValueError, OSError) as exc:
        return UNKNOWN, f"UNKNOWN: {exc}"
    return GREEN, "GREEN: " + json.dumps(numbers, sort_keys=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True)
    parser.add_argument("--repo", default=None, help="accepted for the duty command shape; unused")
    args = parser.parse_args(argv)
    code, line = duty(args.home)
    print(f"mishe: system1-savings: {line}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
