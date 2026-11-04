"""Mind liveness: is a resident mind busy, idle, or idle mid-turn?

Pure classification over observable evidence, so it can be tested without tmux or systemd:
a pane's last lines (a busy spinner with a timer, or Claude's "esc to interrupt"), a plan line marked
"(blocked)", and the seed unit's journal (a held wake with no later clear).

Verdicts:
- BUSY: the pane shows active work.
- BUSY_STALE: busy, but the mind's wall has not been written for longer than WALL_STALE_S.
- IDLE: idle, with nothing held or blocked.
- IDLE_MID_TURN: idle while a wake is held or a plan item is blocked. The turn did not finish.
"""

from __future__ import annotations

from . import clear_journal, omp_pane

WALL_STALE_S = 3600


def _lines(lines: list[str] | str) -> list[str]:
    return lines if isinstance(lines, list) else [lines]



def is_busy(pane_tail: list[str] | str) -> bool:
    """True when the decoded pane frame shows active work."""
    return omp_pane.pane_is_busy(_lines(pane_tail))


def plan_blocked(pane_tail: list[str] | str) -> bool:
    """True when a plan line in the pane is marked blocked."""
    return omp_pane.pane_plan_blocked(_lines(pane_tail))




def wake_held(journal_lines: list[str]) -> bool:
    """True when the latest decoded seed event is held with no later clear."""
    return clear_journal.wake_held(journal_lines)


def verdict(pane_tail: list[str] | str, journal_lines: list[str], wall_age_s: float) -> str:
    """One of BUSY, BUSY_STALE, IDLE, IDLE_MID_TURN."""
    if is_busy(pane_tail):
        return "BUSY_STALE" if wall_age_s > WALL_STALE_S else "BUSY"
    if wake_held(journal_lines) or plan_blocked(pane_tail):
        return "IDLE_MID_TURN"
    return "IDLE"


def bounded_action(pane_tail: list[str] | str, journal_lines: list[str], wall_age_s: float) -> str:
    """The one action a stalled mind needs, so a declared block is never left as an idle end state.

    NONE: the mind is busy or has nothing held or blocked.
    RESEND_WAKE: idle with a held wake whose wall is not newer than the wake; re-deliver it once.
    ESCALATE_STEWARD: idle and blocked (a plan item says so), or still idle after a re-delivery
    attempt; the steward must route the resolver.
    """
    if is_busy(pane_tail):
        return "NONE"
    held = wake_held(journal_lines)
    blocked = plan_blocked(pane_tail)
    if held and not blocked:
        return "RESEND_WAKE"
    if blocked:
        return "ESCALATE_STEWARD"
    return "NONE"
