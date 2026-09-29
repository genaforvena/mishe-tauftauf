from __future__ import annotations

import json
import os
import subprocess
import sys
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
