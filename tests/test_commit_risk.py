from __future__ import annotations

import dataclasses
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
        self.assertEqual(cr.system0(["checks/initiative.py", "tests/test_initiative_check.py"]), "routine")
        self.assertEqual(cr.system0(["src/mishe_tauftauf/release_promotion.py"]), "delivery-path")
        self.assertEqual(cr.system0(["src/mishe_tauftauf/owed.py"]), "dispatch-path")
        self.assertEqual(cr.system0(["src/mishe_tauftauf/engine_codex.py"]), "dispatch-path")
        self.assertEqual(cr.system0(["src/mishe_tauftauf/engine_process.py",
                                     "tests/test_engine_process.py"]), "dispatch-path")
        self.assertEqual(cr.system0(["docs/decision-rungs.md",
                                     "src/mishe_tauftauf/release_promotion.py"]), "delivery-path")
        self.assertEqual(cr.system0(["tools/network_view.py"]), "ordinary")
        self.assertEqual(cr.system0(["tools/delivery_check.py",
                                     "tests/test_delivery_check_script.py"]), "delivery-path")

    def test_reporting_file_lookups_are_exact_and_do_not_shadow_dispatch(self):
        for path in ("tools/household_view.py", "tools/commit-msg-attribution.py",
                     "src/mishe_tauftauf/provider_budget.py",
                     "src/mishe_tauftauf/system1_savings.py"):
            with self.subTest(path=path):
                self.assertEqual(cr.system0([path]), "ordinary")
                self.assertEqual(cr.system0([path, "docs/decision-rungs.md"]), "ordinary")
                self.assertIsNone(cr.path_label(path + "\n"))
                # A view change alongside dispatch stays the model's residual.
                self.assertIsNone(cr.system0([path, "src/mishe_tauftauf/owed.py"]))
                result = cr.classify("unrelated subject", [path], None)
                self.assertEqual((result["label"], result["source"]), ("ordinary", "system0"))

    def test_new_exact_lookups_decide_without_a_model(self):
        checks = (
            ("src/mishe_tauftauf/arm.py", "dispatch-path"),
            ("src/mishe_tauftauf/consumer_fence.py", "delivery-path"),
            ("tools/map_tiles.py", "ordinary"),
            ("src/mishe_tauftauf/commit_risk_text.py", "ordinary"),
            ("tools/map_render.py", "ordinary"),
            ("src/mishe_tauftauf/retirement.py", "delivery-path"),
        )
        for path, label in checks:
            with self.subTest(path=path):
                self.assertEqual(cr.system0([path]), label)
                self.assertEqual(cr.system0([path, "docs/decision-rungs.md"]), label)
                self.assertIsNone(cr.path_label(path + "\n"))
                # Exact files, not families: a sibling still leaves the residual.
                self.assertIsNone(cr.system0([path, "src/mishe_tauftauf/feed.py"]))
                result = cr.classify("unrelated subject", [path], None)
                self.assertEqual((result["label"], result["source"]), (label, "system0"))
        self.assertIsNone(cr.system0(["src/mishe_tauftauf/arm.py",
                                      "src/mishe_tauftauf/consumer_fence.py"]))
        # Two different non-routine exact rules still leave the residual to the model.
        self.assertIsNone(cr.system0(["src/mishe_tauftauf/retirement.py",
                                      "src/mishe_tauftauf/arm.py"]))

    def test_delivery_requests_is_delivery_not_dispatch(self):
        # delivery_requests.py is delivery work (enqueue/consume, patch/revert,
        # release_promotion), not dispatch. Two confident model labels (1.0, 0.89) on
        # that path say delivery-path; listing it in the dispatch pattern used to shadow
        # the delivery deliver\w* match, so both paths decide together now.
        self.assertEqual(cr.system0(["src/mishe_tauftauf/delivery_requests.py"]), "delivery-path")
        self.assertEqual(cr.system0(["src/mishe_tauftauf/delivery_requests.py",
                                      "src/mishe_tauftauf/release_promotion.py"]), "delivery-path")

    def test_ordinary_is_model_only_except_the_view_lookup(self):
        self.assertIn("ordinary", cr.CRITERIA)
        self.assertNotIn(dp.UNCLEAR, cr.CRITERIA)
        self.assertIsNone(cr.system0(["src/mishe_tauftauf/budget_producer.py"]))
        self.assertIsNone(cr.system0(["src/mishe_tauftauf/decision_provider.py"]))
        # The rule is one file, not a family: a router/boundary tool can touch the host guard.
        self.assertIsNone(cr.system0(["tools/network_router.py"]))
        self.assertIsNone(cr.system0(["tools/network_boundary.py"]))
        # If the view changes alongside dispatch code, the residual still goes to the model.
        self.assertIsNone(cr.system0(["tools/network_view.py", "src/mishe_tauftauf/owed.py"]))
        # The delivery check alone is a rule; with dispatch code the residual still goes to the model.
        self.assertIsNone(cr.system0(["tools/delivery_check.py", "src/mishe_tauftauf/owed.py"]))

    def test_two_non_routine_rules_or_an_unmatched_path_are_the_residual(self):
        self.assertIsNone(cr.system0(["src/mishe_tauftauf/owed.py",
                                      "src/mishe_tauftauf/release_promotion.py"]))
        self.assertIsNone(cr.system0(["docs/a.md", "src/mishe_tauftauf/feed.py"]))
        self.assertIsNone(cr.system0(["src/mishe_tauftauf/feed.py"]))
        self.assertIsNone(cr.system0([]))

    def test_delivery_check_lookup_requires_the_complete_filename(self):
        paths = ["tools/delivery_check.py\n"]
        self.assertIsNone(cr.system0(paths))
        result = cr.classify("unknown file", paths, None)
        self.assertEqual((result["label"], result["source"]), (None, "escalate"))

    def test_every_end_anchored_rule_rejects_a_trailing_newline(self):
        for path in ("src/mishe_tauftauf/owed.py\n", "tools/network_view.py\n", "notes.md\n"):
            with self.subTest(path=path):
                self.assertIsNone(cr.path_label(path))
                result = cr.classify("unknown file", [path], None)
                self.assertEqual((result["label"], result["source"]), (None, "escalate"))

    def test_rule_hit_never_calls_the_provider(self):
        calls = []

        def post(*args):
            calls.append(args)
            raise AssertionError("model must not run for a rule hit")

        result = cr.classify("docs: note", ["docs/x.md"], _provider(self.root), post=post)
        self.assertEqual((result["label"], result["source"]), ("routine", "system0"))
        self.assertEqual(calls, [])

    def test_subject_rule_settles_residual_delivery_without_a_model(self):
        calls = []

        def post(*args):
            calls.append(args)
            raise AssertionError("model must not run for a subject rule hit")

        for subject in ("delivery: re-arm the promotion gate", "release review: fresh eyes",
                        "release_promotion: retry the latch"):
            with self.subTest(subject=subject):
                result = cr.classify(subject, ["src/mishe_tauftauf/feed.py"], _provider(self.root), post=post)
                self.assertEqual((result["label"], result["source"], result["reason"]),
                                 ("delivery-path", "system0", "subject rule"))
        self.assertEqual(calls, [])

    def test_path_rule_beats_the_subject_rule(self):
        # Ledger counterexample: "delivery: the pin-delivery check ..." landed on
        # routine paths, and the byte-exact path rule settled it routine. Paths decide
        # first; the subject token only settles the path rule's residual.
        result = cr.classify("delivery: the pin-delivery check keeps the latch", ["docs/x.md"],
                             _provider(self.root), post=_post("delivery-path", 0.9))
        self.assertEqual((result["label"], result["source"], result["reason"]),
                         ("routine", "system0", "path rule"))

    def test_subject_rule_requires_an_area_token_with_a_colon(self):
        provider = _provider(self.root)
        no_colon = cr.classify("release notes for the week", ["src/mishe_tauftauf/feed.py"], provider,
                               post=_post("ordinary", 0.9))
        self.assertEqual((no_colon["label"], no_colon["source"]), ("ordinary", "system1"))
        other_area = cr.classify("network: router load line", ["src/mishe_tauftauf/feed.py"], provider,
                                 post=_post("ordinary", 0.9))
        self.assertEqual((other_area["label"], other_area["source"]), ("ordinary", "system1"))

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


