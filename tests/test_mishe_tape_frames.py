"""Byte-equality of the Rust tape framing core with the Python reader.

Runs only when MISHE_TAPE_BIN names a built mishe-tape binary (see
rust/mishe-tape). Otherwise it skips, so the default test run needs no toolchain.
"""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from mishe_tauftauf import feed as F

BIN = os.environ.get("MISHE_TAPE_BIN")


def header(seq, ts="2026-11-07T23:00:00.123456Z", src="genome", v1=False):
    return ("v1 " if v1 else "") + f"{seq:020d} {ts} {src} ::\n"


def python_items(data: bytes):
    import io
    handle = io.BytesIO(data)
    try:
        for entry in F._iter_streamed_feed(handle, size=len(data)):
            yield ("E", entry.sequence, entry.timestamp, entry.source, entry.body)
    except F.FeedError as exc:
        yield ("ERR", str(exc))


def rust_items(path: Path, size: int):
    proc = subprocess.Popen([BIN, "frames", str(path), "--size", str(size)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out = proc.stdout
        while True:
            line = out.readline()
            if not line:
                break
            parts = line.decode("utf-8").rstrip("\n").split(" ")
            seq, ts, src, count = int(parts[1]), parts[2], parts[3], int(parts[4])
            body = out.read(count).decode("utf-8")
            assert out.read(1) == b"\n"
            yield ("E", seq, ts, src, body)
        rc = proc.wait()
        err = proc.stderr.read().decode("utf-8").strip()
    finally:
        proc.stdout.close()
        proc.stderr.close()
        proc.wait()
    if rc == 2:
        yield ("ERR", err)
    else:
        assert rc == 0, (rc, err)


@unittest.skipUnless(BIN and Path(BIN).is_file(), "MISHE_TAPE_BIN not set to a built mishe-tape")
class FramingEquality(unittest.TestCase):
    def assertSameStream(self, data: bytes):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat.log"
            path.write_bytes(data)
            self.assertEqual(list(rust_items(path, len(data))), list(python_items(data)))

    def test_valid_and_framing_variants(self):
        self.assertSameStream((header(1) + "    | hello\n    .\n" + header(2, v1=True)
                               + "    | a\r\n    | b\n    .-\n").encode())

    def test_python_line_breaks_inside_bodies(self):
        for sep in ("\r", "\x0b", "\x0c", "\x1c", "\x85", " "):
            with self.subTest(separator=repr(sep)):
                self.assertSameStream((header(1) + f"    | a{sep}b\n    .\n").encode("utf-8"))

    def test_malformed_faults_have_identical_text(self):
        cases = [
            header(1, "2026-02-30T00:00:00Z") + "    | x\n    .\n",
            header(1, "2026-01-01T24:00:00Z") + "    | x\n    .\n",
            header(1, "2026-01-01T00:00:60Z") + "    | x\n    .\n",
            header(1, "2027-02-29T00:00:00Z") + "    | x\n    .\n",
            header(1, "2026-01-01T00:00:00.1234567Z") + "    | x\n    .\n",
            header(1) + "    | a\n    .\n" + header(3) + "    | b\n    .\n",
            header(1) + "    | a\n",
            header(1) + "    .\n",
            header(1) + "  | a\n    .\n",
            "not a header\n",
            header(1)[:-1],
            "00000000000000000001 2026-01-01T00:00:00Z gen+me ::\n    | a\n    .\n",
        ]
        for text in cases:
            with self.subTest(text=text[:40]):
                self.assertSameStream(text.encode("utf-8"))

    def test_offsets_match_the_checkpoint_frame_index(self):
        import tempfile
        data = (header(1) + "    | a\n    .\n" + header(2) + "    | b\r\n    | c\n    .\n"
                + header(3) + "    | d\n    .-\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat.log"
            path.write_bytes(data)
            proc = subprocess.run([BIN, "frames", str(path), "--size", str(len(data)), "--offsets"],
                                  capture_output=True, text=True)
            rust = [int(line.split()[2]) for line in proc.stdout.splitlines() if line.startswith("O ")]
        import io
        with io.BytesIO(data) as handle:
            self.assertEqual(rust, F._frame_offsets(handle, 0, len(data)))

    def test_utf8_error_fails_closed_with_documented_text_difference(self):
        # Documented exception: both sides stop with a fault and no entries
        # before it; only the decoder's own message text differs.
        import tempfile
        data = (header(1) + "    | a\xff\n    .\n").encode("latin-1")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat.log"
            path.write_bytes(data)
            rust = list(rust_items(path, len(data)))
        python = list(python_items(data))
        self.assertEqual(rust[0][0], "ERR")
        self.assertEqual(python[0][0], "ERR")
        self.assertTrue(rust[0][1].startswith("feed is not UTF-8"))
        self.assertTrue(python[0][1].startswith("feed is not UTF-8"))


if __name__ == "__main__":
    unittest.main()
