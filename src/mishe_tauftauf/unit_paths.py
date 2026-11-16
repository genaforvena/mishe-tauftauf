"""Consumer-side reader for the paths a unit's declaration names (``mishe-unit-paths``).

The binary ``rust/mishe-unit-paths`` decodes the ``systemctl show`` property text
into the absolute paths the unit itself declares. This module owns binary
resolution and the stdout contract; callers act on the decoded paths and never
interpret the property text themselves.

There is no quiet fallback: a missing, unverified or failing binary, or an answer
outside the contract, raises UnitPathsUnknown so callers can treat the unit as
unreadable. The one declared exception is the binary's ``DEFER`` line: for input
containing a non-ASCII byte the Rust decoder asks the Python boundary in
:mod:`mishe_tauftauf.unit_properties` to answer, because Python's ``\\w`` and
``\\s`` are Unicode-aware where the Rust decoder is ASCII-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

# The boundary's own process runner, captured at import. A caller that mocks
# ``subprocess.run`` to stand in for systemctl must not also intercept this
# binary call: the binary is a separate process with its own stdout contract.
from subprocess import run as _run_binary

from .unit_properties import declared_paths as python_declared_paths

ENV_OVERRIDE = "MISHE_UNIT_PATHS_BIN"


class UnitPathsUnknown(RuntimeError):
    """The declared paths cannot be read. Callers must treat the unit as unreadable."""


def resolve_binary(explicit: str | None = None) -> Path:
    """Explicit argument, then the environment override, then the site install.

    An override must name an executable file. The site install
    ``$SITE/bin/mishe-unit-paths`` must match the sha256 recorded beside it in
    ``mishe-unit-paths.install.json``. Fails closed: no fallback to Python.
    """
    chosen = explicit or os.environ.get(ENV_OVERRIDE)
    if chosen:
        path = Path(chosen)
        if not (path.is_file() and os.access(path, os.X_OK)):
            raise UnitPathsUnknown(f"mishe-unit-paths is not an executable file: {path}")
        return path
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        raise UnitPathsUnknown(f"no mishe-unit-paths binary: pass one, set {ENV_OVERRIDE}, or set a site home")
    path = Path(home) / "bin" / "mishe-unit-paths"
    try:
        record = json.loads((path.parent / "mishe-unit-paths.install.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise UnitPathsUnknown(f"mishe-unit-paths install missing or unreadable: {exc}") from exc
    if not isinstance(record, dict):
        raise UnitPathsUnknown("mishe-unit-paths install record is not a JSON object")
    if not os.access(path, os.X_OK) or record.get("sha256") != digest:
        raise UnitPathsUnknown("mishe-unit-paths install is not executable or does not match its recorded sha256")
    return path


def declared_paths(text: str, *, binary: str | None = None) -> list[str]:
    """Absolute paths declared by ``text``, in order, or UnitPathsUnknown.

    Exit 0 with one absolute path per line is the result; a bare ``DEFER\\n``
    routes the original text to the Python boundary. Every other outcome is
    UNKNOWN, never an empty list that would look like a unit declaring nothing.
    """
    exe = resolve_binary(binary)
    try:
        done = _run_binary([str(exe)], input=text.encode("utf-8"),
                              capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise UnitPathsUnknown(f"mishe-unit-paths could not run: {exc}") from exc
    if done.returncode != 0:
        raise UnitPathsUnknown(f"mishe-unit-paths exit {done.returncode}")
    try:
        output = done.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnitPathsUnknown(f"mishe-unit-paths output is not UTF-8: {exc}") from exc
    if output == "DEFER\n":
        return python_declared_paths(text)
    paths = output.splitlines()
    for path in paths:
        if not path.startswith("/"):
            raise UnitPathsUnknown(f"mishe-unit-paths line outside contract: {path!r}")
    return paths
