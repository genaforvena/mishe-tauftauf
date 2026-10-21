#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Tests for the deterministic personal multi-body reading.

The tool under test is the tracked research instrument, not a site-local copy:
``research/body/personal-multibody.py``.  It is loaded by path because it is a
research instrument, not an importable package module.

The continuity tests are controlled failures: each one builds a delivered
history with a known silence and asserts that the reading names the cause
rather than collapsing every silence into "no data".
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "research" / "body" / "personal-multibody.py"
SPEC = importlib.util.spec_from_file_location("personal_multibody", TOOL)
assert SPEC and SPEC.loader
personal_multibody = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(personal_multibody)


def _node(name: str, delivery_status: str, cursor_age: float, clock_status: str,
          offset: float = -1.0) -> dict:
    return {
        "node": name,
        "session": f"{name}-session",
        "transport": "reachable",
        "phone_status": "fresh-clock-conditional",
        "cursor": 100,
        "channel_delivery": {
            "status": delivery_status,
            "cursor_state": "advancing" if delivery_status == "pass" else "frozen",
            "cursor_age_seconds": cursor_age,
            "reason": None if delivery_status == "pass" else "cursor-freeze",
            "node_mode": "UNKNOWN",
        },
        "clock_domain": {
            "status": clock_status,
            "offset_nominal_s": offset if clock_status == "ok" else None,
            "offset_min_s": offset - 0.5 if clock_status == "ok" else None,
            "offset_max_s": offset + 0.5 if clock_status == "ok" else None,
            "samples": 3 if clock_status == "ok" else 0,
        },
        "stillness": {"status": "unknown/no-available-input", "unavailable_inputs": ["wifi_link_rssi_dbm"]},
        "capabilities": {"accelerometer": "present", "light": "present", "battery": "present"},
        "owner_checkboxes": {},
    }


def _history(now: float, *, samples: int = 8, step: float = 5.0) -> list[dict]:
    rows: list[dict] = []
    for index in range(samples):
        collected = now - (samples - index) * step
        rows.append({
            "sequence": 1000 + index,
            "kind": "acceleration",
            "collected": collected,
            "received": collected + 0.5,
            "fields": {"x": "0.0", "y": "0.0", "z": "9.80665", "accuracy": "3"},
        })
        rows.append({
            "sequence": 2000 + index,
            "kind": "light",
            "collected": collected,
            "received": collected + 0.5,
            "fields": {"lux": "0.0", "accuracy": "3"},
        })
        rows.append({
            "sequence": 3000 + index,
            "kind": "battery",
            "collected": collected,
            "received": collected + 0.5,
            "fields": {"level": "0.41", "charging": "false"},
        })
    return rows


def _space(directory: Path, nodes: list[dict], histories: dict[str, list[dict]]) -> Path:
    body = directory / "body"
    (body / "perception").mkdir(parents=True, exist_ok=True)
    (body / "space.json").write_text(json.dumps(
        {"generated_at_utc": "2026-10-20T23:00:00+00:00", "nodes": nodes}), encoding="utf-8")
    for name, rows in histories.items():
        (body / "perception" / f"{name}.json").write_text(json.dumps(
            {"node": name, "cursor": 9999, "history": rows}), encoding="utf-8")
    return directory


