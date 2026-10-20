from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mishe_tauftauf import discovery, host_boot


BOOT_ID = "63398cfb-ae54-4bc7-b087-e57027a6c576"
PREVIOUS_BOOT_ID = "ff6b21a1-05ae-4749-1b9b-1694f34730f4"
NOW = datetime(2026, 10, 20, 7, 0, 37, tzinfo=timezone.utc).timestamp()

VALID_OBSERVATION = {"id": "sense.proc.loadavg", "kind": "read",
                      "state": "verified", "sample": "1.00 0.90 0.80"}


def _proc_root(tmp_path: Path, boot_id: str = BOOT_ID, uptime: str = "412.34 100.00") -> Path:
    root = tmp_path / "proc"
    (root / "sys/kernel/random").mkdir(parents=True)
    (root / "sys/kernel/random/boot_id").write_text(boot_id + "\n", encoding="utf-8")
    (root / "uptime").write_text(uptime + "\n", encoding="utf-8")
    return root


def _receipt(home: Path, boot_id: str | None = PREVIOUS_BOOT_ID,
             created: str | None = "2026-10-20T04:26:00Z",
             start_utc_ns: int | None = int(
                 datetime(2026, 10, 20, 4, 25, 0, tzinfo=timezone.utc).timestamp()
                 * 1_000_000_000)) -> Path:
    """Write the receipt the plant publishes after a scan."""
    discovery_dir = home / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"observations": [dict(VALID_OBSERVATION)]}
    if created is not None:
        payload["created"] = created
    if boot_id is not None:
        start = {"boot_id": boot_id}
        if start_utc_ns is not None:
            start["utc_ns"] = start_utc_ns
        payload["acquisition"] = {"start": start, "end": {"boot_id": boot_id}}
    path = discovery_dir / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _boot_state(boot_id: str | None = BOOT_ID, uptime_s: float | None = 412.0,
                problems: list[str] | None = None) -> dict:
    return {"boot_id": boot_id, "uptime_s": uptime_s,
            "problems": list(problems or []), "sources": {}}


def test_read_boot_state_decodes_procfs_values(tmp_path: Path) -> None:
    state = host_boot.read_boot_state(_proc_root(tmp_path))

    assert state["boot_id"] == BOOT_ID
    assert state["uptime_s"] == pytest.approx(412.34)
    assert state["problems"] == []


def test_read_boot_state_names_unreadable_and_malformed_inputs(tmp_path: Path) -> None:
    root = _proc_root(tmp_path, boot_id="not-a-uuid")
    (root / "uptime").unlink()

    state = host_boot.read_boot_state(root)

    assert state["boot_id"] is None
    assert state["uptime_s"] is None
    assert "boot-id-malformed" in state["problems"]
    assert "uptime-missing" in state["problems"]


def test_read_boot_state_rejects_an_impossible_uptime(tmp_path: Path) -> None:
    root = _proc_root(tmp_path, uptime="999999999.00 0.00")

    state = host_boot.read_boot_state(root)

    assert state["uptime_s"] is None
    assert "uptime-out-of-range" in state["problems"]


def test_previous_scan_decodes_the_published_receipt() -> None:
    started = datetime(2026, 10, 20, 4, 25, 0, tzinfo=timezone.utc)
    snapshot = {"created": "2026-10-20T04:26:00Z",
                "acquisition": {"start": {
                    "boot_id": PREVIOUS_BOOT_ID,
                    "utc_ns": int(started.timestamp() * 1_000_000_000)}}}

    previous = host_boot.previous_scan(snapshot)

    assert previous is not None
    assert previous["boot_id"] == PREVIOUS_BOOT_ID
    assert previous["created"] == "2026-10-20T04:26:00Z"
    assert previous["acquisition_start_epoch"] == pytest.approx(started.timestamp())


def test_previous_scan_is_absent_without_a_receipt() -> None:
    assert host_boot.previous_scan(None) is None


