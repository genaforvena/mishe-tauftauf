from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mishe_tauftauf import body_perception, seed_culture_views
from mishe_tauftauf.seed import _observation_text


SOURCE = "http://127.0.0.1:8765/node/redmi10"
SESSION = "7afa3a15-50e1-4a1b-b32d-d0c46c3a11b7"


def _row(kind: str, sequence: int, now: float, fields: dict[str, str], *,
         age: float = 1.0, sample_age: float = 3.0) -> dict:
    return {
        "kind": kind,
        "sequence": sequence,
        "collected": now - sample_age,
        "received": now - age,
        "fields": fields,
        "backlog_page_pending_at_receipt": False,
    }


def _linked_state(tmp_path: Path, now: float) -> tuple[Path, Path, dict]:
    core = tmp_path / "core"
    android = tmp_path / "android-body"
    (core / "health").mkdir(parents=True)
    (core / "health" / "linked-sites.json").write_text(json.dumps({
        "version": 1,
        "sites": [{"home": str(android), "session": "android-body", "sha": "a" * 40}],
    }), encoding="utf-8")
    state = {
        "version": 1,
        "node": "redmi10",
        "source": SOURCE,
        "cursor": 3,
        "session": SESSION,
        "latest": {
            "battery": _row("battery", 1, now, {"level": "0.98", "charging": "true"},
                            age=2, sample_age=5),
            "light": _row("light", 2, now, {"lux": "101", "age_ms": "20"},
                          age=1.5, sample_age=4),
            "acceleration": _row("acceleration", 3, now, {
                "x": "3", "y": "4", "z": "12", "age_ms": "10",
            }),
            "wifi_link_rssi_dbm": _row("wifi_link_rssi_dbm", 4, now, {"reason": "disconnected"}),
            "ble_scan": _row("ble_scan", 5, now - 1000, {"reason": "SecurityException"}, age=1000),
        },
        "poll_at": now - 1,
        "has_more": False,
        "error": None,
    }
    state_path = android / "body" / "perception" / "redmi10.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return core, state_path, state


