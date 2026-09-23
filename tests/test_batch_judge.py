from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mishe_tauftauf.cli import initialize
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.judges import run_external_batch
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig


def executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)


class BatchJudgeTests(unittest.TestCase):
    def test_shared_state_and_independent_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judge"
            executable(path, "import json, sys\nr = json.load(sys.stdin)\nassert r['version'] == 1\nassert r['state'] == {'slug': 'sensor', 'top_pain': 'same pane', 'evidence': 'same evidence', 'prediction': 'same prediction'}\nassert set(r['questions']) == {'prediction-met', 'desired-state-met'}\nprint(json.dumps({'results': {'prediction-met': {'probability': 0.9}, 'desired-state-met': {'probability': 0.1}}}))\n")
            results = run_external_batch(path, ("prediction-met", "desired-state-met"), "sensor", "same pane", "same evidence", "same prediction")
            self.assertEqual(results["prediction-met"].outcome, "yes")
            self.assertEqual(results["desired-state-met"].outcome, "no")

    def test_malformed_answer_fails_only_that_question_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judge"
            executable(path, "import json\nprint(json.dumps({'results': {'prediction-met': {'probability': 0.9}, 'desired-state-met': {'probability': True}}}))\n")
            results = run_external_batch(path, ("prediction-met", "desired-state-met"), "sensor", "pane", "evidence")
            self.assertEqual(results["prediction-met"].outcome, "yes")
            self.assertEqual(results["desired-state-met"].outcome, "unknown")

    def test_invalid_envelope_fails_all_questions_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judge"
            executable(path, "print('not json')\n")
            results = run_external_batch(path, ("prediction-met", "desired-state-met"), "sensor", "pane", "evidence")
            self.assertEqual({result.outcome for result in results.values()}, {"unknown"})

    def test_due_prediction_uses_one_batch_call(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "print('DESIRED STATE: ok')\n")
            count = home / "calls"
            judge = home / "judge"
            executable(judge, f"import json, sys\nr = json.load(sys.stdin)\nwith open({str(count)!r}, 'a') as f: f.write(','.join(r['questions']) + '\\n')\nprint(json.dumps({{'results': {{q: {{'probability': 0.95}} for q in r['questions']}}}}))\n")
            past = (datetime.now(timezone.utc) - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            feed = Feed(home)
            entry = feed.append_runtime("prediction/sensor", f"Expected ok.\nCheck at: {past}\n")
            feed.append_runtime("mishe-tauftauf", f"prediction {entry.sequence}: accepted")
            coordinator = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False, batch_judge=judge))
            coordinator.control_failures = {}
            before = count.read_text().splitlines()
            coordinator.due_predictions()
            after = count.read_text().splitlines()
            self.assertEqual(len(after) - len(before), 1)
            self.assertEqual(after[-1], "prediction-met,desired-state-met")


if __name__ == "__main__":
    unittest.main()
