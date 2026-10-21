#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Tests for the delivered-lane clock bound.

The tool under test is the tracked research instrument, not a site-local copy:
``research/body/clock_bound.py``.  It is loaded by path because it is a
research instrument, not an importable package module.

Each test builds a delivered history with a known shape and asserts what the
instrument is allowed to conclude from it.  The assertions that matter most are
the negative ones: the lane bounds the skew from above and must never publish
a two-sided offset, and a reboot must not be read as a clock step.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "research" / "body" / "clock_bound.py"
SPEC = importlib.util.spec_from_file_location("clock_bound", TOOL)
assert SPEC and SPEC.loader
clock_bound = importlib.util.module_from_spec(SPEC)
# The module is registered before it is executed: dataclasses resolve their own
# module through sys.modules, and a path-loaded module is not there yet.
sys.modules[SPEC.name] = clock_bound
SPEC.loader.exec_module(clock_bound)


def _rows(entries: list[tuple[int, int, str]]) -> str:
    return "".join(f"{seq} {epoch} {body}\n" for seq, epoch, body in entries)


def _receipts(entries: list[tuple[float, int]]) -> str:
    lines = []
    for moment, records in entries:
        stamp = datetime.fromtimestamp(moment, timezone.utc).isoformat()
        lines.append(json.dumps({"ts": stamp, "node": "redmi10",
                                 "kind": "observations", "records": records,
                                 "byte_count": 10}))
    return "\n".join(lines) + "\n"


def _hello(seq: int, epoch: int, session: str = "aaaa-bbbb-cccc") -> tuple[int, int, str]:
    return seq, epoch, f"hello node=redmi10 protocol=1 session={session}"


def _light(seq: int, epoch: int, elapsed_ms: int) -> tuple[int, int, str]:
    return seq, epoch, f"light lux=1.0 accuracy=3 age_ms=10 phone_elapsed_ms={elapsed_ms}"


