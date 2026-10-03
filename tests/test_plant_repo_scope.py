import json
from pathlib import Path

import pytest
from mishe_tauftauf import wall, wall_view


def test_restore_honors_local_charter_and_repository_scope(tmp_path):
    home = tmp_path / ".mishe-tauftauf"
    (home / "charters").mkdir(parents=True)
    (home / "charters/docs.md").write_text("Own the tiny-fleet research report.")
    (home / "doctrine.md").write_text("Preserve frozen research protocols.")
    text = wall.restore(home, "docs", "target-plant")
    assert "Own the tiny-fleet research report." in text
    assert "Preserve frozen research protocols." in text
    assert f"REPOSITORY {tmp_path.resolve()}" in text
    assert "installed runtime" in text
    assert "Edit docs/mesh.md" not in text
    assert str(tmp_path / "README.md") in text


def test_external_docs_defaults_to_its_readme_even_with_mesh_page(tmp_path):
    home = tmp_path / ".mishe-tauftauf"
    home.mkdir()
    (tmp_path / "README.md").write_text("Target project reader report")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/mesh.md").write_text("Unselected mesh book")
    text = wall_view.render(home, "docs")
    assert "Target project reader report" in text
    assert "Unselected mesh book" not in text


def test_docs_config_selects_local_document_for_restore_and_render(tmp_path):
    home = tmp_path / ".mishe-tauftauf"
    home.mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/report.md").write_text("Measured local findings")
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "docs_document": "docs/report.md"}))
    assert "Measured local findings" in wall_view.render(home, "docs")
    assert str(tmp_path / "docs/report.md") in wall.restore(home, "docs", "plant")
    (tmp_path / "docs/report.md").unlink()
    assert "STATE: UNKNOWN" in wall_view.render(home, "docs")


@pytest.mark.parametrize("target", ["../other/README.md", "/tmp/other/README.md"])
def test_docs_config_rejects_outside_repository(tmp_path, target):
    home = tmp_path / ".mishe-tauftauf"
    home.mkdir()
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "docs_document": target}))
    assert "STATE: UNKNOWN" in wall_view.render(home, "docs")
    with pytest.raises(ValueError, match="repository"):
        wall.restore(home, "docs", "plant")


def test_new_plant_docs_charter_is_repository_neutral():
    from mishe_tauftauf import seed
    charter = seed._core_charter("docs")
    assert "docs/mesh.md" not in charter
    assert "repository" in charter
    assert "README" in charter


def test_docs_config_rejects_symlink_escape(tmp_path):
    repo = tmp_path / "project"
    home = repo / ".mishe-tauftauf"
    home.mkdir(parents=True)
    outside = tmp_path / "other.md"
    outside.write_text("Other plant reader page")
    (repo / "README.md").symlink_to(outside)
    assert "STATE: UNKNOWN" in wall_view.render(home, "docs")
    with pytest.raises(ValueError, match="repository"):
        wall.restore(home, "docs", "plant")
