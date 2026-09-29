from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from mishe_tauftauf.cli import initialize, main
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig
from mishe_tauftauf.external_view import safe_fleet_view, safe_publish_view
from mishe_tauftauf.feed import Feed


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


class FleetViewTests(unittest.TestCase):
    ADINT = ("STATE: RED\nOBSERVATION: source=top-pane/adint freshness=fresh "
             "goal=fresh value-coverage=partial semantic=obligations:stale signal=5939026726b789b9\n")
    GREEN = ("STATE: GREEN\nOBSERVATION: source=top-pane/adint freshness=fresh "
             "goal=fresh value-coverage=partial semantic=obligations:fresh signal=5939026726b789b9\n")

    def test_fleet_projection_strips_diagnostics_but_preserves_typed_context(self):
        safe = safe_fleet_view(self.ADINT, "adint")
        self.assertEqual(safe, "STATE: RED\nOBSERVATION: source=top-pane/adint freshness=fresh "
                         "goal=fresh value-coverage=partial semantic=obligations:stale\n")
        self.assertEqual(safe_fleet_view("STATE: UNKNOWN\n", "adint"), None)
        witness = ("STATE: RED\nOBSERVATION: source=top-pane/witness freshness=fresh journal=fresh "
                   "signal=0123456789abcdef tasks-total=11 tasks-unfinished=4 tasks-unowned=2 "
                   "mishe-issues=1 issue-digest=0123456789abcdef\n")
        self.assertIn("journal=fresh", safe_fleet_view(witness, "witness"))
        self.assertNotIn("tasks-total", safe_fleet_view(witness, "witness"))

    def test_fleet_malformed_projection_never_calls_spy_judge(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) +
                       ").open('a').write(sys.stdin.read())\nprint('probability 0.9')\n")
            coordinator = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False,
                                                    external_fleet_view_slugs=("adint",)))
            coordinator.control_failures = {}
            capture.write_text("")
            malformed = (
                self.ADINT + "PRIVATE-SENTINEL", self.ADINT.replace("signal=5939026726b789b9", "signal=PRIVATE-SENTINEL"),
                self.ADINT.replace("semantic=obligations:stale", "semantic=obligations:PRIVATE-SENTINEL"),
                self.ADINT.replace("source=top-pane/adint", "source=top-pane/witness"),
                self.ADINT.replace("goal=fresh", "goal=fresh  PRIVATE-SENTINEL"),
                "STATE: UNKNOWN\n",
            )
            for projection in malformed:
                for question in ("publish", "relevance", "desired-state-met", "continue-observing"):
                    with self.subTest(projection=projection, question=question):
                        judgment = coordinator._judge(question, "adint", "PRIVATE-SENTINEL", projection,
                                                      "PRIVATE-SENTINEL", event_source="observation/adint")
                        self.assertEqual(judgment.outcome, "unknown")
                        self.assertNotIn("PRIVATE-SENTINEL", judgment.document)
            self.assertEqual(capture.read_text(), "")

    def test_fleet_all_question_paths_withhold_pane_and_predictions(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nrequest=sys.stdin.read()\nPath(" + repr(str(capture)) +
                       ").open('a').write(request + '\\n--END--\\n')\n"
                       "print('probability 0.1' if 'QUESTION desired-state-met' in request and 'STATE: RED' in request else 'probability 0.9')\n")
            coordinator = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False,
                                                    external_fleet_view_slugs=("adint",)))
            coordinator.control_failures = {}
            capture.write_text("")
            raw = "PRIVATE-SENTINEL"
            self.assertEqual(coordinator._judge("publish", "adint", raw, self.ADINT).outcome, "yes")
            self.assertEqual(coordinator._judge("relevance", "adint", raw, self.ADINT,
                                                event_source="observation/adint").outcome, "yes")
            self.assertEqual(coordinator._judge("relevance", "adint", raw, self.ADINT,
                                                event_source="observation/witness").outcome, "unknown")
            self.assertEqual(coordinator._judge("desired-state-met", "adint", raw, self.ADINT,
                                                event_source="observation/adint").outcome, "no")
            self.assertEqual(coordinator._judge("desired-state-met", "adint", raw, self.GREEN,
                                                event_source="observation/adint").outcome, "yes")
            self.assertEqual(coordinator._judge("continue-observing", "adint", raw, self.GREEN, raw,
                                                event_source="observation/adint").outcome, "yes")
            batch = coordinator._judge_batch(("prediction-met", "desired-state-met"), "adint", raw, raw, raw)
            self.assertTrue(all(result.outcome == "unknown" for result in batch.values()))
            self.assertEqual(coordinator._judge("valid-attempt", "adint", raw, raw, raw).outcome, "unknown")
            self.assertNotIn(raw, capture.read_text())
            self.assertEqual(capture.read_text().count("QUESTION "), 4)

    def test_fleet_batch_judge_receives_only_validated_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            capture = home / "requests"
            judge = home / "batch-judge"
            executable(judge, "import json, sys\nfrom pathlib import Path\nr=json.load(sys.stdin)\nPath(" +
                       repr(str(capture)) + ").open('a').write(json.dumps(r)+'\\n')\n"
                       "print(json.dumps({'results': {q: {'probability': 0.9} for q in r['questions']}}))\n")
            runner = Coordinator(RuntimeConfig(home, batch_judge=judge, launcher="headless", dispatch=False,
                                                external_fleet_view_slugs=("adint",)))
            runner.control_failures = {}
            capture.write_text("")
            self.assertEqual(runner._judge("publish", "adint", "PRIVATE-SENTINEL", self.ADINT).outcome, "yes")
            self.assertEqual(runner._judge("continue-observing", "adint", "PRIVATE-SENTINEL",
                                           self.GREEN, "PRIVATE-SENTINEL",
                                           event_source="observation/adint").outcome, "yes")
            self.assertEqual(runner._judge("publish", "adint", "PRIVATE-SENTINEL",
                                           self.ADINT + "PRIVATE-SENTINEL").outcome, "unknown")
            sent = capture.read_text()
            self.assertEqual(len(sent.splitlines()), 2)
            self.assertNotIn("PRIVATE-SENTINEL", sent)
            self.assertNotIn("signal=5939026726b789b9", sent)

    def test_fleet_route_rejects_unrelated_events_without_waking(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "adint", "print('PRIVATE-SENTINEL')\n")
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nPath(" + repr(str(capture)) +
                       ").open('a').write(sys.stdin.read())\nprint('probability 0.9')\n")
            runner = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False,
                                                external_fleet_view_slugs=("adint",)))
            runner.control_failures = {}
            capture.write_text("")
            unrelated = runner.feed.append("human", "PRIVATE-SENTINEL")
            own = runner.feed.append_runtime("observation/adint", self.ADINT)
            runner.route()
            receipts = [e.body for e in runner.feed.entries() if e.source == "mishe-tauftauf"]
            self.assertTrue(any(f"entry {unrelated.sequence}" in r and "observe" in r for r in receipts))
            self.assertTrue(any(f"entry {unrelated.sequence}" in r and "relevance" in r and "unknown" in r for r in receipts))
            self.assertTrue(any(f"entry {own.sequence}" in r and "relevance" in r and "yes" in r for r in receipts))
            self.assertNotIn("PRIVATE-SENTINEL", capture.read_text())

    def test_fleet_red_requires_explicit_s1_no_before_wake(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "adint", "print('PRIVATE-SENTINEL')\n")
            capture = home / "requests"
            judge = home / "judge"
            executable(judge, "import sys\nfrom pathlib import Path\nrequest=sys.stdin.read()\n"
                       "Path(" + repr(str(capture)) + ").open('a').write(request)\n"
                       "print('probability 0.1')\n")
            runner = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False,
                                                external_fleet_view_slugs=("adint",)))
            runner.control_failures = {}
            capture.write_text("")
            observation = runner.feed.append_runtime("observation/adint", self.ADINT)
            runner.route()
            receipts = [e.body for e in runner.feed.entries() if e.source == "mishe-tauftauf"]
            self.assertTrue(any(f"on entry {observation.sequence}: no" in r
                                and "desired-state-met" in r for r in receipts))
            self.assertTrue(any(f"wake requested top-pain adint for entry {observation.sequence}" == r
                                for r in receipts))
            self.assertNotIn("PRIVATE-SENTINEL", capture.read_text())

    def test_fleet_unknown_retries_latest_observation_after_bounded_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "adint", "print('PRIVATE-SENTINEL')\n")
            verdict = home / "verdict"
            verdict.write_text("unknown")
            judge = home / "judge"
            executable(judge, "from pathlib import Path\n"
                       "kind=Path(" + repr(str(verdict)) + ").read_text()\n"
                       "print('unknown fixture' if kind == 'unknown' else "
                       "'probability 0.5' if kind == 'uncertain' else 'probability 0.1')\n")
            runner = Coordinator(RuntimeConfig(home, judge=judge, launcher="headless", dispatch=False,
                                                external_fleet_view_slugs=("adint",)))
            runner.control_failures = {}
            old = runner.feed.append_runtime("observation/adint", self.ADINT)
            current = runner.feed.append_runtime("observation/adint", self.ADINT.replace(
                "signal=5939026726b789b9", "signal=aaaaaaaaaaaaaaaa"))
            runner.route()
            first = [e.body for e in runner.feed.entries() if e.source == "mishe-tauftauf"]
            self.assertTrue(any(f"entry {current.sequence} for top-pain adint: held" == r for r in first))
            runner.route()
            self.assertEqual(first, [e.body for e in runner.feed.entries() if e.source == "mishe-tauftauf"])
            verdict.write_text("uncertain")
            with patch("mishe_tauftauf.runtime.now",
                       return_value=datetime.now(timezone.utc) + timedelta(seconds=601)):
                runner.route()
            uncertain = [e.body for e in runner.feed.entries() if e.source == "mishe-tauftauf"]
            self.assertTrue(any(f"entry {current.sequence} for top-pain adint: held" == r
                                for r in uncertain))
            self.assertFalse(any(f"wake requested top-pain adint for entry {current.sequence}" == r
                                 for r in uncertain))
            runner.route()
            self.assertEqual(uncertain, [e.body for e in runner.feed.entries() if e.source == "mishe-tauftauf"])
            verdict.write_text("no")
            with patch("mishe_tauftauf.runtime.now",
                       return_value=datetime.now(timezone.utc) + timedelta(seconds=601)):
                runner.route()
            after = [e.body for e in runner.feed.entries() if e.source == "mishe-tauftauf"]
            self.assertTrue(any(f"wake requested top-pain adint for entry {current.sequence}" == r
                                for r in after))
            self.assertFalse(any(f"wake requested top-pain adint for entry {old.sequence}" == r
                                 for r in after))

    def test_fleet_cli_opt_in_sanitizes_published_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "adint", "print('PRIVATE-SENTINEL')\n")
            executable(home / "projectors" / "adint", "print(" + repr(self.ADINT) + ", end='')\n")
            result = main(["--home", str(home), "run", "--once", "--launcher", "headless",
                           "--observe-only", "--judge", "/bin/false", "--slug", "adint",
                           "--external-fleet-view-slug", "adint"])
            self.assertEqual(result, 0)
            observations = [entry.body for entry in Feed(home).entries()
                            if entry.source == "observation/adint"]
            self.assertEqual(observations, [self.ADINT])
            self.assertNotIn("PRIVATE-SENTINEL", "\n".join(observations))
            decisions = [entry.body for entry in Feed(home).entries() if entry.source == "mishe-tauftauf"]
            self.assertTrue(any("desired-state-met" in body and "unknown" in body for body in decisions))
            self.assertTrue(any("for top-pain adint: held" in body for body in decisions))
            self.assertFalse(any("wake requested top-pain adint" in body for body in decisions))



if __name__ == "__main__":
    unittest.main()
