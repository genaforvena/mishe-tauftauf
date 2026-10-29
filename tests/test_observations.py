from __future__ import annotations

from pathlib import Path

from mishe_tauftauf.observations import run_renderer

from mishe_tauftauf.observations import compose_frame

from mishe_tauftauf.observations import reserved_home_name


def test_reserved_home_name_matches_only_the_reserved_prefix() -> None:
    assert reserved_home_name(".mishe-tauftauf")
    assert reserved_home_name(".mishe-")
    assert not reserved_home_name("mishe-tauftauf")
    assert not reserved_home_name(".mishe")
    assert not reserved_home_name(".mesh")


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


def test_shared_admission_outer_deadline_covers_child_without_timeout_retry(tmp_path, monkeypatch):
    import json
    from mishe_tauftauf.observations import RenderedPain
    (tmp_path / "recovery").mkdir()
    (tmp_path / "recovery/config.json").write_text(json.dumps({"host_root": str(tmp_path / "shared")}))
    captured = []
    def render(home, slug, timeout, **kwargs):
        captured.append((timeout, kwargs["retries"]))
        return RenderedPain(slug, "Observed fixture.\n", True)
    monkeypatch.setattr("mishe_tauftauf.observations.run_renderer", render)
    compose_frame(tmp_path, "health")
    assert captured[-1] == (35., 0)
    compose_frame(tmp_path, "witness", timeout=2.)
    assert captured[-1][0] == 2.

def test_renderer_deferred_preserves_historical_cache_as_not_fresh(tmp_path: Path) -> None:
    """Deferred render stays UNKNOWN but body contains cached historical stdout."""
    import json

    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    # Renderer exits 1 with deferred prefix on stderr; its own stdout is untrusted.
    renderer.write_text(
        "#!/bin/sh\n"
        "echo 'evil stdout that must not be trusted' \n"
        "echo 'UNKNOWN \u2014 render deferred: all render slots occupied; controller=LIMITED; cached_at=1 age=2; retained content is not a fresh observation' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    cache_dir = tmp_path / "recovery" / "render-cache"
    cache_dir.mkdir(parents=True)
    cache_dir.joinpath("probe.json").write_text(
        json.dumps({"at": 1, "receipt": {"ok": True, "stdout": "HISTORICAL BODY\nline2\n", "stderr": "", "code": 0}}),
        encoding="utf-8",
    )
    result = run_renderer(tmp_path, "probe")
    assert not result.ok
    assert "render deferred" in (result.reason or "")
    assert result.body.startswith("UNKNOWN \u2014 top-pain probe renderer exit-1: UNKNOWN \u2014 render deferred:")
    assert "HISTORICAL BODY" in result.body
    assert "line2" in result.body
    assert "evil stdout" not in result.body
    assert "retained content is not a fresh observation" in result.body


def test_renderer_success_remains_ok_without_unknown_prefix(tmp_path: Path) -> None:
    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    renderer.write_text("#!/bin/sh\nprintf 'fresh output\\n'\n", encoding="utf-8")
    renderer.chmod(0o755)
    result = run_renderer(tmp_path, "probe")
    assert result.ok
    assert result.reason is None
    assert result.body == "fresh output\n"
    assert not result.body.startswith("UNKNOWN")


def test_renderer_real_failure_does_not_invent_historical_body(tmp_path: Path) -> None:
    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    renderer.write_text("#!/bin/sh\necho 'fail detail' >&2; exit 1\n", encoding="utf-8")
    renderer.chmod(0o755)
    result = run_renderer(tmp_path, "probe")
    assert not result.ok
    assert result.reason == "exit-1: fail detail"
    assert result.body == "UNKNOWN \u2014 top-pain probe renderer exit-1: fail detail\n"
    assert "HISTORICAL" not in result.body


def test_renderer_forged_deferred_without_cache_stays_unknown_only(tmp_path: Path) -> None:
    """A renderer that fakes the deferred prefix without a cache must not get a historical body."""
    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    renderer.write_text(
        "#!/bin/sh\n"
        "echo 'UNKNOWN \u2014 render deferred: fake' >&2; echo 'evil'; exit 1\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    result = run_renderer(tmp_path, "probe")
    assert not result.ok
    assert "render deferred" in (result.reason or "")
    assert result.body == "UNKNOWN \u2014 top-pain probe renderer exit-1: UNKNOWN \u2014 render deferred: fake\n"
    assert "evil" not in result.body


def test_renderer_deferred_bounds_historical_output(tmp_path: Path) -> None:
    """Historical body is truncated to 1 MiB even if cache stdout is larger."""
    import json

    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    renderer.write_text(
        "#!/bin/sh\n"
        "echo 'UNKNOWN \u2014 render deferred: pressure cooldown; controller=LIMITED; cached_at=1 age=2; retained content is not a fresh observation' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    big = "x" * (1024 * 1024 + 5000)
    cache_dir = tmp_path / "recovery" / "render-cache"
    cache_dir.mkdir(parents=True)
    cache_dir.joinpath("probe.json").write_text(
        json.dumps({"at": 1, "receipt": {"ok": True, "stdout": big, "stderr": "", "code": 0}}),
        encoding="utf-8",
    )
    result = run_renderer(tmp_path, "probe")
    assert not result.ok
    # Body = UNKNOWN line + truncated 1 MiB (plus possible newline)
    assert len(result.body) <= 1024 * 1024 + 500
    assert "x" in result.body


def test_renderer_deferred_ignores_non_ok_cache(tmp_path: Path) -> None:
    """Deferred with a non-ok cached receipt must not append its stdout."""
    import json

    top = tmp_path / "top-pains"
    top.mkdir()
    renderer = top / "probe"
    renderer.write_text(
        "#!/bin/sh\n"
        "echo 'UNKNOWN \u2014 render deferred: all render slots occupied; controller=LIMITED; cached_at=1 age=2; retained content is not a fresh observation' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    cache_dir = tmp_path / "recovery" / "render-cache"
    cache_dir.mkdir(parents=True)
    cache_dir.joinpath("probe.json").write_text(
        json.dumps({"at": 1, "receipt": {"ok": False, "stdout": "should not appear", "stderr": "", "code": 1}}),
        encoding="utf-8",
    )
    result = run_renderer(tmp_path, "probe")
    assert not result.ok
    assert "should not appear" not in result.body

