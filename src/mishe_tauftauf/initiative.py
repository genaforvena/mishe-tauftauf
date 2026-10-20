"""Bounded follow-through observations, not a courage or productivity score.

The rolling window covers recorded events; patches and predictions are current
state reconstructed from the available history. Nothing here infers intent from
prose, equates a changed settlement with a fixed bug, or creates obligations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import islice
import json
import math
import os
from pathlib import Path
import stat
import time

from .activity import roles as registered_roles
from .chat_protocol import ProtocolError, decode_lifecycle
from .feed import parse_feed
from .outcome_events import classify_outcomes
from .outcome_publication import eligible_evidence_path
from .post_check import _save
from .predictions import PLAN_RE, STATUS_RE, replay_predictions
from .records import MAX_BYTES
from .wall_patch import verified_delivery

WINDOW_SECONDS = 86400
MAX_TAPE_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 250000
MAX_PATCHES = 512
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 128 * 1024 * 1024
SNAPSHOT_MAX_AGE = 900

LIMITS = [
    "Counts describe recorded evidence, not initiative, courage, productivity or bugs fixed.",
    "Settlements are reported turn results; changed and verified are not independent acceptance.",
    "Outcomes are author reports with currently digest-checked evidence, not semantic acceptance.",
    "Prediction attribution is exact role/channel identity, not proof of the submitting person's identity.",
    "Patch records do not record a role: all patch attribution remains UNKNOWN; no filename/prose inference.",
    "Patch state is the latest saved scoped exercise, not permanent runtime health or a complete attempt history.",
    "Open references are existing unfinished records, not a new task ledger. Absence is not success.",
    "Window counts exclude older events; current gaps may predate the window. Truncated/missing history is UNKNOWN.",
]


def _time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp unavailable")
    if result.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return result.astimezone(timezone.utc)


def _read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("evidence is not a regular file")
        if info.st_size > maximum:
            raise ValueError(f"evidence exceeds {maximum} bytes")
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"evidence exceeds {maximum} bytes")
    return data


def _unknown(reason):
    return {"state": "UNKNOWN", "reason": str(reason)}


def _age(value, now):
    try:
        age = (now - _time(value)).total_seconds()
        return age if age >= 0 else None
    except (ValueError, TypeError):
        return None


def _entries(home, supplied):
    try:
        if supplied is None:
            if not (home / "chat.log").is_file():
                raise ValueError("chat tape unavailable")
            entries = parse_feed(_read(home / "chat.log", MAX_TAPE_BYTES), home=home)
        else:
            entries = list(islice(iter(supplied), MAX_ENTRIES + 1))
        if len(entries) > MAX_ENTRIES:
            raise ValueError(f"history exceeds {MAX_ENTRIES} entry bound")
        # Callers may provide detached FeedEntry objects; records still belong to
        # this site. Never rescan Feed when a snapshot was supplied.
        entries = [replace(entry, home=home) if entry.home != home else entry for entry in entries]
        coverage = {"state": "OBSERVED", "source": "supplied" if supplied is not None else "chat.log"}
        if entries and (entries[0].sequence != 1 or any(
                right.sequence != left.sequence + 1 for left, right in zip(entries, entries[1:]))):
            coverage.update(state="PARTIAL", reason="available history is not contiguous from sequence one; earlier gaps UNKNOWN")
        return entries, coverage
    except (OSError, ValueError) as exc:
        return [], _unknown(exc)


def _patches(home, now):
    directory = home / "patches"
    if not directory.is_dir():
        return {**_unknown("patch store unavailable"), "items": [], "counts": None}
    try:
        paths = list(islice(directory.glob("*.json"), MAX_PATCHES + 1))
        if len(paths) > MAX_PATCHES:
            raise ValueError(f"patch store exceeds {MAX_PATCHES} record bound")
    except (OSError, ValueError) as exc:
        return {**_unknown(exc), "items": [], "counts": None}
    items, counts = [], Counter()
    for path in sorted(paths):
        item = {"ref": f"patches/{path.name}", "id": path.stem, "role": None,
                "attribution": "UNKNOWN"}
        try:
            record = json.loads(_read(path, MAX_BYTES))
            if not isinstance(record, dict) or not isinstance(record.get("phase"), str):
                raise ValueError("patch phase unavailable")
            phase = record["phase"]
            if phase not in {"prepared", "test-unavailable", "test-failed", "test-mutated-source",
                             "review-unavailable", "review-refused", "reviewed", "applying", "applied",
                             "reverted", "reverted-activation-failed", "revert-failed", "revert-observation-failed"}:
                raise ValueError(f"unrecognized patch phase: {phase}")
            verification = record.get("verification")
            verified_at = verification.get("at") if isinstance(verification, dict) else None
            checked = phase == "applied" and verified_delivery(record) and _age(verified_at, now) is not None
            attempted = (phase in {"applying", "applied"} or phase.startswith("revert")
                         or "activation" in record or record.get("delivery_verified") is False)
            test = record.get("test")
            test_code = test.get("code") if isinstance(test, dict) else None
            if not isinstance(test_code, int) or isinstance(test_code, bool):
                test_code = None
            failures = {key: record[key] for key in ("failure", "apply_failure", "rollback_failure", "verification_failure")
                        if record.get(key)}
            failed = bool(failures) or "failed" in phase or phase in {
                "test-unavailable", "review-unavailable", "review-refused", "test-mutated-source"}
            # A completed revert is a disposition, not a standing obligation to
            # deliver an abandoned experiment. Failed recovery remains open.
            open_gap = not checked and phase != "reverted"
            disposition = "CHECKED" if checked else "REVERTED" if phase == "reverted" else "OPEN"
            item.update(phase=phase, state="OBSERVED", prepared_at=record.get("prepared_at"),
                        age_seconds=_age(record.get("prepared_at"), now),
                        attempt_recorded=attempted, test_code=test_code, failures=failures,
                        verification="RECORDED" if checked else "NOT_VERIFIED" if record.get("delivery_verified") is False else "UNKNOWN",
                        verified_at=verified_at, disposition=disposition, open_delivery_gap=open_gap)
            counts["attempt_recorded"] += int(attempted)
            counts["checked_delivery"] += int(checked)
            counts["open_delivery_gap"] += int(open_gap)
            counts["reverted"] += int(phase == "reverted")
            counts["failed"] += int(failed)
        except (OSError, ValueError, TypeError) as exc:
            item.update(_unknown(exc), phase=None, age_seconds=None, open_delivery_gap=None)
            counts["unknown"] += 1
        items.append(item)
    return {"state": "PARTIAL" if counts["unknown"] else "OBSERVED", "items": items,
            "counts": {key: counts[key] for key in ("attempt_recorded", "checked_delivery", "open_delivery_gap", "reverted", "failed", "unknown")},
            "scope": "current saved patch states, all ages; dimensions overlap; role attribution UNKNOWN"}


def collect(home, *, now=None, entries=None):
    """Return JSON-safe evidence with a rolling 24h window and current open gaps.

    Reads at most 128 MiB/250,000 tape entries, 512 one-MiB patch records,
    four MiB per outcome evidence file (128 MiB total), and one-MiB prediction
    plans (128 MiB total). Supplied entries are consumed once.
    Exceeding a bound is explicitly UNKNOWN, never an empty successful sample.
    """
    home = Path(home).resolve()
    now = _time(now or datetime.now(timezone.utc))
    start = now - timedelta(seconds=WINDOW_SECONDS)
    tape, tape_state = _entries(home, entries)
    valid, invalid = [], []
    for entry in tape:
        try:
            stamp = _time(entry.timestamp)
            if stamp > now:
                raise ValueError("event is future-dated")
            valid.append((entry, stamp))
        except (ValueError, TypeError) as exc:
            invalid.append({"ref": f"chat.log#{entry.sequence}", "reason": str(exc)})
    if invalid and tape_state["state"] != "UNKNOWN":
        tape_state = {"state": "PARTIAL", "reason": "invalid/future event times", "gaps": invalid}
    role_names = set(registered_roles(home))
    role_data = {}

    def role(name):
        role_names.add(name)
        if name not in role_data:
            role_data[name] = {"state": "UNKNOWN", "wakes": 0, "settlements": {}, "open_wakes": [],
                               "lifecycle_gaps": [], "outcomes": {}, "outcome_gaps": [], "predictions": []}
        return role_data[name]

    pending, lifecycle_gaps = {}, []
    window_entries = []
    for entry, stamp in valid:
        if stamp >= start:
            window_entries.append(entry)
        try:
            control = decode_lifecycle(entry)
        except ProtocolError as exc:
            lifecycle_gaps.append({"ref": f"chat.log#{entry.sequence}", "reason": str(exc)})
            continue
        if control is None:
            continue
        row = role(control.role)
        if control.kind == "wake":
            if control.role in pending:
                previous, _ = pending[control.role]
                row["lifecycle_gaps"].append({"ref": f"chat.log#{previous.sequence}",
                                              "reason": "wake superseded without a matching settlement"})
            pending[control.role] = (entry, stamp)
            if stamp >= start:
                row["wakes"] += 1
        elif control.kind == "yield":
            original = pending.get(control.role)
            if original is None or original[0].sequence != control.wake:
                row["lifecycle_gaps"].append({"ref": f"chat.log#{entry.sequence}", "wake": control.wake,
                                              "reason": "no matching pending wake in available history"})
                continue
            pending.pop(control.role)
            if stamp >= start:
                result = control.result or "UNKNOWN"
                row["settlements"][result] = row["settlements"].get(result, 0) + 1
    for name, (entry, stamp) in pending.items():
        role(name)["open_wakes"].append({"ref": f"chat.log#{entry.sequence}", "timestamp": entry.timestamp,
                                         "age_seconds": (now - stamp).total_seconds()})
    outcomes = classify_outcomes(home, start, entries=window_entries)
    if tape_state["state"] == "UNKNOWN":
        outcomes["unavailable"] = tape_state["reason"]
    outcome_refs, evidence_cache = [], {}
    evidence_bytes = 0
    for event in outcomes["events"]:
        evidence = event["evidence"]
        key = (evidence["path"], evidence["sha256"])
        if key not in evidence_cache:
            try:
                if not eligible_evidence_path(home, evidence["path"]):
                    raise ValueError("outcome evidence path is not eligible")
                path = Path(evidence["path"])
                budget = min(MAX_EVIDENCE_BYTES, MAX_TOTAL_EVIDENCE_BYTES - evidence_bytes)
                if budget <= 0:
                    raise ValueError("total outcome evidence byte budget exhausted")
                data = _read(path if path.is_absolute() else home / path, budget)
                evidence_bytes += len(data)
                if not data or hashlib.sha256(data).hexdigest() != evidence["sha256"]:
                    raise ValueError("outcome evidence empty or changed")
                evidence_cache[key] = {"state": "OBSERVED"}
            except (OSError, ValueError) as exc:
                evidence_cache[key] = _unknown(exc)
        item = {**event, **evidence_cache[key], "ref": f"chat.log#{event['sequence']}"}
        outcome_refs.append(item)
        row = role(event["role"])
        if item["state"] == "OBSERVED":
            row["outcomes"][event["kind"]] = row["outcomes"].get(event["kind"], 0) + 1
        else:
            row["outcome_gaps"].append(item)
    source_by_sequence = {entry.sequence: entry.source for entry in window_entries}
    for gap in outcomes["incomplete"]:
        name = source_by_sequence.get(gap["sequence"])
        if name in role_names:
            role(name)["outcome_gaps"].append({**gap, "state": "UNKNOWN", "ref": f"chat.log#{gap['sequence']}"})
    prediction_entries = [entry for entry, _ in valid]
    predictions, prediction_gaps = [], []
    statuses = {}
    for entry in prediction_entries:
        if entry.source == "mishe-tauftauf":
            for sequence, status in STATUS_RE.findall(entry.body):
                statuses.setdefault(int(sequence), set()).add(status)
    replay_entries, plan_gaps, plan_bytes = [], {}, 0
    for entry in prediction_entries:
        plans = PLAN_RE.findall(entry.body) if entry.source.startswith("prediction/") else []
        if len(plans) == 1:
            try:
                candidate = (home / plans[0]).resolve()
                if candidate == home or not candidate.is_relative_to(home):
                    raise ValueError("prediction plan is outside home")
                budget = min(MAX_BYTES, MAX_TOTAL_EVIDENCE_BYTES - plan_bytes)
                if budget <= 0:
                    raise ValueError("prediction plan byte budget exhausted")
                data = _read(candidate, budget)
                plan_bytes += len(data)
                data.decode("utf-8")
            except (OSError, ValueError) as exc:
                plan_gaps[entry.sequence] = str(exc)
                continue
        replay_entries.append(entry)
    try:
        replayed = replay_predictions(home, replay_entries)
        for entry in prediction_entries:
            if not entry.source.startswith("prediction/"):
                continue
            prediction = replayed.get(entry.sequence)
            channel = entry.source.split("/", 1)[1]
            item = {"ref": f"chat.log#{entry.sequence}", "channel": channel,
                    "role": channel if channel in role_names else None}
            if prediction is None:
                item.update(_unknown(plan_gaps.get(entry.sequence, "prediction cannot be replayed; check time or plan evidence unavailable")))
                prediction_gaps.append(item)
            else:
                item.update(state="OBSERVED", check_at=prediction.check_at.isoformat(),
                            accepted=prediction.accepted, terminal=prediction.terminal,
                            statuses=sorted(statuses.get(entry.sequence, set())),
                            replaced_by=prediction.replaced_by, desired_observed=prediction.desired_observed,
                            open=prediction.accepted and not prediction.terminal and prediction.replaced_by is None,
                            overdue_seconds=max(0, (now - prediction.check_at).total_seconds()))
            predictions.append(item)
            if item["role"] is not None:
                role(item["role"])["predictions"].append(item)
    except (OSError, ValueError) as exc:
        prediction_gaps.append(_unknown(exc))
    patches = _patches(home, now)
    for name in sorted(role_names):
        row = role(name)
        observed = bool(row["wakes"] or row["settlements"] or row["open_wakes"] or row["outcomes"]
                        or any(item["state"] == "OBSERVED" for item in row["predictions"]))
        gaps = bool(row["lifecycle_gaps"] or row["outcome_gaps"] or "UNKNOWN" in row["settlements"]
                    or any(item["state"] == "UNKNOWN" for item in row["predictions"]) or tape_state["state"] != "OBSERVED")
        row["state"] = "PARTIAL" if observed and gaps else "OBSERVED" if observed else "UNKNOWN"
        row["outcome_coverage"] = ("UNKNOWN" if outcomes["unavailable"] else "PARTIAL"
                                   if row["outcome_gaps"] or tape_state["state"] != "OBSERVED" else "OBSERVED")
        row["patch_attribution"] = "UNKNOWN"
    partial = (tape_state["state"] != "OBSERVED" or outcomes["unavailable"] or outcomes["incomplete"]
               or any(item["state"] == "UNKNOWN" for item in outcome_refs) or prediction_gaps or lifecycle_gaps
               or any(row["lifecycle_gaps"] or "UNKNOWN" in row["settlements"] for row in role_data.values())
               or patches["state"] != "OBSERVED")
    return {"version": 1, "generated_at": now.isoformat(),
            "state": "UNKNOWN" if tape_state["state"] == "UNKNOWN" else "PARTIAL" if partial else "OBSERVED",
            "window": {"start": start.isoformat(), "end": now.isoformat(), "seconds": WINDOW_SECONDS,
                       "scope": "recorded wake, settlement and outcome events in the last 24h; no rate/score"},
            "history": {**tape_state, "entries": len(valid), "window_entries": len(window_entries),
                        "first_sequence": valid[0][0].sequence if valid else None,
                        "last_sequence": valid[-1][0].sequence if valid else None,
                        "first_at": min(stamp for _, stamp in valid).isoformat() if valid else None,
                        "scope": "available tape snapshot, bounded; current open wakes/predictions use this history"},
            "roles": {name: role_data[name] for name in sorted(role_data)},
            "lifecycle_gaps": lifecycle_gaps,
            "outcomes": {"state": "UNKNOWN" if outcomes["unavailable"] else "PARTIAL" if outcomes["incomplete"] or tape_state["state"] != "OBSERVED" or any(item["state"] == "UNKNOWN" for item in outcome_refs) else "OBSERVED",
                         "reason": outcomes["unavailable"], "items": outcome_refs, "gaps": outcomes["incomplete"]},
            "predictions": {"state": "UNKNOWN" if tape_state["state"] == "UNKNOWN" else "PARTIAL" if prediction_gaps or tape_state["state"] != "OBSERVED" else "OBSERVED",
                            "items": predictions, "gaps": prediction_gaps,
                            "scope": "current canonical replay of available history; role/channel attribution only"},
            "patches": patches, "limits": LIMITS}


def _lines(report):
    result = [f"FOLLOW-THROUGH: {report['state']} — recorded last 24h {report['window']['start']}..{report['window']['end']}"]
    if report["history"]["state"] != "OBSERVED":
        result.append("History UNKNOWN/partial: " + report["history"].get("reason", "unavailable"))
    visible_roles = [(name, row) for name, row in report["roles"].items()
                     if row["state"] != "UNKNOWN" or row["lifecycle_gaps"] or row["outcome_gaps"]]
    for name, row in visible_roles[:16]:
        settled = ",".join(f"{key}={value}" for key, value in sorted(row["settlements"].items())) or "none recorded"
        outcomes = ",".join(f"{key}={value}" for key, value in sorted(row["outcomes"].items())) or "none recorded"
        result.append(f"{name}: {row['state']}; wakes={row['wakes']} settled[{settled}] outcomes[{outcomes}] evidence-UNKNOWN={len(row['outcome_gaps'])}")
        for item in row["open_wakes"]:
            result.append(f"  open wake {item['ref']} age={item['age_seconds']:.0f}s")
    if len(visible_roles) > 16:
        result.append(f"  {len(visible_roles) - 16} more roles in checks/initiative.json")
    unknown_roles = len(report["roles"]) - len(visible_roles)
    if unknown_roles:
        result.append(f"Attribution UNKNOWN for {unknown_roles} roles with no usable observations in this window/current gaps.")
    predictions = [item for item in report["predictions"]["items"] if item.get("open") or item["state"] == "UNKNOWN"]
    for item in predictions[:8]:
        result.append(f"Prediction {item['ref']} channel={item['channel']} role={item['role'] or 'UNKNOWN'}: "
                      + (f"open; overdue={item['overdue_seconds']:.0f}s" if item.get("open") else "UNKNOWN"))
    if len(predictions) > 8:
        result.append(f"  {len(predictions) - 8} more prediction gaps in checks/initiative.json")
    patches = report["patches"]
    if patches["counts"] is None:
        result.append("Patches: UNKNOWN — " + patches["reason"])
    else:
        counts = patches["counts"]
        result.append(f"Patches current/all ages: attempted={counts['attempt_recorded']} checked-delivery={counts['checked_delivery']} "
                      f"open-gaps={counts['open_delivery_gap']} reverted={counts['reverted']} failed={counts['failed']} UNKNOWN={counts['unknown']}; role UNKNOWN")
    patch_items = [item for item in patches["items"] if item["state"] == "UNKNOWN"
                   or item["open_delivery_gap"] or item.get("disposition") == "REVERTED"]
    for item in patch_items[:8]:
        age = f"{item['age_seconds']:.0f}s" if item["age_seconds"] is not None else "UNKNOWN"
        result.append(f"  {item['ref']} phase={item['phase'] or 'UNKNOWN'} age={age} verification={item.get('verification', 'UNKNOWN')}")
    if len(patch_items) > 8:
        result.append(f"  {len(patch_items) - 8} more patch records in checks/initiative.json")
    if report["outcomes"]["state"] != "OBSERVED":
        result.append("Outcome coverage: " + report["outcomes"]["state"] + " — "
                      + (report["outcomes"]["reason"] or "missing, changed or invalid evidence; inspect snapshot gaps"))
    result.append("Limits: recorded attempts/results only; changed != fixed; absent evidence UNKNOWN; no score or patch-role attribution.")
    return result


def lines(home, *, now=None, entries=None):
    """Collect once and render concise human observations."""
    return _lines(collect(home, now=now, entries=entries))


def read_snapshot(home, *, now=None, max_age=SNAPSHOT_MAX_AGE):
    """Return a validated fresh cached report, without observing the tape.

    Missing files raise OSError; invalid, stale or future reports raise ValueError.
    UNKNOWN/PARTIAL are valid observations, not certificates of source health.
    """
    try:
        if isinstance(max_age, bool) or not isinstance(max_age, (int, float)) or not math.isfinite(max_age) or max_age < 0:
            raise ValueError("snapshot max_age must be finite and nonnegative")
        now = _time(datetime.now(timezone.utc) if now is None else now)
        report = json.loads(_read(Path(home) / "checks/initiative.json", MAX_TAPE_BYTES))
        states = {"OBSERVED", "PARTIAL", "UNKNOWN"}

        def require(value, expected, name):
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise ValueError(f"invalid initiative snapshot {name}")

        def state(value, name):
            require(value, str, name)
            if value not in states:
                raise ValueError(f"invalid initiative snapshot {name}")

        def count(value, name):
            require(value, int, name)
            if value < 0:
                raise ValueError(f"negative initiative snapshot {name}")

        require(report, dict, "report")
        require(report.get("version"), int, "version")
        if report["version"] != 1:
            raise ValueError("unsupported initiative snapshot")
        state(report.get("state"), "state")
        generated = _time(report.get("generated_at"))
        age = (now - generated).total_seconds()
        if age < 0 or age > max_age:
            raise ValueError("initiative snapshot stale or future-dated")
        for name in ("window", "history", "roles", "outcomes", "predictions", "patches"):
            require(report.get(name), dict, name)
        for name in ("lifecycle_gaps", "limits"):
            require(report.get(name), list, name)
        for limit in report["limits"]:
            require(limit, str, "limit")
        window = report["window"]
        start, end = _time(window.get("start")), _time(window.get("end"))
        if start > end or end > generated:
            raise ValueError("invalid initiative snapshot window")
        count(window.get("seconds"), "window.seconds")
        require(window.get("scope"), str, "window.scope")
        history = report["history"]
        state(history.get("state"), "history.state")
        for name in ("entries", "window_entries"):
            count(history.get(name), f"history.{name}")
        require(history.get("scope"), str, "history.scope")
        for name, row in report["roles"].items():
            require(name, str, "role name")
            require(row, dict, f"role {name}")
            state(row.get("state"), f"role {name}.state")
            state(row.get("outcome_coverage"), f"role {name}.outcome_coverage")
            count(row.get("wakes"), f"role {name}.wakes")
            for key in ("settlements", "outcomes"):
                require(row.get(key), dict, f"role {name}.{key}")
                for label, value in row[key].items():
                    require(label, str, f"role {name}.{key} label")
                    count(value, f"role {name}.{key}.{label}")
            for key in ("open_wakes", "lifecycle_gaps", "outcome_gaps", "predictions"):
                require(row.get(key), list, f"role {name}.{key}")
                for item in row[key]:
                    require(item, dict, f"role {name}.{key} item")
        for name in ("outcomes", "predictions", "patches"):
            section = report[name]
            state(section.get("state"), f"{name}.state")
            require(section.get("items"), list, f"{name}.items")
            for item in section["items"]:
                require(item, dict, f"{name}.item")
                state(item.get("state"), f"{name}.item.state")
                require(item.get("ref"), str, f"{name}.item.ref")
                if name == "predictions":
                    require(item.get("channel"), str, "prediction channel")
                    if item["state"] == "OBSERVED":
                        for key in ("accepted", "terminal", "desired_observed", "open"):
                            require(item.get(key), bool, f"prediction {key}")
                        _time(item.get("check_at"))
                elif name == "outcomes":
                    for key in ("kind", "role", "record_sha256", "timestamp"):
                        require(item.get(key), str, f"outcome {key}")
                    require(item.get("evidence"), dict, "outcome evidence")
                else:
                    require(item.get("id"), str, "patch id")
                    if item["state"] != "UNKNOWN":
                        require(item.get("phase"), str, "patch phase")
                        require(item.get("attempt_recorded"), bool, "patch attempt_recorded")
                        require(item.get("open_delivery_gap"), bool, "patch open_delivery_gap")
                        require(item.get("verification"), str, "patch verification")
            if name != "patches":
                require(section.get("gaps"), list, f"{name}.gaps")
                for item in section["gaps"]:
                    require(item, dict, f"{name}.gap")
        patches = report["patches"]
        if "counts" not in patches:
            raise ValueError("initiative patch counts missing")
        if patches.get("counts") is None:
            if patches["state"] != "UNKNOWN":
                raise ValueError("initiative patch counts unavailable without UNKNOWN state")
            require(patches.get("reason"), str, "patches.reason")
        else:
            require(patches["counts"], dict, "patches.counts")
            for key in ("attempt_recorded", "checked_delivery", "open_delivery_gap", "reverted", "failed", "unknown"):
                count(patches["counts"].get(key), f"patches.counts.{key}")
        # JSON permits NaN/Infinity and overflowed exponents by default. They
        # must not evade freshness/age checks or render as successful counts.
        pending = [report]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("non-finite initiative snapshot value")
        _lines(report)
        return report
    except (TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ValueError(f"invalid initiative snapshot: {exc}") from exc


def snapshot_lines(home, *, now=None, max_age=SNAPSHOT_MAX_AGE):
    """Read checks/initiative.json only; stale/missing/corrupt is UNKNOWN."""
    try:
        return _lines(read_snapshot(home, now=now, max_age=max_age))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return [f"FOLLOW-THROUGH: UNKNOWN — checks/initiative.json unavailable: {exc}"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="print JSON instead of human lines")
    parser.add_argument("--watch", action="store_true", help="refresh the saved report until interrupted")
    parser.add_argument("--interval", type=float, default=60, help="seconds between snapshots (default: 60)")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be positive and finite")
    while True:
        report = collect(args.home)
        _save(args.home / "checks/initiative.json", report)
        print(json.dumps(report, sort_keys=True) if args.json else "\n".join(_lines(report)), flush=True)
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
