from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mishe_tauftauf import discovery
from mishe_tauftauf.access import decide, recover, request, resolve_recovery
from mishe_tauftauf.discovery import scan
from mishe_tauftauf.seed import _observation_text
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



@pytest.mark.parametrize(("states", "expected"), [
    (["unknown"], "STATE: UNKNOWN — 1 source reads unverified"),
    (["drift"], "STATE: UNKNOWN — 1 checked reading reports drift"),
    (["unknown", "unknown", "drift", "drift", "drift"],
     "STATE: UNKNOWN — 2 source reads unverified; 3 checked readings report drift"),
    (["unavailable"], "STATE: GREEN — recent bounded read-only scan"),
])
def test_discover_aggregate_separates_unverified_from_checked_drift(
        tmp_path: Path, current_endpoint, monkeypatch, states: list[str], expected: str) -> None:
    home = tmp_path / "site"
    monkeypatch.setattr(
        "mishe_tauftauf.seed_culture_views._space_light_read",
        lambda _home: {"id": "sense.space.light", "state": "verified",
                       "sample": "fresh light"})
    _write_scan(home, [
        {"id": f"sense.fixture.{index}", "kind": "read", "state": state,
         "sample": "fixture"}
        for index, state in enumerate(states)
    ])

    rendered = discover(home)

    assert expected in rendered
    if "UNKNOWN" in expected:
        assert "STATE: UNKNOWN" in rendered
    else:
        assert "STATE: UNKNOWN" not in rendered
    verdict = (home / "observations" / "discover").read_text(encoding="utf-8")
    if "UNKNOWN" in expected:
        assert verdict.startswith("UNKNOWN discover ")
    else:
        assert verdict.startswith("PASS discover ")

def test_discover_view_does_not_count_a_retired_request_as_pending(tmp_path: Path) -> None:
    """A retired permission request must not read pending on the discover pane."""
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    evidence = home / "artifacts" / "evidence.md"
    evidence.write_text("controlled fixture\n", encoding="utf-8")
    recover(home, "bt-leak-fixture", owner="discover", task="clear-stuck-advertising",
            resolver="health", missing="root authority for a bluetoothd restart",
            action="restart bluetooth", alternatives=["wait for the next restart"],
            cutoff="2099-01-01T00:00:00+00:00", evidence=evidence,
            capability="host-service-restart", unblocks=["site"],
            reason="restore prior host BT state")
    live = discover(home)
    assert "PERMISSION REQUESTS: 1 pending" in live
    assert "REQUEST bt-leak-fixture" in live
    resolve_recovery(home, "bt-leak-fixture", evidence,
                     checked_action="wait for the next restart", via_alternative=True)
    rendered = discover(home)
    assert "PERMISSION REQUESTS: 0 pending" in rendered
    assert "REQUEST bt-leak-fixture" not in rendered



def test_health_detects_dead_top_even_when_bottom_is_alive(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 1 9001\nhealth 1 0 9002\n", "")
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
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\nhealth 1 1 9002\n", "")
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
            return subprocess.CompletedProcess(argv, 0, "permissions 0 0 9001\npermissions 1 1 9002\n", "")
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
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
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


def _green_site_fixture(tmp_path: Path, monkeypatch) -> Path:
    """The clean-GREEN health fixture: active core service, live linked site,
    healthy pane snapshot with a fresh renderer lease."""
    home = tmp_path / "core"
    linked = tmp_path / "linked"
    (home / "health").mkdir(parents=True)
    (linked / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text('["health"]', encoding="utf-8")
    (home / "health" / "services.json").write_text('["core.service"]', encoding="utf-8")
    (home / "health" / "linked-sites.json").write_text(
        json.dumps({"version": 1, "sites": [{"home": str(linked), "session": "example"}]}),
        encoding="utf-8")
    (linked / "health" / "services.json").write_text('["example-health.service"]', encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            if "capture-pane" in argv:
                stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                return subprocess.CompletedProcess(
                    argv, 0, f"-- pane live {stamp} · refresh 5s · ticks every frame --\n", "")
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
        if argv[0] == "systemctl":
            if "show" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, "Id=core.service\nActiveState=active\nSubState=running\nNRestarts=0\n", "")
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.wall_view.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    return home


def test_health_folds_renderer_byte_unknown_into_the_verdict(tmp_path: Path, monkeypatch) -> None:
    """A renderer whose process is unreadable must not leave the verdict GREEN.

    research-methods DM 61672: the clause reads UNKNOWN while the headline a
    resolver reads first stayed GREEN; disposition (a) folds it into the verdict.
    """
    home = _green_site_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr("mishe_tauftauf.wall_view.renderer_bytes_lines", lambda h: (
        ["RENDERER BYTES: UNKNOWN pin=x unreadable=ledger"], "UNKNOWN"))
    rendered = health(home)
    assert "STATE: UNKNOWN — a top-pane renderer is unreadable" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("UNKNOWN health renderer bytes ")
    assert "unreadable=ledger" in report


def test_health_folds_renderer_byte_drift_into_the_fail_cause(tmp_path: Path, monkeypatch) -> None:
    """A superseded renderer release is a named cause, not just a clause line."""
    home = _green_site_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr("mishe_tauftauf.wall_view.renderer_bytes_lines", lambda h: (
        ["RENDERER BYTES: RED pin=x stale=ledger(live=y)"], "DRIFT"))
    rendered = health(home)
    assert "STATE: RED — internal check needs repair" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("FAIL health internal check at ")
    assert "renderer-bytes=RENDERER BYTES: RED pin=x stale=ledger(live=y)" in report


def test_health_stays_green_when_no_renderer_window_exists(tmp_path: Path, monkeypatch) -> None:
    """Empty lines mean there is no renderer to observe, not an unobserved one."""
    home = _green_site_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr("mishe_tauftauf.wall_view.renderer_bytes_lines",
                        lambda h: ([], "UNKNOWN"))
    rendered = health(home)
    assert "STATE: GREEN — internal checks pass" in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith("PASS ")


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
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
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
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
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
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
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


def test_missing_space_boundary_keeps_other_reads_but_cannot_claim_overall_success(
        tmp_path: Path, current_endpoint) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ])
    rendered = senses(home)
    assert "VERIFIED sense.proc.loadavg" in rendered
    assert "UNKNOWN sense.space.light" in rendered
    assert (home / "observations" / "senses").read_text(encoding="utf-8").startswith("UNKNOWN ")




