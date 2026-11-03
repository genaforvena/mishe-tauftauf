"""Steward watch: the per-mind checks the steward ran by hand on each wake, as tested code.

For each resident mind the watch reads three observations and returns one action:

- pane: DEAD when no pane capture exists (missing or empty), otherwise ALIVE.
- wall: STALE when the mind's wall is older than WALL_STALE_S or missing; otherwise FRESH.
  A missing wall fails closed as STALE, never as fresh.
- wake: HELD when the seed journal's last wake event is held with no later clear.
- idle: BUSY (spinner or "esc to interrupt"), IDLE_MID_TURN (idle with a held wake or a
  "(blocked)" plan line), or IDLE.

Actions, most urgent first:
- alert-health: pane is dead; health owns pane recovery.
- dm-held-wake: an idle mind has a held wake or a blocked plan line; its resolver acts.
- resend-wake: idle and stale; the wake should be re-sent. This module only recommends it;
  it never sends a wake itself.
- check-busy-stale: busy with a stale wall; read the pane, do not interrupt.
- ok: nothing to do.

Text interpretation (pane lines, journal lines, spinner glyphs) lives in the approved
boundary adapters `omp_pane` and `clear_journal`; this module acts on decoded values.
`observe_mind` is the only function that touches tmux, the wall file or journalctl, and it
reports unreadable inputs as None rather than guessing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from mishe_tauftauf import clear_journal, omp_pane

WALL_STALE_S = 3600
SESSION = "mishe-self-development-current"
TMUX_SOCKET = "/tmp/tmux-1000/mishe-tauftauf/tmux-1000/default"


def classify_mind(
    role: str,
    pane_tail: list[str] | None,
    journal_lines: list[str] | None,
    wall_age_s: float | None,
) -> dict:
    """Classify one mind and return its observations and the single action to take."""
    pane = "ALIVE" if pane_tail else "DEAD"
    wall = "FRESH" if wall_age_s is not None and wall_age_s <= WALL_STALE_S else "STALE"
    held = clear_journal.wake_held(journal_lines or [])
    wake = "HELD" if held else "CLEAR"

    if pane == "DEAD":
        idle = "UNKNOWN"
    elif omp_pane.pane_is_busy(pane_tail):
        idle = "BUSY"
    elif held or omp_pane.pane_plan_blocked(pane_tail):
        idle = "IDLE_MID_TURN"
    else:
        idle = "IDLE"

    if pane == "DEAD":
        action = "alert-health"
    elif idle == "IDLE_MID_TURN":
        action = "dm-held-wake"
    elif idle == "IDLE" and wall == "STALE":
        action = "resend-wake"
    elif idle == "BUSY" and wall == "STALE":
        action = "check-busy-stale"
    else:
        action = "ok"

    return {"role": role, "pane": pane, "wall": wall, "wake": wake, "idle": idle, "action": action}


def _run(argv: list[str]) -> str | None:
    """Stdout of a read-only probe, or None when it cannot run or fails."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def observe_mind(role: str, home: str, now: float | None = None) -> dict:
    """Read one mind's pane, wall age and seed journal. Unreadable inputs stay None."""
    now = time.time() if now is None else now

    capture = _run(["tmux", "-S", TMUX_SOCKET, "capture-pane", "-p", "-t", f"{SESSION}:{role}"])
    pane_tail = omp_pane.capture_tail(capture, 30) if capture is not None else None

    wall_path = os.path.join(home, "walls", f"{role}.md")
    wall_age = now - os.path.getmtime(wall_path) if os.path.exists(wall_path) else None

    dump = _run(["journalctl", "--user", "-u", f"{SESSION}-{role}.service", "-n", "20", "--no-pager"])
    journal = clear_journal.journal_lines(dump) if dump is not None else None

    return classify_mind(role, pane_tail, journal, wall_age)


def main(argv: list[str] | None = None) -> int:
    """Usage: steward_watch.py ROLE [ROLE ...]. Site home comes from MISHE_SEED_HOME."""
    roles = sys.argv[1:] if argv is None else argv
    home = os.environ.get("MISHE_SEED_HOME")
    if not roles or not home:
        print("UNKNOWN steward watch needs ROLE arguments and MISHE_SEED_HOME", file=sys.stderr)
        return 2
    worst = 0
    for role in roles:
        result = observe_mind(role, home)
        print(
            f"{result['role']}: pane={result['pane']} wall={result['wall']} "
            f"wake={result['wake']} idle={result['idle']} action={result['action']}"
        )
        if result["action"] != "ok":
            worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
