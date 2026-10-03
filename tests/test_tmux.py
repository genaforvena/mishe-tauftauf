from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mishe_tauftauf.cli import initialize, main
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
                deadline = time.monotonic() + 2.0
                lease2 = lease1
                while time.monotonic() < deadline:
                    captured = capture_raw(session, "sensor")
                    leases = [line for line in captured.splitlines() if "-- pane live " in line]
                    if leases:
                        lease2 = leases[-1]
                        if lease2 != lease1:
                            break
                    time.sleep(0.05)
                self.assertNotEqual(lease1, lease2, "pane-live lease did not advance within 2 seconds")
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

    def test_check_pane_retries_one_missed_lease_window(self):
        # A loaded host can starve a healthy renderer past one window, so a single
        # unchanged lease must not be reported as a frozen pane; two consecutive
        # unchanged windows still must.
        from unittest import mock
        from mishe_tauftauf import tmux as tmux_module
        lease = "-- pane live 2026-10-01T00:00:{:02d}.000000Z · refresh 5s · ticks every frame --"
        for samples, expected in (([1, 1, 5], True), ([1, 1, 1], False)):
            with self.subTest(samples=samples):
                seen = []

                def capture(session, slug, samples=samples, seen=seen):
                    seen.append(1)
                    return "BODY\n" + lease.format(samples[min(len(seen), len(samples)) - 1]) + "\n"

                with mock.patch.object(tmux_module, "_pane_stopped_or_dead", return_value=False), \
                        mock.patch.object(tmux_module, "capture_raw", side_effect=capture):
                    ok, line = check_pane(Path("/nonexistent"), "session", "sensor", wait=0.01)
                self.assertEqual(ok, expected, line)
                if not expected:
                    self.assertIn("pane-frozen", line)

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



    def test_doctor_panes_defaults_to_the_plant_recorded_session(self):
        # Regression: `doctor --panes` without --session used the literal
        # "mishe-tauftauf", which a custom-named plant does not own, so the check
        # reported every pane as a dead renderer instead of resolving the session
        # recorded in .seed-raised.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            renderer = home / "top-pains" / "sensor"
            renderer.write_text("#!/bin/sh\nprintf 'DESIRED STATE: green\\n'\n", encoding="utf-8")
            renderer.chmod(0o755)
            session = f"mishe-tauftauf-test-{os.getpid()}-default"
            with mock.patch.dict(os.environ):
                os.environ.pop("MISHE_SEED_SESSION", None)
                try:
                    start(home, session, interval=0.2)
                    (home / ".seed-raised").write_text(f"{session} $1\n", encoding="utf-8")
                    for _ in range(40):
                        if "-- pane live " in capture_raw(session, "sensor"):
                            break
                        time.sleep(0.1)
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        code = main(["--home", str(home), "doctor", "--panes", "--pane-wait", "0.4"])
                finally:
                    stop(home, session)
            output = buffer.getvalue()
            self.assertEqual(code, 0, output)
            self.assertIn("PASS pane-live: sensor", output)
            self.assertNotIn("renderer process is stopped or dead", output)

    def test_doctor_panes_reports_an_unowned_session_honestly(self):
        # A session this home does not own is an addressing mistake, not ten dead
        # renderers: report it once as UNKNOWN and fail.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            renderer = home / "top-pains" / "sensor"
            renderer.write_text("#!/bin/sh\nprintf 'DESIRED STATE: green\\n'\n", encoding="utf-8")
            renderer.chmod(0o755)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["--home", str(home), "doctor", "--panes",
                             "--session", "mishe-tauftauf-unowned", "--pane-wait", "0.1"])
            output = buffer.getvalue()
            self.assertEqual(code, 1, output)
            self.assertIn("UNKNOWN — session 'mishe-tauftauf-unowned' is not owned", output)
            self.assertNotIn("renderer process is stopped or dead", output)

    def test_doctor_panes_treats_an_unconfigured_renderer_as_information(self):
        # A renderer with no configured resident window (observability, legacy
        # one-shot surfaces) must not read as a RED failure that no service can
        # clear. Only the site's declared windows are checked for liveness.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            renderer = home / "top-pains" / "sensor"
            renderer.write_text("#!/bin/sh\nprintf 'DESIRED STATE: green\\n'\n", encoding="utf-8")
            renderer.chmod(0o755)
            session = f"mishe-tauftauf-test-{os.getpid()}-configured"
            with mock.patch.dict(os.environ):
                os.environ.pop("MISHE_SEED_SESSION", None)
                try:
                    start(home, session, interval=0.2)
                    (home / ".seed-raised").write_text(f"{session} $1\n", encoding="utf-8")
                    (home / "health").mkdir(parents=True, exist_ok=True)
                    (home / "health" / "windows.json").write_text('["sensor"]', encoding="utf-8")
                    # A renderer created after the plant has no window; it is a
                    # surface, not a missing resident channel.
                    extra = home / "top-pains" / "observability"
                    extra.write_text("#!/bin/sh\nprintf 'DESIRED STATE: bounded\\n'\n", encoding="utf-8")
                    extra.chmod(0o755)
                    for _ in range(40):
                        if "-- pane live " in capture_raw(session, "sensor"):
                            break
                        time.sleep(0.1)
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        code = main(["--home", str(home), "doctor", "--panes", "--pane-wait", "0.4"])
                finally:
                    stop(home, session)
            output = buffer.getvalue()
            self.assertEqual(code, 0, output)
            self.assertIn("PASS pane-live: sensor", output)
            self.assertIn("INFO pane-surface: observability", output)
            self.assertNotIn("pane-missing: observability", output)

    def test_doctor_panes_reports_a_dead_chartered_mind(self):
        # The renderer lease cannot see a dead mind pane, so doctor must check
        # `.1` for chartered roles. Regression for the tiny-fleet docs mind that
        # died at status 127 while its top pane stayed live and green.
        from mishe_tauftauf.tmux import OWNED_OPTION, _python_command, _tmux
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            renderer = home / "top-pains" / "docs"
            renderer.write_text("#!/bin/sh\nprintf 'DESIRED STATE: green\\n'\n", encoding="utf-8")
            renderer.chmod(0o755)
            (home / "health").mkdir(parents=True, exist_ok=True)
            (home / "health" / "windows.json").write_text('["docs"]', encoding="utf-8")
            session = f"mishe-tauftauf-test-{os.getpid()}-mind"
            try:
                _tmux("new-session", "-d", "-s", session, "-c", str(home))
                _tmux("set-option", "-t", session, OWNED_OPTION, str(home.resolve()))
                _tmux("new-window", "-d", "-t", session, "-n", "docs", "-c", str(home), "sh")
                _tmux("split-window", "-v", "-t", f"{session}:docs", "-c", str(home), "sh")
                _tmux("set-option", "-p", "-t", f"{session}:docs.1", "remain-on-exit", "on")
                command = ("env", f"MISHE_SEED_SESSION={session}",
                           *_python_command("--home", str(home), "pain", "watch", "docs", "--interval", "0.2"))
                _tmux("respawn-pane", "-k", "-t", f"{session}:docs.0", *command)
                for _ in range(40):
                    if "-- pane live " in capture_raw(session, "docs"):
                        break
                    time.sleep(0.1)
                # Kill the mind pane with a distinct status; remain-on-exit keeps
                # it visible so `.1` reads dead while `.0` still advances.
                _tmux("respawn-pane", "-k", "-t", f"{session}:docs.1", "sh", "-c", "exit 7")
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = main(["--home", str(home), "doctor", "--panes",
                                 "--session", session, "--pane-wait", "0.4"])
            finally:
                _tmux("kill-session", "-t", session, check=False)
            output = buffer.getvalue()
            self.assertEqual(code, 1, output)
            self.assertIn("PASS pane-live: docs", output)
            self.assertIn("HOLD mind-pane-dead: docs", output)


    def test_recorded_session_reads_the_raise_receipt(self):
        from mishe_tauftauf.seed import recorded_session
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertIsNone(recorded_session(home))
            (home / ".seed-raised").write_text("planted-session $7\n", encoding="utf-8")
            self.assertEqual(recorded_session(home), "planted-session")
            (home / ".seed-raised").write_text("", encoding="utf-8")
            self.assertIsNone(recorded_session(home))

class MindPaneTests(unittest.TestCase):
    def test_check_mind_pane_reports_missing_dead_and_live(self):
        # `check_pane` reads `.0` only, so a dead chartered mind pane stayed
        # invisible. Cover the three pane states without needing tmux.
        from mishe_tauftauf import tmux as tmux_module
        from mishe_tauftauf.tmux import check_mind_pane

        def result(returncode, stdout):
            return subprocess.CompletedProcess([], returncode, stdout.encode(), b"")

        with mock.patch.object(tmux_module, "_tmux", return_value=result(0, "0")):
            ok, line = check_mind_pane("session", "docs")
        self.assertTrue(ok, line)
        self.assertIn("mind-pane-live: docs", line)
        with mock.patch.object(tmux_module, "_tmux", return_value=result(0, "1")):
            ok, line = check_mind_pane("session", "docs")
        self.assertFalse(ok, line)
        self.assertIn("mind-pane-dead: docs", line)
        with mock.patch.object(tmux_module, "_tmux", return_value=result(1, "")):
            ok, line = check_mind_pane("session", "docs")
        self.assertFalse(ok, line)
        self.assertIn("mind-pane-missing: docs", line)


if __name__ == "__main__":
    unittest.main()
