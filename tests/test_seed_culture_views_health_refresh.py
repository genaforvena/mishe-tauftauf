from __future__ import annotations

import fcntl
import os
from pathlib import Path

from mishe_tauftauf import seed_culture_views
from mishe_tauftauf.seed_culture_views import _report, refresh_health


def test_report_replaces_atomically_and_leaves_no_temp(tmp_path: Path) -> None:
    home = tmp_path / "site"
    _report(home, "health", "PASS first")
    _report(home, "health", "PASS second")
    assert (home / "observations" / "health").read_text(encoding="utf-8") == "PASS second\n"
    assert sorted(p.name for p in (home / "observations").iterdir()) == ["health"]


def test_failed_write_keeps_last_good_report(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "site"
    _report(home, "health", "PASS good")
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated kill before publish")

    monkeypatch.setattr(seed_culture_views.os, "replace", boom)
    try:
        _report(home, "health", "PASS new")
    except OSError:
        pass
    monkeypatch.setattr(seed_culture_views.os, "replace", real_replace)
    assert (home / "observations" / "health").read_text(encoding="utf-8") == "PASS good\n"
    assert sorted(p.name for p in (home / "observations").iterdir()) == ["health"]


def test_refresh_is_single_flight(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "site"
    calls: list[Path] = []
    monkeypatch.setattr(seed_culture_views, "health", lambda h: calls.append(h) or "")
    lock = home / "observations" / seed_culture_views.HEALTH_REFRESH_LOCK
    lock.parent.mkdir(parents=True)
    with lock.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert refresh_health(home) == "SKIPPED busy"
    assert calls == []
    assert refresh_health(home) == "REFRESHED"
    assert calls == [home]


def test_refresh_cli_publishes_report_and_prints_outcome(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "site"
    monkeypatch.setattr(seed_culture_views, "health", lambda h: (_report(h, "health", "PASS cli"), "")[1])
    monkeypatch.setattr("sys.argv", ["seed_culture_views", "--home", str(home), "--view", "health",
                                     "--refresh-report"])
    seed_culture_views.main()
    assert capsys.readouterr().out == "REFRESHED\n"
    assert (home / "observations" / "health").read_text(encoding="utf-8") == "PASS cli\n"
