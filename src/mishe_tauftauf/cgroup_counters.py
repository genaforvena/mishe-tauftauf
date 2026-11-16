"""Consumer-side reader for cgroup v2 counter decoding (``mishe-cgroup-counters``).

The binary ``rust/mishe-cgroup-counters`` owns the byte-exact decode of a
``cpu.stat`` or ``cpu.max`` body. This module owns binary resolution and the
stdout contract; callers act on decoded integers and never interpret the kernel
text themselves.

Resolution fails closed: a *configured* binary that is missing, unverified,
failing or outside its contract raises CgroupCountersUnknown, which
``cpu_stat_text`` maps to its existing ``None`` (UNKNOWN) answer. With no binary
configured at all — an uninstalled checkout or an isolated test — this adapter
uses the retained reference decoder explicitly; a delivered plant with a site
home always uses the binary. There is no silent fallback around a broken binary.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

# The boundary's own process runner, captured at import. A caller that mocks
# another subprocess must not also intercept this binary call.
from subprocess import run as _run_binary

ENV_OVERRIDE = "MISHE_CGROUP_COUNTERS_BIN"
BINARY_NAME = "mishe-cgroup-counters"


class NoBinary(RuntimeError):
    """No binary is configured; an isolated caller may use the reference decoder."""


class CgroupCountersUnknown(RuntimeError):
    """A configured binary cannot answer. Callers must treat the reading as UNKNOWN."""


def resolve_binary(explicit: str | None = None) -> Path:
    """Explicit argument, then the environment override, then the site install.

    An override must name an executable file. The site install must match the
    sha256 recorded beside it. A wrong or unverifiable install is a
    CgroupCountersUnknown; having no site at all is NoBinary.
    """
    chosen = explicit or os.environ.get(ENV_OVERRIDE)
    if chosen:
        path = Path(chosen)
        if not (path.is_file() and os.access(path, os.X_OK)):
            raise CgroupCountersUnknown(f"{BINARY_NAME} is not an executable file: {path}")
        return path
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        raise NoBinary(f"no {BINARY_NAME}: pass one, set {ENV_OVERRIDE}, or set a site home")
    path = Path(home) / "bin" / BINARY_NAME
    try:
        record = json.loads((path.parent / f"{BINARY_NAME}.install.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise CgroupCountersUnknown(f"{BINARY_NAME} install missing or unreadable: {exc}") from exc
    if not isinstance(record, dict):
        raise CgroupCountersUnknown(f"{BINARY_NAME} install record is not a JSON object")
    if not os.access(path, os.X_OK) or record.get("sha256") != digest:
        raise CgroupCountersUnknown(f"{BINARY_NAME} install is not executable or does not match its sha256")
    return path


def _rows(args: list[str], body: str) -> list[tuple[str, int]] | None:
    """Decode ``body`` with the binary; None for exit 2, raise for any other fault."""
    exe = resolve_binary()
    try:
        done = _run_binary([str(exe), *args], input=body.encode("utf-8"),
                           capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise CgroupCountersUnknown(f"{BINARY_NAME} could not run: {exc}") from exc
    if done.returncode == 2:
        return None
    if done.returncode != 0:
        raise CgroupCountersUnknown(f"{BINARY_NAME} exit {done.returncode}")
    try:
        output = done.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CgroupCountersUnknown(f"{BINARY_NAME} output is not UTF-8: {exc}") from exc
    rows: list[tuple[str, int]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not parts[0]:
            raise CgroupCountersUnknown(f"{BINARY_NAME} line outside contract: {line!r}")
        try:
            value = int(parts[1])
        except ValueError as exc:
            raise CgroupCountersUnknown(f"{BINARY_NAME} value outside contract: {line!r}") from exc
        rows.append((parts[0], value))
    return rows


def parse_cpu_stat(text: str | None, counters: tuple[str, ...]) -> dict[str, int] | None:
    """Counters from a cpu.stat body; None unless every named counter parses."""
    if not text:
        return None
    rows = _rows(["stat", *counters], text)
    return None if rows is None else dict(rows)


def parse_cpu_max_period(text: str | None) -> int | None:
    """Period from a cpu.max body; None unless it is a positive integer."""
    if not text:
        return None
    rows = _rows(["max-period"], text)
    return None if rows is None else dict(rows)["period"]
