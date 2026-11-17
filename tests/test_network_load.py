"""The router self-limit detector in checks/network-care.py: a short burst is GREEN, a sustained breach RED,
and a dead or stale reading UNKNOWN, never a calm pass."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("network_care", ROOT / "checks" / "network-care.py")
care = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(care)

NOW = 1_800_000_000.0


def row(ts, load=0.2, mem=248.0):
    return {"ts": ts, "ok": True, "router_load": {"load_1m": load, "load_5m": load, "mem_available_mb": mem}}


def run(start, count, step=300, **kwargs):
    return [row(start + i * step, **kwargs) for i in range(count)]


class LoadStateTests(unittest.TestCase):
    def test_no_readings_is_unknown(self):
        self.assertEqual(care.load_state([], NOW)[0], 3)
        self.assertEqual(care.load_state([{"ts": NOW - 60}], NOW)[0], 3)

    def test_stale_newest_reading_is_unknown_not_green(self):
        self.assertEqual(care.load_state([row(NOW - 3600), row(NOW - 1200)], NOW)[0], 3)

    def test_readings_older_than_the_window_are_ignored(self):
        self.assertEqual(care.load_state([row(NOW - 7 * 3600)], NOW)[0], 3)

    def test_quiet_router_is_green(self):
        code, msg = care.load_state(run(NOW - 1800, 7, load=0.2), NOW)
        self.assertEqual(code, 0, msg)
        self.assertIn("GREEN", msg)

    def test_single_over_limit_sample_is_green_but_named(self):
        code, msg = care.load_state([row(NOW - 1200), row(NOW - 60, load=1.8)], NOW)
        self.assertEqual(code, 0, msg)
        self.assertIn("over its self-limit for 0 min", msg)

    def test_short_breach_is_green_with_the_span(self):
        rows = run(NOW - 1800, 5, load=0.2) + run(NOW - 600, 3, load=1.4)  # 10 minutes over
        code, msg = care.load_state(rows, NOW)
        self.assertEqual(code, 0, msg)
        self.assertIn("for 10 min", msg)
        self.assertIn("under the 30 min alarm", msg)

    def test_breach_of_thirty_minutes_is_red(self):
        rows = run(NOW - 3600, 4, load=0.2) + run(NOW - 1800, 7, load=1.4)  # 30 minutes over
        code, msg = care.load_state(rows, NOW)
        self.assertEqual(code, 2, msg)
        self.assertIn("for 30 min", msg)

    def test_low_free_ram_counts_as_a_breach(self):
        rows = run(NOW - 1800, 7, load=0.2, mem=90.0)
        code, msg = care.load_state(rows, NOW)
        self.assertEqual(code, 2, msg)

    def test_a_gap_ends_the_run(self):
        old = run(NOW - 6 * 3600, 3, load=1.5)
        new = run(NOW - 1200, 5, load=1.5)  # 5 h later; span across the gap is not a sustained breach
        code, msg = care.load_state(old + new, NOW)
        self.assertEqual(code, 0, msg)
        self.assertIn("for 20 min", msg)

    def test_skipped_rows_neither_raise_nor_hide_the_alarm(self):
        skipped = [{"ts": NOW - 300 * i, "ok": False, "error": "skipped"} for i in range(1, 4)]
        code, msg = care.load_state(run(NOW - 1200, 3, load=1.5) + skipped, NOW)
        self.assertEqual(code, 0, msg)  # newest load reading is 10 min old, breach span 10 min
        code, msg = care.load_state(run(NOW - 1900, 7, load=1.5) + skipped[:1], NOW)
        self.assertEqual(code, 2, msg)  # 30-minute breach, a newer skip row does not erase it

    def test_malformed_rows_are_skipped(self):
        rows = [{"ts": "x"}, {"router_load": None}, {"ts": NOW - 30, "router_load": {"load_5m": 0.1}},
                row(NOW - 1200), row(NOW - 60)]
        code, msg = care.load_state(rows, NOW)
        self.assertEqual(code, 0, msg)


class LoadRunTests(unittest.TestCase):
    """The one run walk both the duty and the window print (tools/network_boundary.load_run)."""

    def test_no_readable_sample_is_none(self):
        self.assertIsNone(care.nb.load_run([], NOW))
        self.assertIsNone(care.nb.load_run([{"ts": NOW, "router_load": None}], NOW))

    def test_span_covers_the_whole_over_limit_run(self):
        rows = run(NOW - 3600, 4, load=0.2) + run(NOW - 1500, 6, load=1.4)
        sample = care.nb.load_run(rows, NOW)
        self.assertTrue(sample["over"])
        self.assertAlmostEqual(sample["span_s"], 1500)

    def test_span_is_zero_when_within_the_limit(self):
        sample = care.nb.load_run(run(NOW - 900, 4, load=0.2), NOW)
        self.assertFalse(sample["over"])
        self.assertEqual(sample["span_s"], 0.0)

    def test_a_skipped_row_still_yields_the_last_real_sample(self):
        rows = [row(NOW - 900, load=0.3), row(NOW - 600, load=0.3),
                {"ts": NOW - 120, "ok": False, "error": "skipped"}]
        sample = care.nb.load_run(rows, NOW)
        self.assertEqual(sample["load"], 0.3)
        self.assertAlmostEqual(sample["ago_s"], 600)


class LoadDutyDeclared(unittest.TestCase):
    """The load judge must run as a registered duty, or nothing alarms on a busy router."""

    def test_network_router_load_duty_runs_the_load_check_with_the_standard_codes(self):
        duties = json.loads((ROOT / "duties.json").read_text(encoding="utf-8"))
        items = duties if isinstance(duties, list) else duties.get("duties", [])
        (duty,) = [d for d in items if d.get("name") == "network-router-load"]
        self.assertEqual(duty["command"][1:],
                         ["{repo}/checks/network-care.py", "load", "--home", "{home}", "--repo", "{repo}"])
        self.assertEqual(duty["red_codes"], [2])
        self.assertEqual(duty["unknown_codes"], [3])
        self.assertEqual(duty["resolver"], "network")


if __name__ == "__main__":
    unittest.main()
