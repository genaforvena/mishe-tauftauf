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



OVERDUE_GRACE = 300


def _overdue_path(home):
    return Path(home) / "work/watch-overdue.json"


def _load_overdue(home):
    """The overdue escalation record, independent of the due-token map.

    A lost or corrupt file returns work, never mutes it: the escalation is
    offered again instead of disappearing with the observation.
    """
    try:
        data = read_json(_overdue_path(home))
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    overdue = data.get("overdue")
    errors = data.get("errors")
    return (overdue if isinstance(overdue, dict) else {},
            errors if isinstance(errors, dict) else {})


def _save_overdue(home, overdue, errors):
    write_json(_overdue_path(home), dict(version=1, overdue=overdue, errors=errors))


def _overdue_key(role, identity):
    return role + ":" + identity


def _deliver(home, recipient, text, notify):
    """Notify and mark the inbox. Returns None on success, else the failure.

    The caller records a notification only after both steps succeed, so a
    failed delivery is offered again on the next sweep instead of being muted.
    """
    from . import owed
    try:
        notify(recipient, text)
        owed.touch_inbox(home, recipient)
    except (OSError, ValueError) as exc:
        return str(exc)
    return None


def _escalate_overdue(home, role, identity, wait, overdue, errors, *, now, notify, declared):
    """Escalate one overdue unresolved wait, once per role, task and deadline.

    The overdue state is its own record: a waiting task past its deadline
    keeps the same due token on every later sweep, so a deadline check chained
    to that comparison would never fire.
    """
    key = _overdue_key(role, identity)
    deadline = wait.get("deadline")
    entry = overdue.get(key)
    if isinstance(entry, dict) and entry.get("deadline") == deadline and entry.get("notified_at") is not None:
        return
    # An escalation nobody is dispatched for is a silent escalation. Genome is
    # the standing escalation owner; a site without it escalates to the role
    # the wait already named.
    recipient = "genome" if "genome" in declared else wait.get("escalate_to")
    text = (f"Work {role}/{identity} is still waiting, more than five minutes past its deadline "
            f"({deadline}); the deadline action was: {wait.get('action_at_deadline')}. "
            f"Inspect this unresolved wait and its resolver.")
    error = _deliver(home, recipient, text, notify)
    if error is None:
        overdue[key] = dict(deadline=deadline, notified_at=now, role=role)
        errors.pop("overdue:" + key, None)
    else:
        overdue[key] = dict(deadline=deadline, notified_at=None, role=role)
        errors["overdue:" + key] = error


def _sweep(home, *, repo, now, notify=None):
    """Cheap deterministic sweep; notify once per changed observation or deadline.

    Notification state is separate from task choices. This never advances a
    cursor, consumes mail, clears a fault or overwrites a peer work record.
    """
    if notify is None:
        from .failure import default_notify
        notify = lambda role, text: default_notify(home, role, text)
    path = Path(home) / "work/watch-state.json"
    try:
        seen = read_json(path) or {}
    except (OSError, ValueError):
        seen = {}  # observation loss returns work, never mutes it
    if not isinstance(seen, dict):
        seen = {}
    overdue, errors = _load_overdue(home)
    declared = roles.declared(home)
    live, readable = set(), set()
    for role in sorted(declared):
        record = load(home, role)
        if record is None:
            readable.add(role)
            continue
        if record.get("state") == "UNKNOWN":
            evaluations = [("state", dict(state="unknown", reasons=[record["error"]]), None)]
        else:
            readable.add(role)
            evaluations = [(task["id"], evaluate_wait(home, task["wait"], repo=repo, now=now), task["wait"])
                           for task in record["tasks"] if task["state"] == "waiting"]
            live.update(_overdue_key(role, task["id"]) for task in record["tasks"] if task["state"] == "waiting")
        for identity, evaluation, wait in evaluations:
            key = _overdue_key(role, identity)
            token = dict(evaluation=evaluation, deadline=wait.get("deadline") if wait else None)
            if evaluation["state"] == "unchanged":
                seen.pop(key, None)
                continue
            if seen.get(key) != token:
                recipient = wait["escalate_to"] if wait and evaluation["state"] == "due" else role
                error = _deliver(home, recipient, f"Work {role}/{identity}: {evaluation}; " +
                                 (wait["action_at_deadline"] if wait else "UNKNOWN work state; repair the sidecar"),
                                 notify)
                if error is None:
                    seen[key] = token
                    errors.pop("notify:" + key, None)
                else:
                    errors["notify:" + key] = error
            deadline = wait.get("deadline") if isinstance(wait, dict) else None
            if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) \
                    and now > deadline + OVERDUE_GRACE:
                _escalate_overdue(home, role, identity, wait, overdue, errors,
                                  now=now, notify=notify, declared=declared)
    # A removed or resolved wait stops escalating and stops occupying the record.
    for key in list(overdue):
        entry = overdue[key]
        owner = entry.get("role") if isinstance(entry, dict) else None
        if owner not in declared or (owner in readable and key not in live):
            del overdue[key]
    write_json(path, seen)
    _save_overdue(home, overdue, errors)


def sweep(home, *, repo, now, notify=None):
    import fcntl
    path=Path(home)/"work/.sweep.lock"
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        return _sweep(home,repo=repo,now=now,notify=notify)
