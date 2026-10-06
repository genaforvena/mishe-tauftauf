from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mishe_tauftauf.access import decide, request
from mishe_tauftauf.discovery import scan
from mishe_tauftauf.seed_culture_views import discover, health, permissions, senses


def test_views_show_real_scan_and_scoped_request_without_clock_churn(tmp_path: Path) -> None:
    home = tmp_path / "site"
    assert "UNKNOWN — no discovery scan" in discover(home)
    scan(home)
    first = discover(home)
    assert first == discover(home)
    assert "command.git" in first
    sense = senses(home)
    assert "sense.proc.loadavg" in sense
    request(home, "keyboard-count", "senses", "sense-keyboard", "input.activity.count",
            ["senses/keyboard", "task/sense-keyboard/verify"], "Need count-only probe")
    pending = permissions(home)
    assert "keyboard-count PENDING" in pending
    assert "senses/keyboard" in pending
    decide(home, "keyboard-count", "granted")
    assert "keyboard-count GRANTED" in permissions(home)



def test_health_detects_dead_top_even_when_bottom_is_alive(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 1\nhealth 1 0\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "WINDOWS: RED health dead=health.0" in rendered
    assert "STATE: RED" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("FAIL health internal check at ")
    assert "windows-dead=health.0" in report

def test_health_detects_dead_mind_pane_when_renderer_is_alive(tmp_path: Path, monkeypatch) -> None:
    """A wedged mind pane (.1) must read RED though its renderer lease lives."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    (home / "minds").mkdir()
    (home / "minds" / "health").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0\nhealth 1 1\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "WINDOWS: RED health dead=health.1" in rendered
    assert "STATE: RED" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "windows-dead=health.1" in report


def test_health_ignores_a_dead_bottom_pane_without_a_mind_launcher(tmp_path: Path, monkeypatch) -> None:
    """The permissions panel's bottom shell is not a mind; its death is no fault."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["permissions"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "permissions 0 0\npermissions 1 1\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "WINDOWS: PASS permissions" in rendered

def test_health_flags_a_frozen_renderer_lease_as_red(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    (home / "top-pains").mkdir()
    (home / "top-pains" / "health").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    from datetime import datetime, timedelta, timezone

    def run(argv, **_kwargs):
        if argv[0] == "tmux" and "capture-pane" in argv:
            stamp = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
            return subprocess.CompletedProcess(
                argv, 0, f"-- pane live {stamp} · refresh 5s · ticks every frame --\n", "")
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.wall_view.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "PANE LEASE: RED stale=health(" in rendered
    assert "STATE: RED" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("FAIL health internal check at ")
    assert "pane-lease" in report


def test_health_reports_linked_site_inactive_service_as_red(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "core"
    linked = tmp_path / "linked"
    (home / "health").mkdir(parents=True)
    (linked / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text('["health"]', encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    (home / "health" / "linked-sites.json").write_text(
        json.dumps({"version": 1, "sites": [{"home": str(linked), "session": "example"}]}),
        encoding="utf-8")
    (linked / "health" / "services.json").write_text('["example-health.service"]', encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0\n", "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 3, "inactive\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "LINKED SERVICE example example-health.service: inactive" in rendered
    assert "LINKED SITE example: RED" in rendered
    assert "STATE: RED" in rendered


def test_health_reports_missing_linked_site_services_as_unknown(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "core"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text('["health"]', encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    (home / "health" / "linked-sites.json").write_text(
        json.dumps({"version": 1, "sites": [{"home": str(tmp_path / "missing"), "session": "gone"}]}),
        encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "LINKED SITE gone: UNKNOWN" in rendered
    assert "STATE: UNKNOWN — linked-site service data unavailable" in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith(
        "UNKNOWN health linked-site data unavailable at ")


@pytest.mark.parametrize("fault", [
    "malformed", "bad-schema", "timeout", "bad-status", "budget", "active", "unconfigured",
    "registry-malformed", "registry-schema", "nul-unit", "surrogate-unit",
])
def test_health_linked_service_verdicts(tmp_path: Path, monkeypatch, fault) -> None:
    home = tmp_path / "core"
    linked = tmp_path / "linked"
    (home / "health").mkdir(parents=True)
    (linked / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text('["health"]', encoding="utf-8")
    (home / "health" / "services.json").write_text('["core.service"]', encoding="utf-8")
    (home / "health" / "linked-sites.json").write_text(
        json.dumps({"version": 1, "sites": [{"home": str(linked), "session": "example"}]}),
        encoding="utf-8")
    manifest = ("{" if fault == "malformed" else '{"unit":"example-health.service"}'
                if fault == "bad-schema" else '["example-health.service"]')
    if fault == "nul-unit":
        manifest = json.dumps(["bad\0.service"])
    elif fault == "surrogate-unit":
        manifest = json.dumps(["\ud800.service"])
    (linked / "health" / "services.json").write_text(manifest, encoding="utf-8")
    registry_path = home / "health" / "linked-sites.json"
    if fault == "unconfigured":
        registry_path.unlink()
    elif fault == "registry-malformed":
        registry_path.write_text("{", encoding="utf-8")
    elif fault == "registry-schema":
        registry_path.write_text('{"version":1,"sites":{}}', encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")
    if fault == "budget":
        ticks = iter([0, 6])
        monkeypatch.setattr("mishe_tauftauf.seed_culture_views.monotonic", lambda: next(ticks))
    original_run = subprocess.run

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0\n", "")
        if argv[0] == "systemctl":
            if "show" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, "Id=core.service\nActiveState=active\nSubState=running\nNRestarts=0\n", "")
            if fault in {"nul-unit", "surrogate-unit"}:
                return original_run(argv, **_kwargs)
            if fault == "active":
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            if fault == "timeout":
                raise subprocess.TimeoutExpired(argv, 2)
            if fault == "budget":
                pytest.fail("An exhausted budget must not start a service probe")
            return subprocess.CompletedProcess(argv, 1, "garbage\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    if fault in {"active", "unconfigured"}:
        assert "STATE: GREEN" in rendered
        assert (home / "observations" / "health").read_text(encoding="utf-8").startswith("PASS ")
    else:
        if fault.startswith("registry-"):
            assert "LINKED SITES: UNKNOWN" in rendered
        else:
            assert "LINKED SITE example: UNKNOWN" in rendered
        assert "STATE: UNKNOWN" in rendered
        assert (home / "observations" / "health").read_text(encoding="utf-8").startswith("UNKNOWN ")


def test_senses_reports_absent_source_as_unavailable_not_unchecked(tmp_path: Path, current_endpoint) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
        {"id": "sense.input.keyboard-interrupt-count", "state": "unavailable",
         "sample": "no keyboard interrupt source on this host", "kind": "counter"},
    ])
    rendered = senses(home)
    assert "UNAVAILABLE sense.input.keyboard-interrupt-count" in rendered
    assert "STATE: GREEN" in rendered
    assert "UNAVAILABLE senses 1" in rendered
    assert (home / "observations" / "senses").read_text(encoding="utf-8").startswith("PASS senses 2 ")


def test_senses_still_counts_an_unreadable_counter_as_unknown(tmp_path: Path, current_endpoint) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.input.keyboard-interrupt-count", "state": "unknown",
         "sample": "counter unreadable; /proc/interrupts unavailable", "kind": "counter"},
    ])
    rendered = senses(home)
    assert "UNKNOWN sense.input.keyboard-interrupt-count" in rendered
    assert "STATE: UNKNOWN" in rendered
    assert "UNAVAILABLE senses" not in rendered
    assert (home / "observations" / "senses").read_text(encoding="utf-8").startswith("UNKNOWN ")


def _endpoint(first: int, last: int, utc_ns: int = 1_700_000_000_000_000_000) -> dict:
    return {"clock": "CLOCK_BOOTTIME", "bounds_ns": [first, last],
            "boot_id": "12345678-1234-5678-1234-567812345678",
            "time_namespace": "time:[4026531834]",
            "offsets": {"monotonic": [0, 0], "boottime": [0, 0]}, "utc_ns": utc_ns}


@pytest.fixture
def current_endpoint(monkeypatch) -> dict:
    current = _endpoint(20_000_000_000, 20_000_000_100)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.scan_freshness.endpoint", lambda: current)
    return current


def _write_scan(home: Path, observations: list[dict], *, acquisition: bool = True,
                status: str | None = "succeeded", stage: str = "notification",
                error: str | None = None) -> dict:
    snapshot = {"created": "2099-01-01T00:00:00Z", "scan_id": "sample-1",
                "node": "node", "observations": observations}
    if acquisition:
        snapshot["acquisition"] = {
            "start": _endpoint(10_000_000_000, 10_000_000_100),
            "end": _endpoint(11_000_000_000, 11_000_000_100, 1_700_000_001_000_000_000)}
    directory = home / "discovery"
    directory.mkdir(parents=True)
    (directory / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    if status is not None:
        attempt = {"status": status, "stage": stage, "scan_id": "sample-1",
                   "created": "2023-11-14T22:13:20Z", "artifact": "sample-1.json"}
        if error is not None:
            attempt["error"] = error
        (directory / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")
    return snapshot


def _journal() -> dict:
    return {"id": "sense.journal.kernel-error-count", "state": "verified", "kind": "read",
            "sample": "last-10min kernel-error-count=7", "count": 7,
            "coverage": {"since": "2023-11-14T22:03:20.123456+00:00",
                         "until": "2023-11-14T22:13:20.123456+00:00",
                         "boot_id": "12345678123456781234567812345678",
                         "acquisition_started_ns": 10_000_000_100,
                         "acquisition_finished_ns": 11_000_000_000}}


@pytest.mark.parametrize("pane", [discover, senses])
def test_journal_keeps_original_fixed_window_and_full_metadata(
        tmp_path: Path, current_endpoint, pane) -> None:
    home = tmp_path / "site"
    journal = _journal()
    journal["coverage"]["source_note"] = "original source metadata " + "x" * 180
    _write_scan(home, [journal])
    rendered = pane(home)
    assert "VERIFIED sense.journal.kernel-error-count" in rendered
    assert "historical sample=last-10min kernel-error-count=7 fixed-window count=7" in rendered
    for key, value in journal["coverage"].items():
        assert str(value) in rendered
    assert "freshness=recent utc_consistency=consistent" in rendered
    assert "STATE: GREEN" in rendered


def _unit_failure() -> dict:
    return {"id": "sense.journal.unit-failure-count", "state": "verified", "kind": "read",
            "sample": "last-10min unit-failure-count=2 resources=2", "count": 2,
            "units": {"tmux-spawn-1.scope": 1, "tmux-spawn-2.scope": 1},
            "classes": {"resources": 2},
            "coverage": {"since": "2023-11-14T22:03:20.123456+00:00",
                         "until": "2023-11-14T22:13:20.123456+00:00",
                         "boot_id": "12345678123456781234567812345678",
                         "acquisition_started_ns": 10_000_000_100,
                         "acquisition_finished_ns": 11_000_000_000}}


@pytest.mark.parametrize("pane", [discover, senses])
def test_unit_failure_journal_sense_verifies_with_valid_bounds(
        tmp_path: Path, current_endpoint, pane) -> None:
    home = tmp_path / "site"
    _write_scan(home, [_unit_failure()])
    rendered = pane(home)
    assert "VERIFIED sense.journal.unit-failure-count" in rendered
    assert "historical sample=last-10min unit-failure-count=2 resources=2" in rendered
    assert "source_bounds=valid" in rendered
    assert "STATE: GREEN" in rendered


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("identity", ["sense.journal.kernel-error-count", "sense.journal.kernel-error-rate"])
def test_legacy_journal_preserves_count_but_cannot_establish_current_trust(
        tmp_path: Path, current_endpoint, pane, identity) -> None:
    home = tmp_path / "site"
    journal = _journal()
    journal["id"] = identity
    del journal["coverage"]
    _write_scan(home, [journal], acquisition=False)
    rendered = pane(home)
    assert f"UNKNOWN {identity}" in rendered
    assert "historical sample=last-10min kernel-error-count=7 fixed-window count=7" in rendered
    assert "since=unknown until=unknown boot=unknown" in rendered
    assert "source_bounds=unknown" in rendered
    assert "freshness=unknown utc_consistency=unknown" in rendered
    assert "VERIFIED sense.journal" not in rendered
    assert "STATE: GREEN" not in rendered


@pytest.mark.parametrize("field,value", [
    ("since", None), ("since", "2023-11-14T22:03:20"), ("until", "invalid"),
    ("until", "2023-11-14T22:00:00+00:00"), ("boot_id", None),
    ("boot_id", "12345678-1234-5678-1234-567812345678"), ("count", None),
    ("count", True), ("count", -1),
])
def test_journal_invalid_coverage_cannot_be_verified(
        tmp_path: Path, current_endpoint, field, value) -> None:
    home = tmp_path / "site"
    journal = _journal()
    target = journal if field == "count" else journal["coverage"]
    if value is None:
        del target[field]
    else:
        target[field] = value
    _write_scan(home, [journal])
    rendered = senses(home)
    assert "freshness=recent" in rendered
    assert "UNKNOWN sense.journal.kernel-error-count" in rendered
    assert "source_state=verified source_bounds=unknown" in rendered
    assert "STATE: UNKNOWN" in rendered


def test_recent_acquisition_does_not_verify_unknown_journal_source(
        tmp_path: Path, current_endpoint) -> None:
    home = tmp_path / "site"
    journal = _journal()
    journal["state"] = "unknown"
    journal["reason"] = "journal output incomplete"
    _write_scan(home, [journal])
    rendered = senses(home)
    assert "freshness=recent" in rendered
    assert "UNKNOWN sense.journal.kernel-error-count" in rendered
    assert "journal output incomplete" in rendered
    assert "STATE: UNKNOWN" in rendered


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("status,stage,error", [
    ("running", "acquisition", None), ("failed", "acquisition", "probe failed"),
    ("failed", "publication", "write failed"), ("failed", "notification", "feed append failed"),
    (None, "acquisition", None),
])
def test_incomplete_attempt_remains_visible_without_downgrading_acquired_source(
        tmp_path: Path, current_endpoint, pane, status, stage, error) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ], status=status, stage=stage, error=error)
    rendered = pane(home)
    assert f"ATTEMPT: {(status or 'unknown').upper()}" in rendered
    if status is not None:
        assert f"stage={stage}" in rendered
    if error is not None:
        assert error in rendered
    assert "VERIFIED sense.proc.loadavg" in rendered
    assert "freshness=recent" in rendered
    assert "STATE: UNKNOWN" in rendered
    assert (home / "observations" / pane.__name__).read_text(encoding="utf-8").startswith("UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("marker", ["unreadable", "different-scan"])
def test_attempt_unknown_or_for_another_sample_cannot_claim_success(
        tmp_path: Path, current_endpoint, pane, marker) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ])
    path = home / "discovery" / "attempt.json"
    if marker == "unreadable":
        path.write_text("{", encoding="utf-8")
    else:
        attempt = json.loads(path.read_text(encoding="utf-8"))
        attempt["scan_id"] = "another-scan"
        path.write_text(json.dumps(attempt), encoding="utf-8")
    rendered = pane(home)
    assert "ATTEMPT: UNKNOWN" in rendered if marker == "unreadable" else "sample_match=unknown" in rendered
    assert "VERIFIED sense.proc.loadavg" in rendered
    assert "STATE: UNKNOWN" in rendered
    assert (home / "observations" / pane.__name__).read_text(encoding="utf-8").startswith("UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("first,last,freshness", [
    (910_000_000_000, 910_000_000_000, "recent"),
    (910_000_000_000, 910_000_000_001, "unknown"),
    (911_000_000_001, 911_000_000_001, "stale"),
])
def test_empty_scan_obeys_full_age_interval_at_fifteen_minute_boundary(
        tmp_path: Path, current_endpoint, pane, first, last, freshness) -> None:
    home = tmp_path / "site"
    snapshot = _write_scan(home, [])
    snapshot["acquisition"]["start"]["bounds_ns"] = [10_000_000_000, 10_000_000_000]
    snapshot["acquisition"]["end"]["bounds_ns"] = [11_000_000_000, 11_000_000_000]
    (home / "discovery" / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    current_endpoint["bounds_ns"] = [first, last]
    rendered = pane(home)
    assert f"freshness={freshness}" in rendered
    report = (home / "observations" / pane.__name__).read_text(encoding="utf-8")
    assert report.startswith("PASS " if freshness == "recent" else "UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
def test_empty_legacy_scan_cannot_pass_without_acquisition(
        tmp_path: Path, current_endpoint, pane) -> None:
    home = tmp_path / "site"
    _write_scan(home, [], acquisition=False)
    rendered = pane(home)
    assert "freshness=unknown" in rendered
    assert "STATE: UNKNOWN" in rendered
    assert (home / "observations" / pane.__name__).read_text(encoding="utf-8").startswith("UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("row", [
    {"id": "sense.tmux.cached-read"},
    {"id": "sense.proc.loadavg", "cached": True},
    {"id": "sense.proc.loadavg", "imported": True},
])
def test_cached_source_cannot_be_refreshed_by_enclosing_acquisition(
        tmp_path: Path, current_endpoint, pane, row) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {**row, "state": "verified", "sample": "old source sample", "kind": "read"},
    ])
    rendered = pane(home)
    assert "source_state=verified source_freshness=unknown" in rendered
    assert "STATE: UNKNOWN" in rendered
    assert (home / "observations" / pane.__name__).read_text().startswith("UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("marker", ["cached", "imported"])
def test_cached_journal_preserves_bounds_without_refreshing_source(
        tmp_path: Path, current_endpoint, pane, marker) -> None:
    home = tmp_path / "site"
    journal = {**_journal(), marker: True}
    _write_scan(home, [journal])
    rendered = pane(home)
    assert journal["coverage"]["since"] in rendered
    assert journal["coverage"]["until"] in rendered
    assert "source_freshness=unknown" in rendered
    assert "STATE: UNKNOWN" in rendered
    assert (home / "observations" / pane.__name__).read_text().startswith("UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
def test_acquisition_wall_anomaly_is_visible_without_replacing_boottime_recency(
        tmp_path: Path, current_endpoint, pane) -> None:
    home = tmp_path / "site"
    snapshot = _write_scan(home, [_journal()])
    snapshot["acquisition"]["end"]["utc_ns"] = 1_699_999_999_000_000_000
    (home / "discovery" / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    rendered = pane(home)
    assert "freshness=recent utc_consistency=anomaly" in rendered
    assert "VERIFIED sense.journal.kernel-error-count" in rendered
    assert "STATE: GREEN" in rendered


@pytest.mark.parametrize("manifest", [None, "{", '{"unit":"health.service"}', '["bad"]'])
def test_health_reports_invalid_local_services_manifest_as_unknown(
        tmp_path: Path, monkeypatch, manifest: str | None) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text('["health"]', encoding="utf-8")
    if manifest is not None:
        (home / "health" / "services.json").write_text(manifest, encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "SERVICES: UNKNOWN — local manifest unavailable or malformed" in rendered
    assert "STATE: UNKNOWN — local service data unavailable" in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith(
        "UNKNOWN health local service data unavailable at ")


def _service_home(tmp_path: Path, units: list[str]) -> Path:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text('["health"]', encoding="utf-8")
    (home / "health" / "services.json").write_text(json.dumps(units), encoding="utf-8")
    return home


def _fake_systemctl(monkeypatch, show: str, *, rc: int = 0) -> None:
    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0\n", "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, rc, show, "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")


def test_health_reports_a_restart_window_instead_of_a_lucky_sample(tmp_path: Path, monkeypatch) -> None:
    """A crash-looping unit must not read healthy because one sample missed the window."""
    home = _service_home(tmp_path, ["flaky.service"])
    _fake_systemctl(monkeypatch, "Id=flaky.service\nActiveState=activating\n"
                                 "SubState=auto-restart\nNRestarts=31\n")
    rendered = health(home)
    assert "SERVICE flaky.service: activating/auto-restart restarts=31" in rendered
    assert "STATE: RED" in rendered
    assert json.loads((home / "health" / "service-restarts.json").read_text()) == {"flaky.service": 31}


def test_health_marks_a_new_restart_red_and_a_steady_unit_green(tmp_path: Path, monkeypatch) -> None:
    home = _service_home(tmp_path, ["steady.service"])
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\n"
                                 "SubState=running\nNRestarts=4\n")
    first = health(home)
    assert "SERVICE steady.service: active/running restarts=4" in first
    assert "STATE: GREEN" in first
    (home / "health" / "service-restarts.json").write_text('{"steady.service": 3}', encoding="utf-8")
    assert "STATE: RED" in health(home)
    assert "STATE: GREEN" in health(home)


def test_health_leaves_units_unknown_and_keeps_the_baseline_when_systemctl_fails(
        tmp_path: Path, monkeypatch) -> None:
    home = _service_home(tmp_path, ["steady.service"])
    (home / "health" / "service-restarts.json").write_text('{"steady.service": 4}', encoding="utf-8")
    _fake_systemctl(monkeypatch, "", rc=1)
    rendered = health(home)
    assert "SERVICE steady.service: unknown/unknown" in rendered
    assert "STATE: RED" in rendered
    assert (home / "health" / "service-restarts.json").read_text() == '{"steady.service": 4}'


def test_health_names_an_empty_local_manifest_as_a_cause(tmp_path: Path, monkeypatch) -> None:
    """An empty manifest is RED in the pane, so the durable report must not read PASS."""
    home = _service_home(tmp_path, [])
    _fake_systemctl(monkeypatch, "")
    rendered = health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("FAIL health internal check") and "services-manifest-empty" in report
    assert "STATE: RED" in rendered

def test_health_reports_unknown_when_session_unset(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            raise AssertionError("tmux must not be called when session is unset")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "WINDOWS: UNKNOWN — session unset" in rendered
    assert "STATE: UNKNOWN — session unset" in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith(
        "UNKNOWN health session unset at ")


def test_health_names_runtime_import_root_drift_as_a_cause(tmp_path: Path, monkeypatch) -> None:
    """A covered unit importing another root must be named in the durable report."""
    from mishe_tauftauf import runtime_source
    home = _service_home(tmp_path, ["steady.service"])
    (home / "health" / "runtime-release.json").write_text("{}", encoding="utf-8")
    pinned = tmp_path / "pinned"
    other = tmp_path / "other"
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\n"
                                 "SubState=running\nNRestarts=0\n"
                                 f"Environment=PYTHONPATH={other}/src\n")
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: pinned)
    rendered = health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("FAIL health internal check") and "runtime-drift" in report
    assert "STATE: RED" in rendered
    # A unit whose import root equals the pin is not drift.
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\n"
                                 "SubState=running\nNRestarts=0\n"
                                 f"Environment=PYTHONPATH={pinned}/src\n")
    health(home)
    assert "runtime-drift" not in (home / "observations" / "health").read_text(encoding="utf-8")
