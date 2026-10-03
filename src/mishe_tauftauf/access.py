"""Node-local requests and revocable decisions for this plant's actions."""

from __future__ import annotations

import fcntl
import difflib
import json
import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed
from .observations import validate_home

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
DECISION_RE = re.compile(r"^\[permission\] id=([A-Za-z0-9._-]+) decision=(granted|revoked)\b")


@dataclass(frozen=True)
class Request:
    identity: str
    owner: str
    task: str
    capability: str
    unblocks: tuple[str, ...]
    reason: str
    created: str

    def as_dict(self) -> dict[str, object]:
        return {"id": self.identity, "owner": self.owner, "task": self.task,
                "capability": self.capability, "unblocks": list(self.unblocks),
                "reason": self.reason, "created": self.created}


@contextmanager
def _lock(home: Path):
    validate_home(home)
    root = home / "access"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _request_path(home: Path, identity: str) -> Path:
    if not ID_RE.fullmatch(identity) or identity in {".", ".."}:
        raise ValueError("invalid request ID")
    return home / "access" / "requests" / f"{identity}.json"


def _safe_text(value: str, label: str) -> str:
    if not value.strip() or len(value) > 1024 or any(char in value for char in "\r\n\x00"):
        raise ValueError(f"invalid {label}")
    return value.strip()


