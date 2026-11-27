import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import network_singbox_stream as ns  # noqa: E402


class StreamTest(unittest.TestCase):
    def test_msk_stamp_becomes_utc(self):
        out = ns.to_utc_line("Fri Nov 27 16:22:10 2026 daemon.err sing-box[1]: x")
        self.assertTrue(out.startswith("2026-11-27T13:22:10Z Fri Nov 27 16:22:10 2026"))

    def test_unstamped_line_is_skipped(self):
        self.assertIsNone(ns.to_utc_line("garbage"))

    def test_dedupe_and_failure_status(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            body = "Fri Nov 27 16:22:10 2026 daemon.err sing-box[1]: ERROR[2] x\n"
            fake = mock.Mock(returncode=0, stdout=body, stderr="")
            with mock.patch.object(ns.subprocess, "run", return_value=fake):
                st1 = ns.collect(home, now=0)
                st2 = ns.collect(home, now=60)
            self.assertEqual(st1["added_last_run"], 1)
            self.assertEqual(st2["added_last_run"], 0)
            self.assertEqual((home / ns.STREAM_NAME).read_text().count("sing-box"), 1)
            bad = mock.Mock(returncode=255, stdout="", stderr="no route")
            with mock.patch.object(ns.subprocess, "run", return_value=bad):
                st3 = ns.collect(home, now=120)
            self.assertFalse(st3["ok"])
            self.assertEqual(st3["last_success"], st2["last_success"])
            self.assertEqual(json.loads((home / ns.STATUS_NAME).read_text())["ok"], False)


if __name__ == "__main__":
    unittest.main()