def _queue(directory: Path, node: str, rows: list[tuple[int, int, str]],
           extra_lines: list[str] | None = None) -> None:
    """Write a delivered observation queue: ``SEQ <phone clock> <kind> ...``."""
    queues = directory / "body" / "queues"
    queues.mkdir(parents=True, exist_ok=True)
    lines = [f"{seq} {epoch} {kind} value=1" for seq, epoch, kind in rows]
    lines += extra_lines or []
    (queues / f"{node}.observations.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def _lane(directory: Path, node: str, rows: list[tuple[int, int, int]],
          posts: list[tuple[float, int]]) -> None:
    """Write a delivered lane that carries the phone's own uptime readings.

    ``rows`` are ``(sequence, device epoch, uptime in ms)``.  The uptime
    reading is what lets the instrument account for the phone's own clock
    movement, so a lane without it cannot carry a session-wide bound.
    """
    queues = directory / "body" / "queues"
    queues.mkdir(parents=True, exist_ok=True)
    lines = []
    for sequence, epoch, elapsed in rows:
        if elapsed is None:
            lines.append(f"{sequence} {epoch} hello node={node} protocol=1 "
                         f"session=aaaa-bbbb-cccc")
        else:
            lines.append(f"{sequence} {epoch} light lux=1.0 accuracy=3 "
                         f"age_ms=10 phone_elapsed_ms={elapsed}")
    (queues / f"{node}.observations.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    _journal(directory, node, posts)


def _journal(directory: Path, node: str, posts: list[tuple[float, int]]) -> None:
    queues = directory / "body" / "queues"
    queues.mkdir(parents=True, exist_ok=True)
    entries = []
    for moment, records in posts:
        entries.append({
            "ts": datetime.fromtimestamp(moment, timezone.utc).isoformat(),
            "node": node,
            "kind": "observations",
            "records": records,
        })
    (queues / "writes.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


class MultiBodyGateTests(unittest.TestCase):
    def test_stale_body_blocks_cross_body_fusion(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(
                root,
                [_node("redmi10", "pass", 2.0, "absent"),
                 _node("note3", "fail", 442_000.0, "ok")],
                {"redmi10": _history(now),
                 "note3": _history(now - 400_000.0)},
            )
            report = personal_multibody.build_report(space, 300.0, now)
            fusion = report["cross_body_fusion"]
            self.assertEqual(fusion["status"], "UNKNOWN")
            self.assertEqual(fusion["current_nodes"], ["redmi10"])
            self.assertEqual(fusion["stale_nodes"], ["note3"])

    def test_all_current_but_unmeasured_clock_keeps_fusion_unknown(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(
                root,
                [_node("redmi10", "pass", 2.0, "absent"),
                 _node("note3", "pass", 2.0, "ok")],
                {"redmi10": _history(now), "note3": _history(now)},
            )
            report = personal_multibody.build_report(space, 300.0, now)
            fusion = report["cross_body_fusion"]
            self.assertEqual(fusion["status"], "UNKNOWN")
            self.assertIn("redmi10", fusion["unclocked_nodes"])

    def test_all_current_and_clocked_supports_fusion(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(
                root,
                [_node("redmi10", "pass", 2.0, "ok"), _node("note3", "pass", 2.0, "ok")],
                {"redmi10": _history(now), "note3": _history(now)},
            )
            report = personal_multibody.build_report(space, 300.0, now)
            self.assertEqual(report["cross_body_fusion"]["status"], "supported")

    def test_slow_sampling_blocks_a_motion_label(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now, samples=8, step=5.0)})
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            rate = reading["measurements"]["acceleration"]["sample_rate_hz"]
            self.assertLess(rate, personal_multibody.GAIT_RESOLUTION_HZ)
            self.assertTrue(any("gait" in unknown for unknown in reading["unknowns"]))
            self.assertFalse(any("activity" in claim for claim in reading["claims_supported"]))

    def test_stale_receipt_means_no_current_window(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now - 10_000.0)})
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            self.assertFalse(reading["current_window"])
            self.assertTrue(any("no current delivered window" in u for u in reading["unknowns"]))

    def test_absent_clock_domain_never_reads_as_zero_offset(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "absent")],
                          {"redmi10": _history(now)})
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            self.assertEqual(reading["clock_status"], "absent")
            self.assertIsNone(reading["clock_offset_nominal_s"])
            self.assertTrue(any("absolute phone time is UNKNOWN" in u for u in reading["unknowns"]))

    def test_unreadable_space_reads_unknown_not_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = personal_multibody.build_report(Path(tmp), 300.0, 1_800_000_000.0)
            self.assertEqual(report["status"], "UNKNOWN")

    def test_magnitude_statistics_are_computed_from_axes(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now)})
            report = personal_multibody.build_report(space, 300.0, now)
            accel = report["nodes"][0]["measurements"]["acceleration"]
            self.assertEqual(accel["count"], 8)
            self.assertAlmostEqual(accel["magnitude_mean"], 9.80665, places=4)
            self.assertAlmostEqual(accel["magnitude_std"], 0.0, places=6)


