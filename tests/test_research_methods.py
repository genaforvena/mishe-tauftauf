"""The methods resident uses the ordinary lifecycle and delivery boundaries."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest

from mishe_tauftauf import activity, plant, seed, wall
from mishe_tauftauf.feed import Feed
from coordination import site_sync


ROLE = "research-methods"


def test_research_methods_seeds_bounded_wall_view_and_preserves_local_view(tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "_require_worktree", lambda home: None)
    seed.init(tmp_path, ROLE, "sh")
    top = tmp_path / "top-pains" / ROLE
    text = top.read_text()
    assert "mishe_tauftauf.wall_view" in text
    assert f"--role {ROLE}" in text
    assert "landing_debt" not in text and "task landing-status" not in text
    top.write_text("#!/bin/sh\nprintf 'STATE: UNKNOWN local research source unavailable\\n'\n")
    seed.init(tmp_path, ROLE, "sh")
    assert "local research source unavailable" in top.read_text()



def test_docs_seeds_reader_document_view_and_preserves_local_view(tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "_require_worktree", lambda home: None)
    seed.init(tmp_path, "docs", "sh")
    top = tmp_path / "top-pains" / "docs"
    text = top.read_text()
    assert "mishe_tauftauf.wall_view" in text
    assert "--role docs" in text
    assert "landing_debt" not in text and "task landing-status" not in text
    top.write_text("#!/bin/sh\nprintf 'STATE: UNKNOWN local docs source unavailable\\n'\n")
    seed.init(tmp_path, "docs", "sh")
    assert "local docs source unavailable" in top.read_text()

def test_research_methods_has_a_specific_seeded_charter_and_site_scope(tmp_path):
    repository = tmp_path / "application"
    home = repository / ".mishe-tauftauf"
    home.mkdir(parents=True)
    charter = seed._core_charter(ROLE)
    assert "decision-relevant experiments" in charter
    assert "not an approval gate" in charter
    assert "Witness" in charter
    (home / "charters").mkdir()
    (home / "charters" / f"{ROLE}.md").write_text(charter)
    (home / "doctrine.md").write_text("Retain the project's frozen inputs and budget.")
    text = wall.restore(home, ROLE, "application")
    assert f"REPOSITORY {repository}" in text
    assert "Assess research questions" in text
    assert "frozen inputs and budget" in text
    assert "independent" in text and "reversible activation" in text
    assert "single shared checkout" in text
    assert "Finish actual wake" in text


def test_research_methods_is_persisted_and_uses_normal_supervision(tmp_path):
    assert ROLE in plant.ROLES
    assert plant.unit_name("application", ROLE) in plant.service_manifest(tmp_path, "application", True)
    unit = plant.unit_text(tmp_path, "application", ROLE, "/usr/bin/python3")
    assert f"--slug {ROLE}" in unit
    assert "--self-pick-seconds 3600" in unit
    assert "--clear-grace-seconds 30" in unit
    assert "Restart=always" in unit
    assert "--self-pick-seconds 300" in plant.unit_text(tmp_path, "application", "genome", "/usr/bin/python3")


def test_research_methods_is_visible_to_peers_and_activity_without_local_registration(tmp_path):
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / f"{ROLE}.md").write_text("Question: does the planned comparison distinguish the alternatives?")
    assert ROLE in activity.roles(tmp_path)
    assert f"WALL {ROLE}" in wall.context(tmp_path, "genome")
    event = Feed(tmp_path).append(ROLE, "Changed hypothesis; evidence in artifacts/methods.md.")
    (tmp_path / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "started": "2000-01-01T00:00:00+00:00"}))
    result = activity.check(tmp_path, now=datetime.now(timezone.utc))
    assert result["idle_seconds"] < 5


def test_research_methods_settles_its_actual_wake_without_adding_an_acceptance_gate(tmp_path):
    (tmp_path / "coordination-mode.json").write_text('{"mode":"wall"}')
    message = wall.message(tmp_path, "genome", ROLE, "Review the registered comparison; final acceptance remains Witness's.")
    assert wall.addressed(message, ROLE)
    wake = Feed(tmp_path).append("seed", f"seed wake {ROLE} observation=1\nRead the experiment evidence.")
    notes = tmp_path / "methods.txt"
    notes.write_text("Compared two explanations. Next: ask the author to test the discriminating case.")
    seed.yield_wake(tmp_path, ROLE, wake.sequence, notes, continue_task=True, result="changed")
    assert seed._state(tmp_path, ROLE)[1] is None
    assert (tmp_path / "walls" / f"{ROLE}.md").read_text() == notes.read_text()


@pytest.mark.parametrize("dead", [True, False])
def test_linked_refresh_checks_research_methods_mind_and_lease(tmp_path, monkeypatch, dead):
    assert ROLE in site_sync.ROLES
    (tmp_path / "health").mkdir()
    names = (*site_sync.ROLES, "operator")
    (tmp_path / "health" / "windows.json").write_text(json.dumps(names))
    (tmp_path / "health" / "services.json").write_text('[]')
    (tmp_path / "chat.log").write_text("")
    rows = [f"{name} 0 0" for name in names]
    rows += [f"{name} 1 {int(dead and name == ROLE)}" for name in site_sync.ROLES]
    monkeypatch.setattr(site_sync, "_tmux", lambda *_args: subprocess.CompletedProcess([], 0, "\n".join(rows).encode(), b""))
    calls = []
    def leases(_session, leased):
        assert ROLE in leased
        calls.append(leased)
        return {name: datetime.now(timezone.utc) + timedelta(seconds=len(calls)) for name in leased}
    monkeypatch.setattr(site_sync, "_leases", leases)
    monkeypatch.setattr(site_sync.time, "sleep", lambda _seconds: None)
    if dead:
        with pytest.raises(RuntimeError, match="dead mind pane"):
            site_sync._verify(tmp_path, "application")
    else:
        site_sync._verify(tmp_path, "application")
        assert len(calls) == 2
