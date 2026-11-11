"""Differential checks for ``mishe-tape frames --facts``.

The ``F`` facts are ``outcome_publication``'s own line predicates; this test
computes those predicates in Python from each entry's body and compares them
field by field with the binary's output. It also pins the additive promise:
without ``--facts`` the entry stream is unchanged.

Runs when a framer resolves (``MISHE_TAPE_BIN``, then the site install; see
``tests/tape_binary.py``). A resolved binary that does not know ``--facts`` is
skipped with the reason, so a checkout against an older install reports a
missing capability, not a false fact mismatch.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from mishe_tauftauf import outcome_publication as op
from mishe_tauftauf.feed import _encode_entry

from tests.tape_binary import TAPE_BIN, TAPE_SKIP_REASON

_D = "e" * 64
_OTHER = "a" * 64
_REF = f"[record] records/{_D}.json sha256={_D}"

BODIES = [
    "plain text, no publication\n",
    "trailing text [record] inline, not line-leading\n",
    "Wall outcome accepted by genome\n",
    "\n  \nWall outcome accepted by rust-plan\n",              # reserved, not first raw line
    f"{_REF}\n",                                               # single-line reference
    f"Wall outcome accepted by genome\n{_REF}\n",              # reserved + final reference
    f"{_REF}\n\n",                                             # reference, then blank (not last)
    f"{_REF}\n{_REF}\n",                                       # two references
    f"note\n{_REF}\ntrailing prose\n",                          # reference not last
    f"  {_REF}\n",                                             # indented reference: no digest
    f"Wall outcome blocked by w\u00a0x\n",                      # NBSP in header token
    "\u00a0\u00a0Wall outcome accepted by role\n",             # NBSP leading: Python strips it
    "\u001fWall outcome accepted by role\n",                   # US leading: Python strips it, splitlines does not split it
    "crlf body\r\nsecond line\r\n",
    f"Wall outcome hypothesis-changed by a-b_c.d\n{_REF}\r\n",  # CRLF terminator on the reference
]


def run_frames(data: bytes, *extra: str):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chat.log"
        path.write_bytes(data)
        proc = subprocess.run([TAPE_BIN, "frames", str(path), "--size", str(len(data)), *extra],
                              stdin=subprocess.DEVNULL, capture_output=True)
    return proc.stdout, proc.returncode, proc.stderr.decode("utf-8", "replace").strip()


def build_tape(bodies):
    data = b""
    for n, body in enumerate(bodies, 1):
        data += _encode_entry(n, "2026-11-08T00:00:00.000000Z", "genome", body)
    return data


def parse_with_facts(out: bytes):
    """Return ``[(body, facts)]`` from a ``--facts`` run, in tape order.

    ``facts`` is ``(reserved, references, sole_final, single_line, digest)``.
    """
    items, pos = [], 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        parts = out[pos:nl].decode("ascii").split(" ")
        assert parts[0] == "E", parts
        count = int(parts[4])
        body = out[nl + 1:nl + 1 + count].decode("utf-8")
        assert out[nl + 1 + count:nl + 2 + count] == b"\n"
        f_end = out.index(b"\n", nl + 2 + count)
        fields = out[nl + 2 + count:f_end].decode("ascii").split(" ")
        assert fields[0] == "F" and len(fields) == 7, fields
        assert fields[1] == str(parts[1]), (fields, parts)
        digest = None if fields[6] == "-" else fields[6]
        items.append((body, (fields[2] == "1", int(fields[3]), fields[4] == "1", fields[5] == "1", digest)))
        pos = f_end + 1
    return items


def python_facts(body: str):
    lines = op.body_lines(body)
    reserved = op.has_outcome_prefix(op.first_significant_line(lines))
    refs = op.reference_lines(lines)
    sole_final = op.sole_final_reference(lines) is not None
    digest = None if not refs else op.reference_digest(refs[0])
    return (reserved, len(refs), sole_final, len(lines) == 1, digest)


def binary_supports_facts() -> str | None:
    """Return a skip reason when the resolved binary does not know ``--facts``."""
    out, rc, _ = run_frames(build_tape(["x\n"]), "--facts")
    if rc != 0 or b"\nF " not in out:
        return f"{TAPE_BIN} predates --facts (exit {rc}); install the current framer"
    return None


@unittest.skipUnless(TAPE_BIN, TAPE_SKIP_REASON)
class FrameFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = binary_supports_facts()
        if reason:
            raise unittest.SkipTest(reason)

    def test_facts_match_the_python_predicates(self):
        data = build_tape(BODIES)
        out, rc, err = run_frames(data, "--facts")
        self.assertEqual(rc, 0, err)
        parsed = parse_with_facts(out)
        self.assertEqual(len(parsed), len(BODIES))
        for body, facts in parsed:
            with self.subTest(body=body):
                self.assertEqual(facts, python_facts(body))

    def test_facts_are_additive_and_the_default_stream_is_unchanged(self):
        data = build_tape(BODIES)
        plain, rc_plain, err = run_frames(data)
        self.assertEqual(rc_plain, 0, err)
        with_facts, rc_facts, err = run_frames(data, "--facts")
        self.assertEqual(rc_facts, 0, err)
        # The plain stream is exactly the with-facts stream with every F line removed.
        rebuilt = b"".join(
            block if block.startswith(b"E ") else b""
            for block in _split_records(with_facts))
        self.assertEqual(rebuilt, plain)

    def test_offsets_and_facts_cannot_be_combined(self):
        _, rc, _ = run_frames(build_tape(["x\n"]), "--offsets", "--facts")
        self.assertEqual(rc, 1)


def _split_records(out: bytes):
    """Split a framer stream into ``E``-header records, dropping ``F``/``O`` lines."""
    records, pos = [], 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        head = out[pos:nl]
        if head.startswith(b"E "):
            count = int(head.decode("ascii").split(" ")[4])
            end = nl + 2 + count
            records.append(out[pos:end])
            pos = end
        else:
            pos = nl + 1
    return records


if __name__ == "__main__":
    unittest.main()
