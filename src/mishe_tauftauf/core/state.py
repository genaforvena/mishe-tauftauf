"""The durable dispatch record: what a successor reads before acting.

The record is the reason a restarted supervisor does not repeat an effect.  It
is written atomically, so a crash leaves either the previous record or the
new one, never a half-written mixture that reads as success.

One file per role, so one role's finished turn is not erased by another
role's.  The "one active mind at a time" rule is enforced across those files
by the supervisor, not by keeping them in one place.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dispatch_record import decode_record
from .dispatch_record import encode_record
from .dispatch_record import valid_role
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

    def open_records(self) -> list[DispatchRecord]:
        """Every record that still holds an unresolved effect."""
        return [record for record in self.records() if record.state in OPEN_STATES]

    def clear(self, role: str) -> None:
        """Remove one role's record.  Only for state that is provably finished."""
        try:
            self.path_for(role).unlink()
        except FileNotFoundError:
            pass


__all__ = ["DispatchRecord", "DispatchStore", "DispatchStoreError",
           "COMPLETED", "FAILED", "PENDING", "RECONCILED", "SKIPPED",
           "STARTED", "UNKNOWN"]
