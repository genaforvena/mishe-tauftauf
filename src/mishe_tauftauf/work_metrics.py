"""Fixed-window work facts; absence of evidence never becomes zero productivity."""
from __future__ import annotations
from pathlib import Path
import math
from .state_files import read_json


def _time(record, key):
    value = record.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _before(record, key, end):
    value = _time(record, key)
    return value is not None and value < end


def _during(record, key, start, end):
    value = _time(record, key)
    return value is not None and start <= value < end


def _release_state(record, end):
    state = "not-delivered"
    if ("rollback-started" in record.get("events", []) and _time(record, "rollback_at") is None) or (
        record.get("phase") == "reverted" and _time(record, "reverted_at") is None):
        return "UNKNOWN"
    if _before(record, "activation_started_at", end):
        state = "UNKNOWN"
    if _before(record, "delivered_at", end):
        state = "delivered"
    elif record.get("observed") and _time(record, "delivered_at") is None:
        state = "UNKNOWN"
    for started, completed, failed, outcome in (
        ("rollback_at", "rollback_completed_at", "rollback_failed_at", "rolled-back"),
        ("revert_started_at", "reverted_at", "revert_failed_at", "reverted"),
    ):
        if _before(record, started, end) or _before(record, completed, end) or _before(record, failed, end):
            state = outcome if _before(record, completed, end) else "UNKNOWN"
        elif _time(record, completed) is not None and _time(record, started) is None:
            state = "UNKNOWN"  # an older record cannot locate the start of its later recovery
    return state