def test_senses_pane_renders_each_clock_domain_as_a_separate_read(tmp_path: Path,
                                                                  current_endpoint,
                                                                  monkeypatch) -> None:
    home = tmp_path / "site"
    _write_scan(home, [{"id": "sense.proc.loadavg", "state": "verified",
                        "sample": "1.23", "kind": "read"}])
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._space_clock_domains_read",
                        lambda _home: [
                            {"id": "sense.space.clock-domain.note3", "state": "verified",
                             "sample": "note3 clock-domain current bounds=[24.2,25.2]"},
                            {"id": "sense.space.clock-domain.redmi10", "state": "unknown",
                             "sample": "redmi10 clock-domain absent"}])
    rendered = senses(home)
    assert "VERIFIED sense.space.clock-domain.note3" in rendered
    assert "UNKNOWN sense.space.clock-domain.redmi10" in rendered
    assert "UNKNOWN sense.space.light" in rendered
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


def test_a_drift_row_does_not_raise_the_unchecked_count(
        tmp_path: Path, current_endpoint) -> None:
    """A checked read reporting drift is not a read that never happened.

    The live rows on this fixture home read UNKNOWN, so the drift row has to
    change only the drift clause and leave the unchecked count alone.
    """
    without = tmp_path / "without"
    drifted = tmp_path / "drifted"
    _write_scan(without, [])
    _write_scan(drifted, [
        {"id": "sense.ledger.dv-binding", "state": "drift", "kind": "read",
         "sample": "unbound=applied records=102 bool_dv=57"},
    ])
    baseline = next(line for line in senses(without).splitlines()
                    if line.startswith("STATE: UNKNOWN"))
    rendered = senses(drifted)
    with_drift = next(line for line in rendered.splitlines()
                      if line.startswith("STATE: UNKNOWN"))
    assert "DRIFT sense.ledger.dv-binding: unbound=applied" in rendered
    assert with_drift == baseline + "; 1 checked readings report drift"
    assert "no reading is unchecked" not in with_drift


def test_senses_drift_only_pane_names_no_unchecked_read(
        tmp_path: Path, current_endpoint, monkeypatch) -> None:
    """Drift alone must not read as an unchecked row, and must stay notifying."""
    home = tmp_path / "site"
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10",
                        lambda _home, *_a, **_k: {"id": "sense.body.redmi10",
                                                 "kind": "read", "state": "verified",
                                                 "sample": "c=1"})
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10_light",
                        lambda _home, *_a, **_k: {"id": "sense.body.redmi10.light",
                                                  "kind": "read", "state": "verified",
                                                  "sample": "L=1lux"})
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10_delivery_gaps",
                        lambda _home: {"id": "sense.body.redmi10.delivery-gaps",
                                       "kind": "read", "state": "verified",
                                       "sample": "gaps-24h=2"})
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._space_light_read",
                        lambda _home: {"id": "sense.space.light", "kind": "read",
                                       "state": "verified", "sample": "Note3 light L=5lux",
                                       "reason": "current"})
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._space_clock_domains_read",
                        lambda _home: [])
    _write_scan(home, [
        {"id": "sense.ledger.dv-binding", "state": "drift", "kind": "read",
         "sample": "unbound=applied records=102 bool_dv=57"},
    ])
    rendered = senses(home)
    assert "STATE: UNKNOWN — 1 checked readings report drift; no reading is unchecked" in rendered
    assert (home / "observations" / "senses").read_text(
        encoding="utf-8").startswith("UNKNOWN senses 1 report drift")
    # The notification path keys on an UNKNOWN/RED/FAIL token on the STATE
    # line, so a drift-only pane has to keep carrying one.
    assert "STATE: UNKNOWN" in _observation_text("senses", rendered)


def test_senses_shows_the_host_radio_verdict_from_the_scan(tmp_path: Path, current_endpoint) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.space.host-radio", "state": "verified", "kind": "read",
         "sample": "BLE hci0 powered=yes adv-instances=0/5; wifi wlxabc=down; "
                    "rfkill bt=unblocked wlan=unblocked"},
    ])
    rendered = senses(home)
    assert "VERIFIED sense.space.host-radio: BLE hci0 powered=yes adv-instances=0/5" in rendered



def test_senses_renders_each_carried_body_sense_once_from_the_live_read(
        tmp_path: Path, current_endpoint) -> None:
    """A scan copy must not add a second, disagreeing row beside the live read."""
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
        {"id": "sense.body.redmi10.light", "state": "verified", "kind": "read",
         "sample": "Redmi10 sid=scan-copy L=0lux"},
        {"id": "sense.body.redmi10.delivery-gaps", "state": "verified", "kind": "read",
         "sample": "gaps-24h=99 last-onset=2026-01-01T00:00:00Z"},
    ])
    rendered = senses(home)
    for identity in ("sense.body.redmi10", "sense.body.redmi10.light",
                     "sense.body.redmi10.delivery-gaps"):
        assert rendered.count(identity + ":") == 1, identity
    assert "scan-copy" not in rendered
    assert "gaps-24h=99" not in rendered



