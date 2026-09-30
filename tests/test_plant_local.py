from __future__ import annotations

from pathlib import Path

import pytest
from mishe_tauftauf import plant


def test_refresh_contract_preserves_other_agent_instructions() -> None:
    module = vars(plant)
    refresh = module["refresh_contract"]
    old = "User instruction.\n\n<!-- mishe-tauftauf plant contract -->\nOld rule.\n<!-- end mishe-tauftauf plant contract -->\n\nFinal instruction.\n"
    new = "<!-- mishe-tauftauf plant contract -->\nNew rule.\n"
    result = refresh(old, new)
    assert "User instruction." in result
    assert "Final instruction." in result
    assert "New rule." in result
    assert "Old rule." not in result
    assert refresh(result, new) == result


def test_linked_systemd_fragment_matches_site_unit(tmp_path: Path) -> None:
    module = vars(plant)
    unit = tmp_path / "site" / "mishe-example-genome.service"
    unit.parent.mkdir()
    unit.write_text("[Unit]\n")
    linked = tmp_path / "systemd" / unit.name
    linked.parent.mkdir()
    linked.symlink_to(unit)
    assert module["unit_fragment_matches"](str(linked), unit)
    assert not module["unit_fragment_matches"](str(tmp_path / "other.service"), unit)


def test_replant_keeps_existing_operator_window_name(tmp_path: Path) -> None:
    module = vars(plant)
    site = tmp_path / "site"
    (site / "health").mkdir(parents=True)
    (site / "health" / "windows.json").write_text(
        '["codex", "discover", "genome", "health", "permissions", "senses", "witness"]\n'
    )
    choose = module["preferred_operator_window"]
    assert choose(site, None) == "codex"
    assert choose(site, "codex") == "codex"
    with pytest.raises(ValueError, match="site already uses operator window"):
        choose(site, "different")
    assert choose(tmp_path / "fresh", None) == "operator"


def test_default_plant_reuses_only_existing_resident_site(tmp_path: Path) -> None:
    module = vars(plant)
    choose = module["site_and_session"]
    assert choose(tmp_path, ".mishe-seed", None, None) == (tmp_path / ".mishe-seed", "mishe-seed")
    existing = tmp_path / ".mishe-tauftauf"
    existing.mkdir()
    (existing / ".seed-raised").write_text("mishe-self-development-current $391\n")
    assert choose(tmp_path, ".mishe-seed", None, None) == (existing, "mishe-self-development-current")
    other = tmp_path / ".mishe-seed"
    other.mkdir()
    (other / ".seed-raised").write_text("mishe-seed $392\n")
    with pytest.raises(ValueError, match="multiple resident sites"):
        choose(tmp_path, ".mishe-seed", None, None)


def test_replant_preserves_existing_custom_engine_without_default_binary(tmp_path: Path) -> None:
    home = tmp_path / ".mishe-tauftauf"
    minds = home / "minds"
    minds.mkdir(parents=True)
    with pytest.raises(ValueError, match="agent command unavailable"):
        plant.ensure_engine_for_new_minds(home, "missing-engine-for-test")
    for slug in plant.ROLES:
        (minds / slug).write_text("#!/bin/sh\nexec custom-engine\n", encoding="utf-8")
    plant.ensure_engine_for_new_minds(home, "missing-engine-for-test")


