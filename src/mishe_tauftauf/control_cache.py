"""Optional fail-closed cache for paired startup-control verdicts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

from .judges import controls
from .policy import JudgmentPolicy

VERSION = 1
MAX_BYTES = 64 * 1024


def identity(adapter: Path, mode: str, policy: JudgmentPolicy) -> str:
    """Bind a verdict to executable bytes, control cases, and all policy inputs."""
    if not adapter.is_file() or not os.access(adapter, os.X_OK):
        raise OSError("judge is not executable")
    binary = adapter.read_bytes()
    descriptor = {
        "adapter_sha256": hashlib.sha256(binary).hexdigest(),
        "mode": mode,
        "controls": controls(),
        "policy_version": policy.version,
        "questions": {name: [policy.question_version(name), policy.question_text(name)] for name in controls()},
        "low_threshold": policy.low_threshold,
        "high_threshold": policy.high_threshold,
        "judge_timeout": policy.judge_timeout,
    }
    encoded = json.dumps(descriptor, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read(path: Path, expected_identity: str, ttl: float) -> dict[str, str]:
    """Return explicit failures for every question unless the whole file validates."""
    unknown = {name: "startup controls UNKNOWN: cache absent, stale, or invalid; refresh explicitly" for name in controls()}
    try:
        if path.stat().st_size > MAX_BYTES:
            return unknown
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return unknown
    if not isinstance(record, dict) or set(record) != {"version", "identity", "checked_at", "passed"}:
        return unknown
    checked = record["checked_at"]
    if (
        type(record["version"]) is not int
        or record["version"] != VERSION
        or record["identity"] != expected_identity
        or isinstance(checked, bool)
        or not isinstance(checked, (int, float))
        or not math.isfinite(checked)
        or not 0 <= time.time() - checked <= ttl
        or not isinstance(record["passed"], dict)
        or set(record["passed"]) != set(controls())
        or any(type(value) is not bool for value in record["passed"].values())
    ):
        return unknown
    return {name: "production controls failed at explicit refresh" for name, passed in record["passed"].items() if not passed}


def write(path: Path, expected_identity: str, failures: dict[str, str]) -> None:
    record = {
        "version": VERSION,
        "identity": expected_identity,
        "checked_at": time.time(),
        "passed": {name: name not in failures for name in controls()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".control-cache-", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