def test_senses_keeps_a_live_body_read_when_the_scan_is_stale(
        tmp_path: Path, monkeypatch) -> None:
    """The scan's freshness must not turn a just-taken live read into UNKNOWN."""
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ])
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._scan_reading",
                        lambda _snapshot: ("stale", "consistent"))
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10_delivery_gaps",
                        lambda _home: {"id": "sense.body.redmi10.delivery-gaps",
                                       "kind": "read", "state": "verified",
                                       "reason": "gap-log-current",
                                       "sample": "gaps-24h=2 last-onset=2026-10-16T09:02:52Z"})
    rendered = senses(home)
    assert "VERIFIED sense.body.redmi10.delivery-gaps: gaps-24h=2" in rendered
@pytest.mark.parametrize("pane", [discover, senses])
def test_pane_clips_a_long_sample_at_a_field_boundary_with_a_marker(
        tmp_path: Path, current_endpoint, pane) -> None:
    """A clipped sample must not misstate a field: `all_bad=81` is never `all_bad=8`."""
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.ledger.evidence-binding", "state": "drift", "kind": "read",
         "sample": "bound=1213 latest_roles=10 latest_bad=0 latest_bad_roles=none "
                   "latest_bad_last=none all_bad=81 count_excluded=45 violating=x"},
    ])
    rendered = pane(home)
    assert "all_bad=81 count_excluded=45… source_state=drift" in rendered
    assert "all_bad=8 " not in rendered


@pytest.mark.parametrize("pane", [discover, senses])
def test_pane_keeps_a_comma_list_prefix_when_clipping(
        tmp_path: Path, current_endpoint, pane) -> None:
    """A comma-joined list keeps its leading entries rather than collapsing to none."""
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.runtime.renderer-coverage", "state": "drift", "kind": "read",
         "sample": "renderers=11 drift=" + ",".join(f"mod{i}" for i in range(40))},
    ])
    rendered = pane(home)
    assert "renderers=11 drift=mod0,mod1" in rendered
    assert "… source_state=drift" in rendered


@pytest.mark.parametrize("pane", [discover, senses])
def test_pane_leaves_a_sample_at_the_budget_unmarked(
        tmp_path: Path, current_endpoint, pane) -> None:
    home = tmp_path / "site"
    sample = "x" * 120
    _write_scan(home, [
        {"id": "sense.value", "state": "verified", "kind": "read", "sample": sample},
    ])
    rendered = pane(home)
    assert f"sense.value: {sample} source_state=verified" in rendered


def test_clip_sample_marks_a_cut_and_never_splits_a_field() -> None:
    from mishe_tauftauf.seed_culture_views import _clip_sample
    assert _clip_sample("a=1 " + "b" * 200) == "a=1…"
    assert _clip_sample("c" * 200) == "c" * 120 + "…"
    assert _clip_sample("short") == "short"


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


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("damage", [
    "missing", "syntax", "root", "absent-list", "null-list", "object-list",
    "row", "missing-sample", "id-type", "kind-type", "state-type",
])
def test_rejected_receipt_replaces_prior_success_without_erasing_attempt(
        tmp_path: Path, current_endpoint, pane, damage) -> None:
    snapshot = _write_scan(tmp_path, [
        {"id": "sense.value", "kind": "read", "state": "verified", "sample": 12},
    ], status="failed", error="retained acquisition failure")
    path = tmp_path / "discovery/latest.json"
    if damage == "missing":
        path.unlink()
    elif damage == "syntax":
        path.write_text("{")
    else:
        if damage == "root":
            snapshot = []
        elif damage == "absent-list":
            del snapshot["observations"]
        elif damage == "null-list":
            snapshot["observations"] = None
        elif damage == "object-list":
            snapshot["observations"] = {}
        elif damage == "row":
            snapshot["observations"].append("invalid row")
        elif damage == "missing-sample":
            del snapshot["observations"][0]["sample"]
        else:
            snapshot["observations"][0][damage.removesuffix("-type")] = None
        path.write_text(json.dumps(snapshot))
    report = tmp_path / "observations" / pane.__name__
    report.parent.mkdir()
    report.write_text("PASS controlled predecessor, not acquisition evidence\n")
    attempt = (tmp_path / "discovery/attempt.json").read_bytes()
    rendered = pane(tmp_path)
    assert "STATE: UNKNOWN" in rendered
    assert "retained acquisition failure" in rendered
    assert "VERIFIED sense.value" not in rendered
    assert report.read_text().startswith("UNKNOWN ")
    assert (tmp_path / "discovery/attempt.json").read_bytes() == attempt


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("age", ["recent", "stale"])
def test_valid_drift_retains_source_state_across_freshness(
        tmp_path: Path, current_endpoint, pane, age) -> None:
    _write_scan(tmp_path, [
        {"id": "sense.ledger.evidence-binding", "kind": "read",
         "state": "drift", "sample": "all_bad=63"},
    ])
    if age == "stale":
        current_endpoint["bounds_ns"] = [912_000_000_000, 912_000_000_000]
    rendered = pane(tmp_path)
    display = "DRIFT" if age == "recent" else "UNKNOWN"
    assert f"{display} sense.ledger.evidence-binding: all_bad=63 source_state=drift" in rendered
    assert f"freshness={age}" in rendered
    assert (tmp_path / "observations" / pane.__name__).read_text().startswith("UNKNOWN ")


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
    ("failed", "acquisition", "probe failed"),
    ("failed", "publication", "write failed"),
    ("failed", "notification", "feed append failed"),
])
def test_recorded_attempt_failure_remains_visible_without_downgrading_acquired_source(
        tmp_path: Path, current_endpoint, pane, status, stage, error) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ], status=status, stage=stage, error=error)
    rendered = pane(home)
    assert f"ATTEMPT: {status.upper()}" in rendered
    assert f"stage={stage}" in rendered
    assert error in rendered
    assert "VERIFIED sense.proc.loadavg" in rendered
    assert "freshness=recent" in rendered
    assert "STATE: UNKNOWN — latest scan attempt failed for this sample" in rendered
    assert (home / "observations" / pane.__name__).read_text(encoding="utf-8").startswith("UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
def test_running_attempt_is_in_flight_not_a_failed_read(
        tmp_path: Path, current_endpoint, pane) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ], status="running", stage="acquisition")
    rendered = pane(home)
    assert "ATTEMPT: RUNNING stage=acquisition" in rendered
    assert "VERIFIED sense.proc.loadavg" in rendered
    assert "freshness=recent" in rendered
    assert "latest scan attempt failed" not in rendered
    assert "scan attempt record unavailable" not in rendered
    report = (home / "observations" / pane.__name__).read_text(encoding="utf-8")
    assert "latest attempt failed" not in report
    assert "scan attempt record unavailable" not in report


