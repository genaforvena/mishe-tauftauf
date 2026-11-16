"""The WAN receive-drop detector in checks/network-care.py: a dead or stale reading is UNKNOWN, never GREEN."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("network_care", ROOT / "checks" / "network-care.py")
care = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(care)

NOW = 1_800_000_000.0


def row(ts, dropped, errors=0):
    return {"ts": ts, "uplink": {"device": "eth0", "rx_dropped": dropped, "rx_errors": errors}}


class UplinkStateTests(unittest.TestCase):
    def test_no_readings_is_unknown(self):
        self.assertEqual(care.uplink_state([], NOW)[0], 3)

    def test_stale_newest_reading_is_unknown_not_green(self):
        rows = [row(NOW - 3600, 100), row(NOW - 3000, 200)]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 3)

    def test_single_reading_has_no_rate(self):
        self.assertEqual(care.uplink_state([row(NOW - 60, 100)], NOW)[0], 3)

    def test_steady_rate_is_green(self):
        # 360 drops per hour over two hours
        rows = [row(NOW - 7200 + i * 600, 1000 + 60 * i) for i in range(13)]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 0, msg)
        self.assertIn("360/h", msg)

    def test_high_rate_over_full_span_is_red(self):
        # 600 drops per hour over two hours
        rows = [row(NOW - 7200 + i * 600, 1000 + 100 * i) for i in range(13)]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 2)

    def test_high_rate_over_short_span_is_not_red_yet(self):
        # 600 drops per hour but only 30 minutes of readings
        rows = [row(NOW - 1800 + i * 600, 1000 + 100 * i) for i in range(4)]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 0)

    def test_rx_errors_are_red(self):
        rows = [row(NOW - 1200, 100), row(NOW - 60, 110, errors=2)]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 2)

    def test_counter_reset_is_unknown(self):
        rows = [row(NOW - 3600, 5000), row(NOW - 60, 10)]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 3)

    def test_malformed_rows_are_skipped(self):
        rows = [{"ts": "x"}, {"uplink": None}, row(NOW - 1200, 100), row(NOW - 60, 130)]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 0, msg)


class UplinkDutyDeclared(unittest.TestCase):
    """The uplink judge must run as a registered duty, or nothing alarms on it."""

    def test_network_uplink_duty_runs_the_uplink_check_with_the_standard_codes(self):
        import json
        duties = json.loads((ROOT / "duties.json").read_text(encoding="utf-8"))
        items = duties if isinstance(duties, list) else duties.get("duties", [])
        (duty,) = [d for d in items if d.get("name") == "network-uplink"]
        self.assertEqual(duty["command"][1:], ["{repo}/checks/network-care.py", "uplink", "--home", "{home}", "--repo", "{repo}"])
        self.assertEqual(duty["red_codes"], [2])
        self.assertEqual(duty["unknown_codes"], [3])
        self.assertEqual(duty["resolver"], "network")


if __name__ == "__main__":
    unittest.main()
