"""Differential test: rust/mishe-tape-facts against the Python disposition regexes.

Run with MISHE_TAPE_FACTS_BIN pointing at a built binary. Without it the test is
skipped and says so; a skip is not a pass. For every fixture entry the binary's
DM/REF/OUT facts must equal discovery.py's regexes, or the entry must be marked
DEFER (or the OUT runs '!'), which hands that entry back to the Python regex.
"""

import os
import subprocess
from pathlib import Path

import pytest

from mishe_tauftauf.discovery import DM_TO_RE, RECORD_REF_RE, WALL_OUTCOME_RE

HEX = "a" * 64
HEX2 = "b" * 64

FIXTURES = [
    "[dm] to=senses hello",
    "[dm] to=rust-plan.x_1 hi",
    "[dm] to=chat",
    "[dm] to= empty",
    "[dm] to=Розалия hi",
    "[record] records/%s.json sha256=%s checked" % (HEX, HEX2),
    "prefix [record] records/%s.json sha256=%s" % (HEX, HEX2),
    "[record] records/%s.json sha256=%s" % (HEX.upper(), HEX2),
    "Wall outcome 9ebe by genome for 12 and 40",
    "Wall outcome a-b by rust-plan.x 7",
    "Wall outcome x by  (none)",
    "Wall outcome x by 1 2 3",
    "Wall outcome abc by role-1 no digits here",
    "Wall outcome a\tb by x 5",
    "Wall outcome a b by x 5",
    # 0x1C-0x1F: Python str `\s`/`\S` treat these as whitespace (isspace() is
    # True), so a header token stops there and " by " must follow a real space.
    "Wall outcome a\x1cb by x 5",
    "Wall outcome a\x1fb by x 5",
    "Wall outcome a\x1d by x 5",
    "Wall outcome a by\x1ex 5",
    "Wall outcome a by x\x1c5",
    "Wall outcome a\x1c",
    "Wall outcome a by x 1\x1f2",
    "Wall outcome a by x 1٣ 2",
    "Wall outcome a by x ٣ 12",
    "Wall outcome a by x 12é and 3",
    "Wall outcome a by x 1٣2",
    "plain text 12",
]


def _tape(bodies):
    parts = []
    for seq, body in enumerate(bodies, start=1):
        data = body.encode("utf-8")
        parts.append(f"E {seq} 2026-11-08T12:00:00Z senses {len(data)}\n".encode())
        parts.append(data + b"\n")
    return b"".join(parts)


def _run(binary, tape):
    proc = subprocess.run([binary], input=tape, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr.decode()
    return proc.stdout.decode("utf-8").splitlines()


def _python_cited(body, seq):
    import re
    return re.search(rf"(?<!\d){seq}(?!\d)", body) is not None


@pytest.fixture
def binary():
    value = os.environ.get("MISHE_TAPE_FACTS_BIN")
    if not value:
        pytest.skip("MISHE_TAPE_FACTS_BIN not set: differential not run")
    return value


def test_facts_match_python_regexes(binary):
    lines = _run(binary, _tape(FIXTURES))
    dm, ref, out, defer = {}, {}, {}, set()
    for line in lines:
        fields = line.split(" ")
        kind, seq = fields[0], int(fields[1])
        if kind == "DM":
            dm[seq] = fields[4]
        elif kind == "REF":
            ref[seq] = fields[3]
        elif kind == "OUT":
            out[seq] = fields[3:]
        elif kind == "DEFER":
            defer.add(seq)
        else:
            pytest.fail(f"unknown line kind: {line!r}")
    for seq, body in enumerate(FIXTURES, start=1):
        m = DM_TO_RE.match(body)
        assert (dm.get(seq) == (m.group(1) if m else None)), (seq, body)
        r = RECORD_REF_RE.search(body)
        assert (ref.get(seq) == (r.group(1) if r else None)), (seq, body)
        o = WALL_OUTCOME_RE.match(body) if body.startswith("Wall outcome ") else None
        if seq in defer:
            continue
        if o is None:
            assert seq not in out, (seq, body, out.get(seq))
            continue
        assert seq in out, (seq, body)
        role, runs = out[seq][0], out[seq][1]
        assert role == o.group(1), (seq, body)
        if runs == "!":
            continue
        for probe in range(1, 6):
            cited_rust = str(probe) in (runs.split(",") if runs != "-" else [])
            assert cited_rust == _python_cited(body, probe), (seq, body, probe, runs)


def test_truncated_tape_exits_2_after_partial_facts(binary):
    """Failure contract: a short body exits 2 even after facts were already written.

    The binary streams, so an early REF can precede the failure. The caller must
    discard every fact from a run whose exit status is not 0 (CONTRACT.md, exit 2).
    This test pins that the partial output exists, so no consumer can rely on it.
    """
    good = _tape([f"[record] records/{HEX}.json sha256={HEX2}"])
    truncated = _tape(["[dm] to=alice hello there"])[:-12]
    proc = subprocess.run([binary], input=good + truncated, capture_output=True, timeout=30)
    assert proc.returncode == 2, proc.returncode
    assert proc.stdout.startswith(b"REF 1 "), proc.stdout
    assert proc.stderr.startswith(b"mishe-tape-facts: short body"), proc.stderr


def test_missing_body_terminator_exits_2(binary):
    data = b"[dm] to=alice hi"
    tape = f"E 1 2026-11-08T12:00:00Z senses {len(data)}\n".encode() + data + b"X"
    proc = subprocess.run([binary], input=tape, capture_output=True, timeout=30)
    assert proc.returncode == 2, proc.returncode
    assert proc.stdout == b"", proc.stdout


def test_non_utf8_body_exits_2_without_facts(binary):
    """Fail closed: a body the framer would have rejected never yields a fact.

    `mishe-tape frames` rejects non-UTF-8 input, so this reaches the facts binary
    only from a caller that bypassed the framer. The binary must not emit an OUT
    line for it and exit 0.
    """
    body = b"Wall outcome ok by ops 7 \xff"
    tape = f"E 1 2026-11-08T12:00:00Z senses {len(body)}\n".encode() + body + b"\n"
    proc = subprocess.run([binary], input=tape, capture_output=True, timeout=30)
    assert proc.returncode == 2, proc.returncode
    assert proc.stdout == b"", proc.stdout
    assert proc.stderr.startswith(b"mishe-tape-facts: body is not UTF-8"), proc.stderr