def request(home: Path, identity: str, owner: str, task: str, capability: str,
            unblocks: list[str], reason: str) -> Request:
    path = _request_path(home, identity)
    if not NAME_RE.fullmatch(owner) or not NAME_RE.fullmatch(task) or not NAME_RE.fullmatch(capability):
        raise ValueError("owner, task, and capability need simple names")
    if not unblocks:
        raise ValueError("at least one --unblocks path is required")
    paths = tuple(_safe_text(item, "unblocks path") for item in unblocks)
    reason = _safe_text(reason, "reason")
    with _lock(home):
        if path.exists():
            old = get(home, identity)
            if (old.owner, old.task, old.capability, old.unblocks, old.reason) != (owner, task, capability, paths, reason):
                raise ValueError(f"request {identity} already exists with different scope")
            return old
        item = Request(identity, owner, task, capability, paths, reason,
                       datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(item.as_dict(), handle, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Feed(home).append(owner, f"[permission-request] id={identity} owner={owner} task={task} "
                          f"capability={capability} unblocks={','.join(paths)} reason={reason}\n"
                          f"{owner} needs {capability} for {task}: {reason}. "
                          f"An operator decision would unblock {', '.join(paths)}.")
        return item


def get(home: Path, identity: str) -> Request:
    path = _request_path(home, identity)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        identities = [p.stem for p in (home / "access" / "requests").glob("*.json")]
        matches = difflib.get_close_matches(identity, identities, n=1, cutoff=0.6)
        hint = f"; did you mean {matches[0]}?" if matches else ""
        raise ValueError(f"unknown request {identity}{hint}; use access menu to select a request") from exc
    paths = data.get("unblocks")
    if data.get("id") != identity or not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError(f"malformed request {identity}")
    return Request(identity, data["owner"], data["task"], data["capability"],
                   tuple(data["unblocks"]), data["reason"], data["created"])


def decisions(home: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    for entry in Feed(home).entries():
        if entry.source != "operator/permissions":
            continue
        if match := DECISION_RE.match(entry.body):
            state[match.group(1)] = match.group(2)
    return state


def state(home: Path, identity: str) -> str:
    get(home, identity)
    return decisions(home).get(identity, "pending")


def decide(home: Path, identity: str, decision: str, *,
           expected_request: Request | None = None, expected_state: str | None = None) -> str:
    if decision not in {"granted", "revoked"}:
        raise ValueError("decision must be granted or revoked")
    with _lock(home):
        item = get(home, identity)
        if decision == "granted" and identity in retired_requests(home):
            raise ValueError("recovery already resolved; this permission request is retired")
        current = decisions(home).get(identity, "pending")
        if expected_request is not None and item != expected_request:
            raise ValueError("request scope changed; review the updated request before deciding")
        if expected_state is not None and current != expected_state:
            raise ValueError("request decision changed; review the updated request before deciding")
        if current == decision:
            return current
        Feed(home).append("operator/permissions", f"[permission] id={identity} decision={decision} "
                          f"owner={item.owner} task={item.task} capability={item.capability} "
                          f"unblocks={','.join(item.unblocks)}\n"
                          f"Operator {decision} {item.capability} for {item.owner}'s task {item.task}. "
                          f"Affected paths: {', '.join(item.unblocks)}.")
        return decision


def list_requests(home: Path) -> list[tuple[Request, str]]:
    root = home / "access" / "requests"
    states = decisions(home)
    if not root.exists():
        return []
    return [(item, states.get(item.identity, "pending"))
            for path in sorted(root.glob("*.json"))
            for item in [get(home, path.stem)]]


def _recovery_path(home: Path, identity: str) -> Path:
    _request_path(home, identity)  # Same safe ID namespace as permission requests.
    return home / "access" / "recoveries" / f"{identity}.json"


def _evidence(home: Path, path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_relative_to(home.resolve()) or not path.is_file():
        raise ValueError("recovery evidence must be a file inside the owned site")
    data = path.read_bytes()
    if not data.strip():
        raise ValueError("recovery evidence must be nonempty")
    return {"path": str(path.relative_to(home.resolve())), "sha256": hashlib.sha256(data).hexdigest()}


def _save_recovery(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(row, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def recover(home: Path, identity: str, *, owner: str, task: str, resolver: str,
            missing: str, action: str, alternatives: list[str], cutoff: str,
            evidence: Path, capability: str | None = None,
            unblocks: list[str] | None = None, reason: str | None = None) -> dict:
    """Describe one unresolved obligation; never changes task or mind eligibility.

    A capability explicitly requested by the caller routes to the existing ledger.
    Local repair records deliberately emit no wake, wait, or blocked settlement.
    """
    path = _recovery_path(home, identity)
    for label, value in (("owner", owner), ("task", task), ("resolver", resolver)):
        if not NAME_RE.fullmatch(value):
            raise ValueError(f"invalid {label}")
    try:
        deadline = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("cutoff must be an ISO datetime with timezone") from exc
    if deadline.tzinfo is None:
        raise ValueError("cutoff needs timezone")
    if not alternatives:
        raise ValueError("at least one recovery alternative is required")
    row = {"id": identity, "owner": owner, "task": task, "resolver": resolver,
           "missing": _safe_text(missing, "missing prerequisite"),
           "action": _safe_text(action, "bounded action"),
           "alternatives": [_safe_text(value, "alternative") for value in alternatives],
           "cutoff": deadline.isoformat(), "evidence": _evidence(home, evidence),
           "permission": identity if capability else None}
    if capability:
        if not reason or not unblocks or not NAME_RE.fullmatch(capability):
            raise ValueError("permission recovery needs capability, reason and unblocks")
        row["request"] = {"capability": capability, "reason": _safe_text(reason, "reason"),
                          "unblocks": [_safe_text(value, "unblocks path") for value in unblocks]}
    elif unblocks or reason:
        raise ValueError("unblocks and reason require a capability")
    # Store diagnosis first. A crash before request publication is repaired by
    # repeating the same command; an incomplete route remains visible as UNKNOWN.
    with _lock(home):
        if path.exists():
            old = json.loads(path.read_text())
            if {key: old.get(key) for key in row} != row:
                raise ValueError(f"recovery {identity} already exists with different scope; use a new ID")
        else:
            if capability and _request_path(home, identity).exists():
                item = get(home, identity)
                if (item.owner, item.task, item.capability, list(item.unblocks), item.reason) != (
                        owner, task, capability, row["request"]["unblocks"], row["request"]["reason"]):
                    raise ValueError(f"request {identity} already exists with different scope")
            _save_recovery(path, row)
    if capability:
        request(home, identity, owner, task, capability, row["request"]["unblocks"], row["request"]["reason"])
    return next(item for item in recoveries(home, include_resolved=True) if item["id"] == identity)


def recoveries(home: Path, *, include_resolved: bool = False) -> list[dict]:
    result = []
    states = decisions(home)
    for path in sorted((home / "access" / "recoveries").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("resolved"):
            status = "resolved"
        elif row["permission"]:
            status = ("unknown" if not _request_path(home, row["permission"]).exists() else
                      "retry" if states.get(row["permission"]) == "granted" else "permission")
        else:
            status = "repair"
        if include_resolved or status != "resolved":
            result.append({**row, "status": status})
    return result


def retired_requests(home: Path) -> set[str]:
    return {row["permission"] for row in recoveries(home, include_resolved=True)
            if row["status"] == "resolved" and row["permission"]}


def resolve_recovery(home: Path, identity: str, evidence: Path, *,
                     checked_action: str, via_alternative: bool = False) -> None:
    proof = _evidence(home, evidence)
    with _lock(home):
        path = _recovery_path(home, identity)
        if not path.exists():
            raise ValueError(f"unknown recovery {identity}")
        row = json.loads(path.read_text())
        if not row.get("resolved"):
            checked_action = _safe_text(checked_action, "checked action")
            allowed = row["alternatives"] if via_alternative else [row["action"]]
            if checked_action not in allowed:
                raise ValueError("checked action must match the recorded action or selected alternative")
            if row["permission"] and not via_alternative and state(home, row["permission"]) != "granted":
                raise ValueError("permission route needs a grant before checked retry; use a permitted alternative instead")
            row["resolved"] = {**proof, "checked_action": checked_action,
                               "via_alternative": via_alternative,
                               "at": datetime.now(timezone.utc).isoformat()}
            _save_recovery(path, row)
