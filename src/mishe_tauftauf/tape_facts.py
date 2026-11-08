"""Consumer-side reader for the byte-level tape facts (``mishe-tape-facts``).

Pipeline: ``mishe-tape frames PATH | mishe-tape-facts``. The framer and the facts
extractor are Rust programs (``rust/mishe-tape``, ``rust/mishe-tape-facts``); this
module only runs them and parses their output. The contract is
``rust/mishe-tape-facts/CONTRACT.md``.

Discard-on-failure: a non-zero exit from either stage means the facts are not a
reading of the whole tape, so none of them are returned. The caller gets
``TapeUnknown`` and must report UNKNOWN, never fold a partial read.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .feed import TapeUnknown


@dataclass(frozen=True)
class DmFact:
    sequence: int
    timestamp: str
    source: str
    target: str


@dataclass(frozen=True)
class RefFact:
    sequence: int
    timestamp: str
    sha: str


@dataclass(frozen=True)
class OutFact:
    sequence: int
    timestamp: str
    role: str
    runs: str  # "-", "!" or comma-separated ASCII digit runs


@dataclass(frozen=True)
class DeferFact:
    sequence: int
    timestamp: str


TapeFact = DmFact | RefFact | OutFact | DeferFact


def facts_binary() -> str:
    """Resolve ``mishe-tape-facts``. Fails closed; never falls back to Python.

    ``MISHE_TAPE_FACTS_BIN`` is an explicit override (tests, scratch builds).
    Otherwise the site-local install ``$SITE/bin/mishe-tape-facts`` is used, and
    it must match the sha256 recorded beside it in ``mishe-tape-facts.install.json``.
    """
    override = os.environ.get("MISHE_TAPE_FACTS_BIN")
    if override:
        if not os.path.isfile(override) or not os.access(override, os.X_OK):
            raise TapeUnknown("MISHE_TAPE_FACTS_BIN names no executable file")
        return override
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        raise TapeUnknown("mishe-tape-facts unavailable: no site home and no MISHE_TAPE_FACTS_BIN")
    binary = Path(home) / "bin" / "mishe-tape-facts"
    try:
        record = json.loads((binary.parent / "mishe-tape-facts.install.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise TapeUnknown(f"mishe-tape-facts install missing or unreadable: {exc}") from exc
    if not os.access(binary, os.X_OK) or record.get("sha256") != digest:
        raise TapeUnknown("mishe-tape-facts install is not executable or its sha256 does not match the install record")
    return str(binary)


def parse_facts(output: bytes) -> list[TapeFact]:
    """Parse the facts stdout. Any line outside the contract is a TapeUnknown."""
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TapeUnknown(f"mishe-tape-facts output is not UTF-8: {exc}") from exc
    facts: list[TapeFact] = []
    for line in text.splitlines():
        parts = line.split(" ")
        kind = parts[0]
        try:
            if kind == "DM" and len(parts) == 5:
                facts.append(DmFact(int(parts[1]), parts[2], parts[3], parts[4]))
            elif kind == "REF" and len(parts) == 4:
                facts.append(RefFact(int(parts[1]), parts[2], parts[3]))
            elif kind == "OUT" and len(parts) == 5:
                facts.append(OutFact(int(parts[1]), parts[2], parts[3], parts[4]))
            elif kind == "DEFER" and len(parts) == 3:
                facts.append(DeferFact(int(parts[1]), parts[2]))
            else:
                raise TapeUnknown(f"mishe-tape-facts line outside contract: {line!r}")
        except ValueError as exc:
            raise TapeUnknown(f"mishe-tape-facts line outside contract: {line!r}") from exc
    return facts


def read_tape_facts(tape_path: str, *, tape_bin: str, facts_bin: str | None = None) -> list[TapeFact]:
    """Run ``tape_bin frames tape_path | facts_bin`` and return every fact, or raise.

    Raises ``TapeUnknown`` when either stage exits non-zero, when a stage cannot
    start, or when the output breaks the contract. No partial facts are returned.
    """
    facts_bin = facts_bin or facts_binary()
    with tempfile.TemporaryFile() as framer_err:
        try:
            framer = subprocess.Popen([tape_bin, "frames", tape_path],
                                      stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=framer_err)
        except OSError as exc:
            raise TapeUnknown(f"mishe-tape cannot start: {exc}") from exc
        try:
            extracted = subprocess.run([facts_bin], stdin=framer.stdout,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       timeout=300)
        except OSError as exc:
            framer.kill()
            framer.wait()
            raise TapeUnknown(f"mishe-tape-facts cannot start: {exc}") from exc
        finally:
            if framer.stdout:
                framer.stdout.close()
        framer_rc = framer.wait()
        framer_err.seek(0)
        framer_msg = framer_err.read().decode("utf-8", "replace").strip()
    if framer_rc != 0:
        raise TapeUnknown(f"mishe-tape frames exit {framer_rc}: {framer_msg}")
    if extracted.returncode != 0:
        msg = extracted.stderr.decode("utf-8", "replace").strip()
        raise TapeUnknown(f"mishe-tape-facts exit {extracted.returncode}: {msg}")
    return parse_facts(extracted.stdout)
