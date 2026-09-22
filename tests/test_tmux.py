from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from mishe_tauftauf.cli import initialize
from mishe_tauftauf.tmux import capture_raw, check_pane, repair_top, start, stop


@unittest.skipUnless(os.environ.get("PATH") and __import__("shutil").which("tmux"), "tmux unavailable")
class TmuxTests(unittest.TestCase):
    def test_real_red_green_surface_and_advancing_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            fixture = home / "fixture"; fixture.write_text("red\n", encoding="utf-8")
            freeze = home / "freeze"
            renderer = home / "top-pains" / "sensor"
            renderer.write_text(
                f"#!/bin/sh\n[ -e {freeze} ] && sleep 30\nprintf 'DESIRED STATE: green\\n'; cat {fixture}\n",
                encoding="utf-8",
            )
            renderer.chmod(0o755)
            session = f"mishe-tauftauf-test-{os.getpid()}"
            try:
                start(home, session, interval=0.2)
                time.sleep(0.5)
                red = capture_raw(session, "sensor")
                self.assertIn("red", red)
                lease1 = [line for line in red.splitlines() if "-- pane live " in line][-1]
                time.sleep(0.4)
                lease2 = [line for line in capture_raw(session, "sensor").splitlines() if "-- pane live " in line][-1]
                self.assertNotEqual(lease1, lease2)
                fixture.write_text("green\n", encoding="utf-8")
                time.sleep(0.4)
                self.assertIn("green", capture_raw(session, "sensor"))
                ok, line = check_pane(home, session, "sensor", wait=0.4)
                self.assertTrue(ok, line)
                # Hang the owned renderer inside its bounded probe: the lease must freeze.
                freeze.touch()
                time.sleep(0.3)
                ok, line = check_pane(home, session, "sensor", wait=0.5)
                self.assertFalse(ok)
                self.assertIn("pane-frozen", line)
                freeze.unlink()
                repaired, detail = repair_top(home, session, "sensor")
                self.assertTrue(repaired, detail)
                time.sleep(0.5)
                self.assertIn("green", capture_raw(session, "sensor"))
                repaired, detail = repair_top(home, session, "sensor")
                self.assertFalse(repaired)
                self.assertIn("recurrence within", detail)
            finally:
                stop(home, session)


if __name__ == "__main__":
    unittest.main()
