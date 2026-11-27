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

    def _run_remote(self, logread_body, logread_exit):
        """Run the real REMOTE string under sh with a fake logread on PATH (the router's shell is sh)."""
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "logread"
            fake.write_text(f"#!/bin/sh\nprintf '%s' '{logread_body}'\nexit {logread_exit}\n")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}")
            return subprocess.run(["sh", "-c", ns.REMOTE], capture_output=True, text=True, env=env)

    def test_failed_logread_is_nonzero_even_with_empty_output(self):
        r = self._run_remote("", 1)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_successful_logread_with_no_sing_box_lines_is_zero(self):
        r = self._run_remote("Fri Nov 27 16:22:10 2026 daemon.info dnsmasq: x", 0)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_sing_box_lines_pass_through(self):
        r = self._run_remote("Fri Nov 27 16:22:10 2026 daemon.err sing-box[1]: ERROR[2] x", 0)
        self.assertEqual(r.returncode, 0)
        self.assertIn("sing-box[1]", r.stdout)


if __name__ == "__main__":
    unittest.main()
