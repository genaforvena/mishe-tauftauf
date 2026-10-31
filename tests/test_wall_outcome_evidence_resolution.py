from __future__ import annotations

from pathlib import Path

from mishe_tauftauf.outcome_publication import eligible_evidence_path
from mishe_tauftauf.wall import resolve_outcome_evidence


def _site(tmp_path: Path) -> Path:
    home = tmp_path / "repo" / ".mishe-tauftauf"
    (home / "artifacts").mkdir(parents=True)
    (home / "artifacts" / "wake.md").write_text("evidence\n")
    return home


def test_bare_artifacts_path_from_repo_root_resolves_to_site(tmp_path, monkeypatch):
    home = _site(tmp_path)
    monkeypatch.chdir(home.parent)
    resolved = resolve_outcome_evidence(home, Path("artifacts/wake.md"))
    assert resolved == home / "artifacts" / "wake.md"
    assert eligible_evidence_path(home, resolved)


def test_cwd_eligible_form_keeps_its_meaning(tmp_path, monkeypatch):
    home = _site(tmp_path)
    monkeypatch.chdir(home.parent)
    # The historical cwd-relative form is still the one used when it is eligible.
    resolved = resolve_outcome_evidence(home, Path(".mishe-tauftauf/artifacts/wake.md"))
    assert resolved == home.parent / ".mishe-tauftauf" / "artifacts" / "wake.md"
    assert eligible_evidence_path(home, resolved)


def test_absolute_path_is_unchanged(tmp_path, monkeypatch):
    home = _site(tmp_path)
    monkeypatch.chdir(tmp_path)
    absolute = home / "artifacts" / "wake.md"
    assert resolve_outcome_evidence(home, absolute) == absolute


def test_missing_or_ineligible_path_stays_refused(tmp_path, monkeypatch):
    home = _site(tmp_path)
    monkeypatch.chdir(home.parent)
    missing = resolve_outcome_evidence(home, Path("artifacts/absent.md"))
    assert not eligible_evidence_path(home, missing)
    traversal = resolve_outcome_evidence(home, Path("artifacts/../../secret.md"))
    assert not eligible_evidence_path(home, traversal)
