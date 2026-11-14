from __future__ import annotations

import http.client
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.request

from mishe_tauftauf import decision_provider as dp
from mishe_tauftauf.judges import classify

SECRET = "test-key-123-do-not-print"
CRITERIA = {"alpha": "the state names a lookup", "beta": "the state names a diff review"}


def _answer(question: str, choice: str, probabilities: dict[str, float], confidence: float) -> bytes:
    return json.dumps({"answers": {question: {"choice": choice, "probabilities": probabilities, "confidence": confidence}}}).encode()


class _Fake:
    """Records calls to the transport and returns a fixed body."""

    def __init__(self, body: bytes | Exception):
        self.body = body
        self.calls: list[tuple[str, bytes, str, float]] = []

    def __call__(self, url, body, key, timeout):
        self.calls.append((url, body, key, timeout))
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.key = self.root / "routerai.key"
        self.key.write_text(f"ROUTERAI_API_KEY={SECRET}\n", encoding="utf-8")
        os.chmod(self.key, 0o600)
        self.budget = self.root / "budget.json"

    def tearDown(self):
        self._tmp.cleanup()

    def provider(self, **overrides) -> dp.Provider:
        entry = {
            "protocol": "systemone",
            "base_url": "https://routerai.ru",
            "key_file": str(self.key),
            "model": "typesafe/jev-1.14",
            "daily_cap": 10,
            "timeout": 5,
            "budget_file": str(self.budget),
        }
        entry.update(overrides)
        config = self.root / "providers.json"
        config.write_text(json.dumps({"providers": {"routerai": entry}}), encoding="utf-8")
        return dp.select(dp.load_providers(config), "routerai")

    # configuration -----------------------------------------------------

    def test_config_accepts_a_complete_provider(self):
        provider = self.provider()
        self.assertEqual(provider.protocol, "systemone")
        self.assertEqual(provider.daily_cap, 10)
        self.assertEqual(provider.budget_file, self.budget)

    def test_config_rejects_each_unusable_field(self):
        bad = [
            {"protocol": "chat-completions"},
            {"base_url": "http://routerai.ru"},
            {"base_url": "https://user:pw@routerai.ru"},
            {"key_file": "relative/routerai.key"},
            {"daily_cap": 5000},
            {"daily_cap": True},
            {"timeout": 0},
            {"timeout": 31},
            {"model": "  "},
            {"budget_file": "relative.json"},
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(dp.ProviderConfigError):
                self.provider(**overrides)

    def test_config_rejects_unknown_fields_and_bad_names(self):
        with self.assertRaises(dp.ProviderConfigError):
            self.provider(extra="x")
        config = self.root / "bad-name.json"
        config.write_text(json.dumps({"providers": {"Routerai": {}}}), encoding="utf-8")
        with self.assertRaises(dp.ProviderConfigError):
            dp.load_providers(config)

    def test_loopback_http_is_the_only_plain_http(self):
        self.assertEqual(self.provider(base_url="http://127.0.0.1:8080/").base_url, "http://127.0.0.1:8080")

    def test_missing_config_is_a_config_error_not_a_default(self):
        with self.assertRaises(dp.ProviderConfigError):
            dp.load_providers(self.root / "absent.json")

    # key file ----------------------------------------------------------

    def test_key_file_readable_by_others_is_refused(self):
        os.chmod(self.key, 0o644)
        with self.assertRaises(dp.KeyFileError):
            dp.read_key(self.key)

    def test_key_file_reads_the_named_line(self):
        self.assertEqual(dp.read_key(self.key), SECRET)

    def test_missing_key_file_reads_as_none(self):
        self.assertIsNone(dp.read_key(self.root / "absent.key"))

    def test_malformed_ipv6_base_url_is_a_config_error(self):
        with self.assertRaises(dp.ProviderConfigError):
            dp._checked_base_url("https://[::1")

    def test_redirects_are_refused(self):
        request = urllib.request.Request("https://routerai.ru/api/v1/systemone")
        with self.assertRaises(urllib.error.HTTPError):
            dp._NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/")

    # choice questions --------------------------------------------------

    def test_choice_decides_above_threshold_and_logs_nothing_secret(self):
        fake = _Fake(_answer("q", "alpha", {"alpha": 0.9, "beta": 0.05, "unclear": 0.05}, 0.9))
        decision = dp.decide(self.provider(), "q", "pick", "state", CRITERIA, threshold=0.8, post=fake)
        self.assertTrue(decision.decided)
        self.assertEqual(decision.choice, "alpha")
        url, body, key, timeout = fake.calls[0]
        self.assertEqual(url, "https://routerai.ru/api/v1/systemone")
        self.assertEqual(key, SECRET)
        self.assertNotIn(SECRET.encode(), body)
        self.assertEqual(json.loads(body)["questions"]["q"]["criteria"]["unclear"], dp.UNCLEAR_CRITERION)

    def test_low_confidence_escalates_instead_of_deciding(self):
        fake = _Fake(_answer("q", "alpha", {"alpha": 0.5, "beta": 0.4, "unclear": 0.1}, 0.5))
        decision = dp.decide(self.provider(), "q", "pick", "state", CRITERIA, threshold=0.8, post=fake)
        self.assertIsNone(decision.choice)
        self.assertIn("escalate to a mind", decision.reason)

    def test_escape_option_is_a_non_decision(self):
        fake = _Fake(_answer("q", "unclear", {"alpha": 0.1, "beta": 0.1, "unclear": 0.8}, 0.9))
        decision = dp.decide(self.provider(), "q", "pick", "state", CRITERIA, threshold=0.5, post=fake)
        self.assertFalse(decision.decided)
        self.assertEqual(decision.choice, dp.UNCLEAR)

    def test_malformed_answers_are_unknown_with_a_reason(self):
        cases = {
            b"not json": "non-JSON",
            json.dumps({"answers": []}).encode(): "no answer",
            _answer("q", "gamma", {"alpha": 0.1, "beta": 0.1, "unclear": 0.8}, 0.9): "not an offered option",
            _answer("q", "alpha", {"alpha": 2.0, "beta": 0.0, "unclear": 0.0}, 0.9): "out of range",
            _answer("q", "alpha", {"alpha": 0.5, "beta": 0.2, "unclear": 0.3}, 1.5): "confidence",
        }
        for body, expected in cases.items():
            with self.subTest(expected=expected):
                decision = dp.decide(self.provider(), "q", "pick", "state", CRITERIA, threshold=0.5, post=_Fake(body))
                self.assertIsNone(decision.choice)
                self.assertIn(expected, decision.reason)

    def test_bad_inputs_never_reach_the_transport(self):
        fake = _Fake(b"{}")
        provider = self.provider()
        cases = [
            dp.decide(provider, "q", "x", "s", {}, threshold=0.5, post=fake),
            dp.decide(provider, "q", "x", "s", {"unclear": "no"}, threshold=0.5, post=fake),
            dp.decide(provider, "q", "x", "s", CRITERIA, threshold=2.0, post=fake),
            dp.decide(provider, "q", "x", "s" * (dp.MAX_STATE_CHARS + 1), CRITERIA, threshold=0.5, post=fake),
        ]
        for decision in cases:
            self.assertFalse(decision.decided)
        self.assertEqual(fake.calls, [])

    # budget ------------------------------------------------------------

    def test_daily_cap_stops_the_transport_and_counts_failures(self):
        provider = self.provider(daily_cap=1)
        failing = _Fake(urllib.error.URLError("refused"))
        first = dp.decide(provider, "q", "x", "s", CRITERIA, threshold=0.5, post=failing)
        second = dp.decide(provider, "q", "x", "s", CRITERIA, threshold=0.5, post=failing)
        self.assertIn("request failed", first.reason)
        self.assertIn("daily budget exhausted", second.reason)
        self.assertEqual(len(failing.calls), 1)

    def test_http_client_failures_are_unknown_and_keep_the_call(self):
        provider = self.provider(daily_cap=5)
        for error in (http.client.IncompleteRead(b"partial"), http.client.BadStatusLine("junk")):
            failing = _Fake(error)
            decision = dp.decide(provider, "q", "x", "s", CRITERIA, threshold=0.5, post=failing)
            self.assertFalse(decision.decided)
            self.assertIn("request failed", decision.reason)
            self.assertEqual(len(failing.calls), 1)

    def test_zero_cap_never_calls(self):
        fake = _Fake(b"{}")
        decision = dp.decide(self.provider(daily_cap=0), "q", "x", "s", CRITERIA, threshold=0.5, post=fake)
        self.assertIn("daily budget exhausted", decision.reason)
        self.assertEqual(fake.calls, [])

    def test_missing_key_is_unknown_without_spending_budget(self):
        provider = self.provider(key_file=str(self.root / "absent.key"))
        fake = _Fake(b"{}")
        decision = dp.decide(provider, "q", "x", "s", CRITERIA, threshold=0.5, post=fake)
        self.assertIn("key file is missing or empty", decision.reason)
        self.assertEqual(fake.calls, [])
        self.assertFalse(self.budget.exists())

    def test_reasons_never_carry_the_key(self):
        fake = _Fake(urllib.error.HTTPError("https://routerai.ru", 500, "boom", {}, None))
        decision = dp.decide(self.provider(), "q", "x", "s", CRITERIA, threshold=0.5, post=fake)
        self.assertNotIn(SECRET, decision.reason)

    # yes/no judgments --------------------------------------------------

    def test_yes_no_judgment_uses_the_judges_thresholds(self):
        provider = self.provider()
        yes = dp.judge(provider, "relevance", "top", "temperature", "evidence",
                       post=_Fake(_answer("relevance", "yes", {"yes": 0.9, "no": 0.05, "unclear": 0.05}, 0.9)))
        self.assertEqual(yes.outcome, classify("relevance", 0.9))
        self.assertEqual(yes.outcome, "yes")
        no = dp.judge(provider, "relevance", "top", "temperature", "evidence",
                      post=_Fake(_answer("relevance", "no", {"yes": 0.1, "no": 0.8, "unclear": 0.1}, 0.9)))
        self.assertEqual(no.outcome, "no")
        self.assertEqual(no.probability, 0.1)

    def test_yes_no_escape_and_failure_are_unknown(self):
        provider = self.provider()
        escaped = dp.judge(provider, "publish", "top", "t", "e",
                           post=_Fake(_answer("publish", "unclear", {"yes": 0.4, "no": 0.4, "unclear": 0.2}, 0.9)))
        self.assertEqual(escaped.outcome, "unknown")
        failed = dp.judge(provider, "publish", "top", "t", "e", post=_Fake(urllib.error.URLError("down")))
        self.assertEqual(failed.outcome, "unknown")
        self.assertIn("request failed", failed.reason)

    def test_unknown_question_is_rejected_before_transport(self):
        fake = _Fake(b"{}")
        with self.assertRaises(ValueError):
            dp.judge(self.provider(), "not-a-question", "top", "t", "e", post=fake)
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
