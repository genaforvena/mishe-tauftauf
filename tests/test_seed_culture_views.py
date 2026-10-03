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
    assert "age=" not in sense
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
    assert (home / "observations" / "health").read_text(encoding="utf-8") == "FAIL health internal check\n"


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
    assert (home / "observations" / "health").read_text(encoding="utf-8") == (
        "UNKNOWN health linked-site data unavailable\n")


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
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
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


def test_senses_reports_absent_source_as_unavailable_not_unchecked(tmp_path: Path) -> None:
    home = tmp_path / "site"
    snapshot = {"created": _now_iso(), "node": "node", "observations": [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "1.23", "kind": "read"},
        {"id": "sense.input.keyboard-interrupt-count", "state": "unavailable",
         "sample": "no keyboard interrupt source on this host", "kind": "counter"},
    ]}
    (home / "discovery").mkdir(parents=True)
    (home / "discovery" / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    rendered = senses(home)
    assert "UNAVAILABLE sense.input.keyboard-interrupt-count" in rendered
    assert "STATE: GREEN — all wired sample reads verified" in rendered
    assert "UNAVAILABLE senses 1 named an absent source, not a failed read" in rendered
    assert (home / "observations" / "senses").read_text(encoding="utf-8") == "PASS senses 2 verified samples\n"


def test_senses_labels_cpu_busy_as_short_window_evidence(tmp_path: Path) -> None:
    home = tmp_path / "site"
    snapshot = {"created": _now_iso(), "node": "node", "observations": [
        {"id": "sense.proc.loadavg", "state": "verified", "sample": "37.92 39.70 40.09", "kind": "read"},
        {"id": "sense.proc.cpu-busy", "state": "verified",
         "sample": "short-window=0.1s busy=99.9% idle=0.1% high", "kind": "read"},
    ]}
    (home / "discovery").mkdir(parents=True)
    (home / "discovery" / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    rendered = senses(home)
    assert "VERIFIED sense.proc.cpu-busy: short-window=0.1s busy=99.9% idle=0.1% high" in rendered
    assert "STATE: GREEN — all wired sample reads verified" in rendered
    assert "SUSTAINED" not in rendered
    assert (home / "observations" / "senses").read_text(encoding="utf-8") == "PASS senses 2 verified samples\n"


def test_senses_still_counts_an_unreadable_counter_as_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    snapshot = {"created": _now_iso(), "node": "node", "observations": [
        {"id": "sense.input.keyboard-interrupt-count", "state": "unknown",
         "sample": "counter unreadable; /proc/interrupts unavailable", "kind": "counter"},
    ]}
    (home / "discovery").mkdir(parents=True)
    (home / "discovery" / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    rendered = senses(home)
    assert "UNKNOWN sense.input.keyboard-interrupt-count" in rendered
    assert "STATE: UNKNOWN — 1 senses need a checked read or honest unavailable claim" in rendered
    assert "UNAVAILABLE senses" not in rendered
    assert (home / "observations" / "senses").read_text(encoding="utf-8") == "UNKNOWN senses 1 unverified or stale\n"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
    assert (home / "observations" / "health").read_text(encoding="utf-8") == (
        "UNKNOWN health local service data unavailable\n")


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