@pytest.mark.parametrize("pane", [discover, senses])
@pytest.mark.parametrize("marker", ["absent", "unreadable"])
def test_unreadable_or_absent_attempt_record_is_its_own_unknown(
        tmp_path: Path, current_endpoint, pane, marker) -> None:
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ], status=None if marker == "absent" else "succeeded")
    if marker == "unreadable":
        (home / "discovery" / "attempt.json").write_text("{", encoding="utf-8")
    rendered = pane(home)
    assert "VERIFIED sense.proc.loadavg" in rendered
    assert "freshness=recent" in rendered
    assert "STATE: UNKNOWN — scan attempt record unavailable" in rendered
    assert (home / "observations" / pane.__name__).read_text(encoding="utf-8").startswith("UNKNOWN ")


@pytest.mark.parametrize("pane", [discover, senses])
def test_succeeded_attempt_for_a_newer_scan_keeps_the_sample_state(
        tmp_path: Path, current_endpoint, pane) -> None:
    """The pane read the snapshot before that scan published; freshness covers the age."""
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ])
    path = home / "discovery" / "attempt.json"
    attempt = json.loads(path.read_text(encoding="utf-8"))
    attempt["scan_id"] = "another-scan"
    path.write_text(json.dumps(attempt), encoding="utf-8")
    rendered = pane(home)
    assert "sample_match=unknown" in rendered
    assert "VERIFIED sense.proc.loadavg" in rendered
    assert "latest scan attempt failed" not in rendered
    assert "scan attempt record unavailable" not in rendered


def test_scan_in_flight_never_renders_a_failed_attempt(tmp_path: Path, current_endpoint) -> None:
    """Frames rendered while a scan runs must not read as a failed read.

    The pane reads ``latest.json`` and ``attempt.json`` separately, so a scan
    that starts or commits between the two reads used to flip the STATE line
    to "not succeeded" while the published sample was unchanged. A recorded
    failure still flips it, which the next test keeps checking.
    """
    home = tmp_path / "site"
    scan(home)
    stop = threading.Event()

    def scanner() -> None:
        while not stop.is_set():
            scan(home)

    thread = threading.Thread(target=scanner, daemon=True)
    thread.start()
    try:
        frames = [senses(home) for _ in range(15)]
    finally:
        stop.set()
        thread.join(timeout=60)
    assert frames
    for frame in frames:
        assert "not succeeded" not in frame
        assert "latest scan attempt failed" not in frame
        assert "scan attempt record unavailable" not in frame


@pytest.mark.parametrize("pane", [discover, senses])
def test_controlled_scan_failure_still_flips_the_state(tmp_path: Path, pane, monkeypatch) -> None:
    """A scan that raises keeps its failure visible on the pane.

    The pane's freshness gate follows the live endpoint here, so the sample
    from the first scan is recent and the STATE line names the recorded
    failure rather than staleness.
    """
    home = tmp_path / "site"
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.scan_freshness.endpoint",
                        discovery.endpoint)
    scan(home)
    def raise_probe(failed_home: Path):
        raise RuntimeError("probe failed")
    monkeypatch.setattr("mishe_tauftauf.discovery.sample", raise_probe)
    with pytest.raises(RuntimeError):
        scan(home)
    rendered = pane(home)
    assert "freshness=recent" in rendered
    assert "ATTEMPT: FAILED stage=acquisition " in rendered
    assert "error=RuntimeError: probe failed" in rendered
    assert "STATE: UNKNOWN — latest scan attempt failed for this sample" in rendered
    assert (home / "observations" / pane.__name__).read_text(encoding="utf-8").startswith("UNKNOWN ")


def test_in_flight_attempt_does_not_change_the_observation_digest(
        tmp_path: Path, current_endpoint) -> None:
    """The digest tracks meaning: a scan starting must not flip it."""
    home = tmp_path / "site"
    _write_scan(home, [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
    ])
    settled = _observation_text("senses", senses(home))
    path = home / "discovery" / "attempt.json"
    attempt = json.loads(path.read_text(encoding="utf-8"))
    attempt.update(status="running", stage="acquisition")
    path.write_text(json.dumps(attempt), encoding="utf-8")
    assert _observation_text("senses", senses(home)) == settled


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
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
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
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
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

