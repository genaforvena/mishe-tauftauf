"""Bounded follow-through observations, not a courage or productivity score.

The rolling window covers recorded events; patches and predictions are current
state reconstructed from the available history. Nothing here infers intent from
prose, equates a changed settlement with a fixed bug, or creates obligations.
"""
from __future__ import annotations

from typing import Final

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
from .outcome_events import blocking_gaps, classify_outcomes
from .outcome_publication import eligible_evidence_path, remedy_successor
from .post_check import _save
from .predictions import PLAN_RE, STATUS_RE, replay_predictions
from .records import MAX_BYTES
from .wall_patch import verified_delivery

EVIDENCE_BINDING_CLAUSE_TIME = datetime(2026, 10, 6, 16, 22, 11, tzinfo=timezone.utc)
"""The write-once outcome clause landed in 3c22087; earlier rows are not bound."""

WINDOW_SECONDS = 86400
MAX_TAPE_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 250000
MAX_PATCHES = 512
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 128 * 1024 * 1024
SNAPSHOT_MAX_AGE = 900

INITIATIVE_ARCHIVE_VERSION: Final[int] = 1
"""DGM archive version — array of all prior generated plant-version snapshots (Zhang et al. 2025 arXiv:2505.22954v2 §3/§4.3), distinct from the rolling 24 h collect() window."""

DGM_EMPIRICAL_VALIDATION: Final[str] = "empirical-benchmark, not proof"
"""Schmidhuber 2007 → DGM §3: provably-beneficial proof replaced by empirical benchmark trial."""

ARCHIVE_MAX_VERSIONS: Final[int] = 512
"""Mirrors MAX_PATCHES; bounded archive of plant-version snapshots."""

STAGED_SUBSETS: Final[tuple[int, ...]] = (10, 50, 200)
"""Staged evaluation subsets from DGM §4.2/Appendix E."""

STAGED_TRIGGER: Final[float] = 0.40
"""Staged trigger 0.40 from DGM §4.2/Appendix E."""


def stepping_stone_score(version: dict) -> float:
    """DGM parent-selection weight: score × (1+children_editing), 0 when not editing-capable.

    Appendix C.2: selection roughly proportional to performance score and number
    of children retaining codebase-editing functionality. Only compiling,
    editing-capable versions branch (§3 "only agents that retain ability to edit
    a codebase are added").
    """
    if version.get("editing_capability") is False:
        return 0.0
    score = version.get("score")
    if score is None:
        score = version.get("performance_score", 0.0)
    try:
        s = float(score)
    except Exception:
        return 0.0
    children = version.get("children_editing")
    if children is None:
        children = version.get("children_with_editing_capability")
    if children is None:
        children = version.get("children", 0)
    try:
        c = int(children)
    except Exception:
        c = 0
    if c < 0:
        c = 0
    return s * (1 + c)


def is_stepping_stone(candidate: dict, archive: list[dict]) -> bool:
    """Fig. 3 lineage predicate: dip-then-jump stepping stone.

    True when candidate's immediate score is below its parent/best reference
    yet some later descendant in the archive exceeds the archive best that was
    present at the candidate's creation. Distinguishes "interesting yet
    suboptimal" from "best so far" (Hughes et al. 2024 cited p. 2).
    """
    cand_score = candidate.get("score")
    if cand_score is None:
        cand_score = candidate.get("performance_score")
    best = candidate.get("best_at_creation")
    if best is None:
        parent_id = candidate.get("parent")
        if parent_id is not None:
            for entry in archive:
                if entry.get("id") == parent_id:
                    best = entry.get("score", entry.get("performance_score"))
                    break
    try:
        cs = float(cand_score) if cand_score is not None else None
        bs = float(best) if best is not None else None
    except Exception:
        return False
    if cs is None or bs is None:
        return False
    is_dip = cs < bs
    if not is_dip:
        return False
    cand_id = candidate.get("id")
    parent_map: dict[str, str] = {}
    score_map: dict[str, float] = {}
    for entry in archive:
        eid = entry.get("id")
        if eid is None:
            continue
        parent_map[str(eid)] = str(entry.get("parent")) if entry.get("parent") is not None else ""
        sc = entry.get("score", entry.get("performance_score"))
        try:
            score_map[str(eid)] = float(sc) if sc is not None else float("nan")
        except Exception:
            continue
    for eid, sc in score_map.items():
        if sc <= bs:
            continue
        cur = eid
        seen: set[str] = set()
        while cur and cur not in seen:
            if cand_id is not None and cur == str(cand_id):
                break
            parent = parent_map.get(cur, "")
            if parent == (str(cand_id) if cand_id is not None else ""):
                return True
            if not parent:
                break
            seen.add(cur)
            cur = parent
        if cand_id is None and sc > bs:
            return True
    return False