def test_host_boot_read_reports_a_reboot_between_scans(tmp_path: Path) -> None:
    home = tmp_path / "site"
    _receipt(home)

    with patch("mishe_tauftauf.host_boot.read_boot_state", return_value=_boot_state()):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["id"] == "sense.host.boot"
    assert reading["state"] == "drift"
    assert reading["reboot"] == "yes"
    assert reading["previous_boot_id"] == PREVIOUS_BOOT_ID
    assert "booted=2026-10-20T06:53:45Z" in reading["sample"]
    assert "last-scan=2026-10-20T04:26:00Z" in reading["sample"]
    # The acquisition anchor precedes the prior scan's boot observation.
    assert "outage<=8925s" in reading["sample"]
    assert reading["booted_at_derivation"] == "now-uptime"


def test_host_boot_read_is_verified_when_the_boot_did_not_change(tmp_path: Path) -> None:
    home = tmp_path / "site"
    _receipt(home, boot_id=BOOT_ID)

    with patch("mishe_tauftauf.host_boot.read_boot_state", return_value=_boot_state()):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["state"] == "verified"
    assert reading["reboot"] == "no"
    assert "reboot=no" in reading["sample"]


def test_host_boot_read_without_a_prior_scan_stays_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"

    with patch("mishe_tauftauf.host_boot.read_boot_state", return_value=_boot_state()):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["state"] == "unknown"
    assert "no-prior-scan" in reading["reason"]
    assert "reboot=unknown(no-prior-scan)" in reading["sample"]


def test_host_boot_read_names_a_damaged_prior_scan_apart_from_an_absent_one(tmp_path: Path) -> None:
    home = tmp_path / "site"
    discovery_dir = home / "discovery"
    discovery_dir.mkdir(parents=True)
    (discovery_dir / "latest.json").write_text("{not json", encoding="utf-8")

    with patch("mishe_tauftauf.host_boot.read_boot_state", return_value=_boot_state()):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["state"] == "unknown"
    assert "prior-scan-unreadable" in reading["reason"]
    assert "no-prior-scan" not in reading["reason"]


def test_host_boot_read_keeps_an_unreadable_boot_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    _receipt(home)

    with patch("mishe_tauftauf.host_boot.read_boot_state",
               return_value=_boot_state(boot_id=None, uptime_s=None,
                                        problems=["boot-id-missing"])):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["state"] == "unknown"
    assert "boot-id-missing" in reading["reason"]
    assert "host boot UNKNOWN" in reading["sample"]


def test_host_boot_read_without_a_comparable_prior_boot_id_stays_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    _receipt(home, boot_id=None)

    with patch("mishe_tauftauf.host_boot.read_boot_state", return_value=_boot_state()):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["state"] == "unknown"
    assert "no-comparable-boot-id" in reading["reason"]


def test_host_boot_read_uses_acquisition_start_without_creation_stamp(tmp_path: Path) -> None:
    home = tmp_path / "site"
    _receipt(home, created=None)

    with patch("mishe_tauftauf.host_boot.read_boot_state", return_value=_boot_state()):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["state"] == "drift"
    assert reading["reboot"] == "yes"
    assert "outage<=8925s" in reading["sample"]
    assert "last-scan=" not in reading["sample"]


def test_host_boot_read_omits_bound_without_acquisition_start(tmp_path: Path) -> None:
    home = tmp_path / "site"
    _receipt(home, start_utc_ns=None)

    with patch("mishe_tauftauf.host_boot.read_boot_state", return_value=_boot_state()):
        reading = discovery._host_boot_read(home, now=NOW)

    assert reading["state"] == "drift"
    assert reading["reboot"] == "yes"
    assert "outage<=" not in reading["sample"]
    assert "last-scan=2026-10-20T04:26:00Z" in reading["sample"]


def test_scan_publishes_the_host_boot_sense(tmp_path: Path) -> None:
    home = tmp_path / "site"

    artifact = discovery.scan(home)
    snapshot = json.loads(artifact.read_text(encoding="utf-8"))
    observed = {row["id"]: row for row in snapshot["observations"]}

    assert "sense.host.boot" in observed
    assert observed["sense.host.boot"]["state"] in {"verified", "drift", "unknown"}
    assert observed["sense.host.boot"]["kind"] == "read"
