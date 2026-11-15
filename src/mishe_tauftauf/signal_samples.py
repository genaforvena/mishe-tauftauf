"""Python adapter for the mishe-signal-sample store (rust/mishe-signal-samples).

The binary owns the segment format, validation and the one-pass read. This module
only finds the binary, passes records as TSV on stdin, and maps exit statuses to
results. Gaps stay gaps: nothing here fills a missing time with zero or a last value.

There is no Python fallback. If the binary is missing the caller gets
SignalSampleUnknown, which the caller must report as UNKNOWN.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class SignalSampleUnknown(RuntimeError):
    """The store could not be used: no binary, usage/IO error, or an unexpected status."""


@dataclass
class ReadResult:
    records: list[str] = field(default_factory=list)  # raw TSV lines, exactly as stored
    faults: list[str] = field(default_factory=list)   # stderr diagnostics (exit 2)

    @property
    def complete(self) -> bool:
        return not self.faults


def _binary() -> str:
    override = os.environ.get("MISHE_SIGNAL_SAMPLE_BIN")
    if override:
        if os.access(override, os.X_OK):
            return override
        raise SignalSampleUnknown("MISHE_SIGNAL_SAMPLE_BIN names no executable file")
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if home:
        candidate = Path(home) / "bin" / "mishe-signal-sample"
        if os.access(candidate, os.X_OK):
            return str(candidate)
    raise SignalSampleUnknown("mishe-signal-sample unavailable: no MISHE_SIGNAL_SAMPLE_BIN and no site install")


def format_record(ts_ms: int, name: str, state: str, value: str, source: str) -> str:
    return f"{ts_ms}\t{name}\t{state}\t{value}\t{source}\n"


def append_samples(directory: Path, lines: list[str], *, max_segment_bytes: int = 4 << 20,
                   keep_segments: int = 24, timeout_s: float = 10.0) -> int:
    """Append a complete batch of formatted lines. Returns records written (0 for an empty batch)."""
    if not lines:
        return 0
    batch = "".join(lines)
    try:
        done = subprocess.run(
            [_binary(), "append", str(directory), str(max_segment_bytes), str(keep_segments)],
            input=batch.encode("utf-8"), capture_output=True, timeout=timeout_s, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SignalSampleUnknown(f"append did not run: {exc}") from exc
    if done.returncode == 0:
        return len(lines)
    if done.returncode == 2:
        # Data fault: nothing was written. Surface it; a rejected batch is a producer bug.
        raise ValueError(f"append rejected batch: {done.stderr.decode('utf-8', 'replace').strip()}")
    raise SignalSampleUnknown(f"append exit {done.returncode}: {done.stderr.decode('utf-8', 'replace').strip()}")


def read_samples(directory: Path, *, name: str | None = None, since_ms: int | None = None,
                 until_ms: int | None = None, timeout_s: float = 30.0) -> ReadResult:
    """One pass over the store. Exit 0 is complete; exit 2 keeps the valid records and names the faults."""
    argv = [_binary(), "read", str(directory)]
    if name is not None:
        argv += ["--name", name]
    if since_ms is not None:
        argv += ["--since", str(since_ms)]
    if until_ms is not None:
        argv += ["--until", str(until_ms)]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SignalSampleUnknown(f"read did not run: {exc}") from exc
    if done.returncode not in (0, 2):
        raise SignalSampleUnknown(f"read exit {done.returncode}: {done.stderr.decode('utf-8', 'replace').strip()}")
    text = done.stdout.decode("utf-8", "strict")
    records = text.splitlines() if text else []
    faults = done.stderr.decode("utf-8", "replace").splitlines() if done.returncode == 2 else []
    return ReadResult(records=records, faults=faults)
