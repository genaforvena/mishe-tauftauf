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
    changed_sensor = body_perception.read_redmi10(core, now)
    assert changed_sensor["sample"] != first["sample"]
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
