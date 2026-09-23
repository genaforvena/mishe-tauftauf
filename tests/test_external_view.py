from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mishe_tauftauf.cli import initialize
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig
from mishe_tauftauf.external_view import safe_publish_view


def executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)


class ExternalViewTests(unittest.TestCase):
    def test_observe_batch_protocol_never_receives_raw_renderer_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "print('SECRET-RAW-RENDERER-504')\n")
            executable(home / "projectors" / "sensor", "print('STATE: RED')\n")
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import json, sys\nfrom pathlib import Path\nr = json.load(sys.stdin)\nPath(" + repr(str(capture)) + ").open('a').write(json.dumps(r) + '\\n')\nprint(json.dumps({'results': {q: {'probability': 0.9} for q in r['questions']}}))\n")
            coordinator = Coordinator(RuntimeConfig(home, batch_judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",)))
            coordinator.control_failures = {}
            capture.write_text("")
            coordinator.observe()
            sent = capture.read_text()
            self.assertIn('"top_pain": "STATE: RED\\n"', sent)
            self.assertNotIn("SECRET-RAW-RENDERER-504", sent)

    def test_fixed_summary_is_normalized_without_channel_label(self):
        projection = ("STATE: INTERMEDIATE\n"
                      "CLEANER: candidates=2 held=1 actionable=0 delete=0 unknowns=0 "
                      "head=abcdef123456 task=present task-epoch=133700 task-event-count=268 "
                      "task-events=133684:open,133700:complete\n")
        safe = safe_publish_view(projection)
        self.assertIsNotNone(safe)
        self.assertIn("SUMMARY: candidates=2", safe)
        self.assertNotIn("CLEANER", safe)

    def test_invalid_unicode_projection_is_unknown_without_call(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            judge = home / "judge"
            executable(judge, "print('probability 0.9')\n")
            coordinator = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",)))
            coordinator.control_failures = {}
            result = coordinator._judge("publish", "sensor", "SECRET", "STATE: RED\n\ud800")
            self.assertEqual(result.outcome, "unknown")

    def test_opted_publish_sends_only_projected_state_to_single_judge(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").open('a').write(sys.stdin.read() + '\\n---\\n')\nprint('probability 0.9')\n")
            coordinator = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",)))
            coordinator.control_failures = {}
            capture.write_text("")
            result = coordinator._judge("publish", "sensor", "SECRET-PANE-501", "STATE: RED\n")
            self.assertEqual(result.outcome, "yes")
            sent = capture.read_text()
            self.assertIn("STATE: RED", sent)
            self.assertNotIn("SECRET-PANE-501", sent)

    def test_opted_batch_and_unsupported_questions_never_call_external_judge(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").open('a').write(sys.stdin.read())\nprint('{\"results\":{}}')\n")
            coordinator = Coordinator(RuntimeConfig(home, batch_judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",)))
            coordinator.control_failures = {}
            capture.write_text("")
            result = coordinator._judge_batch(("prediction-met", "desired-state-met"), "sensor", "SECRET-PANE-502", "SECRET-EVIDENCE-502", "SECRET-PREDICTION-502")
            self.assertEqual({item.outcome for item in result.values()}, {"unknown"})
            self.assertEqual(capture.read_text(), "")
            self.assertNotIn("SECRET-PANE-502", next(iter(result.values())).document)

    def test_malformed_projection_fails_closed_without_external_call(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) + ").open('a').write(sys.stdin.read())\nprint('probability 0.9')\n")
            coordinator = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",)))
            coordinator.control_failures = {}
            capture.write_text("")
            result = coordinator._judge("publish", "sensor", "SECRET-PANE-503", "STATE: RED\nLEAK: SECRET-PANE-503\n")
            self.assertEqual(result.outcome, "unknown")
            self.assertEqual(capture.read_text(), "")

    def test_projected_publish_controls_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            judge = home / "judge"
            executable(judge, "import sys\nsys.stdin.read()\nprint('probability 0.9')\n")
            coordinator = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",)))
            self.assertIn("projected-publish", coordinator.control_failures)
            self.assertEqual(coordinator._judge("publish", "sensor", "SECRET", "STATE: RED\n").outcome, "unknown")

    def test_projected_controls_are_cached_only_after_explicit_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "calls"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nr = sys.stdin.read()\nPath(" + repr(str(capture)) + ").open('a').write('x')\nprint('probability 0.1' if 'STATE: GREEN' in r else 'probability 0.9')\n")
            args = dict(judge=judge, launcher="headless", dispatch=False, external_view_slugs=("sensor",), control_cache_ttl=3600)
            absent = Coordinator(RuntimeConfig(home, **args))
            self.assertIn("projected-publish", absent.control_failures)
            self.assertFalse(capture.exists())
            Coordinator(RuntimeConfig(home, refresh_controls=True, **args))
            self.assertEqual(len(capture.read_text()), 14)
            cached = Coordinator(RuntimeConfig(home, **args))
            self.assertEqual(len(capture.read_text()), 14)
            self.assertNotIn("projected-publish", cached.control_failures)


if __name__ == "__main__":
    unittest.main()
