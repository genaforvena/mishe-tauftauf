#!/usr/bin/env python3
"""Verify that the session detector's alerts actually reach the chat tape.

`session_watch.route()` writes `health/session-watch-alert.md` and then appends
a DM to `health` through the guarded site CLI. Since the wall-role guard
(`cli._require_site_role`, 2026-10-15T22:27Z) the detector's source label is
refused, so each alert is written to the file and then dropped with exit 2: the
condition stays durable while its resolver never learns of it, and the service
journal is the only trace. This checker is the deterministic reader for that
failure class — it compares the detector's own durable artifacts against the
tape, read-only, and never writes.

Three independent signals:

* **Channel** — the alert file is rewritten on every route attempt, including a
  failed one (retried every 120s). An alert file newer than the newest routed
  `session-watch` DM by more than `--tolerance` seconds means an alert was
  produced and never delivered.
* **Coverage** — a fresh non-GREEN `health/session-watch.json` must have a
  routed `session-watch` DM at or after its RED onset (`changes`, else
  `observed_at`).
* **Episodes** — the detector appends every produced alert to
  `health/session-watch-events.jsonl`, so that tape is the durable record of an
  episode. The newest non-GREEN episode on it must have a routed
  `session-watch` DM at or after its onset (`--tolerance` slack). This is the
  sampling-independent signal: the state file and the alert file are both
  overwritten, so neither shows an episode that recurred and was dropped
  between two checks (the `routed_key` lifetime defect, 2026-10-16).

Decoding stays on the named boundaries: the tape is read with
`feed.parse_feed`/`feed.entry_timestamp` and DM addressing with
`wall.addressed`; this file decodes only the detector's own JSON state record.

Exit status: 0 the newest alert is routed; 2 an alert is unrouted (any
signal); 3 the check cannot decide — tape, state, event tape or alert file
missing, stale or malformed. A stale or GREEN detector never reports success,
because a detector with nothing to route cannot show whether the channel works.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from mishe_tauftauf import feed, wall  # noqa: E402

SOURCE = "session-watch"
RECIPIENT = "health"
STATE_FILE = "health/session-watch.json"
ALERT_FILE = "health/session-watch-alert.md"
EVENTS_FILE = "health/session-watch-events.jsonl"


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="microseconds")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _routed_dms(entries: list, home: Path) -> tuple[list[tuple[float, int]], int]:
    """Newest routed DMs from the detector, decoded, plus undecodable stamps."""
    routed: list[tuple[float, int]] = []
    undecodable = 0
    for entry in entries:
        if entry.source != SOURCE or not wall.addressed(entry, RECIPIENT):
            continue
        stamp = feed.entry_timestamp(entry)
        if stamp is None:
            undecodable += 1
            continue
        routed.append((stamp.timestamp(), entry.sequence))
    routed.sort()
    return routed, undecodable


def _state(home: Path) -> tuple[dict | None, str | None]:
    path = home / STATE_FILE
    if not path.is_file():
        return None, f"detector state missing: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"detector state unreadable: {exc}"
    if not isinstance(data, dict):
        return None, f"detector state is not an object: {path}"
    return data, None


def _episodes(home: Path) -> tuple[list[tuple[str, str, float]], str | None]:
    """Non-GREEN episodes from the detector's own event tape, oldest first.

    Each entry is ``(state, reason, onset)``; ``onset`` is the first row's
    ``observed_at`` in a contiguous run of one ``(state, reason)``, and a GREEN
    row ends the run. The tape is the only durable record of an alert the
    detector *produced*: the state file and the alert file are both overwritten,
    so neither can show an episode that was produced and then dropped.
    """
    path = home / EVENTS_FILE
    if not path.is_file():
        return [], f"detector event tape missing: {path}"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"detector event tape unreadable: {exc}"
    episodes: list[tuple[str, str, float]] = []
    previous: tuple[str, str] | None = None
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            return [], f"detector event tape line {number} undecodable: {exc}"
        if not isinstance(row, dict):
            return [], f"detector event tape line {number} is not an object"
        state, reason = row.get("state"), row.get("reason")
        if not isinstance(state, str) or not isinstance(reason, str):
            return [], f"detector event tape line {number} has no state/reason strings"
        observed = _number(row.get("observed_at"))
        if observed is None:
            return [], f"detector event tape line {number} carries no usable observed_at"
        if state == "GREEN":
            previous = None
            continue
        if previous != (state, reason):
            episodes.append((state, reason, observed))
        previous = (state, reason)
    return episodes, None


def check(home: Path, now: float, tolerance: float, max_age: float) -> dict:
    report: dict = {"home": str(home), "now": _iso(now), "source": SOURCE,
                    "recipient": RECIPIENT, "tolerance_s": tolerance,
                    "max_age_s": max_age, "failures": [], "exercised": [],
                    "limits": []}

    tape = home / "chat.log"
    if not tape.is_file():
        report["verdict"] = "UNKNOWN"
        report["limits"].append(f"chat tape missing: {tape}")
        report["exit"] = 3
        return report
    try:
        entries = feed.parse_feed(tape.read_bytes(), home=home)
    except (OSError, feed.FeedError) as exc:
        report["verdict"] = "UNKNOWN"
        report["limits"].append(f"chat tape undecodable: {exc}")
        report["exit"] = 3
        return report
    routed, undecodable = _routed_dms(entries, home)
    report["tape_entries"] = len(entries)
    report["routed_dm_count"] = len(routed)
    report["newest_routed"] = ({"sequence": routed[-1][1], "timestamp": _iso(routed[-1][0])}
                               if routed else None)
    if undecodable:
        report["limits"].append(
            f"{undecodable} routed session-watch entries carry no decodable timestamp")
    absent = not routed and undecodable == 0


    alert = home / ALERT_FILE
    alert_mtime = alert.stat().st_mtime if alert.is_file() else None
    report["alert_mtime"] = _iso(alert_mtime) if alert_mtime is not None else None

    # Signal 1 — the channel: the newest alert attempt must be on the tape.
    if alert_mtime is None:
        report["limits"].append(f"no alert file: {alert}")
    elif now - alert_mtime > max_age:
        report["limits"].append(f"alert file stale by {now - alert_mtime:.0f}s; no attempt to read")
    elif absent:
        report["failures"].append(
            f"alert produced {_iso(alert_mtime)} and no session-watch DM to {RECIPIENT} "
            "exists anywhere on the tape")
    elif not routed:
        report["limits"].append("routed session-watch entries exist but none carries a "
                                "decodable timestamp: channel undecided")
    else:
        gap = alert_mtime - routed[-1][0]
        if gap > tolerance:
            report["failures"].append(
                f"alert produced {_iso(alert_mtime)} is {gap:.0f}s newer than the newest routed "
                f"session-watch DM (sequence {routed[-1][1]}, {_iso(routed[-1][0])}): "
                "alert not routed")
        elif gap <= 0:
            report["exercised"].append(
                f"newest alert {_iso(alert_mtime)} routed (sequence {routed[-1][1]}, "
                f"{_iso(routed[-1][0])})")
        else:
            report["limits"].append(
                f"alert {_iso(alert_mtime)} is {gap:.0f}s newer than the newest routed DM, "
                f"within the {tolerance:.0f}s tolerance: undecided")

    # Signal 2 — coverage: a fresh non-GREEN state must have been routed.
    data, state_error = _state(home)
    if data is None:
        report["limits"].append(state_error)
    else:
        observed = _number(data.get("observed_at"))
        kind = data.get("state")
        report["state"] = kind
        report["observed_at"] = _iso(observed) if observed is not None else None
        if observed is None:
            report["limits"].append("detector state carries no usable observed_at")
        elif now - observed > max_age:
            report["limits"].append(
                f"detector state stale by {now - observed:.0f}s; routing not exercised")
        elif kind == "GREEN":
            report["limits"].append("detector GREEN: nothing to route, channel not exercised")
        else:
            changes = [_number(value) for value in data.get("changes") or []]
            stamps = [value for value in changes if value is not None]
            onset = max(stamps) if stamps else observed
            report["red_onset"] = _iso(onset)
            if routed and routed[-1][0] >= onset:
                report["exercised"].append(
                    f"state {kind} since {_iso(onset)} routed (sequence {routed[-1][1]}, "
                    f"{_iso(routed[-1][0])})")
            elif routed or absent:
                report["failures"].append(
                    f"state {kind} since {_iso(onset)} has no routed session-watch DM at or "
                    "after it")
            else:
                report["limits"].append(
                    f"state {kind} since {_iso(onset)}: routed entries carry no decodable "
                    "timestamp: coverage undecided")

    # Signal 3 — episodes: the event tape is the durable record of an alert the
    # detector produced. Signals 1 and 2 both read files the detector overwrites,
    # so neither sees an episode that recurred and was dropped between checks.
    episodes, episode_error = _episodes(home)
    if episode_error:
        report["limits"].append(episode_error)
    elif not episodes:
        report["limits"].append("no non-GREEN episode on the detector event tape: "
                                "episode coverage not exercised")
    else:
        episode_state, episode_reason, onset = episodes[-1]
        report["newest_episode"] = {"state": episode_state, "reason": episode_reason,
                                    "onset": _iso(onset)}
        if routed and routed[-1][0] >= onset - tolerance:
            report["exercised"].append(
                f"newest episode {episode_state} since {_iso(onset)} routed "
                f"(sequence {routed[-1][1]}, {_iso(routed[-1][0])})")
        elif routed or absent:
            report["failures"].append(
                f"newest episode {episode_state} ({episode_reason}) since {_iso(onset)} has "
                "no routed session-watch DM at or after it: alert produced and never delivered")
        else:
            report["limits"].append(
                f"newest episode {episode_state} since {_iso(onset)}: routed entries carry "
                "no decodable timestamp: episode coverage undecided")
        if len(episodes) > 1:
            report["limits"].append(
                f"{len(episodes) - 1} earlier non-GREEN episode(s) on the event tape are "
                "not re-checked; they are historical evidence, not this verdict")

    if report["failures"]:
        report["verdict"] = "RED"
        report["exit"] = 2
    elif report["exercised"]:
        report["verdict"] = "GREEN"
        report["exit"] = 0
        report["limits"].append(
            "success covers the alerts named above only; it does not prove the channel is "
            "live now")
    else:
        report["verdict"] = "UNKNOWN"
        report["exit"] = 3
    return report


def render(report: dict) -> str:
    lines = [f"session-watch routing: {report['verdict']} (exit {report['exit']})",
             f"home: {report['home']}  now: {report['now']}"]
    for key in ("state", "observed_at", "red_onset", "newest_episode", "alert_mtime",
                "newest_routed"):
        if key in report:
            lines.append(f"{key}: {report[key]}")
    for label, key in (("FAIL", "failures"), ("PASS", "exercised"), ("LIMIT", "limits")):
        for text in report[key]:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="site home to audit")
    parser.add_argument("--tolerance", type=float, default=60.0,
                        help="seconds an alert may precede its routed DM (default 60)")
    parser.add_argument("--max-age", type=float, default=600.0,
                        help="staleness bound for the state and alert files (default 600)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    home = args.home.resolve()
    if not home.is_dir():
        print(f"session-watch routing: UNKNOWN (exit 3)\nhome is not a directory: {home}")
        return 3
    report = check(home, time.time(), args.tolerance, args.max_age)
    print(json.dumps(report, indent=1, sort_keys=True) if args.json else render(report))
    return report["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
