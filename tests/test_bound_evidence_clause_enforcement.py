"""The write-once clause must be present and must survive a replacement.

A bound file the clause made read-only is replaced by the plant's own
atomic-write path, so the mode is gone and the digest no longer matches.
This suite measures the three answers the check must give without a second
decoder.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "bound-evidence-clause-enforcement.py"
PACKAGE = "mishe_tauftauf"

# Reuse the real wall.py bytes so a synthetic root can actually publish.
WALL_SOURCE = (REPO / "src" / PACKAGE / "wall.py").read_text(encoding="utf-8")
NO_CLAUSE_WALL = WALL_SOURCE.replace("os.chmod", "os.stat")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_root(base: Path, name: str, wall_source: str) -> Path:
    root = base / name
    (root / PACKAGE).mkdir(parents=True, exist_ok=True)
    # Copy the real package init and all modules the wall import needs.
    for item in (REPO / "src" / PACKAGE).glob("*.py"):
        shutil.copy(item, root / PACKAGE / item.name)
    # Overwrite wall.py with the desired variant.
    (root / PACKAGE / "wall.py").write_text(wall_source, encoding="utf-8")
    return root


def _make_site(base: Path, pin: Path | None = None, cli: Path | None = None,
               declared: dict[Path, str] | None = None) -> Path:
    home = base / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(exist_ok=True)
    (home / "health").mkdir(exist_ok=True)
    (home / "minds").mkdir(exist_ok=True)
    (home / "bin").mkdir(exist_ok=True)
    (home / "charters").mkdir(exist_ok=True)
    (home / "chat.log").write_text("", encoding="utf-8")
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall"}), encoding="utf-8")
    if pin is not None:
        (home / "health" / "runtime-release.json").write_text(
            json.dumps({"source": str(pin)}), encoding="utf-8")
    if declared is not None:
        units = {unit: {"root": str(root)} for root, unit in declared.items()}
        (home / "health" / "declared-roots.json").write_text(
            json.dumps({"units": units}), encoding="utf-8")
    if cli is not None:
        script = home / "bin" / "mishe-tauftauf"
        script.write_text('#!/bin/sh\nexport PYTHONPATH="%s"\nexec python3 "$@"\n' % cli, encoding="utf-8")
        script.chmod(0o755)
    return home


def _write_record(home: Path, name: str, target: Path, sha: str) -> None:
    record = {
        "version": 1,
        "kind": "wall-outcome",
        "payload": {
            "role": "inference-research",
            "kind": "accepted",
            "text": "a contribution",
            "evidence": {"path": str(target), "sha256": sha},
        },
    }
    (home / "records" / name).write_text(json.dumps(record), encoding="utf-8")


def _run(home: Path, repo: Path | None = None, *extra: str) -> subprocess.CompletedProcess:
    args = [sys.executable, str(CHECK), "--home", str(home)]
    if repo is not None:
        args += ["--repo", str(repo)]
    return subprocess.run(args, capture_output=True, text=True, timeout=180)


def test_carries_clause_reads_text_not_import(tmp_path):
    """The measurement survives a refactored helper signature."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("clause_check", str(CHECK))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    _make_root(tmp_path, "good", WALL_SOURCE)
    _make_root(tmp_path, "bad", NO_CLAUSE_WALL)
    assert mod.carries_clause(good) is True
    assert mod.carries_clause(bad) is False
    # Must not import the plant.
    assert "import mishe_tauftauf" not in CHECK.read_text(encoding="utf-8")


