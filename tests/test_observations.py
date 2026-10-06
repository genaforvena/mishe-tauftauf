from __future__ import annotations

from pathlib import Path

from mishe_tauftauf.observations import run_renderer

from mishe_tauftauf.observations import compose_frame


def test_compose_frame_role_aware_budget(tmp_path: Path, monkeypatch) -> None:
    """compose_frame with timeout=None uses the role-aware budget."""
    captured = []
    original = run_renderer

    def mock_run_renderer(home, slug, timeout=10.0, **kwargs):
        captured.append((slug, timeout))
        return original(home, slug, timeout)

    monkeypatch.setattr("mishe_tauftauf.observations.run_renderer", mock_run_renderer)

    # Witness gets 20s (heavy renderer under load)
    compose_frame(tmp_path, "witness")
    assert captured[-1] == ("witness", 20.0)

    # Other roles get the 10s default
    compose_frame(tmp_path, "health")
    assert captured[-1] == ("health", 10.0)

    # Explicit timeout overrides the role-aware budget
    compose_frame(tmp_path, "witness", timeout=5.0)
    assert captured[-1] == ("witness", 5.0)


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

def test_renderer_retries_once_on_timeout(tmp_path: Path, monkeypatch) -> None:
    """run_renderer with retries=1 re-runs after a TimeoutExpired."""
    top = tmp_path / "top-pains"
    top.mkdir()
    marker = tmp_path / "invocations"
    renderer = top / "probe"
    # First call: sleep past the timeout. Second call: succeed.
    renderer.write_text(
        "#!/bin/sh\n"
        f"n=$(cat {marker} 2>/dev/null || echo 0)\n"
        f"echo $((n + 1)) > {marker}\n"
        "if [ \"$n\" -eq 0 ]; then sleep 5; else printf 'ok-after-retry'; fi\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    (tmp_path / ".seed-raised").unlink(missing_ok=True)

    result = run_renderer(tmp_path, "probe", timeout=0.5, retries=1, retry_backoff=0.1)
    assert result.ok
    assert result.body == "ok-after-retry"
    assert marker.read_text().strip() == "2"


def test_renderer_retries_exhausted_returns_timeout(tmp_path: Path, monkeypatch) -> None:
    """run_renderer with retries=1 returns timeout when both attempts expire."""
    top = tmp_path / "top-pains"
    top.mkdir()
    marker = tmp_path / "invocations"
    renderer = top / "probe"
    renderer.write_text(
        "#!/bin/sh\n"
        f"n=$(cat {marker} 2>/dev/null || echo 0)\n"
        f"echo $((n + 1)) > {marker}\n"
        "sleep 5\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    (tmp_path / ".seed-raised").unlink(missing_ok=True)

    result = run_renderer(tmp_path, "probe", timeout=0.5, retries=1, retry_backoff=0.1)
    assert not result.ok
    assert result.reason == "timeout-after-0.5s"
    assert marker.read_text().strip() == "2"


def test_renderer_no_retry_by_default(tmp_path: Path, monkeypatch) -> None:
    """run_renderer with default retries=0 does not re-run after timeout."""
    top = tmp_path / "top-pains"
    top.mkdir()
    marker = tmp_path / "invocations"
    renderer = top / "probe"
    renderer.write_text(
        "#!/bin/sh\n"
        f"n=$(cat {marker} 2>/dev/null || echo 0)\n"
        f"echo $((n + 1)) > {marker}\n"
        "sleep 5\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    (tmp_path / ".seed-raised").unlink(missing_ok=True)

    result = run_renderer(tmp_path, "probe", timeout=0.5)
    assert not result.ok
    assert result.reason == "timeout-after-0.5s"
    assert marker.read_text().strip() == "1"
