"""The WAN receive-drop detector in checks/network-care.py: a dead or stale reading is UNKNOWN, never GREEN."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("network_care", ROOT / "checks" / "network-care.py")
care = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(care)

NOW = 1_800_000_000.0


def row(ts, dropped, errors=0, carrier_down=2, speed=1000, duplex="full"):
    return {"ts": ts, "uplink": {"device": "eth0", "rx_dropped": dropped, "rx_errors": errors},
            "wan_link": {"device": "eth0", "speed_mbps": speed, "duplex": duplex,
                         "carrier_up": carrier_down, "carrier_down": carrier_down}}


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

    def test_missing_physical_link_state_is_unknown_not_green(self):
        rows = [{"ts": NOW - 1200, "uplink": row(NOW - 1200, 100)["uplink"]},
                {"ts": NOW - 60, "uplink": row(NOW - 60, 130)["uplink"]}]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 3)

    def test_link_without_a_negotiated_speed_is_unknown(self):
        rows = [row(NOW - 1200, 100, speed=None), row(NOW - 60, 130, speed=None)]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 3)

    def test_three_carrier_drops_in_the_window_are_red(self):
        rows = [row(NOW - 7200 + i * 600, 1000, carrier_down=2 + i // 4) for i in range(13)]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 2, msg)
        self.assertIn("WAN link went down 3 times", msg)

    def test_two_carrier_drops_in_the_window_are_green_with_a_note(self):
        rows = [row(NOW - 7200 + i * 600, 1000, carrier_down=2 + i // 5) for i in range(13)]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 0, msg)
        self.assertIn("link down 2 times in the window", msg)

    def test_a_reset_inside_the_window_keeps_the_drops_seen_before_it(self):
        # 1 -> 5 (4 drops), reboot, then 2 -> 4 (4 counted since the reboot): 8, not the endpoints' 3.
        rows = [row(NOW - 1800 + i * 300, 100, carrier_down=c)
                for i, c in enumerate([1, 5, 2, 4])]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 2, msg)
        self.assertIn("went down 8 times", msg)

    def test_a_reset_no_longer_hides_a_flapping_cable(self):
        # Endpoints 1 -> 2 read as one drop; the steps are 3 then 2 after a reboot, so 5.
        rows = [row(NOW - 1200 + i * 600, 100, carrier_down=c) for i, c in enumerate([1, 4, 2])]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 2, msg)
        self.assertIn("went down 5 times", msg)

    def test_a_receive_drop_reset_counts_the_drops_seen_before_it(self):
        # 1000 -> 3000 (2000 drops), reset, 10 since: 2010 over 2 h is still above the bound.
        rows = [row(NOW - 7200, 1000), row(NOW - 3600, 3000), row(NOW - 60, 10)]
        self.assertEqual(care.uplink_state(rows, NOW)[0], 2)

    def test_a_receive_drop_reset_below_the_bound_is_unknown(self):
        rows = [row(NOW - 7200, 1000), row(NOW - 3600, 1010), row(NOW - 60, 5)]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 3, msg)
        self.assertIn("restarted", msg)

    def test_carrier_counter_reset_is_not_a_flapping_cable(self):
        rows = [row(NOW - 1200, 100, carrier_down=7), row(NOW - 60, 130, carrier_down=1)]
        code, msg = care.uplink_state(rows, NOW)
        self.assertEqual(code, 3, msg)
        self.assertIn("restarted", msg)
        self.assertNotIn("went down", msg)


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