def test_health_falls_back_to_the_recorded_session(tmp_path: Path, monkeypatch) -> None:
    """An unset env session resolves from the site receipt, not UNKNOWN."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text('["steady.service"]', encoding="utf-8")
    (home / ".seed-raised").write_text("test-session $1\n", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\nhealth 1 0 9002\n", "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(
                argv, 0, "Id=steady.service\nActiveState=active\nSubState=running\nNRestarts=0\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "session unset" not in rendered
    assert "WINDOWS: PASS health" in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith(
        "PASS health internal checks at ")

def test_health_reports_unknown_when_the_pane_read_times_out(tmp_path: Path, monkeypatch) -> None:
    """A timed-out tmux read is no observation; it must not claim every window missing."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            raise subprocess.TimeoutExpired(argv, 2)
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "WINDOWS: UNKNOWN — pane read failed" in rendered
    assert "STATE: UNKNOWN — pane read failed" in rendered
    assert "windows-missing" not in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith(
        "UNKNOWN health pane read failed at ")


def test_health_reports_unknown_when_the_pane_read_fails(tmp_path: Path, monkeypatch) -> None:
    """A non-zero tmux exit is a failed read, not a claim that the session is empty."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 1, "", "can't find session: test-session\n")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "WINDOWS: UNKNOWN — pane read failed" in rendered
    assert "STATE: UNKNOWN — pane read failed" in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith(
        "UNKNOWN health pane read failed at ")


def test_health_keeps_a_real_window_mismatch_red(tmp_path: Path, monkeypatch) -> None:
    """A readable pane list that is missing a manifest window stays RED."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health", "docs"]), encoding="utf-8")
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    rendered = health(home)
    assert "WINDOWS: RED health missing=docs" in rendered
    assert "STATE: RED" in rendered
    assert "windows-missing=docs" in (home / "observations" / "health").read_text(encoding="utf-8")


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
    monkeypatch.setattr("mishe_tauftauf.proc_table.read_pane_pythonpath",
                        lambda _pid: [f"{pinned}/src"])
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


def _pinned_pane_site(tmp_path: Path, monkeypatch, panes: dict[str, str | None]) -> Path:
    """A pinned site whose pane pids resolve to the given roots.

    ``panes`` maps each fake pane pid to the PYTHONPATH a live process would
    name; ``None`` is a pid whose environment reads empty, the mind-window
    UNKNOWN branch. Both windows ship a mind launcher so a rootless pane is
    missing evidence rather than a plain shell.
    """
    home = _service_home(tmp_path, ["steady.service"])
    (home / "health" / "windows.json").write_text('["health", "peer"]', encoding="utf-8")
    (home / "health" / "runtime-release.json").write_text("{}", encoding="utf-8")
    (home / "minds").mkdir(parents=True, exist_ok=True)
    for window in ("health", "peer"):
        (home / "minds" / window).write_text("#!/bin/sh\n", encoding="utf-8")
    pinned = tmp_path / "pinned"
    stale = home / "releases" / "94092ecd9b6a83c72d09b851f7cce3b4a53bf0a0"
    stale.mkdir(parents=True)
    readings = {"9001": f"{pinned}/src", "9002": f"{pinned}/src",
                "9003": f"{pinned}/src", **panes}
    monkeypatch.setattr("mishe_tauftauf.runtime_source.source_for", lambda h, default: pinned)
    monkeypatch.setattr("mishe_tauftauf.proc_table.read_pane_pythonpath",
                        lambda pid: [readings[pid]] if readings.get(pid) else [])
    monkeypatch.setattr("mishe_tauftauf.proc_table.read_pane_cmdline",
                        lambda pid: f"/venv/bin/python -m mishe_tauftauf pain watch {pid}")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            rows = ["health 0 0 9001\nhealth 1 0 9002\npeer 0 0 9003\npeer 1 0 9004\n"]
            return subprocess.CompletedProcess(argv, 0, "".join(rows), "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 0, "Id=steady.service\nActiveState=active\n"
                                             "SubState=running\nNRestarts=0\nEnvironment=\n\n", "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")
    return home


def test_health_passes_pane_roots_on_the_pin(tmp_path: Path, monkeypatch) -> None:
    """A pane whose environment names the pin reads on-pin, and no fault folds."""
    home = _pinned_pane_site(tmp_path, monkeypatch, {"9004": f"{tmp_path}/pinned/src"})
    rendered = health(home)
    assert "PANE ROOTS: PASS pin=pinned on-pin=2" in rendered
    assert "STATE: GREEN" in rendered


def test_health_names_a_pane_root_that_missed_a_promotion_as_stale(tmp_path: Path, monkeypatch) -> None:
    """A pane still importing a superseded release stays visible without a RED:
    rotating onto the pin is that mind's own wake, not a state fault."""
    home = _pinned_pane_site(
        tmp_path, monkeypatch,
        {"9004": str(tmp_path / "site" / "releases"
                     / "94092ecd9b6a83c72d09b851f7cce3b4a53bf0a0" / "src")})
    rendered = health(home)
    assert "PANE ROOTS: STALE pin=pinned on-pin=1 stale=peer:94092ecd9b6a" in rendered
    assert "STATE: GREEN" in rendered
    assert "pane-roots-drift" not in (home / "observations" / "health").read_text(encoding="utf-8")


