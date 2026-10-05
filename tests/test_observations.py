from __future__ import annotations

from pathlib import Path

from mishe_tauftauf.observations import run_renderer


def test_renderer_preserves_or_injects_recorded_session(tmp_path: Path, monkeypatch) -> None:
    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    renderer.write_text("#!/bin/sh\nprintf '%s' \"${MISHE_SEED_SESSION-unset}\"\n", encoding="utf-8")
    renderer.chmod(0o755)
    (tmp_path / ".seed-raised").write_text("recorded-session extra-data\n", encoding="utf-8")

    monkeypatch.setenv("MISHE_SEED_SESSION", "caller-session")
    assert run_renderer(tmp_path, "probe").body == "caller-session"

    monkeypatch.delenv("MISHE_SEED_SESSION")
    assert run_renderer(tmp_path, "probe").body == "recorded-session"
    monkeypatch.setenv("MISHE_SEED_SESSION", "")
    assert run_renderer(tmp_path, "probe").body == "recorded-session"

    monkeypatch.delenv("MISHE_SEED_SESSION")
    (tmp_path / ".seed-raised").unlink()
    assert run_renderer(tmp_path, "probe").body == "unset"


def test_renderer_does_not_invent_session_when_record_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    renderer.write_text("#!/bin/sh\nprintf '%s' \"${MISHE_SEED_SESSION-unset}\"\n", encoding="utf-8")
    renderer.chmod(0o755)
    (tmp_path / ".seed-raised").write_bytes(b"\xff\xfe")
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    assert run_renderer(tmp_path, "probe").body == "unset"