def report(home, *, start, end):
    if end <= start: raise ValueError("report end must follow start")
    root = Path(home) / "wakes"
    rows, inventory, missing, unreadable_keys = {}, [], [], set()
    for path in sorted(root.glob("*.work-start.json")):
        try:
            initial = read_json(path)
            if not start <= initial["started_at"] < end: continue
            key = initial["wake_key"]
            row = read_json(root / f"{key}.work.json") or dict(initial, evidence_state="UNKNOWN")
            finished = _time(row, "ended_at")
            if finished is None or finished >= end:
                row = dict(initial, evidence_state="UNKNOWN", pending_at_cutoff=True,
                           later_completed_at=finished, claimed_result=None)
            rows[key] = row
            inventory.append(str(path.relative_to(home)))
        except (ValueError, KeyError, TypeError, OSError):
            from .work_boundary import work_start_key
            missing.append(str(path))
            unreadable_keys.add(work_start_key(path))
    # Historical snapshots have no trustworthy start timestamp in their JSON.
    # Decode the existing key protocol at the boundary; never infer it from prose.
    from .work_boundary import wake_time, snapshot_key
    for path in sorted(root.glob("*.snapshot.json")):
        key = snapshot_key(path)
        stamp = wake_time(key)
        if key in rows or stamp is None or not start <= stamp < end: continue
        receipt = read_json(root / f"{key}.yield.json") or {}
        rows[key] = dict(wake_key=key, started_at=stamp, evidence_state="UNKNOWN",
                         claimed_result=receipt.get("result") if _before(receipt, "settled_at", end) else None)
        inventory.append(str(path.relative_to(home)))
    observed = sorted({sha for r in rows.values() for sha in r.get("commits_observed", [])})
    own = sorted({sha for r in rows.values() for sha in r.get("commits_attributed", [])})
    usage = [r.get("engine_seconds") for r in rows.values()]
    known_usage = sum(x is not None for x in usage)
    unlocated = len(unreadable_keys - set(rows))
    seconds = sum(x for x in usage if x is not None)
    blocked = [r for r in rows.values() if r.get("claimed_result") == "blocked"]
    typed = [r for r in rows.values() if isinstance(r.get("unchanged_wait"), bool)]
    dirty = {}
    for r in sorted(rows.values(), key=lambda r: r["started_at"]):
        for name, value in r.get("dirty_start", {}).items():
            old = dirty.get(name)
            dirty[name] = dict(digest=value, first_seen=old["first_seen"] if old and old["digest"] == value else r["started_at"])
    latencies = [item for r in rows.values() for item in r.get("check_latencies", [])]
    fault_facts, recovery_facts = None, None
    try:
        experiment = read_json(Path(home)/"work/experiment/manifest.json") or {}
        covered = experiment.get("start", end+1) <= start and end <= experiment.get("end", start-1)
        if covered and experiment.get("fault_events_version") == 1:
            faults = []
            for path in sorted((Path(home)/"work/fault-events").glob("*.json")):
                entry = read_json(path)
                if not isinstance(entry, dict) or not isinstance(entry.get("at"), (int, float)) or not isinstance(entry.get("novel"), bool):
                    raise ValueError("malformed fault event")
                if start <= entry["at"] < end:
                    faults.append(entry); inventory.append(str(path.relative_to(home)))
            fault_facts = dict(fresh=sum(x["novel"] for x in faults), repeated=sum(not x["novel"] for x in faults),
                               coverage="KNOWN", scope="observed failed CLI callbacks")
            from . import signal_store
            if "work-accounting" in signal_store.registry(home):
                accounting = signal_store.read(home, "work-accounting", now=end)
                if accounting.state != "OK" or accounting.value != "GREEN":
                    fault_facts["coverage"] = "UNKNOWN"
        if covered and experiment.get("recovery_events_version") == 1:
            releases, states = [], []
            for path in sorted((Path(home)/"work/releases").glob("*.json")):
                entry = read_json(path)
                if not isinstance(entry, dict) or _time(entry, "created_at") is None:
                    raise ValueError("release record lacks factual event time")
                event_keys = ("created_at", "delivered_at", "rollback_at", "rollback_completed_at", "rollback_failed_at",
                              "revert_started_at", "reverted_at", "revert_failed_at", "reviewed_at")
                if any(_during(entry, key, start, end) for key in event_keys):
                    state = _release_state(entry, end)
                    releases.append(entry); states.append(dict(sha=entry["sha"], state=state, record=str(path.relative_to(home))))
                    inventory.append(str(path.relative_to(home)))
            delivered = [x for x in releases if _release_state(x, end) == "delivered"]
            delivery_events = [x for x in releases if _during(x, "delivered_at", start, end)]
            recovery_facts = dict(
                deliveries=len(delivery_events),
                automatic_rollbacks=sum(_during(x, "rollback_completed_at", start, end) for x in releases),
                explicit_reverts=sum(_during(x, "reverted_at", start, end) for x in releases),
                failed_recoveries=sum(_during(x, "rollback_failed_at", start, end) or _during(x, "revert_failed_at", start, end) for x in releases),
                repeated_sha_deliveries=len(delivery_events)-len({x["sha"] for x in delivery_events}),
                delivered_autonomous_shas=sorted({x["sha"] for x in delivered if x["sha"] in own}),
                accepted_autonomous_shas=sorted({x["sha"] for x in delivered if x.get("review", {}).get("clear") is True and
                                                _before(x, "reviewed_at", end) and x["sha"] in own}),
                states_at_cutoff=states, coverage="UNKNOWN" if any(x["state"] == "UNKNOWN" for x in states) else "KNOWN",
                acceptance_scope="ordinary independent review outcomes")
    except (OSError, ValueError, TypeError, KeyError):
        missing.append("fault/recovery coverage unreadable")
    return dict(version=1, start=start, end=end, wakes=len(rows), rows=list(rows.values()),
                observed_commits=observed, attributed_commits=own,
                engine_seconds=seconds if known_usage else None, missing_usage=len(rows)-known_usage+unlocated,
                unknown_window_wakes=unlocated,
                pending_at_cutoff=sum(r.get("pending_at_cutoff") is True for r in rows.values()),
                commits_per_engine_hour=len(own)/(seconds/3600) if seconds and not missing and known_usage == len(rows) and
                    all(r.get("evidence_state") == "KNOWN" for r in rows.values()) else None,
                missing_receipts=sum(r.get("claimed_result") is None for r in rows.values()),
                unknown_git=sum(r.get("evidence_state") != "KNOWN" for r in rows.values()),
                unchanged_blocked_numerator=sum(r["unchanged_wait"] is True for r in typed),
                unchanged_blocked_denominator=len(typed), blocked_wait_missing=len(rows)-len(typed),
                unchanged_blocked_share=sum(r["unchanged_wait"] is True for r in typed)/len(typed) if typed else None,
                check_to_commit_seconds=dict(known=len(latencies), lower=sum(x["seconds_lower"] for x in latencies)/len(latencies),
                    upper=sum(x["seconds_upper"] for x in latencies)/len(latencies), precision_seconds=1) if latencies else None,
                fault_trigger_novelty=fault_facts, reverts_rework=recovery_facts,
                dirty_paths=dirty, inventory=inventory, unreadable=missing,
                limitations=["Commit presence is not acceptance or usefulness.",
                             "Legacy missing facts and unbound check latency remain UNKNOWN."])