def test_normal_replant_keeps_installed_immutable_source(tmp_path):
    import json
    import subprocess
    from mishe_tauftauf import seed

    release = tmp_path / "release"
    package = release / "src/mishe_tauftauf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "seed_doctrine.md").write_text("Reviewed frozen doctrine.\n")
    (package / "seed_discover_charter.md").write_text("Reviewed frozen discovery charter.\n")
    subprocess.run(["git", "-C", str(release), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(release), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(release), "-c", "user.name=test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "immutable runtime"], check=True)
    sha = subprocess.check_output(["git", "-C", str(release), "rev-parse", "HEAD"], text=True).strip()
    home = tmp_path / "application/.mishe-tauftauf"
    (home / "health").mkdir(parents=True)
    (home / "health/runtime-release.json").write_text(json.dumps({
        "version": 1, "source": str(release), "sha": sha, "session": "owned"}))
    assert f"Environment=PYTHONPATH={release / 'src'}" in plant.unit_text(home, "owned", "genome", "python3")
    argv = seed._mind_launch_argv(home, "genome")
    assert any(arg.startswith(f"PYTHONPATH={release / 'src'}") for arg in argv)
    assert seed._core_doctrine(home) == "Reviewed frozen doctrine.\n"
    assert seed._core_charter("discover", home) == "Reviewed frozen discovery charter.\n"
    from mishe_tauftauf.runtime_source import refresh_cli
    cli = home / "bin/mishe-tauftauf"
    cli.parent.mkdir()
    cli.write_text('#!/bin/sh\nexport PYTHONPATH=/old/src${PYTHONPATH:+:$PYTHONPATH}\nexec /usr/bin/python3 -m mishe_tauftauf "$@"\n')
    cli.chmod(0o755)
    refresh_cli(home, release)
    assert str(release / "src") in cli.read_text()
    assert cli.stat().st_mode & 0o111
    # A corrupt or changed pin must fail instead of silently returning to mutable code.
    (package / "__init__.py").write_text("changed")
    with pytest.raises(ValueError, match="runtime.*clean|runtime.*changed"):
        plant.unit_text(home, "owned", "genome", "python3")


def test_runtime_source_rejects_whitespace_before_pin_write(tmp_path):
    from mishe_tauftauf.runtime_source import select_source
    home = tmp_path / "site"
    with pytest.raises(ValueError, match="whitespace"):
        select_source(home, tmp_path / "release with spaces", "owned")
    assert not (home / "health/runtime-release.json").exists()


def test_runtime_only_cli_preserves_dirty_application_and_contract(tmp_path: Path) -> None:
    import subprocess
    import sys
    import uuid

    workspace = tmp_path / "application"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    home = workspace / ".mishe-tauftauf"
    session = "mishe-refresh-test-" + uuid.uuid4().hex[:10]
    argv = [sys.executable, "-m", "mishe_tauftauf.plant", "--workspace", str(workspace),
            "--home", str(home), "--session", session, "--engine-command", "cat", "--no-services"]
    try:
        initial = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        assert initial.returncode == 0, initial.stderr
        agents = workspace / "AGENTS.md"
        agents.write_text("Unlanded application contract.\n")
        application = workspace / "application.py"
        application.write_text("unlanded application work\n")
        mind = home / "minds/genome"
        original_mind = mind.read_bytes()
        refresh = subprocess.run([*argv, "--runtime-only"], capture_output=True, text=True, timeout=45)
        assert refresh.returncode == 0, refresh.stderr
        assert agents.read_text() == "Unlanded application contract.\n"
        assert application.read_text() == "unlanded application work\n"
        assert mind.read_bytes() == original_mind
        # Select an independent committed package and exercise the real refresh caller.
        import shutil
        release = tmp_path / "release"
        shutil.copytree(Path(plant.__file__).parents[1], release / "src", ignore=shutil.ignore_patterns("__pycache__"))
        subprocess.run(["git", "-C", str(release), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(release), "add", "src"], check=True)
        subprocess.run(["git", "-C", str(release), "-c", "user.name=test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "runtime snapshot"], check=True)
        selected = subprocess.run([*argv, "--runtime-only", "--runtime-source", str(release)],
                                  capture_output=True, text=True, timeout=45)
        assert selected.returncode == 0, selected.stderr
        assert str(release / "src") in (home / "bin/mishe-tauftauf").read_text()
        assert agents.read_text() == "Unlanded application contract.\n"
        assert application.read_text() == "unlanded application work\n"
        assert mind.read_bytes() == original_mind
        pid = subprocess.check_output(["tmux", "display-message", "-p", "-t", f"{session}:genome.0", "#{pane_pid}"],
                                      text=True).strip()
        assert f"PYTHONPATH={release / 'src'}".encode() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    finally:
        subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                        "seed", "stop", "--session", session], capture_output=True, timeout=15)


def test_runtime_refresh_does_not_wait_for_delivery_projection(tmp_path):
    import subprocess
    import sys
    import uuid
    workspace = tmp_path / "application"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    home = workspace / ".mishe-tauftauf"
    session = "mishe-maintenance-test-" + uuid.uuid4().hex[:10]
    script = """
from mishe_tauftauf import delivery, ci_watch
import runpy

def unavailable_maintenance(home):
    raise RuntimeError('delivery projection may wait indefinitely for configured model admission')
delivery.check_all = unavailable_maintenance
ci_watch.read = lambda home: dict(state='unknown', sha='unknown', run='none', url='none', detail='isolated CI fixture')
runpy.run_module('mishe_tauftauf.plant', run_name='__main__')
"""
    try:
        initial_script = script.replace("delivery.check_all = unavailable_maintenance", "delivery.check_all = lambda home: None")
        initial = subprocess.run([sys.executable, "-c", initial_script, "--workspace", str(workspace),
            "--home", str(home), "--session", session, "--engine-command", "cat", "--no-services"],
            capture_output=True, text=True, timeout=45)
        assert initial.returncode == 0, initial.stderr
        result = subprocess.run([sys.executable, "-c", script, "--workspace", str(workspace),
            "--home", str(home), "--session", session, "--engine-command", "cat", "--no-services",
            "--runtime-only"], capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr
        assert "plant ready" in result.stdout
        from mishe_tauftauf import ci_watch
        assert ci_watch.latest(home)["state"] == "unknown"
    finally:
        subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                        "seed", "stop", "--session", session], capture_output=True, timeout=15)
