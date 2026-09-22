from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mishe_tauftauf.checks import run_check
from mishe_tauftauf.cli import initialize
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.judges import run_external
from mishe_tauftauf.observations import compose_frame, run_filter
from mishe_tauftauf.predictions import PredictionError, append_prediction, pending_predictions, replay_predictions
from mishe_tauftauf.runtime import Coordinator, RuntimeConfig


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class CoreTests(unittest.TestCase):
    def test_real_check_and_renderer_failure_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "#!/bin/sh\nprintf 'DESIRED STATE: reading=42\\n'\n")
            run_check(home, "sensor", ["sh", "-c", "echo red >&2; exit 3"])
            frame = compose_frame(home, "sensor").body
            self.assertIn("exit: 3", frame)
            self.assertIn("red", frame)
            executable(home / "top-pains" / "sensor", "#!/bin/sh\nexit 4\n")
            self.assertIn("UNKNOWN — top-pain sensor renderer exit-4", compose_frame(home, "sensor").body)

    def test_filter_holds_jitter_without_changing_frame_and_broken_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            filter_path = home / "filters" / "sensor"
            executable(filter_path, "#!/bin/sh\nexit 1\n")
            result = run_filter(home, "sensor", "value=1\n", "value=2\n")
            self.assertFalse(result.passed)
            self.assertEqual("value=2\n", "value=2\n")
            filter_path.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            filter_path.chmod(0o755)
            result = run_filter(home, "sensor", "same", "same")
            self.assertTrue(result.passed)
            self.assertIn("UNKNOWN event filter sensor", result.diagnostic)

    def test_prediction_validation_replacement_and_overdue_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home); feed = Feed(home)
            future = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
            first = append_prediction(feed, "sensor", f"Expected reading.\nCheck at: {future}\n")
            feed.append_runtime("mishe-tauftauf", f"prediction {first.sequence}: accepted")
            with self.assertRaises(PredictionError):
                append_prediction(feed, "sensor", "bad replacement", replaces=first.sequence)
            self.assertEqual([p.sequence for p in pending_predictions(home, feed.entries())], [first.sequence])
            second = append_prediction(feed, "sensor", f"Revised.\nCheck at: {future}\n", replaces=first.sequence)
            feed.append_runtime("mishe-tauftauf", f"prediction {second.sequence}: accepted")
            self.assertEqual([p.sequence for p in pending_predictions(home, feed.entries())], [second.sequence])
            # Restart reconstruction uses feed text, not process objects.
            self.assertEqual(replay_predictions(home, Feed(home).entries())[first.sequence].replaced_by, second.sequence)

    def test_judge_output_is_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); marker = root / "owned"
            judge = root / "judge"
            executable(judge, f"#!/bin/sh\nprintf 'probability 0.9\\ntouch {marker}\\n'\n")
            result = run_external(judge, "publish", "sensor", "pane", "evidence")
            self.assertEqual(result.outcome, "unknown")
            self.assertFalse(marker.exists())

    def test_unchanged_observation_due_check_runs_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(home / "top-pains" / "sensor", "#!/bin/sh\nprintf 'DESIRED STATE: ok\\nUNKNOWN unchanged\\n'\n")
            judge = home / "judge"
            executable(judge, "#!/bin/sh\ncase \"$(sed -n '1p')\" in *valid-attempt*) echo 'probability 0.95';; *relevance*) echo 'probability 0.95';; *publish*) echo 'probability 0.95';; *) echo 'probability 0.05';; esac\n")
            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            entry = Feed(home).append_runtime("prediction/sensor", f"Expected ok.\nCheck at: {past}\n")
            Feed(home).append_runtime("mishe-tauftauf", f"prediction {entry.sequence}: accepted")
            coordinator = Coordinator(RuntimeConfig(home, judge, "headless", interval=0.1))
            coordinator.acquire()
            try:
                coordinator.due_predictions()
            finally:
                coordinator.close()
            bodies = [item.body for item in Feed(home).entries()]
            self.assertTrue(any(f"prediction {entry.sequence}: insufficient-evidence" in body for body in bodies))
            self.assertTrue(any("requires reasoning" in body for body in bodies))

    def test_same_observer_minds_serialize_and_crash_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); initialize(home)
            executable(
                home / "minds" / "sensor",
                "#!/bin/sh\nsleep 0.4\nexit 7\n",
            )
            feed = Feed(home)
            first = feed.append_runtime("observation/sensor", "first problem")
            second = feed.append_runtime("observation/sensor", "arrived while busy")
            coordinator = Coordinator(RuntimeConfig(home, launcher="headless"))
            worker = threading.Thread(target=coordinator.invoke, args=("sensor", first))
            worker.start()
            time.sleep(0.1)
            coordinator.invoke("sensor", second)
            worker.join(3)
            entries = feed.entries()
            starts = [entry for entry in entries if entry.body.startswith("mind starting top-pain sensor")]
            self.assertEqual(len(starts), 1)
            self.assertTrue(any("exited without a tied handoff" in entry.body for entry in entries))
            coordinator.invoke("sensor", second)
            starts = [entry for entry in feed.entries() if entry.body.startswith("mind starting top-pain sensor")]
            self.assertEqual(len(starts), 2)


if __name__ == "__main__":
    unittest.main()
