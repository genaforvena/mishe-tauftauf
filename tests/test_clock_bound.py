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


def _receipts(entries: list[tuple[float, int]], rows: list | None = None) -> str:
    """The host's journal lines for a delivered history.

    ``byte_count`` is the host's own statement of how many bytes the POST body
    carried (``server.py``: ``len(body)``), so it has to be the **real** size
    of the rows the receipt delivered.  A receipt that claims a size its rows
    do not have is a damaged journal, and the instrument must say so rather
    than quietly pairing by count.

    The receipts are walked **backward from the live tail**, the same way the
    instrument pairs: the last receipt owns the last ``records`` rows, the one
    before it owns the rows before those, and any rows left over at the head
    belong to no receipt.  The byte count of each receipt is computed from
    the rows it owns under exactly that rule, using the instrument's own
    formula, so the fixture and the tool cannot drift apart.
    """
    lines = []
    remaining = list(rows) if rows is not None else None
    for moment, records in reversed(entries):
        stamp = datetime.fromtimestamp(moment, timezone.utc).isoformat()
        byte_count = 10
        if remaining is not None:
            owned = remaining[-records:] if records else []
            remaining = remaining[:-records] if records else remaining
            byte_count = sum(clock_bound.post_body_bytes(row) for row in owned)
        lines.append(json.dumps({"ts": stamp, "node": "redmi10",
                                 "kind": "observations", "records": records,
                                 "byte_count": byte_count}))
    lines.reverse()
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.2, 2)], rows))
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.05, 1), (1010.5, 1)], rows))
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.5, 1), (1011.05, 1)], rows))
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 2), (900005.1, 2)], rows))
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1065.1, 1)], rows))
        measurement = clock_bound.measure(rows, receipts)
        self.assertTrue(measurement["sessions"][-1]["clock_step"])

    def test_a_stable_session_is_not_reported_as_a_step(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1010, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1010.1, 1)], rows))
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
        receipts = clock_bound.parse_receipts(_receipts([(1010.1, 3)], rows))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertEqual(current["reboots"], [])
        self.assertFalse(current["opened_after_reboot"])

    def test_the_freshest_sample_is_the_greatest_uptime_not_the_last_row(self):
        # The same batch, written newest first: the last row in the queue is
        # the oldest row of the batch, so the bound at the freshest sample
        # must come from the row with the greatest uptime.  The counter is
        # large on both rows, so the fall is a reordering and not a reset.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1010, 800_000),
            _light(3, 1005, 795_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1010.05, 3)], rows))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertEqual(current["ambiguous_resets"], [])
        # The row with uptime 800000 was produced at device epoch 1010 and
        # received at 1010.05, so its observed value is 0.05 - the last row
        # in the queue would have said 5.05.
        self.assertAlmostEqual(current["behind_upper_at_last_sample_s"], 0.05)

    def test_a_reset_withholds_the_freshest_sample_because_the_order_is_unknown(self):
        # A fall onto a fresh-boot value is a reset or a reordering of the
        # device's first minutes, and the two cannot be told apart.  Which
        # row was produced last is then not established either, so the
        # freshest-sample bound is withheld rather than guessed: a row from
        # before the reset carries the larger counter and would otherwise be
        # named as the freshest one.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 500_000),
            _light(3, 1010, 5_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1010.1, 3)], rows))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertTrue(current["ambiguous_resets"])
        self.assertIsNone(current["behind_upper_at_last_sample_s"])
        self.assertIsNone(current["behind_upper_s"])
        # The premise-free per-row bound does not need a chronology.
        self.assertIsNotNone(current["observed_min_s"])

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
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 4)], rows))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertIsNone(current["behind_upper_s"])
        # The order of the rows across the reset is not established either,
        # so the freshest-sample bound is withheld as well.
        self.assertIsNone(current["behind_upper_at_last_sample_s"])
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
        receipts = clock_bound.parse_receipts(_receipts([(1015.1, 4)], rows))
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1065.1, 1)], rows))
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1510.1, 1)], rows))
        measurement = clock_bound.measure(rows, receipts)
        current = measurement["sessions"][-1]
        self.assertAlmostEqual(current["observed_min_s"], 0.1)
        self.assertAlmostEqual(current["observed_max_s"], 500.1)
        self.assertAlmostEqual(current["behind_upper_s"], 0.1)
        self.assertAlmostEqual(current["delivery_latency_lower_s"], 500.0)

    def test_a_prompt_lane_has_a_steady_lateness_profile(self):
        # Every row is delivered 0.1 s after the phone produced it, so one
        # correction ages the whole session and the profile says so.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1010, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 2), (1010.1, 1)], rows))
        measurement = clock_bound.measure(rows, receipts)
        lateness = measurement["sessions"][-1]["lateness"]
        self.assertEqual(lateness["rows"], 2)
        self.assertAlmostEqual(lateness["observed_min_s"], 0.1)
        self.assertAlmostEqual(lateness["observed_max_s"], 0.1)
        self.assertAlmostEqual(lateness["spread_s"], 0.0)
        self.assertEqual(lateness["stale_rows"], 0)
        self.assertTrue(lateness["constant_correction_within_tolerance"])

    def test_a_backlogged_lane_is_not_correctable_by_one_figure(self):
        # One row is delivered on time and another 500 s late.  The median
        # and the worst case alone do not say that one figure cannot serve
        # the session, so the profile carries the spread and the verdict.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1010, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 1), (1510.1, 1)], rows))
        measurement = clock_bound.measure(rows, receipts)
        lateness = measurement["sessions"][-1]["lateness"]
        self.assertEqual(lateness["rows"], 2)
        self.assertAlmostEqual(lateness["observed_min_s"], 0.1)
        self.assertAlmostEqual(lateness["observed_max_s"], 500.1)
        self.assertAlmostEqual(lateness["spread_s"], 500.0)
        self.assertEqual(lateness["stale_rows"], 1)
        self.assertFalse(lateness["constant_correction_within_tolerance"])
        # The residual is the spread plus the device epoch's own slack.
        self.assertAlmostEqual(lateness["relative_age_residual_s"],
                               500.0 + clock_bound.QUANTIZATION_S)
        self.assertIn("own receipt", lateness["note"])

    def test_the_reason_never_states_an_inequality_the_lane_does_not_support(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 5000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.02, 2)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1006.0)
        self.assertNotIn("host-device <=", document["reason"])
        self.assertIn("behind the host", document["reason"])
        self.assertIn("session aaaa-bbb", document["reason"])

    def test_the_reason_fits_the_reader_budget_and_keeps_the_bound(self):
        # The reason is the prose a reader of the document gets, and the
        # reader's budget is 200 characters: past that the android-body
        # reader replaces the whole reason with a bare class token, so
        # every fact it carried is lost.  The reason therefore has to fit,
        # and the facts that do not fit are carried by the typed field
        # instead of being truncated away.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 800_000),
            _light(3, 1010, 805_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 2), (1510.1, 1)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1511.0)
        reason = document["reason"]
        self.assertLessEqual(len(reason), clock_bound.MAX_REASON_CHARS)
        self.assertEqual(clock_bound.MAX_REASON_CHARS, 200)
        # The bound, its coverage and the side the lane does not bound all
        # survive, and the reason is never cut inside a clause.
        self.assertIn("at most 0.100s behind the host", reason)
        self.assertIn("2 monotonic rows of session aaaa-bbb", reason)
        self.assertIn("the ahead side is unbounded by this lane", reason)
        self.assertNotIn("...", reason)

    def test_the_typed_field_carries_the_bound_the_premise_and_the_coverage(self):
        # The reason's budget cannot hold the premise and the coverage, so
        # the typed field carries them: the bound as a number with its
        # relation stated, the premise it rests on, and the rows it covers.
        # The shape is the one core proposed (genome DM 85077).
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 800_000),
            _light(3, 1010, 805_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 2), (1510.1, 1)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1511.0)
        field = document["clock_bound"]
        self.assertEqual(field["version"], clock_bound.CLOCK_BOUND_VERSION)
        # The relation is signed and explicit: the phone's clock minus the
        # host's is at least this many seconds.  A consumer cannot read it
        # as a measured offset or as an interval, because the field also
        # names the side the lane does not bound.
        self.assertAlmostEqual(field["phone_minus_host_gte_s"], -0.100)
        self.assertEqual(field["unbounded_side"], "above")
        self.assertTrue(field["published"])
        self.assertEqual(field["session_id"], "aaaa-bbbb-cccc")
        self.assertEqual(field["covered_rows"], 2)
        self.assertEqual(field["rows"], 3)
        self.assertEqual(field["premise"], list(clock_bound.BEHIND_BOUND_PREMISES))
        self.assertEqual(len(field["premise"]), 2)

    def test_the_typed_field_carries_the_lane_lateness_the_prose_drops(self):
        # The delivery-latency lower bound says that a stale timestamp is
        # the lane's lateness and not the phone's clock.  The prose reason
        # cannot hold it - the budget drops the optional clause - so the
        # typed field carries it, or the fact is lost entirely.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 800_000),
            _light(3, 1010, 805_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 2), (1510.1, 1)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1511.0)
        field = document["clock_bound"]
        self.assertIn("delivery_latency_lower_s", field)
        self.assertAlmostEqual(field["delivery_latency_lower_s"], 500.0)

    def test_the_typed_field_is_absent_when_the_lane_supports_no_bound(self):
        # A reset the lane cannot classify withholds the session-wide bound,
        # and with it the freshest-sample bound, so there is no bound to
        # publish and the field says nothing rather than guessing.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 100_000),
            _light(3, 905, 5_000),
            _light(4, 1005, 105_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.1, 4)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1006.0)
        field = document["clock_bound"]
        self.assertFalse(field["published"])
        self.assertIsNone(field["phone_minus_host_gte_s"])
        self.assertIn("fresh-boot value", field["withheld_reason"])
        self.assertIn("no session-wide bound", document["reason"])

    def test_the_typed_field_carries_no_value_when_the_bound_is_withheld(self):
        # A field that says "not published" must not also carry a number a
        # consumer could read as a bound, and it must say why in a value
        # rather than in prose.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 500_000),
            _light(3, 1010, 5_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1010.1, 3)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1011.0)
        field = document["clock_bound"]
        self.assertFalse(field["published"])
        self.assertIsNone(field["phone_minus_host_gte_s"])
        self.assertEqual(field["premise"], [])
        self.assertIn("fresh-boot value", field["withheld_reason"])

    def test_the_withheld_reason_fits_the_reader_budget(self):
        # The android-body reader replaces a reason past 200 characters with
        # the bare token "clock-domain-unavailable", which loses every fact
        # the reason carried.  The withheld branch's reason is the longest
        # one this producer writes, so it is the one that has to fit.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 500_000),
            _light(3, 1010, 5_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1010.1, 3)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1011.0)
        reason = document["reason"]
        self.assertLessEqual(
            len(reason), clock_bound.MAX_REASON_CHARS,
            f"the withheld reason is {len(reason)} characters: {reason!r}")
        # The facts a reader needs survive, and the tightest bound is
        # qualified as the bound at the row that produced it - it is not a
        # bound that holds across the session.
        self.assertIn("no session-wide bound", reason)
        self.assertIn("at most 0.100s behind the host", reason)
        self.assertIn("at the row that produced it", reason)
        self.assertIn("the ahead side is unbounded by this lane", reason)

    def test_the_withheld_reason_says_which_row_the_tightest_bound_is_at(self):
        # 82349's independent reading found that publishing min(observed)
        # as a session-wide bound was wrong: it is a bound at the row that
        # produced it.  The qualifier is what keeps that finding fixed.
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 500_000),
            _light(3, 1010, 5_000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1010.1, 3)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1011.0)
        self.assertIn("at the row that produced it", document["reason"])

    def test_the_emitted_document_publishes_the_bound_and_no_offset(self):
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000, "aaaa-bbbb-cccc"),
            _light(2, 1005, 5000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.02, 2)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1006.0)
        self.assertEqual(document["status"], "unavailable")
        self.assertIsNone(document["offset_min_s"])
        self.assertIsNone(document["offset_max_s"])
        self.assertIsNone(document["offset_nominal_s"])
        self.assertEqual(document["node"], "redmi10")
        self.assertLessEqual(len(document["reason"]), clock_bound.MAX_REASON_CHARS)
        self.assertIn("0.020s behind the host", document["reason"])
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
        receipts = clock_bound.parse_receipts(_receipts([(1005.02, 2)], rows))
        measurement = clock_bound.measure(rows, receipts)
        document = clock_bound.clock_bound_document("redmi10", measurement, 1006.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body" / "clock-domain-redmi10.json"
            clock_bound.write_document(path, document)
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["node"], "redmi10")
            self.assertEqual(written["status"], "unavailable")
            self.assertEqual(oct(path.stat().st_mode)[-3:], "600")