def test_health_flags_a_pane_root_off_the_release_tree_as_drift(tmp_path: Path, monkeypatch) -> None:
    """A pane importing the checkout or a foreign root is drift, named in the report."""
    home = _pinned_pane_site(tmp_path, monkeypatch, {"9004": f"{tmp_path}/src"})
    rendered = health(home)
    assert f"PANE ROOTS: DRIFT pin=pinned on-pin=1 drift=peer:{tmp_path}" in rendered
    assert "STATE: RED" in rendered
    assert f"pane-roots-drift=peer:{tmp_path}" in (home / "observations" / "health").read_text(encoding="utf-8")


def test_health_reports_an_unreadable_pane_environment_as_unknown(tmp_path: Path, monkeypatch) -> None:
    """A live mind pane whose environment reads no root is missing evidence, not PASS."""
    home = _pinned_pane_site(tmp_path, monkeypatch, {"9004": None})
    rendered = health(home)
    assert "PANE ROOTS: UNKNOWN — pane environments unreadable unread=peer" in rendered
    assert "STATE: UNKNOWN — pane root data unavailable" in rendered
    assert (home / "observations" / "health").read_text(encoding="utf-8").startswith(
        "UNKNOWN health pane root data unavailable at ")

def test_health_excludes_a_plain_shell_that_carries_a_pythonpath(tmp_path: Path, monkeypatch) -> None:
    """A shell pane with a stale PYTHONPATH is no mind: no root to compare.

    The permissions window's bottom pane is an interactive bash whose environ
    still names a superseded release; counting it keeps PANE ROOTS STALE
    forever. The pane's process, not its environment, decides.
    """
    home = _pinned_pane_site(
        tmp_path, monkeypatch,
        {"9004": str(tmp_path / "site" / "releases"
                     / "94092ecd9b6a83c72d09b851f7cce3b4a53bf0a0" / "src")})
    monkeypatch.setattr(
        "mishe_tauftauf.proc_table.read_pane_cmdline",
        lambda pid: ("bash --noprofile --norc -i" if pid == "9004"
                      else "/venv/bin/python -m mishe_tauftauf pain watch peer"))
    rendered = health(home)
    assert "PANE ROOTS: PASS pin=pinned on-pin=2" in rendered
    assert "STATE: GREEN" in rendered


def _camera_site(tmp_path: Path, monkeypatch, *, state, artifact_age_s, events, journal=(), journal_rc=0):
    """A camera node: mesh artifacts under HOME/.mesh, camera service active."""
    home = _service_home(tmp_path, ["mesh-cam-watch.service"])
    (home / "health" / "camera.json").write_text('{"version": 1}', encoding="utf-8")
    mesh = tmp_path / ".mesh"
    mesh.mkdir(parents=True, exist_ok=True)
    if state is not None:
        (mesh / ".cam-watch.state").write_text(state + "\n", encoding="utf-8")
    if artifact_age_s is not None:
        artifact = mesh / "cam-prev.jpg"
        artifact.write_bytes(b"jpeg")
        stamp = time.time() - artifact_age_s
        os.utime(artifact, (stamp, stamp))
    if events is not None:
        (mesh / "cam-events.log").write_text("".join(events), encoding="utf-8")

    def run(argv, **_kwargs):
        if argv[0] == "tmux":
            return subprocess.CompletedProcess(argv, 0, "health 0 0 9001\n", "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 0, "Id=mesh-cam-watch.service\n"
                                             "ActiveState=active\nSubState=running\n"
                                             "NRestarts=0\nEnvironment=\n\n", "")
        if argv[0] == "journalctl":
            body = "".join(row + "\n" for row in journal)
            return subprocess.CompletedProcess(argv, journal_rc, body, "")
        return subprocess.CompletedProcess(argv, 0, "PASS doctor\n", "")

    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run", run)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")
    monkeypatch.setenv("HOME", str(tmp_path))
    return home