class RecordNewTest(unittest.TestCase):
    def setUp(self):
        import subprocess
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        self.home = pathlib.Path(self.tmp.name) / "home"
        self.repo.mkdir()
        self.git = lambda *a: subprocess.run(["git", "-C", str(self.repo), *a], check=True,
                                             capture_output=True, text=True).stdout
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self._commit(["docs/a.md"], "docs: a")
        self._commit(["src/mishe_tauftauf/release_x.py"], "release: x")
        # Residual: feed.py matches no rule, so no System 0 answer. The space checks -z parsing.
        self._commit(["tests/t one.py", "src/mishe_tauftauf/feed.py"], "mixed: residual")
        # Residual paths, but the subject names the area: the subject rule settles it.
        self._commit(["src/mishe_tauftauf/feed.py", "docs/delivery.md"], "delivery: residual named")

    def tearDown(self):
        self.tmp.cleanup()

    def _commit(self, paths, subject):
        for path in paths:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(subject + path, encoding="utf-8")
            self.git("add", "--", path)
        self.git("commit", "-q", "-m", subject)

    def test_no_provider_records_system0_only_then_idempotent(self):
        rows = cr.record_new(self.repo, self.home, None)
        self.assertEqual([r["label"] for r in rows], ["routine", "delivery-path", "delivery-path"])
        self.assertEqual({r["source"] for r in rows}, {"system0"})
        self.assertEqual(cr.record_new(self.repo, self.home, None), [])
        self.assertEqual(len(cr.recorded_shas(self.home)), 3)

    def test_subject_rule_records_residual_without_a_provider_and_keeps_it_answered(self):
        rows = cr.record_new(self.repo, self.home, None)
        self.assertEqual([r["label"] for r in rows], ["routine", "delivery-path", "delivery-path"])
        self.assertEqual(rows[2]["reason"], "subject rule")
        self.assertEqual(cr.unanswered(self.repo, self.home, None), [self.git("rev-parse", "HEAD~1").strip()])

    def test_residual_is_answered_when_a_provider_exists(self):
        cr.record_new(self.repo, self.home, None)
        seen = {}

        def stub(subject, paths, provider, *, post=None, threshold=cr.THRESHOLD):
            seen["subject"], seen["paths"] = subject, paths
            return {"label": "dispatch-path", "source": "system1", "confidence": 0.9, "reason": "stub"}

        original = cr.classify
        cr.classify = stub
        try:
            rows = cr.record_new(self.repo, self.home, object())
        finally:
            cr.classify = original
        self.assertEqual([r["label"] for r in rows], ["dispatch-path"])
        self.assertEqual(seen["subject"], "mixed: residual")
        self.assertIn("tests/t one.py", seen["paths"])
        self.assertEqual(len(cr.recorded_shas(self.home)), 4)

    def test_unanswered_commit_is_not_recorded_so_a_later_pass_asks_again(self):
        def refused(subject, paths, provider, *, post=None, threshold=cr.THRESHOLD):
            return {"label": None, "source": "escalate", "confidence": None, "reason": "call refused"}

        original = cr.classify
        cr.classify = refused
        try:
            self.assertEqual(cr.record_new(self.repo, self.home, object()), [])
        finally:
            cr.classify = original
        self.assertEqual(len(cr.recorded_shas(self.home)), 0)


