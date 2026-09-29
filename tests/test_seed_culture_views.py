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
