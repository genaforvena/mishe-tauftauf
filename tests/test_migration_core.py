from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mishe_tauftauf.cli import initialize, main
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.judges import Judgment
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


class MigrationCoreTests(unittest.TestCase):
    def test_projected_observation_and_judgment_never_persist_raw_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            secret = "PRIVATE-PANE-SECRET-9382"
            executable(home / "top-pains" / "sensor", f"printf '%s\\n' 'RED {secret}'\n")
            executable(home / "projectors" / "sensor", "printf 'sensor red'\n")
            coordinator = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False))
            coordinator.observe()
            data = Feed(home).read_bytes()
            self.assertIn(b"sensor red", data)
            self.assertNotIn(secret.encode(), data)
            self.assertNotIn(b"PREVIOUS", data)

    def test_projector_failure_is_unknown_and_no_raw_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "printf 'SECRET-9217'\n")
            executable(home / "projectors" / "sensor", "printf 'bad SECRET-9217'\nexit 9\n")
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False)).observe()
            data = Feed(home).read_bytes()
            self.assertIn(b"UNKNOWN", data)
            self.assertNotIn(b"SECRET-9217", data)

    def test_stable_projection_does_not_duplicate_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "printf 'RED'\n")
            executable(home / "projectors" / "sensor", "printf 'sensor red'\n")
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False)).observe()
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False)).observe()
            observations = [entry for entry in Feed(home).entries() if entry.source == "observation/sensor"]
            self.assertEqual(len(observations), 1)

    def test_observation_only_request_replays_without_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "printf 'RED'\n")
            executable(home / "minds" / "sensor", f"touch '{home / 'launched'}'\n")
            feed = Feed(home)
            feed.append_runtime("observation/sensor", "sensor red")
            first = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False))
            first.route()
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False)).retry_unfinished_wakes()
            self.assertFalse((home / "launched").exists())
            self.assertEqual(sum("wake requested top-pain sensor for entry 1" in e.body for e in feed.entries()), 1)

    def test_context_selects_complete_entries_under_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            feed = Feed(home)
            for n in range(60):
                feed.append("human", f"old-{n} " + "x" * 2048)
            stimulus = feed.append("human", "latest event")
            context = Coordinator(RuntimeConfig(home, launcher="headless"))._context("sensor", stimulus, "invocation")
            self.assertIn("latest event", context)
            self.assertLessEqual(len(context.encode()), 48 * 1024)
            self.assertNotIn("old-0", context)

    def test_dispatch_receipt_is_idempotent_and_requires_request(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            self.assertEqual(main(["--home", str(home), "dispatch-receipt", "sensor", "1", "delivered"]), 2)
            feed = Feed(home)
            feed.append_runtime_once("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            self.assertEqual(main(["--home", str(home), "dispatch-receipt", "sensor", "1", "delivered"]), 0)
            self.assertEqual(main(["--home", str(home), "dispatch-receipt", "sensor", "1", "delivered"]), 0)
            self.assertEqual(sum(e.body == "wake delivered top-pain sensor for entry 1" for e in feed.entries()), 1)

    def test_dispatch_receipt_rejects_conflicting_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            feed = Feed(home)
            feed.append_runtime("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            self.assertEqual(main(["--home", str(home), "dispatch-receipt", "sensor", "1", "refused"]), 0)
            self.assertEqual(main(["--home", str(home), "dispatch-receipt", "sensor", "1", "delivered"]), 2)
            self.assertEqual(main(["--home", str(home), "dispatch-receipt", "sensor", "1", "refused"]), 0)
            self.assertEqual(len([e for e in feed.entries() if e.body.startswith("wake refused")]), 1)

    def test_refused_attempt_can_retry_with_new_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            feed = Feed(home)
            feed.append_runtime("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            base = ["--home", str(home), "dispatch-receipt", "sensor", "1"]
            self.assertEqual(main(base + ["refused", "--generation", "3", "--request-id", "attempt-1"]), 0)
            self.assertEqual(main(base + ["delivered", "--generation", "3", "--request-id", "attempt-2"]), 0)
            self.assertEqual(main(base + ["delivered", "--generation", "3", "--request-id", "attempt-1"]), 2)
            self.assertEqual(len([e for e in feed.entries() if e.body.startswith("wake delivered")]), 1)

    def test_malformed_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            feed = Feed(home)
            feed.append_runtime("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            feed.append_runtime("mishe-tauftauf", "wake delivered top-pain sensor for entry 1 extra")
            self.assertEqual(main(["--home", str(home), "dispatch-receipt", "sensor", "1", "delivered"]), 2)
            self.assertEqual(len([e for e in feed.entries() if e.body == "wake delivered top-pain sensor for entry 1"]), 0)

    def test_policy_validation_and_versioned_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            policy = home / "policy.json"
            policy.write_text('{"version":"mesh-1","question_versions":{"publish":"p2"},"low_threshold":0.1,"high_threshold":0.9,"judge_timeout":1}', encoding="utf-8")
            executable(home / "top-pains" / "sensor", "printf 'RED'\n")
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False, policy=policy)).observe()
            receipts = [e.body for e in Feed(home).entries() if e.body.startswith("judged publish")]
            self.assertTrue(receipts)
            self.assertIn("question-version=p2 policy-version=mesh-1", receipts[0])
            policy.write_text('{"low_threshold":0.95,"high_threshold":0.8}', encoding="utf-8")
            with self.assertRaises(ValueError):
                Coordinator(RuntimeConfig(home, launcher="headless", policy=policy))

    def test_due_prediction_receipts_do_not_store_fresh_raw_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            secret = "SECRET-DUE-PANE-117"
            executable(home / "top-pains" / "sensor", f"printf '{secret}'\n")
            past = (datetime.now(timezone.utc) - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            feed = Feed(home)
            prediction = feed.append_runtime("prediction/sensor", f"Expected sensor green.\nCheck at: {past}\n")
            feed.append_runtime("mishe-tauftauf", f"prediction {prediction.sequence}: accepted")
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False)).due_predictions()
            self.assertNotIn(secret.encode(), feed.read_bytes())
            self.assertIn(b"requires reasoning", feed.read_bytes())

    def test_selected_run_touches_only_one_channel_and_shares_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            for slug in ("alpha", "beta"):
                executable(home / "top-pains" / slug, f"printf '{slug} red\\n'\n")
                executable(home / "projectors" / slug, f"printf '{slug} red\\n'\n")
            base = ["--home", str(home), "run", "--once", "--launcher", "headless", "--observe-only"]
            self.assertEqual(main(base + ["--slug", "alpha"]), 0)
            feed = Feed(home)
            first = feed.entries()
            self.assertTrue(any(e.source == "observation/alpha" for e in first))
            self.assertFalse(any(e.source == "observation/beta" for e in first))
            self.assertEqual(main(base + ["--slug", "beta"]), 0)
            all_entries = feed.entries()
            self.assertTrue(any(e.source == "observation/beta" for e in all_entries))
            self.assertFalse(any(e.body == "top-pain alpha absent" for e in all_entries))
            self.assertEqual([e.sequence for e in all_entries], list(range(1, len(all_entries) + 1)))

    def test_selected_run_rejects_missing_slug_and_shared_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "alpha", "printf 'alpha red\\n'\n")
            base = ["--home", str(home), "run", "--once", "--launcher", "headless", "--observe-only"]
            self.assertEqual(main(base + ["--slug", "missing"]), 2)
            first = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False, slug="alpha"))
            second = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False, slug="alpha"))
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.close()

    def test_selected_run_scopes_due_predictions(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            for slug in ("alpha", "beta"):
                executable(home / "top-pains" / slug, "printf 'red\\n'\n")
            past = (datetime.now(timezone.utc) - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            feed = Feed(home)
            prediction = feed.append_runtime("prediction/beta", f"Expected beta green.\nCheck at: {past}\n")
            feed.append_runtime("mishe-tauftauf", f"prediction {prediction.sequence}: accepted")
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False, slug="alpha")).due_predictions()
            self.assertFalse(any("requires reasoning" in e.body for e in feed.entries()))
            Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False, slug="beta")).due_predictions()
            self.assertTrue(any(e.source == "observation/beta" and "requires reasoning" in e.body for e in feed.entries()))

    def test_channel_observations_route_once_while_shared_events_fan_out(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            slugs = [f"sensor-{n}" for n in range(17)]
            for slug in slugs:
                executable(home / "top-pains" / slug, "printf 'red\\n'\n")
            feed = Feed(home)
            observations = [feed.append_runtime(f"observation/{slug}", f"{slug} changed") for slug in slugs]
            shared = [feed.append("human", "broadcast to every channel"), feed.append("task", "task event")]
            coordinator = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False))
            coordinator._pane = lambda slug: "red"
            coordinator._judge = lambda question, slug, pane, evidence, prediction=None: Judgment(
                question, 0.9 if question == "relevance" else 0.1,
                "yes" if question == "relevance" else "no", "fixture", "fixture")
            coordinator.route()
            requests = [e.body for e in feed.entries() if e.body.startswith("wake requested ")]
            for event, slug in zip(observations, slugs):
                self.assertEqual([r for r in requests if r.endswith(f"for entry {event.sequence}")],
                                 [f"wake requested top-pain {slug} for entry {event.sequence}"])
            for event in shared:
                self.assertEqual(sum(r.endswith(f"for entry {event.sequence}") for r in requests), 17)
            self.assertEqual(len(requests), 51)

    def test_automatic_wakes_target_only_their_explicit_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            slugs = [f"sensor-{n}" for n in range(17)]
            for slug in slugs:
                executable(home / "top-pains" / slug, "printf 'red\\n'\n")
            feed = Feed(home)
            automatic = []
            for n, slug in enumerate(slugs, 1):
                body = (f"automatic channel={slug} event={n:020d}-{'a' * 32} "
                        f"source=consume source-seq={n} observed-at=2026-09-23T00:00:00Z "
                        f"prompt-sha256={'b' * 64} status=delivered")
                automatic.append(feed.append_runtime("automatic-wake", body))
            malformed = feed.append_runtime("automatic-wake", "automatic channel=sensor-0 private fixture")
            shared = feed.append("human", "broadcast to every channel")
            coordinator = Coordinator(RuntimeConfig(home, launcher="headless", dispatch=False))
            coordinator._pane = lambda slug: "red"
            coordinator._judge = lambda question, slug, pane, evidence, prediction=None: Judgment(
                question, 0.9 if question == "relevance" else 0.1,
                "yes" if question == "relevance" else "no", "fixture", "fixture")
            coordinator.route()
            entries = feed.entries()
            requests = [e.body for e in entries if e.body.startswith("wake requested ")]
            for event, slug in zip(automatic, slugs):
                self.assertEqual([r for r in requests if r.endswith(f"for entry {event.sequence}")],
                                 [f"wake requested top-pain {slug} for entry {event.sequence}"])
            self.assertFalse(any(r.endswith(f"for entry {malformed.sequence}") for r in requests))
            self.assertEqual(sum(r.endswith(f"for entry {shared.sequence}") for r in requests), 17)
            self.assertEqual(len(requests), 34)
            self.assertTrue(any(e.body == f"UNKNOWN automatic-wake entry {malformed.sequence}: target unavailable"
                                for e in entries))


if __name__ == "__main__":
    unittest.main()
