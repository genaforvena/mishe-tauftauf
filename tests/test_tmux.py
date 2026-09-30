from __future__ import annotations

import os
import subprocess
import sys
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
                for _ in range(40):
                    red = capture_raw(session, "sensor")
                    if "red" in red and "-- pane live " in red:
                        break
                    time.sleep(0.1)
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

    def test_pain_read_rejects_a_home_that_does_not_own_the_session(self):
        # Regression: `pain read --launcher tmux` used to ignore --home and read
        # whichever pane the session named, so a typo'd home returned the real
        # site's GREEN pane and exit 0, hiding the mistake.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            renderer = home / "top-pains" / "sensor"
            renderer.write_text("#!/bin/sh\nprintf 'DESIRED STATE: green\\n'\n", encoding="utf-8")
            renderer.chmod(0o755)
            impostor = home / ".mishe-tuftauf"
            # An initialized but unrelated site reaches the ownership gate;
            # uninitialized typo homes are rejected earlier by site validation.
            initialize(impostor)
            session = f"mishe-tauftauf-test-{os.getpid()}"
            try:
                start(home, session, interval=0.2)
                for _ in range(40):
                    if "green" in capture_raw(session, "sensor"):
                        break
                    time.sleep(0.25)
                self.assertIn("green", capture_raw(session, "sensor"))
                wrong = subprocess.run(
                    [sys.executable, "-m", "mishe_tauftauf", "--home", str(impostor),
                     "pain", "read", "sensor", "--launcher", "tmux", "--session", session],
                    capture_output=True, text=True)
                self.assertEqual(wrong.returncode, 1, wrong.stderr)
                self.assertIn("is not owned by", wrong.stderr)
                right = subprocess.run(
                    [sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                     "pain", "read", "sensor", "--launcher", "tmux", "--session", session],
                    capture_output=True, text=True)
                self.assertEqual(right.returncode, 0, right.stderr)
                self.assertIn("green", right.stdout)
            finally:
                stop(home, session)


if __name__ == "__main__":
    unittest.main()
