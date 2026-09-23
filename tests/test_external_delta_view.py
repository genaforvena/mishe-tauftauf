import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mishe_tauftauf.cli import initialize
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig


def executable(path, body):
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)


class ExternalDeltaViewTests(unittest.TestCase):
    def coordinator(self, home, capture):
        judge = home / "judge"
        executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").open('a').write(sys.stdin.read() + '\\n---\\n')\nprint('probability 0.9')\n")
        runner = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False, external_delta_view_slugs=("sensor",)))
        runner.control_failures = {}
        capture.write_text("")
        return runner

    def test_changed_pair_is_named_and_receipt_versioned_without_raw_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            runner = self.coordinator(home, capture)
            judged = runner._judge("publish", "sensor", "SECRET-RAW-PANE", "STATE: RED\n", previous_projection="STATE: GREEN\n")
            self.assertEqual(judged.outcome, "yes")
            sent = capture.read_text()
            self.assertIn('"previous": "STATE: GREEN\\n"', sent)
            self.assertIn('"current": "STATE: RED\\n"', sent)
            self.assertNotIn("SECRET", sent)
            runner._receipt(judged, "sensor", 0)
            self.assertIn("question-version=projected-pair-v1.1", runner.feed.entries()[-1].body)

    def test_identical_pair_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            runner = self.coordinator(home, capture)
            judged = runner._judge("publish", "sensor", "RAW", "STATE: RED\n", previous_projection="STATE: RED\n")
            self.assertEqual(judged.outcome, "yes")
            self.assertIn('"previous": "STATE: RED\\n"', capture.read_text())

    def test_missing_or_malformed_side_is_unknown_without_provider_or_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            runner = self.coordinator(home, capture)
            for previous, current in ((None, "STATE: RED\n"), ("STATE: UNKNOWN\n", "STATE: RED\n"), ("STATE: GREEN\n", "STATE: RED\nSECRET")):
                judged = runner._judge("publish", "sensor", "SECRET-RAW", current, previous_projection=previous)
                self.assertEqual(judged.outcome, "unknown")
                self.assertNotIn("SECRET", judged.document)
            self.assertEqual(capture.read_text(), "")
            self.assertNotIn("SECRET", runner.feed.read_bytes().decode())

    def test_batch_questions_never_receive_raw_pane_or_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").open('a').write(sys.stdin.read())\nprint('{\"results\":{}}')\n")
            runner = Coordinator(RuntimeConfig(home, batch_judge=judge, launcher="headless", dispatch=False, external_delta_view_slugs=("sensor",)))
            runner.control_failures = {}
            capture.write_text("")
            judgments = runner._judge_batch(("prediction-met", "desired-state-met"), "sensor", "SECRET-RAW", "SECRET-EVIDENCE")
            self.assertEqual({item.outcome for item in judgments.values()}, {"unknown"})
            self.assertEqual(capture.read_text(), "")
            self.assertTrue(all("SECRET" not in item.document for item in judgments.values()))

    def test_observe_only_uses_last_published_safe_view_and_never_dispatches(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "print('SECRET-RAW-RENDERER')\n")
            executable(home / "projectors" / "sensor", "print('STATE: RED')\n")
            capture = home / "requests"
            runner = self.coordinator(home, capture)
            runner.observe()
            self.assertEqual(capture.read_text(), "")
            self.assertTrue(any("judged publish" in entry.body and "unknown" in entry.body for entry in runner.feed.entries()))
            runner.feed.append_runtime("observation/sensor", "STATE: GREEN\n")
            executable(home / "top-pains" / "sensor", "print('SECRET-RAW-RENDERER-CHANGED')\n")
            runner.observe()
            self.assertIn('"previous": "STATE: GREEN\\n"', capture.read_text())
            self.assertNotIn("SECRET", capture.read_text())
            self.assertFalse(any(entry.body.startswith("mind starting") for entry in runner.feed.entries()))

    def test_malformed_projection_is_not_written_to_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "print('SECRET-RAW')\n")
            executable(home / "projectors" / "sensor", "print('STATE: RED\\nSECRET-PRIVATE')\n")
            capture = home / "requests"
            runner = self.coordinator(home, capture)
            runner.observe()
            self.assertEqual(capture.read_text(), "")
            self.assertNotIn("SECRET", runner.feed.read_bytes().decode())
            self.assertFalse(any(entry.source == "observation/sensor" for entry in runner.feed.entries()))

    def test_legacy_external_view_also_withholds_malformed_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "print('SECRET-RAW')\n")
            executable(home / "projectors" / "sensor", "print('STATE: RED\\nSECRET-PRIVATE')\n")
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").write_text(sys.stdin.read())\nprint('probability 0.9')\n")
            runner = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",)))
            runner.control_failures = {}
            capture.write_text("")
            runner.observe()
            self.assertEqual(capture.read_text(), "")
            self.assertNotIn("SECRET", runner.feed.read_bytes().decode())

    def test_paired_cache_is_unknown_until_explicit_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").open('a').write(sys.stdin.read() + '\\n---\\n')\nprint('probability 0.9')\n")
            runner = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False,
                                               external_delta_view_slugs=("sensor",), control_cache_ttl=3600))
            self.assertIn("projected-delta-publish", runner.control_failures)
            self.assertFalse(capture.exists())
            judged = runner._judge("publish", "sensor", "SECRET", "STATE: RED\n", previous_projection="STATE: GREEN\n")
            self.assertEqual(judged.outcome, "unknown")
            self.assertFalse(capture.exists())

    def test_observe_only_without_provider_stays_unknown_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "print('SECRET-RAW-PANE')\n")
            executable(home / "projectors" / "sensor", "print('STATE: RED')\n")
            with mock.patch("mishe_tauftauf.runtime.importlib.util.find_spec", return_value=None):
                runner = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False,
                                                   external_delta_view_slugs=("sensor",)))
                runner.observe()
            feed = runner.feed.read_bytes().decode()
            self.assertIn("judged publish", feed)
            self.assertIn("unknown", feed)
            self.assertNotIn("SECRET", feed)
            self.assertNotIn("mind starting", feed)


if __name__ == "__main__":
    unittest.main()
