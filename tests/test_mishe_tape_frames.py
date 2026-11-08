"""Framing contract checks for the mishe-tape binary.

Expected answers live in ``tests/fixtures/tape_framing_golden.json``. They were
recorded from the retired Python whole-tape reader before its deletion; the
binary's contract is ``rust/mishe-tape/CONTRACT.md``. Runs when a binary resolves
(MISHE_TAPE_BIN, then the site install; see tests/tape_binary.py), else skips with
the reason.
"""
import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.tape_binary import TAPE_BIN, TAPE_SKIP_REASON

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "tape_framing_golden.json")
                    .read_text(encoding="utf-8"))["cases"]


def run_frames(data: bytes, *extra: str):
    """Return (stdout bytes, exit status, stderr text) for one framer run over ``data``."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chat.log"
        path.write_bytes(data)
        proc = subprocess.run([TAPE_BIN, "frames", str(path), "--size", str(len(data)), *extra],
                              stdin=subprocess.DEVNULL, capture_output=True)
    return proc.stdout, proc.returncode, proc.stderr.decode("utf-8", "replace").strip()


def parse_entries(out: bytes):
    """Decode the binary's ``E`` records exactly as the contract describes them."""
    items, pos = [], 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        parts = out[pos:nl].decode("ascii").split(" ")
        assert parts[0] == "E", parts
        count = int(parts[4])
        body = out[nl + 1:nl + 1 + count].decode("utf-8")
        assert out[nl + 1 + count:nl + 2 + count] == b"\n", "body not newline-terminated"
        items.append(["E", int(parts[1]), parts[2], parts[3], body])
        pos = nl + 2 + count
    return items


@unittest.skipUnless(TAPE_BIN, TAPE_SKIP_REASON)
class FramingGolden(unittest.TestCase):
    def test_every_golden_case(self):
        for name, case in GOLDEN.items():
            with self.subTest(case=name):
                data = base64.b64decode(case["data_b64"])
                out, rc, err = run_frames(data)
                if case["error"] is None:
                    self.assertEqual(rc, 0, err)
                    self.assertEqual(parse_entries(out), case["entries"])
                elif name.startswith("utf8_"):
                    # Documented difference (CONTRACT.md): the detail text may differ and
                    # entries before the bad bytes are framed; callers fold none of them.
                    self.assertEqual(rc, 2)
                    self.assertTrue(err.startswith("feed is not UTF-8"), err)
                else:
                    self.assertEqual(rc, 2, err)
                    # The golden entries are empty because the retired whole-tape reader
                    # raised before returning. The contract writes the entries before a
                    # fault, and callers fold none of them; only the fault text is compared.
                    self.assertEqual(err, case["error"])

    def test_offsets_are_absolute_frame_starts(self):
        data = (b"v1 00000000000000000001 2026-11-07T23:00:00.123456Z genome ::\n    | a\n    .\n"
                b"v1 00000000000000000002 2026-11-07T23:00:00.123456Z genome ::\n    | b\r\n    | c\n    .\n")
        out, rc, err = run_frames(data, "--offsets")
        self.assertEqual(rc, 0, err)
        second = data.index(b"v1 00000000000000000002")
        self.assertEqual(out.decode("ascii").splitlines(),
                         [f"O 1 0", f"O 2 {second}"])

    def test_unknown_flag_is_a_usage_error(self):
        _, rc, _ = run_frames(b"", "--no-such-flag")
        self.assertEqual(rc, 1)


@unittest.skipUnless(TAPE_BIN, TAPE_SKIP_REASON)
class HeaderDigits(unittest.TestCase):
    """Headers accept ASCII digits only (CONTRACT.md, framing rules)."""

    def test_ascii_sequence_frames(self):
        data = b"00000000000000000001 2026-11-08T00:00:00.5Z chat ::\n    | ok\n    .\n"
        out, rc, err = run_frames(data)
        self.assertEqual(rc, 0, err)
        self.assertEqual(parse_entries(out), [["E", 1, "2026-11-08T00:00:00.5Z", "chat", "ok\n"]])

    def test_unicode_digit_sequence_is_a_fault(self):
        data = ("\u0660" * 20 + " 2026-11-08T00:00:00Z chat ::\n    | ok\n    .\n").encode("utf-8")
        _, rc, err = run_frames(data)
        self.assertEqual(rc, 2)
        self.assertEqual(err, "malformed header at line 1")
