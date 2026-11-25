"""A device that answers now still shows its silent runs from the last 24 h."""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
spec = importlib.util.spec_from_file_location("nb_history_under_test", REPO / "tools" / "network_boundary.py")
nb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nb)

NOW = 1_795_600_000.0
NAME = "iMac-Rozalia"


def row(ts, ok):
    dev = {"ok": True, "avg_ms": 50.0, "loss_pct": 0.0} if ok else {"ok": False, "why": "no reply"}
    return {"ts": ts, "devices": {NAME: dev}}


class DeviceHistory(unittest.TestCase):
    def test_no_failures_is_empty(self):
        rows = [row(NOW - 600, True), row(NOW - 300, True)]
        self.assertEqual(nb.device_outage_phrase(rows, NAME, NOW), "")

    def test_two_runs_counted_and_newest_answer_does_not_hide_them(self):
        rows = [
            row(NOW - 20000, False), row(NOW - 19000, False), row(NOW - 18000, True),
            row(NOW - 9000, False), row(NOW - 8000, False), row(NOW - 7000, True),
            row(NOW - 60, True),
        ]
        phrase = nb.device_outage_phrase(rows, NAME, NOW)
        self.assertIn("silent in 2 runs over the last 24 h", phrase)
        self.assertIn("longest 0.3 h", phrase)

    def test_failures_older_than_24h_are_ignored(self):
        rows = [row(NOW - 90000, False), row(NOW - 89000, False), row(NOW - 60, True)]
        self.assertEqual(nb.device_outage_phrase(rows, NAME, NOW), "")

    def test_unreadable_or_future_rows_are_skipped(self):
        rows = [{"ts": "x", "devices": {NAME: {"ok": False}}}, row(NOW + 500, False), row(NOW - 60, True)]
        self.assertEqual(nb.device_outage_phrase(rows, NAME, NOW), "")


if __name__ == "__main__":
    unittest.main()
