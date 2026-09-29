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
