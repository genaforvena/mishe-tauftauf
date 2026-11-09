"""Consumer-side reader: src/mishe_tauftauf/tape_facts.py over the real Rust binaries.

Needs MISHE_TAPE_BIN (mishe-tape) and MISHE_TAPE_FACTS_BIN (mishe-tape-facts).
Without them the tests skip and say so; a skip is not a pass.
"""

import os

import pytest

from mishe_tauftauf.feed import TapeUnknown
from mishe_tauftauf.tape_facts import DmFact, OutFact, read_tape_facts

TAPE_BIN = os.environ.get("MISHE_TAPE_BIN", "")
FACTS_BIN = os.environ.get("MISHE_TAPE_FACTS_BIN", "")

needs_binaries = pytest.mark.skipif(
    not (TAPE_BIN and FACTS_BIN and os.path.isfile(TAPE_BIN) and os.path.isfile(FACTS_BIN)),
    reason="MISHE_TAPE_BIN and MISHE_TAPE_FACTS_BIN must name built binaries",
)


def _entry(seq, body: bytes) -> bytes:
    # Raw chat.log entry: header ending " ::", body lines prefixed "    | ", then "    .".
    assert b"\n" not in body
    return f"{seq:020d} 2026-11-08T12:00:00Z senses ::\n".encode() + b"    | " + body + b"\n    .\n"


@needs_binaries
def test_reads_facts_from_whole_tape(tmp_path):
    tape = tmp_path / "chat.log"
    tape.write_bytes(_entry(1, b"[dm] to=rust-plan hello")
                     + _entry(2, b"Wall outcome 9ebe by genome for 1"))
    facts = read_tape_facts(str(tape), tape_bin=TAPE_BIN, facts_bin=FACTS_BIN)
    assert facts == [
        DmFact(1, "2026-11-08T12:00:00Z", "senses", "rust-plan"),
        OutFact(2, "2026-11-08T12:00:00Z", "genome", "9,1"),  # every ASCII digit run in the body
    ]


@needs_binaries
def test_truncated_tape_discards_every_fact(tmp_path):
    # The first entry is complete and yields a DM; the second has no '    .' terminator.
    # The framer exits 2 after the first entry, so the reader must return nothing.
    tape = tmp_path / "chat.log"
    tape.write_bytes(_entry(1, b"[dm] to=rust-plan hello")
                     + b"00000000000000000002 2026-11-08T12:00:00Z senses ::\n    | abc")
    with pytest.raises(TapeUnknown, match="mishe-tape frames exit 2"):
        read_tape_facts(str(tape), tape_bin=TAPE_BIN, facts_bin=FACTS_BIN)


@needs_binaries
def test_missing_body_terminator_discards_every_fact(tmp_path):
    tape = tmp_path / "chat.log"
    tape.write_bytes(b"00000000000000000001 2026-11-08T12:00:00Z senses ::\n    | helloX\n")
    with pytest.raises(TapeUnknown):
        read_tape_facts(str(tape), tape_bin=TAPE_BIN, facts_bin=FACTS_BIN)


@needs_binaries
def test_missing_facts_binary_is_unknown(tmp_path):
    tape = tmp_path / "chat.log"
    tape.write_bytes(_entry(1, b"[dm] to=rust-plan hello"))
    with pytest.raises(TapeUnknown, match="cannot start"):
        read_tape_facts(str(tape), tape_bin=TAPE_BIN,
                        facts_bin=str(tmp_path / "no-such-facts"))


@needs_binaries
def test_hung_facts_stage_times_out_as_unknown(tmp_path):
    # A facts stage that never finishes must become UNKNOWN, not a raw
    # TimeoutExpired escaping the reader.
    import time
    hung = tmp_path / "hung-facts"
    hung.write_text("#!/bin/sh\nsleep 30\n")
    hung.chmod(0o755)
    tape = tmp_path / "chat.log"
    tape.write_bytes(_entry(1, b"[dm] to=rust-plan hello"))
    started = time.monotonic()
    with pytest.raises(TapeUnknown, match="timed out"):
        read_tape_facts(str(tape), tape_bin=TAPE_BIN, facts_bin=str(hung), timeout=0.5)
    assert time.monotonic() - started < 20


def test_unknown_output_line_is_unknown():
    from mishe_tauftauf.tape_facts import parse_facts

    with pytest.raises(TapeUnknown, match="outside contract"):
        parse_facts(b"DM 1 t senses\n")
    with pytest.raises(TapeUnknown, match="not UTF-8"):
        parse_facts(b"\xff\n")


def _site_with_facts_install(tmp_path, body: bytes, record_sha: str | None):
    import hashlib
    import json
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "mishe-tape-facts"
    binary.write_bytes(body)
    binary.chmod(0o755)
    digest = record_sha if record_sha is not None else hashlib.sha256(body).hexdigest()
    (bindir / "mishe-tape-facts.install.json").write_text(json.dumps({"sha256": digest}))
    return binary


def test_site_install_with_matching_sha_resolves(tmp_path, monkeypatch):
    from mishe_tauftauf.tape_facts import facts_binary
    binary = _site_with_facts_install(tmp_path, b"#!/bin/sh\nexit 0\n", None)
    monkeypatch.delenv("MISHE_TAPE_FACTS_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    assert facts_binary() == str(binary)


def test_site_install_with_sha_mismatch_is_unknown(tmp_path, monkeypatch):
    from mishe_tauftauf.tape_facts import facts_binary
    _site_with_facts_install(tmp_path, b"#!/bin/sh\nexit 0\n", "0" * 64)
    monkeypatch.delenv("MISHE_TAPE_FACTS_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    with pytest.raises(TapeUnknown):
        facts_binary()


def test_no_site_install_and_no_override_is_unknown(tmp_path, monkeypatch):
    from mishe_tauftauf.tape_facts import facts_binary
    monkeypatch.delenv("MISHE_TAPE_FACTS_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    with pytest.raises(TapeUnknown):
        facts_binary()
