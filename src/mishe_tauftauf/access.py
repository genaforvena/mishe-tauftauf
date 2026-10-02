"""Node-local requests and revocable decisions for this plant's actions."""

from __future__ import annotations

import fcntl
import difflib
import json
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
    if data.get("id") != identity or not isinstance(data.get("unblocks"), list):
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
