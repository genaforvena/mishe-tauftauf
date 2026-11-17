#!/usr/bin/env python3
"""Detector: a hold that outlived the work it was written for, or none.

Observed 2026-11-17: a bare ``mishe hold on`` (old default reason ``held``) was
written at 12:46:40Z, 14 s before an out-of-wake promotion started. The
promotion delivered and the hold stayed. It suppressed every wake for 2 h 40 min,
no unit or flip token owned it, ``clear_stale_hold`` deliberately never clears a
manual hold, and no signal reported it: the plant looked quiet because it could
not start anything. A human noticed and cleared it at 15:26Z.

A hold suppresses wake starts only, so its own fault cannot wake a resolver while
it stands. This detector therefore runs as a producer (a timer, not a wake), and
it reports the state in two places that do not depend on wakes:

* while the hold stands: RED names its writer and its age, on every run;
* after it disappears: one RED says a stale hold ended with no recorded
  disposition, so the incident is still routed to its resolver once wakes start.

Ownership is decoded from the hold text only (``mishe_tauftauf.hold_text``):
a promotion hold or a root-migration flip names a pid, and that pid's liveness
decides it -- a live writer is GREEN however long it holds. A hold naming no
writer (a manual ``mishe hold on`` reason, plant_boot's boot guard) can only be
judged by age, measured from the file's own mtime, so re-running ``mishe hold on``
refreshes it. A hold with a dead writer is RED at once: nobody will release it.

Exit status:
  0  no hold, or a hold whose writer is alive and whose age is inside the bound
  2  a hold names a dead pid, an anonymous hold is older than --max-age, or a
     hold that outlived the bound has ended with no recorded disposition
  3  undecidable: the hold or this detector's own state cannot be read
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

MAX_AGE_S = 3600.0
"""How long an anonymous hold (a manual reason, the boot guard) may stand unrefreshed.

A promotion or flip hold names its writer, so its own stage bounds decide it; a manual hold is
normally a short maintenance window and wakes are silently off for its whole length. The leaked
hold above stood 9600 s, so a 3600 s bound reports it three times over.
"""


def _hold_text_module():
    """The site's hold dialect, decoded by its literal boundary rather than here."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from mishe_tauftauf import hold_text
    return hold_text


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("hold-state state is not an object")
    return data


def _write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def evaluate(home: Path, now: float, max_age: float = MAX_AGE_S, state_path: Path | None = None):
    home = Path(home)
    state_path = Path(state_path) if state_path else home / "health" / "hold-state.json"
    hold = home / "hold"
    try:
        state = _read_state(state_path)
    except (OSError, ValueError) as exc:
        # Without the state we cannot tell an undispositioned stale episode from a quiet plant.
        return 3, [f"UNKNOWN: {state_path}: {exc}"]

    try:
        text = hold.read_text(encoding="utf-8")
        age = now - hold.stat().st_mtime
    except FileNotFoundError:
        if state.get("stale") and not state.get("released_reported"):
            state["released_reported"] = True
            state["released_at"] = now
            try:
                _write_state(state_path, state)
            except OSError as exc:
                return 3, [f"UNKNOWN: cannot record the released hold in {state_path}: {exc}"]
            return 2, [f"a hold written at {_iso(state.get('since', now))} stood "
                       f"{int(now - state.get('since', now))}s and ended with no recorded disposition; "
                       f"its text was {state.get('text', '')!r}"]
        if state.get("stale"):
            state = {}
            try:
                _write_state(state_path, state)
            except OSError:
                pass
        return 0, ["no hold: wake starts are not suppressed"]

    try:
        hold_text = _hold_text_module()
    except Exception as exc:                                  # noqa: BLE001 - any import failure is undecidable
        return 3, [f"UNKNOWN: cannot load the hold dialect: {exc}"]

    pid = hold_text.owner_pid(text)
    stale = False
    if pid is not None:
        if _pid_alive(pid):
            verdict = 0
            lines = [f"hold names live pid {pid} ({int(age)}s since it was written)"]
        else:
            stale = True
            verdict = 2
            lines = [f"hold names pid {pid}, which is gone: no writer will release it "
                     f"(promotion holds are cleared by clear_stale_hold; a flip releases its own); "
                     f"text {text.strip()!r}"]
    elif age > max_age:
        stale = True
        verdict = 2
        lines = [f"manual hold written at {_iso(now - age)} has stood {int(age)}s without a refresh "
                 f"(bound {int(max_age)}s); text {text.strip()!r}. A live writer re-runs "
                 f"`mishe hold on --reason ...` to confirm it; if it leaked, `mishe hold off`"]
    else:
        verdict = 0
        lines = [f"manual hold: {text.strip()!r}, written {int(age)}s ago"]

    held_text = text.strip()
    if stale and (not state.get("stale") or state.get("text") != held_text):
        state = {"stale": True, "text": held_text, "since": now - age, "released_reported": False}
        try:
            _write_state(state_path, state)
        except OSError as exc:
            return 3, [f"UNKNOWN: cannot record the stale hold in {state_path}: {exc}"]
    elif not stale and state:
        try:
            _write_state(state_path, {})
        except OSError:
            pass
    return verdict, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--state", type=Path, help="where the last verdict is remembered (default: HOME/health/hold-state.json)")
    parser.add_argument("--max-age", type=float, default=MAX_AGE_S,
                        help="seconds an anonymous hold may stand without a refresh")
    args = parser.parse_args(argv)
    code, lines = evaluate(args.home, time.time(), args.max_age, args.state)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
