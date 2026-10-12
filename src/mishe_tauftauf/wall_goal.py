"""Wall-goal retention reader: literal charter Goal vs wall Goal comparison.

Bounded first seam for the operator wall-goal retention request (chat 21232):
a recurring deterministic reading, provisionally ``sense.mind.wall-goal-retention``,
over each resident role. This module is the isolated producer only: pure
functions over caller-supplied texts. No discovery wiring, no consumer, no live
activation.

Contract (per 21232):
- Authoritative source: the role charter's explicit ``Goal:`` field. Goals are
  never inferred from chat, prose, or the latest task.
- Wall contract: one explicit wall ``Goal:`` field whose normalized value equals
  the charter goal, plus an independently changeable ``Next:`` line.
- Verdicts: ``retained`` (exactly one wall goal, equal), ``missing`` (readable
  wall, field absent), ``mismatch`` (different value or duplicate field),
  ``unknown`` (any input missing, oversized, non-text, or ambiguous — never clean).
- Consumer: Witness/Health wall continuity review; missing/mismatch routes to the
  role owner. No automatic wall rewrite, wake clearing, pane action, or activation.

Text handling: a ``Goal:`` field is a line whose first five characters are
exactly ``Goal:`` (column zero, case-sensitive). The normalized value drops one
optional leading space and trailing spaces/tabs. Comparison is exact literal
equality; paraphrase never matches. The ``Next:`` line is never interpreted.

Parsing-policy note: this module performs only character equality, slicing, and
length checks — no flagged parsing calls — so ``tools/check_parsing.py`` passes
with no policy addition and the frozen legacy budget untouched. If a future
wiring hunk adds real parsing sites, that hunk's author owes the reviewed exact-
path boundary entry per docs/protocol-boundaries.md.

Limits: pure-text comparison only. It cannot tell a stale-copied goal from a
live one, nor judge whether the charter goal itself is still the right mission.
Callers supply identity/digest/sample metadata; an oversized input (above
_MAX_CHARS) reports unknown to bound computation.
"""
from __future__ import annotations

import hashlib

_FIELD = "Goal:"
_MAX_CHARS = 65536

RETAINED = "retained"
MISSING = "missing"
MISMATCH = "mismatch"
UNKNOWN = "unknown"


def _split_lines(text: str) -> list:
    """Split on newline without flagged parsing calls."""
    out = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\n":
            out.append(text[start:i])
            start = i + 1
        i += 1
    out.append(text[start:])
    return out


def _drop_cr(line: str) -> str:
    if line[len(line) - 1:] == "\r":
        return line[:len(line) - 1]
    return line


def _normalize(value: str) -> str:
    """Drop one optional leading space and trailing spaces/tabs."""
    if value[:1] == " ":
        value = value[1:]
    while value[len(value) - 1:] == " " or value[len(value) - 1:] == "\t":
        value = value[:len(value) - 1]
    return value


def _goal_values(text: str) -> list:
    """Normalized values of every column-zero ``Goal:`` line."""
    found = []
    for raw in _split_lines(text):
        line = _drop_cr(raw)
        if line[:5] == _FIELD:
            found.append(_normalize(line[5:]))
    return found


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assess_goal(charter_text, wall_text) -> dict:
    """Compare one charter text against one wall text.

    Returns a plain dict: verdict plus reason and both document digests when
    both inputs are usable text. Non-string, oversized, or ambiguous inputs
    report unknown, never retained/missing/mismatch.
    """
    if not isinstance(charter_text, str) or not isinstance(wall_text, str):
        return {"verdict": UNKNOWN, "reason": "non-text-input"}
    if len(charter_text) > _MAX_CHARS or len(wall_text) > _MAX_CHARS:
        return {"verdict": UNKNOWN, "reason": "oversize-input"}
    charter_goals = _goal_values(charter_text)
    if len(charter_goals) == 0:
        return {"verdict": UNKNOWN, "reason": "no-authoritative-goal"}
    if len(charter_goals) > 1:
        return {"verdict": UNKNOWN, "reason": "ambiguous-charter"}
    wall_goals = _goal_values(wall_text)
    base = {"charter_digest": _digest(charter_text), "wall_digest": _digest(wall_text)}
    if len(wall_goals) == 0:
        base.update({"verdict": MISSING, "reason": "wall-goal-absent"})
        return base
    if len(wall_goals) > 1:
        base.update({"verdict": MISMATCH, "reason": "duplicate-wall-goal"})
        return base
    if wall_goals[0] == charter_goals[0]:
        base.update({"verdict": RETAINED, "reason": "exact-match"})
        return base
    base.update({"verdict": MISMATCH, "reason": "value-differs"})
    return base


def assess_roles(entries) -> list:
    """Assess many roles; entries maps role to (charter_text, wall_text).

    Each row carries role, verdict, reason, and digests when available.
    A caller-side role list or charter/wall read that is missing or stale must
    surface as explicit unknown rows from the caller, not as silence here.
    """
    rows = []
    for role in sorted(entries):
        charter_text, wall_text = entries[role]
        row = {"role": role}
        row.update(assess_goal(charter_text, wall_text))
        rows.append(row)
    return rows


def summarize(rows) -> dict:
    """Count verdicts across assess_roles rows."""
    counts = {RETAINED: 0, MISSING: 0, MISMATCH: 0, UNKNOWN: 0}
    for row in rows:
        verdict = row.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts
