from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