LIMITS = [
    "Counts describe recorded evidence, not initiative, courage, productivity or bugs fixed.",
    "Settlements are reported turn results; changed and verified are not independent acceptance.",
    "Outcomes are author reports with currently digest-checked evidence, not semantic acceptance.",
    "An outcome whose own bound file no longer matches its digest is re-evidenced, not checked, when a later outcome of the same role binds the clause's corrected form of it under the remedy naming; the row's own defect and the successor are published beside it, and the evidence-binding gate publishes the successor's byte relation beside the same link.",
    "Prediction attribution is exact role/channel identity, not proof of the submitting person's identity.",
    "Patch records do not record a role: all patch attribution remains UNKNOWN; no filename/prose inference.",
    "Patch state is the latest saved scoped exercise, not permanent runtime health or a complete attempt history.",
    "Open references are existing unfinished records, not a new task ledger. Absence is not success.",
    "newest_checked_evidence is the age of the newest digest-checked outcome evidence in this window: evidence recency, not usefulness, capability or a work quota.",
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
    """Return the tape entries, their coverage, and the instant the bytes were read.

    The instant is taken after the tape bytes were read, never before. The tape
    grows while it is read, so an entry another writer appends during the read
    carries a stamp later than a clock taken before the read; judged against
    that earlier clock such an entry reads as an event from the future.
    """
    try:
        if supplied is None:
            if not (home / "chat.log").is_file():
                raise ValueError("chat tape unavailable")
            raw = _read(home / "chat.log", MAX_TAPE_BYTES)
            snapshot = datetime.now(timezone.utc)
            entries = parse_feed(raw, home=home)
        else:
            snapshot = datetime.now(timezone.utc)
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
        return entries, coverage, snapshot
    except (OSError, ValueError) as exc:
        return [], _unknown(exc), datetime.now(timezone.utc)


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
    tape, tape_state, snapshot = _entries(home, entries)
    # A caller that pins the instant pins the whole cycle: the pinned instant
    # still judges the tape. An unpinned clock is the instant the tape bytes
    # were read, so an entry another writer appended during the read carries a
    # stamp that is not later than the clock and is read as the real entry it
    # is, not as an event from the future.
    now = _time(now if now is not None else snapshot)
    start = now - timedelta(seconds=WINDOW_SECONDS)
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
                               "lifecycle_gaps": [], "outcomes": {}, "outcome_gaps": [],
                               "re_evidenced_outcomes": [], "predictions": [],
                               "newest_checked_evidence": None}
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
                if not data:
                    raise ValueError("outcome evidence empty")
                observed = hashlib.sha256(data).hexdigest()
                if observed != evidence["sha256"]:
                    raise ValueError(
                        f"outcome evidence digest changed: recorded {evidence['sha256']} observed {observed}")
                evidence_cache[key] = {"state": "OBSERVED"}
            except (OSError, ValueError) as exc:
                evidence_cache[key] = _unknown(exc)

    def re_evidenced_by(event):
        """A later outcome of the same role that re-evidences this one.

        The clause's remedy for an already-bound artifact that was edited after
        publication is a new artifact under the lost artifact's own stem plus a
        remedy suffix, cited in a new outcome. The reader therefore recognises
        the plant's own relation instead of reporting a coverage gap for work
        the plant already replaced.

        The relation is decidable without reading prose: the successor's path
        names the lost artifact's corrected form. Three conditions keep it
        honest, and each is required:

        * the successor is **later** than the row it re-evidences, ordered by
          the event's own timestamp with the tape sequence as the tie-breaker,
          so a backdated publication cannot act as a future repair;
        * the successor is of the **same role**, so one role's outcome cannot
          re-evidence another's;
        * the successor's own evidence is **intact** — digest-checked — so a
          broken successor moves the loss instead of replacing it.

        The row's own evidence defect is not erased: the item keeps its
        original state and reason beside the successor that re-evidenced it.
        """
        lost_path = Path(event["evidence"]["path"])
        lost_order = (_time(event["timestamp"]), event["sequence"])
        for candidate in outcomes["events"]:
            if candidate["role"] != event["role"]:
                continue
            if (_time(candidate["timestamp"]), candidate["sequence"]) <= lost_order:
                continue
            if not remedy_successor(lost_path, Path(candidate["evidence"]["path"])):
                continue
            state = evidence_cache.get((candidate["evidence"]["path"], candidate["evidence"]["sha256"]))
            if state is None or state.get("state") != "OBSERVED":
                continue
            return candidate
        return None

    for event in outcomes["events"]:
        evidence = event["evidence"]
        key = (evidence["path"], evidence["sha256"])
        state = evidence_cache[key]
        item = {**event, **state, "ref": f"chat.log#{event['sequence']}"}
        successor = None
        if item["state"] != "OBSERVED":
            successor = re_evidenced_by(event)
            if successor is not None:
                item = {**item, "state": "OBSERVED", "link_kind": "remedy-name",
                        "re_evidenced_by": f"chat.log#{successor['sequence']}",
                        "own_evidence_state": state["state"],
                        "own_evidence_reason": state.get("reason")}
        outcome_refs.append(item)
        row = role(event["role"])
        if item["state"] == "OBSERVED" and successor is None:
            row["outcomes"][event["kind"]] = row["outcomes"].get(event["kind"], 0) + 1
            age = (now - _time(item["timestamp"])).total_seconds()
            if row["newest_checked_evidence"] is None or age < row["newest_checked_evidence"]:
                row["newest_checked_evidence"] = age
        elif successor is not None:
            row["re_evidenced_outcomes"].append(item)
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
    # Whole-tape blocked-instrument projection: a single binding-relevant gap
    # anywhere on the tape hides the bound evidence set, so the writer must
    # refuse every role. Uses the clause-time window so an old gap still blocks,
    # and only the whole-tape check can see it.
    try:
        whole = classify_outcomes(home, EVIDENCE_BINDING_CLAUSE_TIME, entries=[e for e, _ in valid])
    except (OSError, ValueError) as exc:
        whole = {"unavailable": str(exc), "incomplete": [], "events": []}
    if whole.get("unavailable"):
        outcome_block = {"state": "UNKNOWN", "reason": whole["unavailable"], "gaps": []}
    else:
        blocked = blocking_gaps(whole)
        outcome_block = {"state": "BLOCKED" if blocked else "CLEAR", "reason": None, "gaps": blocked}
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
            "outcome_block": outcome_block,
            "predictions": {"state": "UNKNOWN" if tape_state["state"] == "UNKNOWN" else "PARTIAL" if prediction_gaps or tape_state["state"] != "OBSERVED" else "OBSERVED",
                            "items": predictions, "gaps": prediction_gaps,
                            "scope": "current canonical replay of available history; role/channel attribution only"},
            "patches": patches, "limits": LIMITS}