def _cam_onset(seconds_ago: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ") + "  cam state SHUTTERED→CAPTURE-FAIL (dev=/dev/video0)\n"


def test_health_passes_a_fresh_camera_node(tmp_path: Path, monkeypatch) -> None:
    home = _camera_site(tmp_path, monkeypatch, state="SEEING|diff=1.2|mean=56|",
                        artifact_age_s=30, events=(_cam_onset(1000),))
    rendered = health(home)
    assert ("CAMERA: PASS class=SEEING artifact-age=30s capture-fails=0/900s "
            "kernel-urbs=0/900s") in rendered
    assert "STATE: GREEN" in rendered


def test_health_skips_the_camera_check_on_a_node_without_one(tmp_path: Path, monkeypatch) -> None:
    home = _service_home(tmp_path, ["mesh-cam-watch.service"])
    monkeypatch.setenv("HOME", str(tmp_path))
    _fake_systemctl(monkeypatch, "Id=mesh-cam-watch.service\nActiveState=active\n"
                                 "SubState=running\nNRestarts=0\nEnvironment=\n\n")
    rendered = health(home)
    assert "CAMERA:" not in rendered
    assert "STATE: GREEN" in rendered


def test_health_skips_the_camera_check_without_a_declared_camera(tmp_path: Path, monkeypatch) -> None:
    """A site that does not declare a camera never reads this node's ~/.mesh.

    Live mesh artifacts exist but no health/camera.json declaration does, so the
    check must not run: an undeclared plant cannot inherit a neighbor's camera
    fault surface, and unmodified health tests stay isolated from the real node.
    """
    home = _service_home(tmp_path, ["mesh-cam-watch.service"])
    mesh = tmp_path / ".mesh"
    mesh.mkdir(parents=True)
    (mesh / ".cam-watch.state").write_text("SEEING|diff=1.0|mean=50|\n", encoding="utf-8")
    (mesh / "cam-prev.jpg").write_bytes(b"jpeg")
    (mesh / "cam-events.log").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    _fake_systemctl(monkeypatch, "Id=mesh-cam-watch.service\nActiveState=active\n"
                                 "SubState=running\nNRestarts=0\nEnvironment=\n\n")
    rendered = health(home)
    assert "CAMERA:" not in rendered
    assert "STATE: GREEN" in rendered


def test_health_flags_a_stale_camera_artifact_as_red(tmp_path: Path, monkeypatch) -> None:
    """A cam-prev.jpg older than the daemon's declared 120s bound is a dead watch."""
    home = _camera_site(tmp_path, monkeypatch, state="SHUTTERED|diff=1.0|mean=18|",
                        artifact_age_s=400, events=())
    rendered = health(home)
    assert "CAMERA: RED class=SHUTTERED artifact-age=400s" in rendered
    assert "STATE: RED" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "camera-artifact-stale" in report


def test_health_flags_repeated_camera_capture_failures_as_red(tmp_path: Path, monkeypatch) -> None:
    """Three CAPTURE-FAIL onsets inside 15 minutes are a fault, not a self-healed cycle."""
    events = [_cam_onset(120), _cam_onset(600), _cam_onset(800)]
    home = _camera_site(tmp_path, monkeypatch, state="SHUTTERED|diff=1.0|mean=18|",
                        artifact_age_s=5, events=events)
    rendered = health(home)
    assert "CAMERA: RED class=SHUTTERED artifact-age=5s capture-fails=3/900s" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "camera-capture-fail=3" in report


def test_health_flags_camera_kernel_resubmit_failures_as_red(tmp_path: Path, monkeypatch) -> None:
    kernel = ["kernel: uvcvideo 1-6:1.1: Failed to resubmit video URB (-1)."] * 11
    home = _camera_site(tmp_path, monkeypatch, state="SEEING|diff=1.2|mean=56|",
                        artifact_age_s=5, events=(_cam_onset(1000),), journal=kernel)
    rendered = health(home)
    assert "CAMERA: RED class=SEEING" in rendered and "kernel-urbs=11/900s" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "camera-kernel-urb=11" in report


def test_health_reports_an_unreadable_camera_source_as_unknown(tmp_path: Path, monkeypatch) -> None:
    """Broken monitoring stays UNKNOWN, and a non-zero journalctl read names it."""
    home = _camera_site(tmp_path, monkeypatch, state=None, artifact_age_s=5, events=(), journal_rc=1)
    rendered = health(home)
    assert "CAMERA: UNKNOWN" in rendered and "camera state unreadable" in rendered
    assert "STATE: UNKNOWN — camera state unavailable" in rendered
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("UNKNOWN health camera state unavailable at ")


def test_health_passes_a_camera_node_below_the_fault_thresholds(tmp_path: Path, monkeypatch) -> None:
    """Two recent onsets and nine kernel errors stay below the recorded bounds."""
    events = [_cam_onset(60), _cam_onset(600), _cam_onset(1000)]
    kernel = ["kernel: uvcvideo 1-6:1.1: Failed to resubmit video URB (-1)."] * 9
    home = _camera_site(tmp_path, monkeypatch, state="SEEING|diff=1.2|mean=56|",
                        artifact_age_s=119, events=events, journal=kernel)
    rendered = health(home)
    assert ("CAMERA: PASS class=SEEING artifact-age=119s capture-fails=2/900s "
            "kernel-urbs=9/900s") in rendered
    assert "STATE: GREEN" in rendered


def test_health_names_an_unowned_server_keeper_as_a_cause(tmp_path: Path, monkeypatch) -> None:
    """A server held by a foreign keeper must route to a resolver.

    A live, shielded session with a finished test's keeper currently reads
    verified on session-stability, so the drift has to be named here or it
    never reaches a mind before something stops the keeper.
    """
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text(json.dumps([]), encoding="utf-8")
    (home / "discovery").mkdir(parents=True)
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run",
                        lambda argv, **_kw: subprocess.CompletedProcess(
                            argv, 0, "health 0 0 9001\n", "") if argv[0] == "tmux"
                        else subprocess.CompletedProcess(argv, 0, "PASS doctor\n", ""))
    keeper = {"id": "sense.host.server-keeper", "state": "drift",
              "sample": "session=test-session server-pid=11 foreign-keeper=x-session-1.service"}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.latest",
                        lambda _home: {"observations": [keeper]})
    health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert report.startswith("FAIL health internal check")
    assert "server-keeper=" in report
    assert "test-session server-pid=11" in report
    assert "STATE: RED" in health(home)