class ChannelAvailabilityTests(unittest.TestCase):
    """A channel that says why it has no value is not a channel at rest."""

    def _stale_window(self, now: float) -> list[dict]:
        rows: list[dict] = []
        for index in range(4):
            collected = now - (4 - index) * 5.0
            rows.append({
                "sequence": 1000 + index,
                "kind": "acceleration",
                "collected": collected,
                "received": collected + 0.5,
                "fields": {"reason": "stale"},
            })
            rows.append({
                "sequence": 2000 + index,
                "kind": "light",
                "collected": collected,
                "received": collected + 0.5,
                "fields": {"lux": "20.0", "accuracy": "3"},
            })
        return rows

    def test_unavailable_channel_is_never_a_quiet_channel(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": self._stale_window(now)})
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            accel = reading["channel_availability"]["acceleration"]
            self.assertEqual(accel["status"], "unavailable")
            self.assertEqual(accel["reasons"], ["stale"])
            self.assertTrue(any("acceleration channel is unavailable" in unknown
                                and "stale" in unknown
                                for unknown in reading["unknowns"]))
            self.assertTrue(any("not a quiet channel" in unknown
                                for unknown in reading["unknowns"]))

    def test_a_valued_channel_is_not_reported_unavailable(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now)})
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            self.assertEqual(
                reading["channel_availability"]["acceleration"]["status"], "valued")
            self.assertFalse(any("acceleration channel is unavailable" in unknown
                                 for unknown in reading["unknowns"]))

    def test_clock_document_without_a_measurement_names_its_reason(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "absent")],
                          {"redmi10": _history(now)})
            (root / "body" / "clock-domain-redmi10.json").write_text(json.dumps({
                "node": "redmi10",
                "status": "unavailable",
                "reason": "no adb path to the redmi10 from this host",
                "samples": 0,
                "offset_nominal_s": None,
            }), encoding="utf-8")
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            self.assertEqual(reading["clock_document"]["status"], "unavailable")
            self.assertEqual(reading["clock_reason"], "no adb path to the redmi10 from this host")
            self.assertIsNone(reading["clock_offset_nominal_s"])
            self.assertTrue(any("no adb path to the redmi10" in unknown
                                for unknown in reading["unknowns"]))


class EventTransitionTests(unittest.TestCase):
    """The dated transitions are what make a reading longitudinal."""

    def _events(self, root: Path, text: str) -> None:
        (root / "body" / "perception" / "events.txt").write_text(text, encoding="utf-8")

    def test_channel_state_change_is_dated_and_reasoned(self) -> None:
        now = 1_800_000_000.0
        lines = (
            "  2026-10-21T03:24:13+00:00 redmi10 phone=fresh-clock-conditional "
            "transport=reachable acceleration=['fresh-clock-conditional', 9, None] "
            "light=['fresh-clock-conditional', 4, None]\n"
            "  2026-10-21T03:24:17+00:00 redmi10 phone=fresh-clock-conditional "
            "transport=reachable "
            "acceleration=['unavailable/fresh-clock-conditional', None, 'stale'] "
            "light=['fresh-clock-conditional', 4, None]\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now)})
            self._events(root, lines)
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            transitions = reading["transitions"]
            self.assertEqual(transitions["status"], "ok")
            changes = [t for t in transitions["transitions"]
                       if t.get("channel") == "acceleration"]
            self.assertTrue(changes)
            self.assertEqual(changes[-1]["at"], "2026-10-21T03:24:17+00:00")
            self.assertIn("stale", changes[-1]["to"])
            self.assertIn("value=9", changes[-1]["from"])

    def test_light_bucket_transition_is_reported(self) -> None:
        now = 1_800_000_000.0
        line = ("  measured-light-bucket-transition id=redmi10:session:155471 "
                "redmi10 source=http://127.0.0.1:8765/node/redmi10 session=session "
                "bucket=0->4; from=#155467 lux=0.0 phone_sample=2026-10-21T03:23:01+00:00 "
                "consumer_receipt=2026-10-21T03:23:02+00:00; to=#155471 lux=20.0 "
                "phone_sample=2026-10-21T03:23:06+00:00 "
                "consumer_receipt=2026-10-21T03:23:08+00:00 freshness=fresh-clock-conditional; "
                "historical-measurement-only\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now)})
            self._events(root, line)
            report = personal_multibody.build_report(space, 300.0, now)
            transitions = report["nodes"][0]["transitions"]["transitions"]
            buckets = [t for t in transitions if t.get("kind") == "light-bucket"]
            self.assertEqual(len(buckets), 1)
            self.assertEqual(buckets[0]["from"], "0.0")
            self.assertEqual(buckets[0]["to"], "20.0")
            self.assertEqual(buckets[0]["bucket"], "0->4")
            self.assertEqual(buckets[0]["at"], "2026-10-21T03:23:06+00:00")

    def test_unreadable_event_log_yields_no_transition(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now)})
            report = personal_multibody.build_report(space, 300.0, now)
            transitions = report["nodes"][0]["transitions"]
            self.assertEqual(transitions["status"], "unavailable")
            self.assertEqual(transitions["transitions"], [])

    def test_malformed_event_lines_claim_nothing(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")],
                          {"redmi10": _history(now)})
            self._events(root, "not a state line\n2026-10-21T03:24:17+00:00 redmi10 acceleration=[\n")
            report = personal_multibody.build_report(space, 300.0, now)
            transitions = report["nodes"][0]["transitions"]
            self.assertEqual(transitions["status"], "unavailable")