class ClockBoundTests(unittest.TestCase):
    def test_pairing_is_exact_and_leaves_host_written_head_rows_unpaired(self):
        rows = clock_bound.parse_rows(_rows([
            (1, 1000, "probe identity uid=1"),
            (2, 1000, "probe identity uid=1"),
            (3, 1005, _light(3, 1005, 5000)[2]),
            (4, 1010, _light(4, 1010, 10000)[2]),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.2, 2)]))
        pairs, unpaired = clock_bound.pair_rows(rows, receipts)
        self.assertEqual(unpaired, 2)
        self.assertEqual([pair.seq for pair in pairs], [3, 4])
        self.assertAlmostEqual(pairs[0].observed, 0.2)

    def test_the_bound_is_the_smallest_observed_value_and_the_lower_side_stays_unresolved(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1010, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.05, 1), (1010.5, 1)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertAlmostEqual(current["behind_upper_s"], 0.05)
        self.assertIsNone(current["behind_lower_s"])
        self.assertAlmostEqual(current["behind_upper_at_last_sample_s"], 0.5)

    def test_the_bound_accounts_for_the_phones_own_clock_movement(self):
        # The wall clock moves against the phone's monotonic clock by 1 s
        # across the session, so the smallest observed value - which happens
        # at the later row - is not a bound for the whole session: the
        # earlier row's level is the worse one and the bound must carry it.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1011, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.5, 1), (1011.05, 1)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertAlmostEqual(current["observed_min_s"], 0.05)
        self.assertAlmostEqual(current["behind_upper_s"], 0.5)
        self.assertAlmostEqual(current["wall_drift_s"], 1.0)

    def test_a_reboot_marks_the_session_that_opened_after_it(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 800000),
            _hello(3, 900000, "dddd-eeee-ffff"),
            _light(4, 900005, 47000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 2), (900005.1, 2)]))
        measurement = clock_bound.measure(rows, receipts)
        sessions = measurement["sessions"]
        self.assertEqual(len(sessions), 2)
        self.assertFalse(sessions[0]["opened_after_reboot"])
        self.assertTrue(sessions[1]["opened_after_reboot"])

    def test_a_wall_clock_step_inside_a_session_is_detected(self):
        # The device epoch jumps by 60 s while the monotonic clock keeps
        # running: the phone's wall clock was corrected mid-session.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1065, 6000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1065.1, 1)]))
        measurement = clock_bound.measure(rows, receipts)
        self.assertTrue(measurement["sessions"][-1]["clock_step"])

    def test_a_stable_session_is_not_reported_as_a_step(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1010, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1010.1, 1)]))
        measurement = clock_bound.measure(rows, receipts)
        self.assertFalse(measurement["sessions"][-1]["clock_step"])

    def test_a_batch_written_in_reverse_order_is_not_read_as_a_reboot(self):
        # A delivery batch is written newest first, so the phone's uptime
        # counter decreases inside it.  Nothing restarted: the counter is
        # still large, and the device epoch moves with it.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1010, 10000),
            _light(3, 1005, 5000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1010.1, 3)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertEqual(current["reboots"], [])
        self.assertFalse(current["opened_after_reboot"])

    def test_the_freshest_sample_is_the_greatest_uptime_not_the_last_row(self):
        # The same batch, written newest first: the last row in the queue is
        # the oldest row of the batch, so the bound at the freshest sample
        # must come from the row with the greatest uptime.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1010, 10000),
            _light(3, 1005, 5000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1010.05, 3)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        # The row with uptime 10000 was produced at device epoch 1010 and
        # received at 1010.05, so its observed value is 0.05 - the last row
        # in the queue would have said 5.05.
        self.assertAlmostEqual(current["behind_upper_at_last_sample_s"], 0.05)

    def test_a_reset_onto_a_fresh_boot_value_withholds_the_session_wide_bound(self):
        # The phone's uptime counter falls from 100 s to 5 s and the device
        # epoch falls with it.  Those bytes are two things at once: a
        # reverse-order batch (the device kept running) or a reboot whose
        # wall clock was re-read backwards (the rows on either side of it
        # then do not share one monotonic offset).  The lane cannot tell
        # them apart, so the bound is withheld rather than published on the
        # reordering reading.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 100_000),
            _light(3, 905, 5_000),
            _light(4, 1005, 105_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 4)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertIsNone(current["behind_upper_s"])
        self.assertIsNotNone(current["behind_upper_at_last_sample_s"])
        self.assertTrue(current["ambiguous_resets"])
        self.assertIn("fresh-boot value", current["session_wide_withheld_reason"])

    def test_a_reset_hidden_in_the_devices_own_order_withholds_the_bound(self):
        # The uptime counter falls by more than the wall clock's own
        # quantization when the rows are ordered by the phone's own clock:
        # the session spans a reset whatever the delivered order says.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 100_000),
            _light(3, 1010, 50_000),
            _light(4, 1015, 55_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1015.1, 4)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertIsNone(current["behind_upper_s"])
        self.assertTrue(current["masked_resets"])

    def test_a_wall_clock_step_is_accounted_for_rather_than_withholding_the_bound(self):
        # The wall clock jumps 60 s against the phone's own monotonic clock
        # while the rows arrive on time.  The movement is measured (the
        # drift), and the bound follows the rows rather than being withheld:
        # the phone's clock was at most the observed amount behind at every
        # row, and the step shows up as the clock being ahead afterwards.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1065, 6000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1065.1, 1)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        # The wall clock moved 60 s while the monotonic clock moved 1 s, so
        # the value against the monotonic reference moved 59 s.
        self.assertAlmostEqual(current["wall_drift_s"], 59.0)
        self.assertIsNotNone(current["behind_upper_s"])
        self.assertAlmostEqual(current["behind_upper_s"], 0.1)

    def test_the_delivery_latency_is_bounded_below_by_the_clock_bound(self):
        # One row arrives on time and another 500 s late, while the phone's
        # own clock tracks its monotonic clock throughout.  The clock bound
        # is then tight (0.1 s), and the lateness of the late row cannot be
        # the clock: it was delivered at least 500 s after the phone
        # produced it.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1010, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1510.1, 1)]))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertAlmostEqual(current["observed_min_s"], 0.1)
        self.assertAlmostEqual(current["observed_max_s"], 500.1)
        self.assertAlmostEqual(current["behind_upper_s"], 0.1)
        self.assertAlmostEqual(current["delivery_latency_lower_s"], 500.0)

    def test_the_reason_never_states_an_inequality_the_lane_does_not_support(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 5000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.02, 2)]))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1006.0)
        self.assertNotIn("host-device <=", document["reason"])
        self.assertIn("behind the host", document["reason"])
        self.assertIn("session aaaa-bbb ", document["reason"])

    def test_the_emitted_document_publishes_the_bound_and_no_offset(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 5000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.02, 2)]))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1006.0)
        self.assertEqual(document["status"], "unavailable")
        self.assertIsNone(document["offset_min_s"])
        self.assertIsNone(document["offset_max_s"])
        self.assertIsNone(document["offset_nominal_s"])
        self.assertEqual(document["node"], "redmi10")
        self.assertLessEqual(len(document["reason"]), clock_bound.MAX_REASON_CHARS)
        self.assertIn("+0.020s", document["reason"])
        self.assertLessEqual(set(document), clock_bound.DOCUMENT_KEYS)

    def test_a_malformed_journal_line_is_skipped_not_guessed(self):
        text = _receipts([(1005.2, 1)]) + "not json at all\n"
        receipts = clock_bound.parse_receipts(text)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].records, 1)

    def test_the_document_is_written_atomically_and_is_readable_json(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 5000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.02, 2)]))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1006.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body" / "clock-domain-redmi10.json"
            clock_bound.write_document(path, document)
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["node"], "redmi10")
            self.assertEqual(written["status"], "unavailable")
            self.assertEqual(oct(path.stat().st_mode)[-3:], "600")


if __name__ == "__main__":
    unittest.main()
