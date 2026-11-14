from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from mishe_tauftauf import commit_risk as cr
from mishe_tauftauf import decision_provider as dp


def _provider(root: pathlib.Path, key: bool = True) -> dp.Provider:
    key_file = root / "routerai.key"
    if key and not key_file.exists():
        key_file.write_text("ROUTERAI_API_KEY=test-only\n", encoding="utf-8")
        key_file.chmod(0o600)
    return dp.Provider(name="routerai", protocol="systemone", base_url="https://routerai.ru",
                       key_file=key_file, model="typesafe/jev-1.14", daily_cap=10,
                       spend_limit=50.0, price_per_token=4.5e-06, timeout=5.0,
                       budget_file=root / "budget.json")


def _post(choice: str, confidence: float):
    probs = {label: 0.0 for label in list(cr.CRITERIA) + [dp.UNCLEAR]}
    probs[choice] = 1.0

    def post(url, body, key, timeout):
        return json.dumps({"answers": {cr.QUESTION: {"choice": choice, "probabilities": probs,
                                                     "confidence": confidence}}}).encode()
    return post


class CommitRiskTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_path_rules_decide_pure_commits_without_a_model(self):
        self.assertEqual(cr.system0(["docs/a.md", "tests/test_x.py"]), "routine")
        self.assertEqual(cr.system0(["src/mishe_tauftauf/release_promotion.py"]), "delivery-path")
        self.assertEqual(cr.system0(["src/mishe_tauftauf/owed.py"]), "dispatch-path")

    def test_mixed_or_unmatched_paths_are_the_residual(self):
        self.assertIsNone(cr.system0(["docs/a.md", "src/mishe_tauftauf/owed.py"]))
        self.assertIsNone(cr.system0(["src/mishe_tauftauf/feed.py"]))
        self.assertIsNone(cr.system0([]))

    def test_rule_hit_never_calls_the_provider(self):
        calls = []

        def post(*args):
            calls.append(args)
            raise AssertionError("model must not run for a rule hit")

        result = cr.classify("docs: note", ["docs/x.md"], _provider(self.root), post=post)
        self.assertEqual((result["label"], result["source"]), ("routine", "system0"))
        self.assertEqual(calls, [])

    def test_residual_takes_a_confident_model_label(self):
        result = cr.classify("feed refactor", ["src/mishe_tauftauf/feed.py"], _provider(self.root),
                             post=_post("dispatch-path", 0.9))
        self.assertEqual((result["label"], result["source"]), ("dispatch-path", "system1"))

    def test_low_confidence_or_escape_escalates(self):
        low = cr.classify("x", ["src/mishe_tauftauf/feed.py"], _provider(self.root), post=_post("routine", 0.4))
        self.assertEqual((low["label"], low["source"]), (None, "escalate"))
        escape = cr.classify("x", ["src/mishe_tauftauf/feed.py"], _provider(self.root),
                             post=_post(dp.UNCLEAR, 0.95))
        self.assertEqual((escape["label"], escape["source"]), (None, "escalate"))

    def test_failure_drill_missing_provider_escalates_not_defaults(self):
        result = cr.classify("x", ["src/mishe_tauftauf/feed.py"], None)
        self.assertEqual((result["label"], result["source"]), (None, "escalate"))
        self.assertEqual(result["reason"], "no decision provider")

    def test_missing_key_file_is_unknown_and_escalates(self):
        result = cr.classify("x", ["src/mishe_tauftauf/feed.py"], _provider(self.root, key=False))
        self.assertEqual((result["label"], result["source"]), (None, "escalate"))

    def test_ledger_keeps_outcome_open_for_later_ground_truth(self):
        cr.record(self.root, "abc123", {"label": "routine", "source": "system0"}, now=1.0)
        row = json.loads((self.root / "decision-log" / "commit-risk.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["sha"], "abc123")
        self.assertIsNone(row["outcome"])


if __name__ == "__main__":
    unittest.main()
