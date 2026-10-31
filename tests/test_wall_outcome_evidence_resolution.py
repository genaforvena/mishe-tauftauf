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


def test_refused_outcome_audit_records_submitted_and_resolved_form(tmp_path, monkeypatch):
    import json

    from mishe_tauftauf import wall

    home = _site(tmp_path)
    monkeypatch.chdir(home.parent)
    # A bare artifacts/ path that resolves to a file which is not eligible
    # (traversal) is refused; the audit names what was submitted and what was checked.
    submitted = Path("artifacts/../../secret.md")
    wall._audit_outcome_refusal(
        home, "senses", "hypothesis-changed", "refused", [],
        evidence=str(submitted),
        resolved=str(wall._resolved_form_for_audit(home, submitted)),
    )
    entry = json.loads((home / "logs" / "outcome-refusal-audit.jsonl").read_text().splitlines()[-1])
    assert entry["evidence"] == str(submitted)
    assert entry["resolved"] == str(resolve_outcome_evidence(home, submitted))
    assert "dms" not in entry and "content" not in entry


def test_resolved_form_for_audit_never_raises(tmp_path, monkeypatch):
    from mishe_tauftauf import wall

    home = _site(tmp_path)
    monkeypatch.chdir(home.parent)
    assert wall._resolved_form_for_audit(home, Path("artifacts/wake.md")) == str(home / "artifacts" / "wake.md")
