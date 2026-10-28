"""The durable dispatch record: what a successor reads before acting.

The record is the reason a restarted supervisor does not repeat an effect.  It
is written atomically, so a crash leaves either the previous record or the
new one, never a half-written mixture that reads as success.

One file per role, so one role's finished turn is not erased by another
role's.  The "one active mind at a time" rule is enforced across those files
by the supervisor, not by keeping them in one place.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dispatch_record import decode_record
from .dispatch_record import encode_record
from .dispatch_record import read_record_fields
from .dispatch_record import valid_role
from .dispatch_record import write_record_fields
from .interfaces import (COMPLETED, FAILED, OPEN_STATES, PENDING, RECONCILED,
                          SKIPPED, STARTED, UNKNOWN, DispatchState)


class DispatchStoreError(RuntimeError):
    """The durable record is missing, malformed or not a regular file."""


@dataclass(frozen=True)
class DispatchRecord:
    """One durable dispatch attempt."""

    role: str
    key: str
    state: DispatchState
    detail: str = ""
    pid: int | None = None
    attempt: int = 1

    def to_json(self) -> dict[str, Any]:
        return {"role": self.role, "key": self.key, "state": self.state.name,
                "detail": self.detail, "pid": self.pid, "attempt": self.attempt}

    @classmethod
    def from_json(cls, data: Any) -> "DispatchRecord":
        try:
            fields = decode_record(data)
        except ValueError as exc:
            raise DispatchStoreError(str(exc)) from exc
        return cls(role=fields["role"], key=fields["key"],
                   state=DispatchState(fields["state"]), detail=fields["detail"],
                   pid=fields["pid"], attempt=fields["attempt"])


class DispatchStore:
    """The durable records of the last dispatch attempt per role.

    The store is a directory of one file per role.  A malformed file is an
    error, not an absent record: silently treating unreadable state as
    "nothing happened" is exactly how a duplicate effect gets shipped.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def lock_path(self) -> Path:
        """The lock that serializes dispatch across processes."""
        return self.root / ".dispatch.lock"

    def path_for(self, role: str) -> Path:
        if not valid_role(role):
            raise DispatchStoreError(f"role {role!r} cannot name a dispatch record")
        return self.root / f"{role}.json"

    def read(self, role: str) -> DispatchRecord | None:
        """The record a successor reads for ``role``, or ``None``."""
        path = self.path_for(role)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DispatchStoreError(f"dispatch record unreadable: {exc}") from exc
        return DispatchRecord.from_json(raw)

    def write(self, record: DispatchRecord) -> None:
        """Replace the record atomically."""
        path = self.path_for(record.role)
        payload = encode_record(record.to_json())
        # The key history is kept beside the record: a key that has been
        # used stays used, whichever key came after it.
        used = self.used_keys(record.role) | {record.key}
        payload = encode_record(dict(record.to_json(), used_keys=sorted(used)))
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.root,
            prefix=f".{path.name}.", delete=False)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def records(self) -> list[DispatchRecord]:
        """Every readable record, ordered by role."""
        found = []
        for path in sorted(self.root.glob("*.json")):
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise DispatchStoreError(f"dispatch record {path.name} unreadable: {exc}") from exc
            found.append(DispatchRecord.from_json(raw))
        return found

    def used_keys(self, role: str) -> set[str]:
        """Every durable key this role has dispatched under.

        A key names one piece of work.  The role's current record holds only
        its most recent key, so the earlier ones are kept beside it: a key
        that has been used stays used, whichever key came after it.
        """
        path = self.path_for(role)
        if not path.exists():
            return set()
        try:
            data = read_record_fields(path)
        except ValueError as exc:
            raise DispatchStoreError(str(exc)) from exc
        keys = data.get("used_keys") or []
        found = {key for key in keys if isinstance(key, str) and key}
        current = data.get("key")
        if isinstance(current, str) and current:
            found.add(current)
        return found

    def record_use(self, role: str, key: str) -> None:
        """Remember that ``role`` dispatched under ``key``."""
        path = self.path_for(role)
        try:
            data = read_record_fields(path)
        except ValueError:
            data = {}
        keys = [value for value in (data.get("used_keys") or [])
                if isinstance(value, str) and value]
        if key not in keys:
            keys.append(key)
        data["used_keys"] = keys
        write_record_fields(path, data)

    def open_records(self) -> list[DispatchRecord]:
        """Every record that still holds an unresolved effect."""
        return [record for record in self.records() if record.state in OPEN_STATES]

    def clear(self, role: str) -> None:
        """Remove one role's record.  Only for state that is provably finished.

        The key history is kept.  It is what stops a finished key from being
        dispatched again, so removing it with the record would undo the
        guard that makes a key name one piece of work.
        """
        record = self.read(role)
        if record is not None and record.state in OPEN_STATES:
            raise DispatchStoreError(
                f"dispatch {record.key!r} for role {role!r} is {record.state.name}; "
                "reconcile it before clearing")
        path = self.path_for(role)
        if not path.exists():
            return
        keys = self.used_keys(role)
        last = record.key if record is not None else ""
        path.unlink()
        if keys:
            # The history survives the record it belonged to.  The last key
            # stays as the record's own, so the file is still a record.
            write_record_fields(path, {"role": role, "key": last,
                                       "state": "reconciled", "detail": "",
                                       "pid": None, "attempt": 1,
                                       "used_keys": sorted(keys)})


__all__ = ["DispatchRecord", "DispatchStore", "DispatchStoreError",
           "COMPLETED", "FAILED", "PENDING", "RECONCILED", "SKIPPED",
           "STARTED", "UNKNOWN"]