class ContinuityAttributionTests(unittest.TestCase):
    """A silence is attributed to the body or to the link, never left bare."""

    def _reading(self, root: Path, now: float) -> dict:
        space = _space(root, [_node("redmi10", "pass", 2.0, "absent")],
                       {"redmi10": _history(now)})
        report = personal_multibody.build_report(space, 300.0, now)
        return report["nodes"][0]

    def test_body_silence_under_a_live_link_is_body_side(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"), (3, 1010, "light"),
                    (4, 2010, "light"), (5, 2015, "light")]
            _queue(root, "redmi10", rows)
            posts = [(1000.0 + 100.0 * step, 3) for step in range(11)]
            posts.append((2010.0, 2))
            _journal(root, "redmi10", posts)
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["status"], "ok")
            self.assertEqual(len(continuity["body_holes"]), 1)
            hole = continuity["body_holes"][0]
            self.assertEqual(hole["duration_s"], 1000.0)
            self.assertLess(hole["link_silent_share"], 0.5)
            self.assertIn("body-side", hole["cause"])

    def test_silence_in_both_is_reported_as_not_separable(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"),
                    (3, 2010, "light"), (4, 2015, "light")]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 2), (2010.0, 2)])
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(len(continuity["body_holes"]), 1)
            hole = continuity["body_holes"][0]
            self.assertGreaterEqual(hole["link_silent_share"], 0.5)
            self.assertIn("not separable", hole["cause"])

    def test_a_single_boundary_post_does_not_make_a_long_silence_look_live(self) -> None:
        """One delivery at the edge of a silence is not a live link."""
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"),
                    (3, 10_000, "light"), (4, 10_005, "light")]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 2), (1006.0, 1), (10_000.0, 2)])
            continuity = self._reading(root, now)["continuity"]
            hole = continuity["body_holes"][0]
            self.assertGreaterEqual(hole["link_silent_share"], 0.5)
            self.assertIn("not separable", hole["cause"])

    def test_link_silence_with_samples_inside_is_link_side(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(index + 1, 1000 + index * 5, "light") for index in range(400)]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 4), (2995.0, 4)])
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["body_holes"], [])
            self.assertEqual(len(continuity["link_holes"]), 1)
            hole = continuity["link_holes"][0]
            self.assertGreater(hole["samples_inside"], 0)
            self.assertIn("link-side", hole["cause"])

    def test_link_silence_with_a_faltering_body_is_not_called_link_side(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"),
                    (3, 2000, "light"), (4, 2005, "light")]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 2), (2000.0, 2)])
            continuity = self._reading(root, now)["continuity"]
            hole = continuity["link_holes"][0]
            self.assertLess(hole["samples_inside"], hole["expected_samples"])
            self.assertTrue(hole["cause"].startswith("partial"),
                            hole["cause"])

    def test_an_unmeasured_link_is_not_reported_as_a_live_link(self) -> None:
        """No host posts for a node means the link's state is unknown."""
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"),
                    (3, 2010, "light"), (4, 2015, "light")]
            _queue(root, "redmi10", rows)
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["coverage"]["link_state"], "unknown")
            self.assertIsNone(continuity["coverage"]["live_delivery_coverage"])
            hole = continuity["body_holes"][0]
            self.assertIsNone(hole["link_silent_share"])
            self.assertIn("UNKNOWN", hole["cause"])
            self.assertNotIn("the host kept receiving", hole["cause"])

    def test_coverage_separates_what_the_body_produced_from_what_was_seen_live(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(index + 1, 1000 + index * 5, "light") for index in range(400)]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 4), (2995.0, 4)])
            coverage = self._reading(root, now)["continuity"]["coverage"]
            self.assertEqual(coverage["observation_coverage"], 1.0)
            self.assertLess(coverage["live_delivery_coverage"], 0.1)
            self.assertGreater(coverage["link_silent_s"], 0.0)

    def test_malformed_queue_rows_are_kept_as_evidence(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light")]
            _queue(root, "redmi10", rows, extra_lines=["invalid line here"])
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["queue"]["malformed_rows"], 1)
            self.assertEqual(continuity["queue"]["malformed_samples"], ["invalid line here"])

    def test_missing_queue_reads_continuity_unknown_not_an_empty_history(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reading = self._reading(root, now)
            self.assertEqual(reading["continuity"]["status"], "unavailable")
            self.assertTrue(any("observation continuity is UNKNOWN" in unknown
                                for unknown in reading["unknowns"]))

    def test_phone_clock_offset_is_never_derived_from_the_delivered_lane(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reading = self._reading(root, now)
            identifiability = reading["clock_identifiability"]
            self.assertEqual(identifiability["status"],
                             "not-identifiable-from-the-delivered-lane")
            self.assertIn("transport delay", identifiability["reason"])
            self.assertTrue(any("clock offset is not recoverable" in unknown
                                for unknown in reading["unknowns"]))

    def test_a_measured_offset_decomposes_the_lag_into_a_transport_delay(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = _history(now)
            for row in rows:
                row["received"] = row["collected"] + 5.0
            space = _space(root, [_node("note3", "pass", 2.0, "ok")],
                           {"note3": rows})
            report = personal_multibody.build_report(space, 300.0, now)
            identifiability = report["nodes"][0]["clock_identifiability"]
            self.assertEqual(identifiability["status"],
                             "decomposed-with-the-measured-offset")
            # the fixture's lag is 5.0 s and the node's offset is -1.0 s
            self.assertAlmostEqual(identifiability["transport_delay_s"]["median_s"],
                                   4.0, places=6)

    def test_an_impossible_transport_delay_is_reported_as_inconsistent(self) -> None:
        """A negative transport delay is evidence, not a measurement."""
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = _space(root, [_node("note3", "pass", 2.0, "ok")],
                           {"note3": _history(now)})
            report = personal_multibody.build_report(space, 300.0, now)
            identifiability = report["nodes"][0]["clock_identifiability"]
            # the fixture's lag is 0.5 s and the node's offset is -1.0 s
            self.assertEqual(identifiability["status"],
                             "inconsistent-with-the-measured-offset")
            self.assertLess(identifiability["transport_delay_s"]["median_s"], 0.0)

    def test_cadence_is_measured_from_the_busiest_channel(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"), (3, 1010, "light"),
                    (4, 1010, "battery"), (5, 1015, "light")]
            _queue(root, "redmi10", rows)
            cadence = self._reading(root, now)["continuity"]["cadence"]
            self.assertEqual(cadence["channel"], "light")
            self.assertEqual(cadence["interval_s"], 5.0)


class BoundaryTests(unittest.TestCase):
    """The boundaries where an over-confident reading would hide."""

    def _reading(self, root: Path, now: float, node: dict | None = None) -> dict:
        space = _space(root, [node or _node("redmi10", "pass", 2.0, "absent")],
                       {"redmi10": _history(now)})
        report = personal_multibody.build_report(space, 300.0, now)
        return report["nodes"][0]

    def test_a_single_host_post_leaves_the_link_state_unknown(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"),
                    (3, 10_000, "light"), (4, 10_005, "light")]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 2)])
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["coverage"]["link_state"], "unknown")
            self.assertIsNone(continuity["coverage"]["live_delivery_coverage"])
            self.assertIsNone(continuity["body_holes"][0]["link_silent_share"])

    def test_a_missing_queue_row_refuses_continuity_instead_of_guessing(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"),
                    (7, 10_000, "light"), (8, 10_005, "light")]
            _queue(root, "redmi10", rows)
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["status"], "unavailable")
            self.assertIn("not contiguous", continuity["reason"])

    def test_an_ordinary_sampling_interval_is_not_a_hole(self) -> None:
        """A cadence slower than the threshold is still a cadence, not a hole."""
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1100, "light"), (3, 1200, "light")]
            _queue(root, "redmi10", rows)
            continuity = self._reading(root, now,
                                       node=_node("redmi10", "pass", 2.0, "absent"))["continuity"]
            self.assertEqual(continuity["cadence"]["interval_s"], 100.0)
            self.assertEqual(continuity["body_holes"], [])

    def test_the_measured_offset_is_applied_before_the_two_clocks_are_compared(self) -> None:
        """The phone clock runs 100 s ahead; the hole still meets the silence."""
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1100, "light"),
                    (3, 2100, "light"), (4, 2200, "light")]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 2), (1006.0, 2), (2100.0, 2)])
            node = _node("redmi10", "pass", 2.0, "ok", offset=100.0)
            continuity = self._reading(root, now, node=node)["continuity"]
            self.assertEqual(continuity["alignment"]["status"], "measured")
            hole = continuity["body_holes"][0]
            # the hole's phone reading is converted to the host's timeline
            self.assertIsNotNone(hole["start_utc"])
            # the phone reads 1100 where the host reads 1000
            self.assertEqual(hole["start_phone"], "1970-01-01T00:18:20+00:00")
            self.assertEqual(hole["start_utc"], "1970-01-01T00:16:40+00:00")
            self.assertGreater(hole["link_silent_share"], 0.5)
            self.assertIn("not separable", hole["cause"])

    def test_an_unmeasured_clock_names_the_difference_that_would_overturn_it(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light"),
                    (3, 10_000, "light"), (4, 10_005, "light")]
            _queue(root, "redmi10", rows)
            _journal(root, "redmi10", [(1000.0, 2), (1006.0, 2), (10_000.0, 2)])
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["alignment"]["status"], "unverified")
            self.assertIsNone(continuity["body_holes"][0]["start_utc"])
            self.assertIn("verdict_holds_unless_clock_differs_by_s",
                          continuity["body_holes"][0])

    def test_every_malformed_row_is_counted_even_when_the_samples_are_capped(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light")]
            _queue(root, "redmi10", rows,
                   extra_lines=["broken %d" % index for index in range(10)])
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["queue"]["malformed_rows"], 10)
            self.assertEqual(len(continuity["queue"]["malformed_samples"]),
                             personal_multibody.MALFORMED_SAMPLES_KEPT)

    def test_an_all_malformed_queue_keeps_its_evidence(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _queue(root, "redmi10", [], extra_lines=["broken %d" % index
                                                     for index in range(4)])
            continuity = self._reading(root, now)["continuity"]
            self.assertEqual(continuity["status"], "unavailable")
            self.assertEqual(continuity["queue"]["malformed_rows"], 4)
            self.assertTrue(continuity["queue"]["malformed_samples"])

    def test_the_clock_anchor_is_present_without_a_perception_state_file(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(1, 1000, "light"), (2, 1005, "light")]
            _queue(root, "redmi10", rows)
            space = _space(root, [_node("redmi10", "pass", 2.0, "ok")], {})
            report = personal_multibody.build_report(space, 300.0, now)
            reading = report["nodes"][0]
            self.assertEqual(reading["continuity"]["clock_anchor"]["status"],
                             "anchored")

    def test_sub_threshold_gaps_still_count_against_the_observation_coverage(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [(index + 1, 1000 + index * 5, "light") for index in range(20)]
            rows.append((21, 1000 + 20 * 5 + 100, "light"))
            _queue(root, "redmi10", rows)
            continuity = self._reading(root, now)["continuity"]
            # a 100 s gap is below the hole threshold but is still missing time
            self.assertEqual(continuity["body_holes"], [])
            self.assertLess(continuity["coverage"]["observation_coverage"], 1.0)


class ClockBoundTests(unittest.TestCase):
    """What the delivered lane can say about a phone's clock, and no more.

    A node with no measured clock-domain document used to be reported as
    "absolute phone time is UNKNOWN" and nothing else.  The delivered lane
    does bound the phone's clock from above, and the reading has to say so
    with the side that stays unresolved named.
    """

    def _reading(self, root: Path, now: float) -> dict:
        space = _space(root, [_node("redmi10", "fail", 100.0, "absent")],
                       {"redmi10": _history(now)})
        report = personal_multibody.build_report(space, 300.0, now)
        return report["nodes"][0]

    def test_the_delivered_lane_bounds_the_phone_clock_from_above(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lane(root, "redmi10",
                  [(1, 1000, None), (2, 1005, 5000), (3, 1010, 10000)],
                  [(1005.05, 2), (1010.1, 1)])
            reading = self._reading(root, now)
            bound = reading["clock_bound"]
            self.assertEqual(bound["status"], "bounded-from-the-delivered-lane")
            self.assertIsNotNone(bound["behind_upper_s"])
            self.assertIsNone(bound["behind_lower_s"])
            self.assertEqual(bound["session"], "aaaa-bbbb-cccc")
            self.assertEqual(bound["rows"], 3)
            self.assertTrue(bound["premise"])

    def test_the_bound_is_named_in_the_claim_and_the_unresolved_side_in_the_unknown(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lane(root, "redmi10",
                  [(1, 1000, None), (2, 1005, 5000), (3, 1010, 10000)],
                  [(1005.05, 2), (1010.1, 1)])
            reading = self._reading(root, now)
            claims = " ".join(reading["claims_supported"])
            unknowns = " ".join(reading["unknowns"])
            self.assertIn("behind", claims)
            self.assertIn("aaaa-bbbb-cccc", claims)
            self.assertIn("AHEAD", unknowns)
            self.assertIn("absolute phone time is UNKNOWN", unknowns)

    def test_without_a_delivered_lane_no_bound_is_claimed(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reading = self._reading(root, now)
            bound = reading["clock_bound"]
            self.assertEqual(bound["status"], "unavailable")
            self.assertTrue(bound["reason"])
            self.assertNotIn("behind", " ".join(reading["claims_supported"]))

    def test_a_reset_withholds_the_session_wide_bound_and_says_why(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The phone's uptime counter falls onto a fresh-boot value: a
            # reset and a reverse-order batch look alike here, so no
            # session-wide bound is published and the reading says why.
            _lane(root, "redmi10",
                  [(1, 1000, None), (2, 1005, 100_000), (3, 905, 5_000),
                   (4, 1005, 105_000)],
                  [(1005.1, 4)])
            reading = self._reading(root, now)
            bound = reading["clock_bound"]
            self.assertIsNone(bound["behind_upper_s"])
            self.assertIsNotNone(bound["behind_upper_at_last_sample_s"])
            self.assertTrue(bound["ambiguous_resets"])
            self.assertTrue(bound["session_wide_withheld_reason"])
            unknowns = " ".join(reading["unknowns"])
            self.assertIn("no session-wide clock bound", unknowns)

    def test_the_bound_is_never_a_two_sided_interval(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lane(root, "redmi10",
                  [(1, 1000, None), (2, 1005, 5000), (3, 1010, 10000)],
                  [(1005.05, 2), (1010.1, 1)])
            reading = self._reading(root, now)
            self.assertIsNone(reading["clock_bound"]["behind_lower_s"])
            self.assertIsNone(reading["clock_offset_nominal_s"])
            self.assertEqual(reading["clock_offset_bounds_s"], [None, None])


if __name__ == "__main__":
    unittest.main()