def test_health_keeps_a_plant_owned_server_keeper_clean(tmp_path: Path, monkeypatch) -> None:
    """The plant's own keeper must not add a RED cause of its own."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text(json.dumps([]), encoding="utf-8")
    (home / "discovery").mkdir(parents=True)
    monkeypatch.setenv("MISHE_SEED_SESSION", "test-session")
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.ci_line", lambda _home: "CI: PASS")
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.subprocess.run",
                        lambda argv, **_kw: subprocess.CompletedProcess(
                            argv, 0, "health 0 0 9001\n", "") if argv[0] == "tmux"
                        else subprocess.CompletedProcess(argv, 0, "PASS doctor\n", ""))
    keeper = {"id": "sense.host.server-keeper", "state": "verified",
              "sample": "session=test-session server-pid=11 keeper=test-session-session-1.service"}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.latest",
                        lambda _home: {"observations": [keeper]})
    health(home)
    assert "server-keeper" not in (home / "observations" / "health").read_text(encoding="utf-8")


def _redmi_monitor_home(tmp_path: Path) -> Path:
    home = _service_home(tmp_path, ["steady.service"])
    android_home = tmp_path / "android-body" / ".mishe-tauftauf"
    (android_home / "health").mkdir(parents=True)
    (android_home / "health" / "services.json").write_text("[]", encoding="utf-8")
    registry = {"version": 1, "sites": [{"session": "android-body", "home": str(android_home)}]}
    (home / "health" / "linked-sites.json").write_text(json.dumps(registry), encoding="utf-8")
    return home


@pytest.mark.parametrize(("reason", "online", "active", "expected"), [
    ("consumer-poll-stale", False, True, "RED"),
    ("channel-not-current", True, False, "RED"),
    ("consumer-poll-stale", False, False, "UNKNOWN"),
])
def test_health_routes_redmi_loss_using_online_or_active_peer(
        tmp_path: Path, monkeypatch, reason: str, online: bool, active: bool,
        expected: str) -> None:
    home = _redmi_monitor_home(tmp_path)
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\nSubState=running\nNRestarts=0\n")
    channels = {kind: "stale" for kind in ("battery", "light", "acceleration")}
    reading = {"id": "sense.body.redmi10", "state": "unknown",
               "reason": reason, "channels": channels}
    peer_status = {"id": "sense.net.tailnet-peer", "state": "verified",
                   "peers": {"Redmi 10": {"online": online, "active": active}}}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10", lambda _home: reading)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._tailnet_peer_read",
                        lambda: peer_status)

    rendered = health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    if expected == "RED":
        assert "REDMI10 MONITOR: RED poll-stale-live-path" in rendered
        assert report.startswith("FAIL health internal check")
        assert "redmi10-poll-stale-live-path" in report
        assert "STATE: RED" in rendered
    else:
        assert "REDMI10 MONITOR: UNKNOWN poll-stale-peer-not-live" in rendered
        assert report.startswith("UNKNOWN health Redmi10 monitor poll-stale-peer-not-live")
        assert "STATE: UNKNOWN — Redmi10 monitor poll-stale-peer-not-live" in rendered


def test_health_keeps_redmi_monitor_unknown_when_tailnet_is_unreadable(
        tmp_path: Path, monkeypatch) -> None:
    home = _redmi_monitor_home(tmp_path)
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\nSubState=running\nNRestarts=0\n")
    reading = {"id": "sense.body.redmi10", "state": "unknown",
               "reason": "consumer-poll-stale"}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10", lambda _home: reading)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._tailnet_peer_read",
                        lambda: {"id": "sense.net.tailnet-peer", "state": "unknown", "peers": {}})

    rendered = health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "REDMI10 MONITOR: UNKNOWN tailnet-state-unavailable" in rendered
    assert report.startswith("UNKNOWN health Redmi10 monitor tailnet-state-unavailable")


def test_health_keeps_fresh_redmi_data_green_without_tailnet_read(
        tmp_path: Path, monkeypatch) -> None:
    home = _redmi_monitor_home(tmp_path)
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\nSubState=running\nNRestarts=0\n")
    reading = {"id": "sense.body.redmi10", "state": "verified",
               "channels": {kind: "fresh-clock-conditional"
                            for kind in ("battery", "light", "acceleration")}}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10", lambda _home: reading)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._tailnet_peer_read",
                        lambda: (_ for _ in ()).throw(AssertionError("fresh data does not need tailnet")))

    rendered = health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "REDMI10 MONITOR: VERIFIED consumer-current" in rendered
    assert report.startswith("PASS health internal checks at ")
    assert "STATE: GREEN" in rendered


def test_health_keeps_redmi_monitor_unknown_when_reader_evidence_is_missing(
        tmp_path: Path, monkeypatch) -> None:
    home = _redmi_monitor_home(tmp_path)
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\nSubState=running\nNRestarts=0\n")
    reading = {"id": "sense.body.redmi10", "state": "unknown",
               "reason": "consumer-state-missing"}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10", lambda _home: reading)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._tailnet_peer_read",
                        lambda: (_ for _ in ()).throw(AssertionError("missing reader does not prove loss")))

    rendered = health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "REDMI10 MONITOR: UNKNOWN consumer-not-current" in rendered
    assert report.startswith("UNKNOWN health Redmi10 monitor consumer-not-current")


def test_health_keeps_a_partial_redmi_channel_stale_as_unknown(
        tmp_path: Path, monkeypatch) -> None:
    home = _redmi_monitor_home(tmp_path)
    _fake_systemctl(monkeypatch, "Id=steady.service\nActiveState=active\nSubState=running\nNRestarts=0\n")
    reading = {"id": "sense.body.redmi10", "state": "unknown",
               "reason": "channel-not-current",
               "channels": {"battery": "stale", "light": "fresh-clock-conditional",
                            "acceleration": "fresh-clock-conditional"}}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.read_redmi10", lambda _home: reading)
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views._tailnet_peer_read",
                        lambda: (_ for _ in ()).throw(AssertionError("partial channel loss is not whole-body loss")))

    rendered = health(home)
    report = (home / "observations" / "health").read_text(encoding="utf-8")
    assert "REDMI10 MONITOR: UNKNOWN consumer-not-current" in rendered
    assert report.startswith("UNKNOWN health Redmi10 monitor consumer-not-current")
