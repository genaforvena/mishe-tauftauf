"""Small, fail-closed supervisor for an isolated Mishe life-support trial.

This module deliberately owns only a durable dispatch record and a bounded
worker process.  It does not admit workloads, clear the host latch, or infer
health from panes.  A started worker whose outcome is not recorded remains
UNKNOWN and cannot be replayed automatically.
"""
from __future__ import annotations

import configparser
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class SupervisorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Dispatch:
    role: str
    wake: int
    state: str
    pid: int | None = None
    detail: str = ""


class MinimalSupervisor:
    """One-role-at-a-time durable worker supervisor.

    ``root`` must be a trial directory, never the production site root.  The
    state file is written atomically so a crash leaves the prior record intact.
    """

    def __init__(self, root: str | Path, *, role: str, timeout: float = 10.0):
        if not role or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in role):
            raise ValueError("invalid role")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.root = Path(root)
        self.role = role
        self.timeout = timeout
        self.state_path = self.root / "supervisor.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.state_path.exists():
            return {"role": self.role, "next_wake": 1, "dispatch": None}
        parser = configparser.ConfigParser()
        parser.read(self.state_path)
        section = parser["supervisor"]
        dispatch = None
        if section.get("state", ""):
            dispatch = {"role": section["role"], "wake": int(section["wake"]),
                        "state": section["state"], "pid": int(section["pid"]) if section.get("pid") else None,
                        "detail": section.get("detail", "")}
        return {"role": section.get("role", self.role), "next_wake": int(section.get("next_wake", "1")),
                "dispatch": dispatch}

    def _write(self, data: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        dispatch = data.get("dispatch") or {}
        parser = configparser.ConfigParser()
        parser["supervisor"] = {
            "role": data["role"], "next_wake": str(data["next_wake"]),
            "wake": str(dispatch.get("wake", "")), "state": dispatch.get("state", ""),
            "pid": str(dispatch.get("pid") or ""), "detail": dispatch.get("detail", ""),
        }
        with tmp.open("w") as handle:
            parser.write(handle)
        os.replace(tmp, self.state_path)

    def status(self) -> Dispatch | None:
        value = self._read().get("dispatch")
        return Dispatch(**value) if value else None

    def dispatch(self, command: list[str]) -> Dispatch:
        if not command:
            raise ValueError("empty command")
        data = self._read()
        current = data.get("dispatch")
        if current and current["state"] in {"started", "unknown"}:
            raise SupervisorError("prior dispatch requires reconciliation; replay refused")
        wake = int(data["next_wake"])
        record = {"role": self.role, "wake": wake, "state": "started", "pid": None, "detail": ""}
        data["dispatch"] = record
        data["next_wake"] = wake + 1
        self._write(data)
        try:
            proc = subprocess.Popen(command, start_new_session=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            record["pid"] = proc.pid
            self._write(data)
            try:
                output, _ = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, 15)
                try:
                    output, _ = proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, 9)
                    output, _ = proc.communicate()
                record.update(state="unknown", detail="worker timeout; effects require reconciliation")
                self._write(data)
                return Dispatch(**record)
            if proc.returncode != 0:
                record.update(state="failed", detail=(output or "worker failed")[-1000:])
            else:
                record.update(state="completed", detail=(output or "")[-1000:])
            self._write(data)
            return Dispatch(**record)
        except Exception as exc:
            record.update(state="unknown", detail=f"dispatch error: {exc}")
            self._write(data)
            return Dispatch(**record)

    def reconcile(self, *, detail: str) -> Dispatch:
        data = self._read()
        current = data.get("dispatch")
        if not current or current["state"] != "unknown":
            raise SupervisorError("only an unknown dispatch can be reconciled")
        current.update(state="reconciled", detail=detail)
        self._write(data)
        return Dispatch(**current)

    def revert(self) -> None:
        """Remove only this trial's state; production services are untouched."""
        if self.state_path.exists():
            self.state_path.unlink()