class RowAgeTests(unittest.TestCase):
    """Every delivered row's age comes from that row's own receipt."""

    def _lane(self) -> tuple[list, list]:
        rows = clock_bound.parse_rows(_rows([
            _hello(1, 1000),
            _light(2, 1005, 5000),
            _light(3, 1010, 10000),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.05, 2), (1510.1, 1)], rows))
        return rows, receipts

    def test_every_delivered_row_carries_its_own_age(self):
        rows, receipts = self._lane()
        chunks, unpaired = clock_bound.pair_chunks(rows, receipts)
        ages = clock_bound.row_ages(chunks, 2000.0)
        self.assertEqual(unpaired, 0)
        self.assertEqual([age.seq for age in ages], [1, 2, 3])
        # The last POST delivered one row 500 s late; the two rows before it
        # were delivered together.  Each row's age is its own receipt's age.
        self.assertAlmostEqual(ages[0].delivery_age_s, 2000.0 - 1005.05)
        self.assertAlmostEqual(ages[1].delivery_age_s, 2000.0 - 1005.05)
        self.assertAlmostEqual(ages[2].delivery_age_s, 2000.0 - 1510.1)
        self.assertAlmostEqual(ages[2].observed_s, 1510.1 - 1010)
        self.assertAlmostEqual(ages[2].sample_age_s, 2000.0 - 1010)

    def test_the_delivery_age_is_the_lower_bound_of_the_observation_age(self):
        rows, receipts = self._lane()
        chunks, _ = clock_bound.pair_chunks(rows, receipts)
        for age in clock_bound.row_ages(chunks, 2000.0):
            # The observation happened no later than the delivery, so the
            # delivery age is the lower bound of the observation's age; the
            # row's own lag is the difference between the two frames.
            self.assertAlmostEqual(age.sample_age_s - age.delivery_age_s,
                                   age.observed_s)
            self.assertGreaterEqual(age.delivery_age_s, 0.0)

    def test_the_age_index_covers_the_history_without_re_deriving_the_pairing(self):
        rows, receipts = self._lane()
        chunks, unpaired = clock_bound.pair_chunks(rows, receipts)
        index = clock_bound.age_index(chunks, 2000.0)
        self.assertEqual(sum(entry["rows"] for entry in index), 3)
        self.assertEqual((index[0]["first_seq"], index[0]["last_seq"]), (1, 2))
        self.assertEqual((index[1]["first_seq"], index[1]["last_seq"]), (3, 3))
        self.assertAlmostEqual(index[0]["delivery_age_s"], 2000.0 - 1005.05)
        self.assertAlmostEqual(index[1]["delivery_age_s"], 2000.0 - 1510.1)
        # The rows of one POST share its age, and each row's own sample clock
        # still says when it was produced.
        self.assertAlmostEqual(index[0]["sample_age_min_s"], 2000.0 - 1005)
        self.assertAlmostEqual(index[0]["sample_age_max_s"], 2000.0 - 1000)

    def test_the_profile_names_the_lane_s_own_ends(self):
        rows, receipts = self._lane()
        chunks, unpaired = clock_bound.pair_chunks(rows, receipts)
        profile = clock_bound.age_profile(chunks, 2000.0, unpaired)
        self.assertEqual(profile["status"], "ok")
        self.assertEqual(profile["rows"], 3)
        self.assertEqual(profile["posts"], 2)
        self.assertEqual(profile["unpaired_head_rows"], unpaired)
        self.assertAlmostEqual(profile["newest_row_delivery_age_s"], 2000.0 - 1510.1)
        self.assertAlmostEqual(profile["oldest_row_delivery_age_s"], 2000.0 - 1005.05)
        # The oldest sample is the one with the smallest clock reading, so it
        # is the one that reaches furthest back.
        self.assertAlmostEqual(profile["oldest_row_sample_age_s"], 2000.0 - 1000)
        self.assertAlmostEqual(profile["newest_row_sample_age_s"], 2000.0 - 1010)
        self.assertAlmostEqual(profile["history_span_s"], 10)

    def test_the_index_is_written_atomically_as_one_json_line_per_post(self):
        rows, receipts = self._lane()
        chunks, _ = clock_bound.pair_chunks(rows, receipts)
        index = clock_bound.age_index(chunks, 2000.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifacts" / "age-index.jsonl"
            clock_bound.write_age_index(path, index)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertAlmostEqual(json.loads(lines[1])["delivery_age_s"],
                                   2000.0 - 1510.1)
            self.assertEqual(oct(path.stat().st_mode)[-3:], "600")


class ByteWitnessTests(unittest.TestCase):
    """The host's byte witness, and what it repairs.

    The host writes two numbers about every POST: how many rows it appended,
    and how many bytes the body carried (``server.py``: ``len(body)``).  They
    come from two different measurements, so a receipt whose rows do not add
    up to its byte count is pointing at the wrong rows.

    These tests pin the behaviour that matters: a lost receipt's rows are
    found and left unpaired rather than absorbed by their neighbour, the
    repair is published, and a witness that cannot place a receipt does not
    guess.
    """

    @staticmethod
    def _lane() -> tuple[list, list]:
        """Four receipts and the rows they delivered, plus one lost receipt.

        The rows are sized so that their byte sums differ, so a byte witness
        can tell one window from another.  Rows 5-8 belong to a receipt whose
        journal line was destroyed; receipt 4 then has to skip over them.
        """
        entries = [
            (seq, 1000, "x" * seq) for seq in range(1, 13)
        ]
        rows = clock_bound.parse_rows(_rows(entries))
        # The last receipt owns the last four rows, the one before it the
        # four before those, and so on.  The receipt that owned rows 5-8 is
        # the one that was lost.
        receipts = clock_bound.parse_receipts(_receipts([
            (1005.1, 4), (1005.2, 4), (1005.4, 4),
        ], rows))
        return rows, receipts

    def test_the_byte_witness_reproduces_the_hosts_own_byte_count(self):
        # The formula the host uses to state a POST's size: the row's epoch,
        # a space, the row's body, and the newline that ends it.
        rows = clock_bound.parse_rows(_rows([(1, 1000, "hello node=redmi10")]))
        self.assertEqual(clock_bound.post_body_bytes(rows[0]),
                         len("1000 hello node=redmi10\n".encode("utf-8")))

    def test_a_clean_lane_needs_no_repair(self):
        rows, receipts = self._lane()
        pairing = clock_bound.pair_lane(rows, receipts)
        self.assertEqual(pairing.repairs, ())
        self.assertEqual(pairing.unpaired_rows, 0)
        self.assertEqual(clock_bound.verify_pairing(pairing.chunks)["status"],
                         "verified")

    def test_a_lost_receipt_leaves_its_rows_unpaired_rather_than_absorbed(self):
        # The receipt that owned rows 5-8 is gone from the journal.  Its rows
        # stay in the queue, and the receipt behind it must not claim them.
        rows = clock_bound.parse_rows(_rows([
            (seq, 1000, "x" * seq) for seq in range(1, 10)
        ]))
        # One receipt, claiming four rows.  Its count alone would give it the
        # last four rows (6-9), but the bytes it states belong to rows 5-8,
        # because that is what it actually delivered.
        receipt = clock_bound.Receipt(1005.1, 4, "redmi10", "observations",
                                      sum(clock_bound.post_body_bytes(row)
                                          for row in rows[4:8]))
        pairing = clock_bound.pair_lane(rows, [receipt])
        self.assertEqual([row.seq for row in pairing.chunks[0].rows],
                         [5, 6, 7, 8])
        # Rows 1-4 (before the first receipt) and row 9 (after the last) own
        # no receipt, and are counted rather than absorbed.
        self.assertEqual(pairing.unpaired_rows, 5)
        self.assertEqual(pairing.paired_rows + pairing.unpaired_rows,
                         len(rows))

    def test_the_repair_is_published_with_the_reading(self):
        rows = clock_bound.parse_rows(_rows([
            (seq, 1000, "x" * seq) for seq in range(1, 10)
        ]))
        receipt = clock_bound.Receipt(1005.1, 4, "redmi10", "observations",
                                      sum(clock_bound.post_body_bytes(row)
                                          for row in rows[4:8]))
        pairing = clock_bound.pair_lane(rows, [receipt])
        self.assertEqual(len(pairing.repairs), 1)
        repair = pairing.repairs[0]
        self.assertEqual(repair["resolution"], "realigned")
        self.assertEqual(repair["first_seq"], 5)
        self.assertEqual(repair["last_seq"], 8)
        self.assertEqual(repair["unpaired_rows"], 1)
        self.assertEqual(repair["records"], 4)
        self.assertEqual(repair["byte_count"],
                         sum(clock_bound.post_body_bytes(row) for row in rows[4:8]))

    def test_a_witness_that_cannot_place_a_receipt_does_not_guess(self):
        # The receipt claims a byte count no window of rows has.  The count
        # stands, the contradiction is reported, and no row is moved.
        rows = clock_bound.parse_rows(_rows([
            (1, 1000, "row-one-aaaa"),
            (2, 1000, "row-two-bbbb"),
            (3, 1000, "row-three-cccc"),
        ]))
        receipt = clock_bound.Receipt(1005.1, 3, "redmi10", "observations", 999999)
        pairing = clock_bound.pair_lane(rows, [receipt])
        self.assertEqual(len(pairing.repairs), 1)
        self.assertEqual(pairing.repairs[0]["resolution"], "unplaceable")
        # The rows keep the receipt they had: nothing was moved.
        self.assertEqual([row.seq for row in pairing.chunks[0].rows], [1, 2, 3])

    def test_an_ambiguous_witness_is_reported_rather_than_resolved(self):
        # Three windows of rows have the byte sum this receipt claims, and
        # nothing in the journal says which one it was.  The witness alone
        # cannot place it, so the receipt keeps its count, no row is moved,
        # and the ambiguity is published rather than resolved by a guess.
        rows = clock_bound.parse_rows(_rows([
            (1, 1000, "aaa"),
            (2, 1000, "bbb"),
            (3, 1000, "ccc"),
            (4, 1000, "ddd"),
            (5, 1000, "ee"),
            (6, 1000, "f"),
        ]))
        size = sum(clock_bound.post_body_bytes(row) for row in rows[0:2])
        receipt = clock_bound.Receipt(1005.1, 2, "redmi10", "observations", size)
        pairing = clock_bound.pair_lane(rows, [receipt])
        self.assertEqual(len(pairing.repairs), 1)
        self.assertEqual(pairing.repairs[0]["resolution"], "ambiguous")
        self.assertEqual(pairing.repairs[0]["candidates"], 3)
        # The count stands: the rows keep the receipt they had.
        self.assertEqual([row.seq for row in pairing.chunks[0].rows], [5, 6])
        self.assertEqual(pairing.paired_rows + pairing.unpaired_rows,
                         len(rows))

    def test_a_journal_line_padded_with_nul_is_recovered_and_counted(self):
        # A damaged line can arrive with its beginning overwritten by NULs
        # and the JSON object behind it still intact.  The object is the
        # record the host wrote, so it is used - and the damage is counted,
        # because a reader must see that the journal was damaged.
        entries = [
            (1, 1000, "row-one-aaaa"),
            (2, 1000, "row-two-bbbb"),
        ]
        rows = clock_bound.parse_rows(_rows(entries))
        line = json.dumps({"ts": "2026-10-15T10:00:00+00:00",
                           "node": "redmi10", "kind": "observations",
                           "records": 2,
                           "byte_count": sum(clock_bound.post_body_bytes(row)
                                             for row in rows)})
        damaged = "\x00" * 40 + line
        receipts = clock_bound.parse_receipts(damaged + "\n")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].records, 2)
        self.assertEqual(clock_bound.count_padded_receipt_lines(damaged + "\n"), 1)
        # An undamaged line is not counted as damaged.
        self.assertEqual(clock_bound.count_padded_receipt_lines(line + "\n"), 0)

    def test_the_byte_count_is_read_only_when_it_is_an_integer(self):
        # A journal that carries something else in the field carries no
        # witness, and the instrument must not treat it as one.
        for value in (True, "120", 1.5, -1, None):
            line = json.dumps({"ts": "2026-10-15T10:00:00+00:00",
                               "node": "redmi10", "kind": "observations",
                               "records": 1, "byte_count": value})
            receipts = clock_bound.parse_receipts(line + "\n")
            self.assertIsNone(receipts[0].byte_count, value)

    def test_a_receipt_without_a_witness_still_pairs_by_count(self):
        # Not every host writes a byte count.  The count is then all there
        # is, and the pairing must work exactly as it did before.
        rows = clock_bound.parse_rows(_rows([
            (1, 1000, "probe identity uid=1"),
            (2, 1000, "probe identity uid=1"),
            (3, 1005, _light(3, 1005, 5000)[2]),
        ]))
        line = json.dumps({"ts": "2026-10-15T10:00:00+00:00",
                           "node": "redmi10", "kind": "observations",
                           "records": 1})
        receipts = clock_bound.parse_receipts(line + "\n")
        self.assertIsNone(receipts[0].byte_count)
        pairing = clock_bound.pair_lane(rows, receipts)
        self.assertEqual(pairing.repairs, ())
        self.assertEqual([row.seq for row in pairing.chunks[0].rows], [3])

    def test_the_witness_verdict_reaches_the_published_reading(self):
        # A repair that is computed and thrown away is a repair nobody can
        # see: the reader would publish ages derived from a corrected pairing
        # without knowing the pairing had been corrected.  The verdict and
        # every repair therefore travel with the measurement.
        rows = clock_bound.parse_rows(_rows([
            (seq, 1000, "x" * seq) for seq in range(1, 10)
        ]))
        receipt = clock_bound.Receipt(1005.1, 4, "redmi10", "observations",
                                      sum(clock_bound.post_body_bytes(row)
                                          for row in rows[4:8]))
        pairing = clock_bound.pair_lane(rows, [receipt])
        witness = clock_bound.pairing_witness(pairing)
        self.assertEqual(witness["status"], "verified")
        self.assertTrue(witness["repaired"])
        self.assertEqual(witness["unpaired_rows"], 5)
        self.assertEqual(len(witness["repairs"]), 1)
        self.assertEqual(witness["repairs"][0]["resolution"], "realigned")

    def test_the_measurement_publishes_the_witness_and_its_repairs(self):
        rows = clock_bound.parse_rows(_rows([
            (seq, 1000, "x" * seq) for seq in range(1, 10)
        ]))
        receipt = clock_bound.Receipt(1005.1, 4, "redmi10", "observations",
                                      sum(clock_bound.post_body_bytes(row)
                                          for row in rows[4:8]))
        measurement = clock_bound.measure(rows, [receipt])
        witness = measurement["pairing_witness"]
        self.assertEqual(witness["status"], "verified")
        self.assertTrue(witness["repaired"])
        self.assertEqual(len(witness["repairs"]), 1)
        self.assertEqual(witness["repairs"][0]["first_seq"], 5)
        self.assertEqual(witness["repairs"][0]["last_seq"], 8)
        # The repair is published, so a reader can tell a repaired history
        # from a clean one.
        summary = clock_bound.render_summary("redmi10", measurement)
        self.assertIn("pairing witness: verified", summary)
        self.assertIn("repair: realigned", summary)

    def test_a_measurement_with_no_pairing_says_it_was_unchecked(self):
        # A reading that nobody checked must not look like a reading that
        # was checked and found sound.
        rows = clock_bound.parse_rows(_rows([
            (1, 1000, "probe identity uid=1"),
            (2, 1005, _light(2, 1005, 5000)[2]),
        ]))
        receipts = clock_bound.parse_receipts(_receipts([(1005.2, 1)], rows))
        chunks, unpaired = clock_bound.pair_chunks(rows, receipts)
        measurement = clock_bound.measure_chunks(chunks, unpaired)
        self.assertEqual(measurement["pairing_witness"]["status"], "unchecked")
        self.assertFalse(measurement["pairing_witness"]["repaired"])
        self.assertEqual(measurement["pairing_witness"]["repairs"], [])

    def test_the_history_profile_carries_the_witness_too(self):
        # The history profile is what the reader publishes about the delivered
        # history, and its ages descend from the pairing, so the pairing's
        # state has to travel with it.
        rows = clock_bound.parse_rows(_rows([
            (seq, 1000, "x" * seq) for seq in range(1, 10)
        ]))
        receipt = clock_bound.Receipt(1005.1, 4, "redmi10", "observations",
                                      sum(clock_bound.post_body_bytes(row)
                                          for row in rows[4:8]))
        pairing = clock_bound.pair_lane(rows, [receipt])
        profile = clock_bound.age_profile(pairing.chunks, 2000.0,
                                          pairing.unpaired_rows,
                                          pairing=pairing)
        self.assertEqual(profile["pairing_witness"]["status"], "verified")
        self.assertTrue(profile["pairing_witness"]["repaired"])
        self.assertEqual(len(profile["pairing_witness"]["repairs"]), 1)

    def test_a_repair_never_double_owns_a_row(self):
        # A repair may not reach back over rows the receipt behind it already
        # owns.  A window that did would claim those rows twice, and the
        # orphan count would come out negative - the repair appearing to give
        # back more rows than the lost receipt took - so the history would be
        # longer than the queue and the reader would count rows twice.
        rows = clock_bound.parse_rows(_rows([
            (seq, 1000, "x" * seq) for seq in range(1, 10)
        ]))
        receipt = clock_bound.Receipt(1005.1, 2, "redmi10", "observations",
                                      sum(clock_bound.post_body_bytes(row)
                                          for row in rows[1:3]))
        pairing = clock_bound.pair_lane(rows, [receipt])
        owned = [row.seq for chunk in pairing.chunks for row in chunk.rows]
        self.assertEqual(len(owned), len(set(owned)))
        self.assertGreaterEqual(pairing.unpaired_rows, 0)
        self.assertEqual(pairing.paired_rows + pairing.unpaired_rows, len(rows))

    def test_the_history_length_is_the_queue_length_whatever_the_damage(self):
        # The rows a receipt owns and the rows no receipt owns must always add
        # up to the rows the queue holds.  A repair that moved rows without
        # counting them would leave the history short by exactly those rows,
        # with nothing in the reading to show for it.
        # A witness no window of rows has: the receipt cannot be placed.
        rows = clock_bound.parse_rows(_rows([
            (1, 1000, "aaaa"),
            (2, 1000, "bbbb"),
            (3, 1000, "cccc"),
        ]))
        cases = [(clock_bound.Receipt(1005.1, 2, "redmi10", "observations", 999999),
                  "unplaceable")]
        # A witness that belongs to rows the count-based walk did not pick:
        # the pairing is realigned and the rows in between are counted as
        # unowned.  The rows carry different lengths, so the witness names
        # one window rather than any of several.
        sized = clock_bound.parse_rows(_rows([
            (1, 1000, "aaa"),
            (2, 1000, "bbbb"),
            (3, 1000, "ccccc"),
        ]))
        cases.append((clock_bound.Receipt(
            1005.1, 2, "redmi10", "observations",
            sum(clock_bound.post_body_bytes(row) for row in sized[0:2])),
            "realigned"))
        for receipt, expected in cases:
            rows = sized if expected == "realigned" else rows
            pairing = clock_bound.pair_lane(rows, [receipt])
            self.assertEqual(pairing.paired_rows + pairing.unpaired_rows,
                             len(rows))
            self.assertGreaterEqual(pairing.unpaired_rows, 0)
            owned = [row.seq for chunk in pairing.chunks for row in chunk.rows]
            self.assertEqual(len(owned), len(set(owned)))
            self.assertEqual(pairing.repairs[0]["resolution"], expected)

    def test_a_contradiction_is_published_rather_than_hidden(self):
        # A receipt whose rows do not add up to the bytes it claims cannot be
        # placed.  The reading says so; it does not quietly keep a pairing it
        # has already proved wrong.
        rows = clock_bound.parse_rows(_rows([
            (1, 1000, "row-one-aaaa"),
            (2, 1000, "row-two-bbbb"),
            (3, 1000, "row-three-cccc"),
        ]))
        receipt = clock_bound.Receipt(1005.1, 3, "redmi10", "observations", 999999)
        measurement = clock_bound.measure(rows, [receipt])
        witness = measurement["pairing_witness"]
        self.assertEqual(witness["repairs"][0]["resolution"], "unplaceable")
        self.assertTrue(witness["repaired"])
        summary = clock_bound.render_summary("redmi10", measurement)
        self.assertIn("repair: unplaceable", summary)


if __name__ == "__main__":
    unittest.main()