def test_standing_loss_splits_defeated_from_never_applied(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("clause_check2", str(CHECK))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    home = tmp_path / "home"
    home.mkdir()
    (home / "records").mkdir()
    # Intact row
    intact = home / "artifacts" / "intact.md"
    intact.parent.mkdir(parents=True)
    intact.write_text("same\n", encoding="utf-8")
    os.chmod(intact, 0o444)
    _write_record(home, "a.json", intact, _digest(intact))
    # Lost writable (never chmod'd)
    writable = home / "writable.md"
    writable.write_text("original\n", encoding="utf-8")
    os.chmod(writable, 0o664)
    _write_record(home, "b.json", writable, hashlib.sha256(b"original\n").hexdigest())
    writable.write_text("changed\n", encoding="utf-8")
    os.chmod(writable, 0o664)
    # Lost read-only (clause defeated: file is 0444 yet digest differs)
    defeated = home / "defeated.md"
    defeated.write_text("original\n", encoding="utf-8")
    os.chmod(defeated, 0o444)
    _write_record(home, "c.json", defeated, hashlib.sha256(b"original\n").hexdigest())
    # Re-create as new inode with same 0444 mode but different bytes (chmod reapplied)
    defeated.unlink()
    defeated.write_text("replaced\n", encoding="utf-8")
    os.chmod(defeated, 0o444)
    # Missing
    _write_record(home, "d.json", home / "missing.md", "0" * 64)
    loss = mod.standing_loss(home, since=None)
    assert loss["decided"] is True
    assert loss["total"] == 3  # intact + 2 lost existing
    assert loss["lost_writable"] == 1
    assert loss["lost_readonly"] == 1
    assert loss["missing"] == 1


def test_probe_shows_clause_defeated_by_replace(tmp_path):
    """Publish through a real root, then replace — mode and bytes are gone."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("clause_check3", str(CHECK))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    # Use a copy of the real package so _publish can run.
    root = _make_root(tmp_path, "probe-root", WALL_SOURCE)
    result = mod.probe_root(root)
    assert result.get("decided") is True, result
    assert result["clause_applied"] is True
    assert result["read_only_survived"] is False
    assert result["bytes_survived"] is False
    assert oct(result["mode_after_publish"]) == "0o444"
    assert oct(result["mode_after_replace"]) == "0o664"


def test_absent_root_is_a_failure(tmp_path):
    home = _make_site(tmp_path, pin=_make_root(tmp_path, "pin-good", WALL_SOURCE),
                      cli=_make_root(tmp_path, "cli-bad", NO_CLAUSE_WALL))
    # Declare an old overlay that lacks the clause — live set includes it.
    bad = tmp_path / "bad-overlay" / PACKAGE
    bad_root = _make_root(tmp_path, "bad-overlay", NO_CLAUSE_WALL)
    # Wire it as declared via site file.
    (home / "health" / "declared-roots.json").write_text(
        json.dumps({"units": {"mishe-self-development-current-initiative-watch.service": {"root": str(bad_root)}}}),
        encoding="utf-8")
    result = _run(home, REPO)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "DOES NOT CARRY the clause" in result.stdout
    assert str(bad_root) in result.stdout


def test_defeated_clause_is_a_failure_even_when_present_everywhere(tmp_path):
    # Every root carries the clause, yet the probe defeats it — the check must still fail.
    pin = _make_root(tmp_path, "pin-good2", WALL_SOURCE)
    home = _make_site(tmp_path, pin=pin, cli=pin)
    result = _run(home, REPO)
    # On this host the live set may add extra roots; at minimum the probe defeats the clause.
    assert result.returncode == 2, result.stdout + result.stderr
    assert "did not hold" in result.stdout


def test_check_reads_published_artifacts_only(tmp_path):
    source = CHECK.read_text(encoding="utf-8")
    assert "import mishe_tauftauf" not in source
    assert "from mishe_tauftauf" not in source
    # The only chat.log in the check is the transient disposable-home probe
    # fixture in /tmp, never the site home's tape. Keep the site-home
    # reader limited to records.
    assert source.count("chat.log") <= 2
    assert "records" in source


def test_check_writes_nothing_into_the_site(tmp_path):
    pin = _make_root(tmp_path, "pin-write", WALL_SOURCE)
    home = _make_site(tmp_path, pin=pin, cli=pin)
    # One intact record so standing_loss has something to read.
    target = home / "artifacts" / "note.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello\n", encoding="utf-8")
    os.chmod(target, 0o444)
    _write_record(home, "r.json", target, _digest(target))
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    _run(home, REPO)
    after = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    assert before == after


def test_no_publishable_root_is_unknown(tmp_path, monkeypatch):
    """UNKNOWN when nothing can publish — isolated from the host's live roots."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("clause_check_unknown", str(CHECK))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    # Host has live roots, so subprocess would never be UNKNOWN. Patch
    # live_roots so candidate_roots is empty for an empty site.
    monkeypatch.setattr(mod, "live_roots", lambda: {})
    home = tmp_path / "empty-site"
    home.mkdir()
    (home / "records").mkdir()
    assert mod.candidate_roots(home) == {}
    rc = mod.main(["--home", str(home), "--repo", str(REPO)])
    assert rc == 3
