"""Cached follow-through surface and independent producer-freshness guard.

Rendering never scans Git or chat. Put this projection after the MIND WALL
boundary: changing diagnostic counts must not recursively create another wake.
The guard checks producer freshness, not whether the reported work is healthy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from .post_check import _save


def lines(home: Path, *, now=None) -> list[str]:
    from .initiative import snapshot_lines as initiative_lines
    from .substrate_watch import snapshot_lines as substrate_lines

    return [*_outcome_refusal_lines(home, now=now),
            *substrate_lines(home, now=now, max_age=120),
            *initiative_lines(home, now=now, max_age=180)]


REFUSAL_AUDIT_MAX_BYTES = 262144
"""Bound on the refusal audit tail the projection reads."""


def _outcome_refusal_lines(home: Path, *, now=None) -> list[str]:
    """Report refused outcome publications, so a denial is visible to the plant.

    The writer records every refusal it makes. Without that record a
    plant-wide denial of the outcome instrument is indistinguishable from a
    plant that produced nothing.
    """
    from .wall import OUTCOME_REFUSAL_AUDIT

    home = Path(home).resolve()
    now = now or datetime.now(timezone.utc)
    path = home / OUTCOME_REFUSAL_AUDIT
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [f"OUTCOME WRITER REFUSALS: none recorded (no audit file yet at {OUTCOME_REFUSAL_AUDIT})"]
    except OSError as exc:
        return [f"OUTCOME WRITER REFUSALS: UNKNOWN — audit unreadable: {exc}"]
    if len(raw) > REFUSAL_AUDIT_MAX_BYTES:
        raw = raw[-REFUSAL_AUDIT_MAX_BYTES:]
    entries = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("ts"), str):
            continue
        entries.append(entry)
    if not entries:
        return [f"OUTCOME WRITER REFUSALS: none recorded (audit {OUTCOME_REFUSAL_AUDIT} holds no readable entry)"]
    horizon = now - timedelta(hours=24)
    recent = []
    for entry in entries:
        try:
            stamp = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if stamp >= horizon:
            recent.append(entry)
    if not recent:
        return [f"OUTCOME WRITER REFUSALS: 0 in the last 24h "
                f"(audit {OUTCOME_REFUSAL_AUDIT}, {len(entries)} older entries retained)"]
    denials = [entry for entry in recent if entry.get("gaps")]
    last = recent[-1]
    gaps = last.get("gaps") or []
    refs = ",".join(f"chat.log#{row.get('sequence')}" for row in gaps
                    if isinstance(row, dict) and row.get("sequence") is not None)
    detail = f" blocked-by={refs}" if refs else ""
    reason = str(last.get("reason", "unspecified"))[:160]
    return [f"OUTCOME WRITER REFUSALS: {len(recent)} in the last 24h "
            f"({len(denials)} coverage denials); last={last.get('role')}/{last.get('kind')}: {reason}{detail}"]




def _load(path: Path, maximum: int) -> dict:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("snapshot exceeds size bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("snapshot is not an object")
    return value


def health(home: Path, *, now=None) -> dict:
    """Route one alert per freshness-failure episode, retaining bad guard state."""
    from .wall import message
    from .initiative import read_snapshot
    from .substrate_watch import _read_state

    home = Path(home).resolve()
    now = now or datetime.now(timezone.utc)
    path = home / "checks/follow-through-health.json"
    save_path = path
    problems = []
    previous = {}
    try:
        previous = _load(path, 65536)
        if previous.get("version") != 1 or previous.get("state") not in {"GREEN", "UNKNOWN"}:
            raise ValueError("invalid guard state")
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as exc:
        problems.append(f"guard state unreadable: {type(exc).__name__}")
        try:
            raw = path.read_bytes()
            retained = home / "artifacts" / ("follow-through-guard-corrupt-" + hashlib.sha256(raw).hexdigest() + ".json")
            retained.parent.mkdir(parents=True, exist_ok=True)
            if not retained.exists():
                retained.write_bytes(raw)
        except OSError:
            problems.append("guard state retention failed")
            # Keep the sole damaged original when it could not be archived.
            save_path = home / "checks/follow-through-health-retention-failed.json"
            try:
                previous = _load(save_path, 65536)
            except (OSError, ValueError, TypeError):
                previous = {}
    producers = {}
    for name, stamp, state_key, maximum_age in (
        ("substrate-watch", "checked_at", "status", 120),
        ("initiative", "generated_at", "state", 180),
    ):
        try:
            report = (_read_state(home / "checks/substrate-watch.json") if name == "substrate-watch"
                      else read_snapshot(home, now=now, max_age=maximum_age))
            at = datetime.fromisoformat(report[stamp].replace("Z", "+00:00"))
            age = (now - at).total_seconds()
            if not 0 <= age <= maximum_age:
                raise ValueError("stale or future producer timestamp")
            producers[name] = {"fresh": True, "reported_state": report[state_key], "age_seconds": age}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            producers[name] = {"fresh": False}
            problems.append(f"{name}: missing, stale, broken or invalid snapshot")
    state = "UNKNOWN" if problems else "GREEN"
    key = hashlib.sha256(json.dumps(problems, sort_keys=True).encode()).hexdigest()
    row = {"version": 1, "checked_at": now.isoformat(), "state": state,
           "producers": producers, "problems": problems,
           "scope": "Producer snapshot freshness only; not successful repairs or runtime health."}
    if problems and previous.get("alert_key") != key:
        event = message(home, "follow-through-watch", "health",
            "Follow-through observation UNKNOWN: " + "; ".join(problems) + ". "
            f"Evidence {save_path.relative_to(home)}. Resolver Health: inspect the owned "
            "substrate/initiative services and retained snapshot errors within 120 seconds; "
            "restore observation, preserving original failure evidence. Do not call missing monitoring success.")
        row["alert_sequence"] = event.sequence
    if problems:
        row["alert_key"] = key
        if "alert_sequence" not in row and previous.get("alert_key") == key:
            row["alert_sequence"] = previous.get("alert_sequence")
    _save(save_path, row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Check producer freshness once")
    parser.add_argument("--watch", action="store_true", help="Independently guard producer freshness")
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("interval must be finite and positive")
    if not (args.check or args.watch):
        print("\nMIND WALL — shared follow-through evidence; not an authored wall")
        print("\n".join(lines(args.home)))
        return 0
    from .session_watch import notify
    notify("READY=1")
    while True:
        row = health(args.home)
        print(json.dumps(row, sort_keys=True), flush=True)
        notify("WATCHDOG=1\nSTATUS=" + row["state"] + " producer freshness only")
        if not args.watch:
            return 0 if row["state"] == "GREEN" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
