"""Per-role readiness, bounded evidence waits and advisory path claims.

Walls remain the human record. Missing state uses legacy behavior; malformed
state cannot suppress work. No content in walls or chat is interpreted here.
"""
from __future__ import annotations
import math
from pathlib import Path
from . import roles, signal_store
from .state_files import read_json, write_json
from .work_receipts import digest


def location(home, role):
    if role not in roles.declared(home):
        raise ValueError(f"unknown installed role {role!r}")
    return Path(home) / "work/roles" / f"{role}.json"


def validate_wait(home, wait):
    if not isinstance(wait, dict):
        raise ValueError("wait must be a mapping")
    for key in ("resolver", "escalate_to"):
        if wait.get(key) not in roles.declared(home):
            raise ValueError(f"unknown {key}")
    deadline = wait.get("deadline")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise ValueError("wait needs a finite deadline")
    for key in ("evidence_needed", "action_at_deadline"):
        if not isinstance(wait.get(key), str) or not wait[key]:
            raise ValueError(f"wait needs {key}")
    if not isinstance(wait.get("signals", {}), dict) or not isinstance(wait.get("paths", {}), dict):
        raise ValueError("wait watches must be mappings")
    if wait.get("external") and wait.get("resolver") != "steward":
        raise ValueError("external dependencies need accountable Steward ownership")


def validate(home, role, record):
    location(home, role)
    if not isinstance(record, dict) or record.get("version") != 1 or record.get("role") != role:
        raise ValueError("work record version/role mismatch")
    if not isinstance(record.get("tasks"), list):
        raise ValueError("work tasks must be a list")
    ids = set()
    for task in record["tasks"]:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"] or task["id"] in ids:
            raise ValueError("tasks need unique ids")
        ids.add(task["id"])
        if task.get("state") not in ("ready", "waiting", "done"):
            raise ValueError("invalid task state")
        from .work_boundary import work_identifier
        retries = task.get("retry_intentions", [])
        if not isinstance(retries, list) or any(not work_identifier(x) for x in retries):
            raise ValueError("retry intentions need confined intention identifiers")
        if task["state"] == "waiting":
            validate_wait(home, task.get("wait", {}))
    if not isinstance(record.get("claims", []), list):
        raise ValueError("claims must be a list")
    for claim in record.get("claims", []):
        if not isinstance(claim, dict) or not isinstance(claim.get("path"), str) or not isinstance(claim.get("expires"), (int, float)) or not math.isfinite(claim["expires"]):
            raise ValueError("claim needs path and finite expiry")
    return record


def load(home, role):
    try:
        record = read_json(location(home, role))
        return validate(home, role, record) if record is not None else None
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return dict(state="UNKNOWN", error=str(exc), tasks=[])


def save(home, role, record):
    write_json(location(home, role), validate(home, role, record))


def path_digest(repo, name):
    if repo is None:
        raise ValueError("repository unavailable")
    root = Path(repo).resolve(); path = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts or not path.resolve().is_relative_to(root):
        raise ValueError("watch escapes repository")
    if any(p in ("chat.log", "walls", "wakes", "inbox") for p in Path(name).parts):
        raise ValueError("volatile coordination paths cannot define incident novelty")
    return digest(path) if path.is_file() else "ABSENT" if not path.exists() else "DIRECTORY"


def evaluate_wait(home, wait, *, repo, now):
    try:
        validate_wait(home, wait)
        if now >= wait["deadline"]:
            return dict(state="due", reasons=["deadline"])
        reasons, evidence = [], {}
        for name, expected in wait.get("signals", {}).items():
            sig = signal_store.read(home, name, now=now)
            actual = dict(revision=signal_store.rev(home, name), state=sig.state, value=sig.value)
            evidence[name] = actual
            if isinstance(expected, dict):
                if any(actual.get(k) != v for k, v in expected.items()): reasons.append(f"signal:{name}")
            elif actual["revision"] != expected:
                reasons.append(f"signal:{name}")
            # Missing/broken monitoring is an actionable gap even if revision did not move.
            if sig.state != "OK" and not isinstance(expected, dict): reasons.append(f"monitor:{name}:{sig.state}")
        for name, expected in wait.get("paths", {}).items():
            actual = path_digest(repo, name)
            evidence[name] = actual
            if actual != expected: reasons.append(f"path:{name}")
        return dict(state="changed" if reasons else "unchanged", reasons=reasons, evidence=evidence)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(state="unknown", reasons=[str(exc)])


