from __future__ import annotations

import json
import os
import subprocess
import sys
import pytest
from pathlib import Path

from coordination import launcher

def test_launcher_wires_coordinator_only_for_core_checkout(tmp_path: Path, monkeypatch) -> None:
    kernel = tmp_path / "kernel"
    core_home = kernel / ".mishe-seed"
    example = tmp_path / "example"
    target_home = example / ".mishe-tauftauf"
    monkeypatch.setattr(launcher, "ROOT", kernel)
    calls = []
    monkeypatch.setattr(launcher, "install_follower", lambda home, session: (
        calls.append(("follower", home, session)), "core-coordination.service")[1])
    monkeypatch.setattr(launcher, "register_site", lambda home, session: (
        calls.append(("register", home, session)), True)[1])

    monkeypatch.setattr(launcher, "plant_main", lambda: (core_home, "core", kernel, True))
    launcher.main()
    monkeypatch.setattr(launcher, "plant_main", lambda: (target_home, "example", example, True))
    launcher.main()
    assert calls == [("follower", core_home, "core"), ("register", target_home, "example")]


def test_core_follower_is_tracked_as_a_core_service(tmp_path: Path, monkeypatch) -> None:
    kernel = tmp_path / "kernel"
    home = kernel / ".mishe-seed"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text('["core-ci.service"]\n', encoding="utf-8")
    monkeypatch.setattr(launcher, "ROOT", kernel)
    commands = []

    def run(argv, **_kwargs):
        commands.append(argv)
        if "show" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "is-active" in argv:
            return subprocess.CompletedProcess(argv, 3, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.install_follower(home, "core") == "core-coordination.service"
    unit = home / "core-coordination.service"
    assert "coordination.site_sync" in unit.read_text(encoding="utf-8")
    assert json.loads((home / "health" / "services.json").read_text(encoding="utf-8")) == [
        "core-ci.service", "core-coordination.service"]
    assert any("link" in command for command in commands)


def test_target_python_path_exposes_core_without_host_coordinator(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    result = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util, mishe_tauftauf; assert importlib.util.find_spec('coordination') is None"],
        cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_hold_is_recorded_before_the_gate_refused_notice(tmp_path: Path, monkeypatch) -> None:
    # The blocker notice append is publication-gated and may raise; the registry
    # must already carry the hold or every CI tick retries the same refused append.
    kernel = tmp_path / "kernel"
    core_home = kernel / ".mishe-tauftauf"
    target_home = tmp_path / "linked" / ".mishe-tauftauf"
    (core_home / "health").mkdir(parents=True)
    (target_home / "health").mkdir(parents=True)
    from coordination import site_sync
    from mishe_tauftauf.feed import FeedError
    monkeypatch.setattr(site_sync, "KERNEL", kernel.resolve())
    site_sync._save(core_home, [{"home": str(target_home), "session": "linked", "sha": "old"}])

    def _fail(*args, **kwargs):
        raise RuntimeError("publication correction required")

    monkeypatch.setattr(site_sync, "_target_state", _fail)
    monkeypatch.setattr(site_sync, "_git", _fail)
    monkeypatch.setattr(site_sync, "_verify", lambda *a, **k: None)

    order = []
    monkeypatch.setattr(site_sync.Feed, "append", lambda self, source, body, **k: (
        order.append(("append", source, body)), _raise())[1])

    def _raise():
        raise FeedError("publication correction required")

    monkeypatch.setattr(site_sync, "_update_site", lambda *a, **k: order.append(("registry", k)))
    with pytest.raises(FeedError):
        site_sync.sync_registered_sites(core_home, {"state": "pass", "sha": "new"})

    # The registry write precedes any notice publication attempt.
    kinds = [step[0] for step in order]
    assert kinds.index("registry") < kinds.index("append")
    assert order[0] == ("registry", {"sha": "old", "error": "publication correction required"})


def test_a_python_subscript_is_not_structured_json() -> None:
    # D01 used to raw_decode argv[0] as a JSON list and refuse ordinary prose
    # carrying source syntax, which wedged the linked-site coordinator.
    from mishe_tauftauf import post_check

    for body in ("ValueError: agent command unavailable: codex; argv[0] was bare",
                 "The retry reads rows[0] then data[2] before bailing out.",
                 "state[\"sha\"] matched, so the hold was cleared."):
        assert post_check._deterministic(body) == []
    # Trivial literals are prose, not state; substantial structured state is refused.
    for body in ('The counts are [1, 2, 3].', 'version list ["v1"]', 'config {"sites": []}'):
        assert not any(row["id"] == "D01" for row in post_check._deterministic(body))
    for body in ('Here is the state: {"status":"done","counts":[1,2,3],"nested":{"a":1}}.',
                 'The report is [{"a":1},{"b":2}].'):
        assert any(row["id"] == "D01" for row in post_check._deterministic(body))
