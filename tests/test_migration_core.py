from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mishe_tauftauf.cli import initialize, main
from mishe_tauftauf.feed import Feed
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


if __name__ == "__main__":
    unittest.main()