def all_waiting(record):
    return bool(record and record.get("state") != "UNKNOWN" and record.get("tasks") and
                any(t["state"] == "waiting" for t in record["tasks"]) and
                all(t["state"] != "ready" for t in record["tasks"]))


def claims(home, *, now):
    found = []
    for role in sorted(roles.declared(home)):
        record = load(home, role) or {}
        for claim in record.get("claims", []):
            found.append(dict(claim, role=role, expired=now >= claim["expires"]))
    return found


def summary(home, role, *, now):
    record = load(home, role)
    if record is None: return "Legacy work state (no sidecar)."
    return str(record) + "\nAdvisory peer claims: " + str(claims(home, now=now))


def incident_wait(home, role, incident, *, now, repo=None):
    record = load(home, role) or {}
    for task in record.get("tasks", []):
        if task["state"] == "waiting" and task["wait"].get("incident") == incident:
            wait = task["wait"]
            if evaluate_wait(home, wait, repo=repo, now=now)["state"] == "unchanged":
                return wait
    return None


def settle_waits(home, role, *, now):
    """Replace only explicitly mapped retries. Never remove unrelated intentions."""
    from . import intentions
    record = load(home, role)
    if not record or record.get("state") == "UNKNOWN": return
    for task in record.get("tasks", []):
        if task["state"] != "waiting": continue
        for identity in task.get("retry_intentions", []):
            intentions.remove(home, role, identity)
    if all_waiting(record):
        intentions.add(home, role, "idle-initiative", note="opportunity discovery after 24h waiting",
                       due=now + 86400, spontaneous=True)


def next_probe(home, *, now):
    records = [load(home, role) for role in roles.declared(home)]
    return now + 60 if any(r is not None for r in records) else None


def _sweep(home, *, repo, now, notify=None):
    """Cheap deterministic sweep; notify once per changed observation or deadline.

    Notification state is separate from task choices. This never advances a
    cursor, consumes mail, clears a fault or overwrites a peer work record.
    """
    from . import owed
    if notify is None:
        from .failure import default_notify
        notify = lambda role, text: default_notify(home, role, text)
    path = Path(home) / "work/watch-state.json"
    try:
        seen = read_json(path) or {}
    except (OSError, ValueError):
        seen = {}  # observation loss returns work, never mutes it
    for role in sorted(roles.declared(home)):
        record = load(home, role)
        if record is None: continue
        evaluations = [("state", dict(state="unknown", reasons=[record["error"]]), None)] if record.get("state") == "UNKNOWN" else [
            (task["id"], evaluate_wait(home, task["wait"], repo=repo, now=now), task["wait"])
            for task in record["tasks"] if task["state"] == "waiting"]
        for identity, evaluation, wait in evaluations:
            key = role + ":" + identity
            token = dict(evaluation=evaluation, deadline=wait.get("deadline") if wait else None)
            if evaluation["state"] == "unchanged":
                seen.pop(key, None)
                continue
            if seen.get(key) == token: continue
            recipient = wait["escalate_to"] if wait and evaluation["state"] == "due" else role
            notify(recipient, f"Work {role}/{identity}: {evaluation}; " +
                   (wait["action_at_deadline"] if wait else "UNKNOWN work state; repair the sidecar"))
            owed.touch_inbox(home, recipient)
            seen[key] = token
            if wait and now > wait["deadline"] + 300:
                notify("genome", f"Work scheduler missed {role}/{identity} deadline by more than five minutes; inspect reporter/scheduler")
                owed.touch_inbox(home, "genome")
    write_json(path, seen)


def sweep(home, *, repo, now, notify=None):
    import fcntl
    path=Path(home)/"work/.sweep.lock"
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        return _sweep(home,repo=repo,now=now,notify=notify)