class AdvisoryDutyTest(unittest.TestCase):
    """The duty's observation: UNKNOWN only for a missing provider or a service failing with cap room."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        self.home = pathlib.Path(self.tmp.name) / "home"
        self.repo.mkdir()
        self.home.mkdir()
        self.fake = dataclasses.replace(_provider(self.home), daily_cap=10)
        originals = (dp.load_providers, dp.select, dp.calls_today, cr.unanswered, cr.record_new)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore, originals)

    def _restore(self, originals):
        dp.load_providers, dp.select, dp.calls_today, cr.unanswered, cr.record_new = originals

    def _with_provider(self, calls, pending):
        dp.load_providers = lambda path: {}
        dp.select = lambda providers, name: self.fake
        dp.calls_today = lambda provider, day: calls
        cr.unanswered = lambda repo, home, provider, **kw: pending
        cr.record_new = lambda repo, home, provider, **kw: []

    def test_missing_provider_config_is_unknown(self):
        code, reason = cr.advisory_duty(self.repo, self.home)
        self.assertEqual(code, 3)
        self.assertTrue(reason.startswith("UNKNOWN"))

    def test_unanswered_with_cap_room_is_unknown(self):
        self._with_provider(calls=2, pending=["abc"])
        code, reason = cr.advisory_duty(self.repo, self.home)
        self.assertEqual(code, 3)
        self.assertIn("1 residual commits unanswered", reason)

    def test_unanswered_with_cap_spent_is_green_held(self):
        self._with_provider(calls=10, pending=["abc"])
        code, reason = cr.advisory_duty(self.repo, self.home)
        self.assertEqual(code, 0)
        self.assertIn("held by the daily cap", reason)


if __name__ == "__main__":
    unittest.main()
