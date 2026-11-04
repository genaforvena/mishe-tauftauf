from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mishe_tauftauf.checks import run_check
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.judges import run_external
from mishe_tauftauf.observations import compose_frame, run_filter
from mishe_tauftauf.predictions import PredictionError, append_prediction, pending_predictions, replay_predictions


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class CoreTests(unittest.TestCase):



    def test_judge_output_is_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); marker = root / "owned"
            judge = root / "judge"
            executable(judge, f"#!/bin/sh\nprintf 'probability 0.9\\ntouch {marker}\\n'\n")
            result = run_external(judge, "publish", "sensor", "pane", "evidence")
            self.assertEqual(result.outcome, "unknown")
            self.assertFalse(marker.exists())




if __name__ == "__main__":
    unittest.main()
