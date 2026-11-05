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
                check_to_commit_seconds=None, fault_trigger_novelty=None, reverts_rework=None,
                dirty_paths=dirty, inventory=inventory, unreadable=missing,
                limitations=["Commit presence is not acceptance or usefulness.",
                             "Legacy missing facts and unbound check latency remain UNKNOWN."])
