from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from mishe_tauftauf import decision_provider as dp
from mishe_tauftauf import dm_triage as dt


def _provider(root: pathlib.Path, key: bool = True) -> dp.Provider:
    key_file = root / "routerai.key"
    if key and not key_file.exists():
        key_file.write_text("ROUTERAI_API_KEY=test-only\n", encoding="utf-8")
        key_file.chmod(0o600)
    return dp.Provider(name="routerai", protocol="systemone", base_url="https://routerai.ru",
                       key_file=key_file, model="typesafe/jev-1.14", daily_cap=10,
                       spend_limit=50.0, price_per_token=4.5e-06, timeout=5.0,
                       budget_file=root / "budget.json")


def _post(choice: str, confidence: float, question: str):
    if question == dt.QUESTION:
        labels = list(dt.CRITERIA)
    elif question == dt.URGENCY_QUESTION:
        labels = list(dt.URGENCY_CRITERIA)
    else:
        labels = list(dt.ANSWERED_CRITERIA)
    probs = {label: 0.0 for label in labels + [dp.UNCLEAR]}
    probs[choice] = 1.0

    def post(url, body, key, timeout):
        return json.dumps({"answers": {question: {"choice": choice, "probabilities": probs,
                                                   "confidence": confidence}}}).encode()
    return post


class DmTriageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_tag_rule_decides_without_a_model(self):
        self.assertEqual(dt.system0_tag("[ci] state=ok sha=abc\nbody"), "fyi")
        self.assertEqual(dt.system0_tag("[permission] id=x decision=granted owner=genome"), "action")
        self.assertIsNone(dt.system0_tag("[dm] to=system1\nhello there"))
        self.assertIsNone(dt.system0_tag("plain prose message"))

    def test_classify_rule_hit_never_calls_provider(self):
        calls = []

        def post(url, body, key, timeout):
            calls.append(1)
            raise AssertionError("must not be called")

        result = dt.classify(7, "genome", "[ci] state=ok\n", None, post=post)
        self.assertEqual(result["source"], "system0")
        self.assertEqual(result["label"], "fyi")
        self.assertEqual(calls, [])

    def test_classify_uses_model_for_residual(self):
        provider = _provider(self.root)
        post = _post("fyi", 0.9, dt.QUESTION)
        result = dt.classify(7, "genome", "[dm] to=system1\nstatus: all green", provider, post=post)
        self.assertEqual(result["source"], "system1")
        self.assertEqual(result["label"], "fyi")
        self.assertEqual(result["confidence"], 0.9)

    def test_classify_escalates_below_threshold(self):
        provider = _provider(self.root)
        post = _post("action", 0.4, dt.QUESTION)
        result = dt.classify(7, "genome", "[dm] to=system1\nplease fix", provider, post=post)
        self.assertEqual(result["source"], "escalate")
        self.assertIsNone(result["label"])

    def test_no_provider_escalates(self):
        result = dt.classify(7, "genome", "[dm] to=system1\nhi", None)
        self.assertEqual(result["source"], "escalate")
        self.assertIsNone(result["label"])

    def test_derived_state_carries_no_full_body(self):
        body = "[dm] to=system1\n# Secret subject line\n" + ("secret detail " * 200)
        state = dt.derived_state(42, "mind/genome", body, roles=("system1",))
        self.assertEqual(state["from"], "genome")
        self.assertEqual(state["to"], "system1")
        self.assertEqual(state["subject"], "Secret subject line")
        self.assertLessEqual(len(state["first_line"]), dt.MAX_EXCERPT_CHARS)
        self.assertNotIn("secret detail " * 50, json.dumps(state))
        self.assertIn(state["len"], {"<=120", "<=400", "<=1200", "<=4000", ">4000"})

    def test_derived_state_reads_plain_prose_content_without_a_heading(self):
        body = "[dm] to=system1\nPlease review the pin failure\n" + ("opaque " * 400)
        state = dt.derived_state(43, "genome", body, roles=("system1",))
        self.assertEqual(state["content"], "Please review the pin failure")
        self.assertEqual(state["subject"], "")
        self.assertLessEqual(len(state["content"]), dt.MAX_EXCERPT_CHARS)
        self.assertNotIn("opaque " * 50, json.dumps(state))

    def test_derived_state_skips_repeated_address_line(self):
        body = ("[dm] to=system1\n[dm] to=system1\nPlease review the diff\n"
                + ("opaque " * 400))
        state = dt.derived_state(44, "field-review", body, roles=("system1",))
        self.assertEqual(state["content"], "Please review the diff")
        self.assertNotIn("[dm]", state["content"])
        self.assertNotIn("opaque " * 50, json.dumps(state))

    def test_derived_state_prefers_an_explicit_request_over_a_lead_in(self):
        body = ("[dm] to=genome\nReview result, both commits.\n\n"
                "5ed2398d ACCEPTED, no defect.\n\n"
                "Please review 6f48d99f as the non-author: the locking question I want checked.\n")
        state = dt.derived_state(99, "genome", body, roles=("genome",))
        self.assertTrue(state["content"].startswith("Please review 6f48d99f"), state["content"])
        self.assertNotIn("Review result", state["content"])

    def test_lead_in_naming_a_request_is_not_taken_as_the_ask(self):
        body = ("[dm] to=genome\nThree results, one request.\n\n"
                "Please review 5ed2398d as the non-author.\n")
        state = dt.derived_state(100, "genome", body, roles=("genome",))
        self.assertTrue(state["content"].startswith("Please review"), state["content"])

    def test_record_and_dedup(self):
        provider = _provider(self.root)
        mail = [(101, "[dm] to=system1\nfirst"), (102, "[ci] state=ok\n")]
        rows = dt.record_unread(self.home, "system1", mail, provider, roles=("system1",), now=1000.0,
                                post=_post("fyi", 0.9, dt.QUESTION))
        self.assertEqual(len(rows), 2)
        self.assertEqual(dt.recorded_seqs(self.home), {101, 102})
        # Second pass over the same mail adds nothing.
        rows2 = dt.record_unread(self.home, "system1", mail, provider, roles=("system1",), now=2000.0,
                                 post=_post("action", 0.9, dt.QUESTION))
        self.assertEqual(rows2, [])
        lines = dt.log_path(self.home).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        row = json.loads(lines[0])
        self.assertEqual(row["seq"], 101)
        self.assertIsNone(row["outcome"])

    def test_record_unread_never_raises(self):
        rows = dt.record_unread(self.home, "system1", [(1, "[dm] to=system1\nx")], None, now=1.0)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)  # escalate row still recorded

    def test_urgency_and_answered_questions(self):
        provider = _provider(self.root)
        urg = dt.score_urgency(5, "genome", "[dm] to=system1\nRED signal, resolver needed now",
                               provider, roles=("system1",), post=_post("3", 0.95, dt.URGENCY_QUESTION))
        self.assertEqual(urg["urgency"], "3")
        ans = dt.answered(5, "genome", "[dm] to=system1\nany update?", provider, roles=("system1",),
                          post=_post("unanswered", 0.9, dt.ANSWERED_QUESTION))
        self.assertFalse(ans["answered"])


if __name__ == "__main__":
    unittest.main()
