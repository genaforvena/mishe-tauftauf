from __future__ import annotations

import runpy
from pathlib import Path


def test_refresh_contract_preserves_other_agent_instructions() -> None:
    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "plant_local.py"))
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
    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "plant_local.py"))
    unit = tmp_path / "site" / "mishe-example-genome.service"
    unit.parent.mkdir()
    unit.write_text("[Unit]\n")
    linked = tmp_path / "systemd" / unit.name
    linked.parent.mkdir()
    linked.symlink_to(unit)
    assert module["unit_fragment_matches"](str(linked), unit)
    assert not module["unit_fragment_matches"](str(tmp_path / "other.service"), unit)
