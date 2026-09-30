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
    finally:
        subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                        "seed", "stop", "--session", session], capture_output=True, timeout=15)
