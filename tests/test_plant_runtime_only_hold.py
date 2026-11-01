"""A runtime-only refresh must survive a Genome singleton hold; a full plant must not.

Operator defect 2026-11-01: plant --runtime-only aborted at PROMOTE-BEGIN because
seed.start("genome") refused while the life-support Genome pane was live. The
refusal is a deliberate duplicate guard and stays. Only the runtime-only path
treats it as a visible hold and continues with the remaining roles.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mishe_tauftauf import plant, seed


def _setup(tmp_path: Path, monkeypatch, start_impl) -> tuple[Path, list[str]]:
    home = tmp_path / "site"
    home.mkdir()
    (home / "charters").mkdir()
    (home / "top-pains").mkdir()
    (home / "health").mkdir()
    order: list[str] = []

    monkeypatch.setattr(plant.seed, "_require_worktree", lambda h: None)
    monkeypatch.setattr(plant.seed, "init", lambda h, slug, engine_command: None)
    monkeypatch.setattr(plant.seed, "start", start_impl(order))
    monkeypatch.setattr(plant, "owns_session", lambda h, session: True)
    monkeypatch.setattr(plant, "ensure_engine_for_new_minds", lambda h, engine_command: None)
    monkeypatch.setattr(plant.seed_permission_panel, "ensure", lambda h, session: None)
    monkeypatch.setattr("mishe_tauftauf.operator_view.ensure", lambda *a: None)
    monkeypatch.setattr(plant, "_tmux", lambda *a, **k: SimpleNamespace(
        stdout=SimpleNamespace(decode=lambda: "")))
    monkeypatch.setattr(plant, "write_service_manifest", lambda h, session, persist: [])
    monkeypatch.setattr(plant, "reconcile_services",
                        lambda h, session, persist, python, **kw: order.append("reconcile_services"))
    monkeypatch.setattr(plant.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(
                            returncode=0, stdout="" if k.get("text") else b""))
    return home, order


def _start_with_genome_hold(order):
    def start(home, session, slug, timeout):
        order.append(f"start:{slug}")
        if slug == "genome":
            raise seed.GenomeSingletonHold("UNKNOWN genome singleton: life-support pane live")
        return ""
    return lambda: start


def test_runtime_only_reports_genome_hold_and_completes(tmp_path: Path, monkeypatch, capsys) -> None:
    home, order = _setup(tmp_path, monkeypatch, lambda order: _start_with_genome_hold(order)())
    # Every window except the held genome exists after the refresh.
    live = [*plant.ROLES, "operator", "permissions"]
    live = [name for name in live if name != "genome"]
    monkeypatch.setattr(plant, "_tmux", lambda *a, **k: SimpleNamespace(
        stdout=SimpleNamespace(decode=lambda: "\n".join(live) + "\n")))

    plant.plant(home, "core", "python3", "operator", True, runtime_only=True)

    out = capsys.readouterr().out
    assert "HOLD genome: UNKNOWN genome singleton" in out
    assert "reconcile_services" in order
    assert "start:genome" in order
    assert [s for s in order if s.startswith("start:") and s != "start:genome"], "other roles must still start"
    # health reads windows.json: a held genome must stay an expected window after the refresh.
    import json
    expected = json.loads((home / "health" / "windows.json").read_text())
    assert "genome" in expected
    assert set(live) <= set(expected)


def test_runtime_only_still_requires_non_held_windows(tmp_path: Path, monkeypatch) -> None:
    # The hold excuses only genome. Any other missing window still fails the refresh.
    home, order = _setup(tmp_path, monkeypatch, lambda order: _start_with_genome_hold(order)())
    monkeypatch.setattr(plant, "_tmux", lambda *a, **k: SimpleNamespace(
        stdout=SimpleNamespace(decode=lambda: "operator\nwitness\n")))

    with pytest.raises(RuntimeError, match="missing windows"):
        plant.plant(home, "core", "python3", "operator", True, runtime_only=True)


def test_full_plant_still_raises_on_genome_hold(tmp_path: Path, monkeypatch) -> None:
    home, order = _setup(tmp_path, monkeypatch, lambda order: _start_with_genome_hold(order)())

    with pytest.raises(seed.GenomeSingletonHold):
        plant.plant(home, "core", "python3", "operator", True, runtime_only=False)
    assert "reconcile_services" not in order


def test_genome_hold_is_a_value_error() -> None:
    # Existing callers catching ValueError from seed.start keep working.
    assert issubclass(seed.GenomeSingletonHold, ValueError)
