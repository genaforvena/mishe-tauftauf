"""Fixed-window work facts; absence of evidence never becomes zero productivity."""
from __future__ import annotations
from pathlib import Path
from .state_files import read_json


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
                         claimed_result=receipt.get("result"))
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
            releases = []
            for path in sorted((Path(home)/"work/releases").glob("*.json")):
                entry = read_json(path)
                stamp = entry.get("reverted_at", entry.get("rollback_at", entry.get("created_at")))
                if not isinstance(stamp, (int, float)):
                    raise ValueError("release record lacks factual event time")
                if start <= stamp < end:
                    releases.append(entry); inventory.append(str(path.relative_to(home)))
            recovery_facts = dict(automatic_rollbacks=sum("rollback-started" in x.get("events", []) for x in releases),
                explicit_reverts=sum(x.get("phase") == "reverted" for x in releases),
                failed_recoveries=sum(x.get("phase") == "UNKNOWN" for x in releases),
                repeated_sha_deliveries=len([x for x in releases if x.get("observed") is True])-len({x["sha"] for x in releases if x.get("observed") is True}),
                delivered_autonomous_shas=sorted({x["sha"] for x in releases if x.get("phase") == "delivered" and x.get("observed") is True and x["sha"] in own}),
                accepted_autonomous_shas=sorted({x["sha"] for x in releases if x.get("phase") == "delivered" and x.get("review", {}).get("clear") is True and x["sha"] in own}),
                coverage="KNOWN", acceptance_scope="ordinary independent review outcomes")
    except (OSError, ValueError, TypeError, KeyError):
        missing.append("fault/recovery coverage unreadable")
    return dict(version=1, start=start, end=end, wakes=len(rows), rows=list(rows.values()),
                observed_commits=observed, attributed_commits=own,
                engine_seconds=seconds if known_usage else None, missing_usage=len(rows)-known_usage+unlocated,
                unknown_window_wakes=unlocated,
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