def _lines(report):
    result = [f"FOLLOW-THROUGH: {report['state']} — recorded last 24h {report['window']['start']}..{report['window']['end']}"]
    if report["history"]["state"] != "OBSERVED":
        result.append("History UNKNOWN/partial: " + report["history"].get("reason", "unavailable"))
    visible_roles = [(name, row) for name, row in report["roles"].items()
                     if row["state"] != "UNKNOWN" or row["lifecycle_gaps"] or row["outcome_gaps"]
                     or row.get("re_evidenced_outcomes")]
    for name, row in visible_roles[:16]:
        settled = ",".join(f"{key}={value}" for key, value in sorted(row["settlements"].items())) or "none recorded"
        outcomes = ",".join(f"{key}={value}" for key, value in sorted(row["outcomes"].items())) or "none recorded"
        checked_age = row.get("newest_checked_evidence")
        recency = (f"checked-evidence-age={checked_age:.0f}s"
                   if isinstance(checked_age, (int, float)) and not isinstance(checked_age, bool)
                   and math.isfinite(checked_age) and checked_age >= 0 else "checked-evidence-age=none")
        result.append(f"{name}: {row['state']}; wakes={row['wakes']} settled[{settled}] outcomes[{outcomes}] {recency} evidence-UNKNOWN={len(row['outcome_gaps'])}")
        for item in row["open_wakes"]:
            result.append(f"  open wake {item['ref']} age={item['age_seconds']:.0f}s")
        for item in row.get("re_evidenced_outcomes", []):
            result.append(f"  outcome {item['ref']} re-evidenced by {item['re_evidenced_by']} "
                          f"({item['link_kind']}); own evidence {item['own_evidence_state']}")
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
    instrument = report.get("outcome_block", {})
    if instrument.get("state") == "BLOCKED":
        for gap in instrument.get("gaps", [])[:8]:
            result.append(f"OUTCOME INSTRUMENT BLOCKED — binding gap at chat.log#{gap['sequence']}: {gap.get('reason','damaged publication')} (outcome-block=chat.log#{gap['sequence']})")
        if len(instrument.get("gaps", [])) > 8:
            result.append(f"  {len(instrument['gaps']) - 8} more blocked gaps in whole-tape projection")
    elif instrument.get("state") == "UNKNOWN":
        result.append("OUTCOME INSTRUMENT UNKNOWN — whole-tape assessment unavailable: " + (instrument.get("reason") or "unavailable"))
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
        report = json.loads(_read(Path(home) / "checks/initiative.json", MAX_TAPE_BYTES))
        # The clock is taken after the read, not before it: a report that the
        # producer wrote while this reader was reading it carries a later
        # stamp than the reader's start, and it was already on disk when the
        # reader saw it. Reading it as future-dated would report a race as a
        # broken producer.
        now = _time(datetime.now(timezone.utc) if now is None else now)
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