def test_redmi_reader_exposes_fresh_samples_with_source_and_uncertainty(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, _path, _state = _linked_state(tmp_path, now)

    row = body_perception.read_redmi10(core, now)

    assert row["state"] == "verified"
    assert row["id"] == "sense.body.redmi10"
    assert row["source"] == SOURCE
    assert row["cursor"] == 3
    assert row["session"] == SESSION
    assert row["acceleration_phone_sample_epoch_s"] == now - 3
    assert row["acceleration_consumer_receipt_epoch_s"] == now - 1
    assert row["channel_provenance"]["battery"] == {
        "sequence": 1, "phone_sample_epoch_s": now - 5,
        "consumer_receipt_epoch_s": now - 2,
    }
    assert row["channel_provenance"]["light"] == {
        "sequence": 2, "phone_sample_epoch_s": now - 4,
        "consumer_receipt_epoch_s": now - 1.5,
    }
    assert row["channel_provenance"]["acceleration"]["sequence"] == 3
    assert "b=0.98+" in row["sample"]
    assert "L=101lux" in row["sample"]
    assert "|a|=13.00m/s² incl-g" in row["sample"]
    assert row["channels"] == {
        "battery": "fresh-clock-conditional",
        "light": "fresh-clock-conditional",
        "acceleration": "fresh-clock-conditional",
    }
    assert f"source={SOURCE} session={SESSION} cursor=3" in row["details"]
    assert "acceleration phone_sample=2027-01-15T07:59:57Z" in row["details"]
    assert "consumer_receipt=2027-01-15T07:59:59Z" in row["details"]
    assert "host-receipt freshness conditional" in row["details"]
    assert "Wi-Fi disconnected" in row["details"]
    assert "BLE last scan error SecurityException (stale)" in row["details"]
    assert "phone/host clock split unknown" in row["details"]
    assert "not person-movement evidence" in row["details"]


def test_cursor_progress_stays_visible_without_changing_observation_summary(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    state["latest"]["acceleration"]["received"] = now - 60
    state_path.write_text(json.dumps(state), encoding="utf-8")
    first = body_perception.read_redmi10(core, now)

    def frame(row):
        return (f"UNKNOWN {row['id']}: {row['sample']}\n"
                f"REDMI10 SOURCE/LIMITS: {row['details']}\n"
                "STATE: UNKNOWN — 1 source read unverified")
    first_observation = _observation_text("senses", frame(first))
    state["cursor"] = 4
    state_path.write_text(json.dumps(state), encoding="utf-8")
    advanced = body_perception.read_redmi10(core, now)

    assert advanced["state"] == "unknown"
    assert advanced["cursor"] == 4
    assert "cursor=4" in advanced["details"]
    assert advanced["sample"] == first["sample"]
    assert _observation_text("senses", frame(advanced)) == first_observation
    state["cursor"] = 5
    state["latest"]["light"]["sequence"] = 5
    state["latest"]["light"]["fields"]["lux"] = "102"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    in_band = body_perception.read_redmi10(core, now)
    assert in_band["sample"] != first["sample"]
    # Same-band ambient-light jitter (101→102lux) is presentation churn, not
    # a digest event: the cursor advance is normalized away with the light.
    assert _observation_text("senses", frame(in_band)) == first_observation

    state["latest"]["light"]["fields"]["lux"] = "130"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    changed_sensor = body_perception.read_redmi10(core, now)
    assert changed_sensor["sample"] != first["sample"]
    # A cross-band environmental transition must reach the digest.
    assert _observation_text("senses", frame(changed_sensor)) != first_observation

    state["session"] = "new-session"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    changed_session = body_perception.read_redmi10(core, now)
    assert changed_session["sample"] != changed_sensor["sample"]
    assert _observation_text("senses", frame(changed_session)) != \
        _observation_text("senses", frame(changed_sensor))


def test_senses_pane_refreshes_linked_redmi_without_waiting_for_a_scan(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    core, _path, _state = _linked_state(tmp_path, now)
    scan_id = "scan-fixture"
    scan_dir = core / "discovery"
    scan_dir.mkdir()
    (scan_dir / "latest.json").write_text(json.dumps({
        "created": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "scan_id": scan_id,
        "observations": [{"id": "sense.proc.loadavg", "kind": "read",
                          "state": "verified", "sample": "1.23"}],
    }), encoding="utf-8")
    (scan_dir / "attempt.json").write_text(json.dumps({
        "status": "succeeded", "stage": "notification", "scan_id": scan_id,
        "created": datetime.fromtimestamp(now, timezone.utc).isoformat(),
    }), encoding="utf-8")
    monkeypatch.setattr(seed_culture_views, "_scan_reading",
                        lambda _snapshot: ("recent", "consistent"))

    rendered = seed_culture_views.senses(core)

    assert "VERIFIED sense.body.redmi10: Redmi10 sid=7afa3a15" in rendered
    assert "REDMI10 SOURCE/LIMITS: source=" + SOURCE in rendered
    assert "Wi-Fi disconnected" in rendered
    assert "not person-movement evidence" in rendered
    assert "UNKNOWN sense.space.light" in rendered
    assert "STATE: UNKNOWN" in rendered


@pytest.mark.parametrize(("fault", "expected"), [
    ("missing-state", "UNKNOWN: consumer-state-missing"),
    ("malformed-state", "UNKNOWN: consumer-state-malformed"),
    ("stale-poll", "UNKNOWN: consumer-poll-stale"),
    ("stale-channel", "L=stale"),
    ("future-receipt", "b=unknown/clock-future"),
    ("backlog", "UNKNOWN: consumer-backlog-or-state-invalid"),
    ("missing-value", "b=unknown/value"),
    ("wrong-source", "UNKNOWN: consumer-state-invalid"),
])
def test_redmi_missing_or_untrustworthy_evidence_stays_unknown(
        tmp_path: Path, fault: str, expected: str) -> None:
    now = 1_800_000_000.0
    core, path, state = _linked_state(tmp_path, now)
    if fault == "missing-state":
        path.unlink()
    elif fault == "malformed-state":
        path.write_text("{", encoding="utf-8")
    elif fault == "stale-poll":
        state["poll_at"] = now - 31
        path.write_text(json.dumps(state), encoding="utf-8")
    elif fault == "stale-channel":
        state["latest"]["light"]["received"] = now - 31
        path.write_text(json.dumps(state), encoding="utf-8")
    elif fault == "future-receipt":
        state["latest"]["battery"]["received"] = now + 60
        path.write_text(json.dumps(state), encoding="utf-8")
    elif fault == "backlog":
        state["has_more"] = True
        path.write_text(json.dumps(state), encoding="utf-8")
    elif fault == "missing-value":
        del state["latest"]["battery"]["fields"]["level"]
        path.write_text(json.dumps(state), encoding="utf-8")
    elif fault == "wrong-source":
        state["source"] = "http://127.0.0.1:8765/node/note3"
        path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10(core, now)

    assert row["state"] == "unknown"
    assert expected in row["sample"]


def test_redmi_missing_link_registry_stays_unknown(tmp_path: Path) -> None:
    row = body_perception.read_redmi10(tmp_path / "core", now=1_800_000_000.0)

    assert row["state"] == "unknown"
    assert "linked-site-registry-missing" in row["reason"]


STABLE_TRANSITION = {
    "kind": "measured_light_bucket_transition",
    "bucket_from": 4,
    "bucket_to": 3,
    "claim": "historical-measured-light-change-only",
    "freshness_at_detection": "fresh-clock-conditional",
    "validity_seconds": 30,
    "from": {"sequence": 25455, "lux": 17.0},
    "to": {"sequence": 25459, "lux": 10.0},
}


def test_redmi_surfaces_measured_light_transition_as_adjacent_context(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, _path, state = _linked_state(tmp_path, now)
    state["last_light_transition"] = STABLE_TRANSITION
    (tmp_path / "android-body" / "body" / "perception" / "redmi10.json").write_text(
        json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10(core, now)

    assert row["light_transition"] == STABLE_TRANSITION
    assert "measured-light-transition=4->3 validity=30s freshness=fresh-clock-conditional;" in row["details"]
    assert "not person-movement evidence" in row["details"]
    assert row["state"] == "verified"


def test_redmi_does_not_surface_malformed_light_transition(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, _path, state = _linked_state(tmp_path, now)
    state["last_light_transition"] = {
        "kind": "measured_light_bucket_transition",
        "claim": "historical-measured-light-change-only",
    }
    (tmp_path / "android-body" / "body" / "perception" / "redmi10.json").write_text(
        json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10(core, now)

    assert row["light_transition"] is None
    assert "measured-light-transition=" not in row["details"]

def test_redmi_shows_motion_leg_absent_when_acceleration_stale(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, path, state = _linked_state(tmp_path, now)
    state["latest"]["acceleration"]["received"] = now - 31
    state["latest"]["acceleration"]["fields"]["reason"] = "stale"
    path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10(core, now)

    assert row["state"] == "unknown"
    assert "|a|=motion-leg-absent" in row["sample"]


def test_redmi_light_sense_stays_verified_when_acceleration_is_stale(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    state["latest"]["acceleration"]["received"] = now - 31
    state["latest"]["acceleration"]["fields"]["reason"] = "stale"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10_light(core, now)

    assert row["id"] == body_perception.LIGHT_SENSE_ID
    assert row["state"] == "verified"
    assert row["channels"] == {"light": "fresh-clock-conditional"}
    assert "|a|=motion-leg-absent" in row["sample"]
    assert "L=101lux" in row["sample"]


def test_redmi_light_sense_becomes_unknown_when_light_is_not_current(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    state["latest"]["light"]["received"] = now - 31
    state_path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10_light(core, now)

    assert row["id"] == body_perception.LIGHT_SENSE_ID
    assert row["state"] == "unknown"


def test_redmi_light_sense_preserves_measured_light_transition(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    state["last_light_transition"] = STABLE_TRANSITION
    state_path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10_light(core, now)

    assert row["light_transition"] == STABLE_TRANSITION


def _linked_perception(tmp_path: Path) -> tuple[Path, Path]:
    core = tmp_path / "core"
    android = tmp_path / "android-body"
    (core / "health").mkdir(parents=True)
    (core / "health" / "linked-sites.json").write_text(json.dumps({
        "version": 1,
        "sites": [{"home": str(android), "session": "android-body", "sha": "a" * 40}],
    }), encoding="utf-8")
    perception = android / "body" / "perception"
    perception.mkdir(parents=True)
    return core, perception


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _gap_line(detected: float, *, received: float, cursor: int = 10,
              host_stopped: object = False) -> str:
    return json.dumps({
        "kind": "class-c-delivery-gap", "detected_at": _stamp(detected), "cursor": cursor,
        "session": SESSION, "last_received_epoch_s": received, "window_seconds": 120.0,
        "reason": "delivery halted with polls succeeding; no code=2, no null-lux freeze",
        "host_stopped": host_stopped,
    })


def _excursion_line(span_min: float, span_max: float, *, node: str = "redmi10") -> str:
    return (f"excursion emitted={_stamp(1_700_000_000)} node={node} session={SESSION} "
            f"cursor_start=1 cursor_end=32 retained_count=32 window_truncated=true "
            f"phone_elapsed_ms=1,2 acquisition_span_ms={span_min},{span_max} "
            f"light_std=1.0 accel_guard=ok")


def test_delivery_gap_reader_counts_recent_halts_and_receipt_lag(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, perception = _linked_perception(tmp_path)
    # Newest first: the log is append-ordered in production, so the reader's own
    # ordering must be what makes the last record the most recent one.
    (perception / body_perception.GAP_LOG_NAME).write_text("\n".join([
        _gap_line(now - 600, received=now - 1000, cursor=200),
        _gap_line(now - 3600, received=now - 4000, cursor=100),
        _gap_line(now - 23 * 3600, received=now - 24 * 3600, cursor=75),
        _gap_line(now - 25 * 3600, received=now - 26 * 3600, cursor=60),
        _gap_line(now - 30 * 3600, received=now - 31 * 3600, cursor=50),
    ]) + "\n", encoding="utf-8")
    (perception / body_perception.EXCURSION_LOG_NAME).write_text("\n".join([
        _excursion_line(1000.0, 2000.0),
        # Another node's line is not this body's receipt lag, whatever it says.
        _excursion_line(500_000.0, 999_999.0, node="note3"),
        _excursion_line(5000.0, 9000.0),
    ]) + "\n", encoding="utf-8")

    row = body_perception.read_redmi10_delivery_gaps(core, now)

    assert row["id"] == body_perception.GAP_SENSE_ID
    assert row["state"] == "verified"
    assert row["reason"] == "gap-log-current"
    assert row["gaps_24h"] == 3
    assert row["gap_records"] == 5
    assert row["last_onset_epoch_s"] == now - 1000
    assert row["last_gap_cursor"] == 200
    assert row["host_stopped"] is False
    assert row["max_receipt_lag_s"] == 9.0
    expected_onset = datetime.fromtimestamp(now - 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert row["sample"] == (f"gaps-24h=3 last-onset={expected_onset} "
                             "host-stopped=false max-receipt-lag=9s")
    # A reading that is re-derived from the same source must not churn the
    # scan's change identity with a growing age.
    assert body_perception.read_redmi10_delivery_gaps(core, now)["sample"] == row["sample"]


def test_delivery_gap_reader_absent_log_is_unknown(tmp_path: Path) -> None:
    core, _perception = _linked_perception(tmp_path)

    row = body_perception.read_redmi10_delivery_gaps(core, 1_800_000_000.0)

    assert row["state"] == "unknown"
    assert row["reason"] == "gap-log-missing"
    assert "gap-log-missing" in row["sample"]


def test_delivery_gap_reader_empty_log_is_unknown(tmp_path: Path) -> None:
    core, perception = _linked_perception(tmp_path)
    (perception / body_perception.GAP_LOG_NAME).write_text("\n", encoding="utf-8")

    row = body_perception.read_redmi10_delivery_gaps(core, 1_800_000_000.0)

    assert row["state"] == "unknown"
    assert row["reason"] == "gap-log-empty"


def test_delivery_gap_reader_malformed_line_cannot_claim_a_count(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, perception = _linked_perception(tmp_path)
    (perception / body_perception.GAP_LOG_NAME).write_text("\n".join([
        _gap_line(now - 600, received=now - 1000),
        '{"kind": "class-c-delivery-gap", "detected_at": "2026-10-16T09:04:54+00:00"',
        _gap_line(now - 300, received=now - 700, cursor="77"),
        _gap_line(now - 300, received=now - 700, host_stopped="no"),
    ]) + "\n", encoding="utf-8")

    row = body_perception.read_redmi10_delivery_gaps(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "gap-log-malformed"
    assert row["malformed"] == 3


def test_delivery_gap_reader_oversized_log_is_unknown_not_a_prefix_count(
        tmp_path: Path) -> None:
    """A truncated read cannot be parsed as if it were the whole log."""
    now = 1_800_000_000.0
    core, perception = _linked_perception(tmp_path)
    filler = _gap_line(now - 600, received=now - 1000) + "\n"
    (perception / body_perception.GAP_LOG_NAME).write_text(
        filler * 2 + "x" * body_perception.MAX_GAP_LOG_BYTES, encoding="utf-8")

    row = body_perception.read_redmi10_delivery_gaps(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "gap-log-oversized"


def test_delivery_gap_reader_missing_excursion_log_keeps_the_halt_count(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, perception = _linked_perception(tmp_path)
    (perception / body_perception.GAP_LOG_NAME).write_text(
        _gap_line(now - 600, received=now - 1000) + "\n", encoding="utf-8")

    row = body_perception.read_redmi10_delivery_gaps(core, now)

    assert row["state"] == "verified"
    assert row["gaps_24h"] == 1
    assert row["max_receipt_lag_s"] is None
    assert "max-receipt-lag=unknown-missing" in row["sample"]


def test_delivery_gap_reader_rejects_a_future_detection_stamp(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, perception = _linked_perception(tmp_path)
    (perception / body_perception.GAP_LOG_NAME).write_text(
        _gap_line(now + 600, received=now - 1000) + "\n", encoding="utf-8")

    row = body_perception.read_redmi10_delivery_gaps(core, now)

    assert row["state"] == "unknown"
    assert row["reason"] == "gap-log-clock-future"

def test_dense_baseline_stays_out_of_the_render_time_body_rows() -> None:
    """The dense baseline only updates on the discovery-scan renewal, so the
    Senses pane has no render-time live read for it. Keeping its id out of
    ``BODY_SENSE_IDS`` makes the pane render the scan row under the scan
    freshness gate; adding it would filter the row out of the scan rows and
    append nothing, dropping the reading from the pane silently."""
    assert body_perception.DENSE_SENSE_ID not in body_perception.BODY_SENSE_IDS


def _dense_queue(tmp_path: Path, rows: list[str]) -> tuple[Path, float]:
    """A core site linked to an Android site carrying one dense queue."""
    core = tmp_path / "core"
    android = tmp_path / "android-body"
    (core / "health").mkdir(parents=True)
    (core / "health" / "linked-sites.json").write_text(json.dumps({
        "version": 1,
        "sites": [{"home": str(android), "session": "android-body", "sha": "a" * 40}],
    }), encoding="utf-8")
    queue = android / "body" / "queues" / body_perception.DENSE_QUEUE_NAME
    queue.parent.mkdir(parents=True)
    queue.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return core, 1_800_000_000.0


def test_dense_baseline_motion_coverage_counts_rows_carrying_values(
        tmp_path: Path) -> None:
    """An acceleration row without axis values is a delivery gap, not motion.

    Counting those rows as coverage made the reported rate describe the poll
    cadence, so a stream delivering only ``unavailable`` rows still read as a
    healthy fraction and hid the absent motion channel.
    """
    now = 1_800_000_000.0
    rows: list[str] = []
    for index in range(20):
        epoch = int(now) - 300 + index * 5
        rows.append(f"{index} {epoch} light lux=5.0 accuracy=3 age_ms=10")
        rows.append(f"{index} {epoch} acceleration unavailable reason=stale")
        rows.append(f"{index} {epoch} battery level=0.5 charging=true")
        rows.append(f"{index} {epoch} wifi_link_rssi_dbm unavailable reason=disconnected")
    epoch = int(now) - 100
    rows.append(f"live {epoch} light lux=6.0 accuracy=3 age_ms=10")
    rows.append(f"live {epoch} acceleration x=0.1 y=0.2 z=9.9 accuracy=3 age_ms=10")
    rows.append(f"live {epoch} battery level=0.5 charging=true")
    rows.append(f"live {epoch} wifi_link_rssi_dbm unavailable reason=disconnected")
    core, now = _dense_queue(tmp_path, rows)

    row = body_perception.read_redmi10_dense_baseline(core, now=now + 1)

    assert row["accel_count"] == 21
    assert row["motion_count"] == 1
    # 21 of 84 rows are acceleration rows: the delivery rate.
    assert row["accel_ratio"] == pytest.approx(21 / 84)
    # One of 84 rows carries motion values: the observation rate.
    assert row["motion_ratio"] == pytest.approx(1 / 84)
    assert "motion=1%" in row["sample"]
    assert "motion-coverage=1.2%" in row["details"]
    assert "delivery-coverage=25.0%" in row["details"]


def test_dense_baseline_without_any_motion_values_reports_zero_motion_coverage(
        tmp_path: Path) -> None:
    """A stream that delivers no motion rows at all must not read as motion."""
    now = 1_800_000_000.0
    rows: list[str] = []
    for index in range(20):
        epoch = int(now) - 100 + index * 5
        rows.append(f"{index} {epoch} light lux=5.0 accuracy=3 age_ms=10")
        rows.append(f"{index} {epoch} acceleration unavailable reason=stale")
        rows.append(f"{index} {epoch} battery level=0.5 charging=true")
        rows.append(f"{index} {epoch} wifi_link_rssi_dbm unavailable reason=disconnected")
    core, now = _dense_queue(tmp_path, rows)

    row = body_perception.read_redmi10_dense_baseline(core, now=now + 1)

    assert row["motion_count"] == 0
    assert row["motion_ratio"] == 0.0
    assert "motion=0%" in row["sample"]
    # The delivery rate stays visible: rows arrived, but none carried motion.
    assert row["accel_ratio"] == pytest.approx(20 / 80)


def _peer_report(hostname: str = "Redmi 10", *, online: bool = True,
                 active: bool = True) -> dict:
    """A ``tailscale status --json`` report, keyed by node key as it really is."""
    return {
        "BackendState": "Running",
        "Self": {"HostName": "mesh-home"},
        "Peer": {
            "nodekey:0ee4c8c63b75f0978a7aa1c3adf53f83bf00ad113bd98fe6798f9f3a1367630f": {
                "HostName": hostname,
                "DNSName": "redmi-10.tail3e4555.ts.net.",
                "Online": online,
                "Active": active,
            },
        },
    }


def _with_cursor_advance(state: dict, when: float) -> dict:
    """A consumer state that last saw the delivery cursor advance at ``when``."""
    state[body_perception.CURSOR_ADVANCE_FIELD] = when
    return state


def test_peer_delivery_reads_delivering_when_the_cursor_advanced(
        tmp_path: Path) -> None:
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    _with_cursor_advance(state, now - 5)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10_peer_delivery(core, now, peer=_peer_report())

    assert row["state"] == "verified"
    assert row["verdict"] == "delivering"
    assert row["reason"] == "cursor-advancing"
    assert row["peer_online"] is True
    assert row["cursor"] == 3
    assert row["delivery_age_seconds"] == pytest.approx(5)
    assert "verdict=delivering" in row["sample"]


def test_peer_delivery_uses_the_cursor_advance_not_the_poll_time(
        tmp_path: Path) -> None:
    """A consumer that keeps polling a silent phone has a fresh poll time and
    a frozen cursor. Gating on the poll time would report that as a healthy
    read; the delivery age has to come from the cursor's own advance time."""
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    # The poll is current, the cursor has not moved for an hour.
    state["poll_at"] = now - 1
    _with_cursor_advance(state, now - 3600)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10_peer_delivery(core, now, peer=_peer_report())

    assert row["verdict"] == "present-not-sending"
    assert row["reason"] == "cursor-frozen"
    assert row["delivery_age_seconds"] == pytest.approx(3600)


def test_peer_delivery_separates_absent_from_present_but_silent(
        tmp_path: Path) -> None:
    """A phone that is not on the tailnet and a phone that is on it but not
    sending are different faults, and the consumer's own transport=reachable
    cannot tell them apart because it describes the host's own path."""
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    _with_cursor_advance(state, now - 3600)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    silent = body_perception.read_redmi10_peer_delivery(
        core, now, peer=_peer_report(online=True, active=False))
    absent = body_perception.read_redmi10_peer_delivery(
        core, now, peer=_peer_report(online=False, active=False))

    assert silent["verdict"] == "present-not-sending"
    assert silent["peer_online"] is True
    assert absent["verdict"] == "peer-absent"
    assert absent["peer_online"] is False
    assert silent["state"] == absent["state"] == "verified"


def test_peer_delivery_does_not_claim_reachability_from_peer_flags(
        tmp_path: Path) -> None:
    """``Online`` and ``Active`` are control-plane presence and recent
    traffic, not proof that this host can reach the phone's data path, so the
    reading must not say the phone is reachable."""
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    _with_cursor_advance(state, now - 3600)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    row = body_perception.read_redmi10_peer_delivery(core, now, peer=_peer_report())

    assert row["verdict"] == "present-not-sending"
    assert "not proof that this host can reach the phone" in row["details"]
    assert "reachable and not sending" not in row["details"]


def test_peer_delivery_is_unknown_without_a_readable_peer_state(
        tmp_path: Path) -> None:
    """Without the peer report the reading cannot name the phone, so it stays
    UNKNOWN rather than inferring presence from the cursor alone."""
    now = 1_800_000_000.0
    core, state_path, state = _linked_state(tmp_path, now)
    _with_cursor_advance(state, now - 5)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    missing = body_perception.read_redmi10_peer_delivery(core, now, peer=None)
    absent = body_perception.read_redmi10_peer_delivery(
        core, now, peer={"BackendState": "Running", "Self": {}, "Peer": {}})

    assert missing["state"] == "unknown"
    assert missing["reason"] == "peer-state-unavailable"
    assert absent["state"] == "unknown"
    assert absent["reason"] == "peer-absent-from-tailnet"


def test_peer_delivery_is_unknown_without_a_cursor_advance_time(
        tmp_path: Path) -> None:
    """A consumer state that has never seen a cursor advance cannot say how
    long delivery has been silent, so the reading stays UNKNOWN."""
    now = 1_800_000_000.0
    core, _path, _state = _linked_state(tmp_path, now)

    row = body_perception.read_redmi10_peer_delivery(core, now, peer=_peer_report())

    assert row["state"] == "unknown"
    assert row["reason"] == "cursor-advance-time-absent"
