from __future__ import annotations

import os
import tarfile
from pathlib import Path

from mishe_tauftauf.site_retention import (
    apply_plan,
    corpus_basenames,
    corpus_text,
    plan_class,
)

NOW = 1_800_000_000.0
OLD = NOW - 10 * 86400
YOUNG = NOW - 60


def _file(path: Path, body: str = "x", mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_old_unreferenced_file_is_a_candidate(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    f = _file(cls / "check-moving-pin-446248f0.out")
    plan = plan_class(cls, "", now=NOW, min_age_s=86400)
    assert plan.candidates == [f]
    assert plan.summary()["candidate_bytes"] == len("x")


def test_file_named_by_corpus_is_kept(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    _file(cls / "auto-deliver-8a01f628.out")
    corpus = '{"output": "delivery/auto-deliver-8a01f628.out"}'
    plan = plan_class(cls, corpus, now=NOW, min_age_s=86400)
    assert plan.candidates == []
    assert len(plan.kept_referenced) == 1


def test_young_file_is_kept(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    _file(cls / "fresh.out", mtime=YOUNG)
    plan = plan_class(cls, "", now=NOW, min_age_s=86400)
    assert plan.candidates == []
    assert len(plan.kept_young) == 1


def test_scripts_are_never_candidates(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    _file(cls / "cadence.sh")
    _file(cls / "heal.sh")
    _file(cls / "helper.py")
    plan = plan_class(cls, "", now=NOW, min_age_s=86400)
    assert plan.candidates == []
    assert len(plan.kept_protected) == 3


def test_symlinks_are_not_candidates(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    target = _file(tmp_path / "elsewhere.out")
    cls.mkdir(parents=True)
    (cls / "link.out").symlink_to(target)
    plan = plan_class(cls, "", now=NOW, min_age_s=86400)
    assert plan.candidates == []


def test_planning_deletes_nothing(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    f = _file(cls / "old.out")
    plan_class(cls, "", now=NOW, min_age_s=86400)
    assert f.exists()


def test_missing_class_is_an_empty_plan(tmp_path: Path) -> None:
    plan = plan_class(tmp_path / "absent", "", now=NOW, min_age_s=86400)
    assert plan.summary()["candidates"] == 0


def test_corpus_reads_only_listed_suffixes(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "patches"
    _file(corpus_dir / "a.json", body="ALPHA.out")
    _file(corpus_dir / "b.txt", body="BETA.out")
    text = corpus_text([corpus_dir], (".json",))
    assert "ALPHA.out" in text
    assert "BETA.out" not in text


def test_corpus_basenames_collects_across_dirs(tmp_path: Path) -> None:
    a = tmp_path / "patches"
    b = tmp_path / "signals"
    _file(a / "p.json", body="one.out")
    _file(b / "s.detail", body="two.out")
    names = corpus_basenames([a, b], (".json", ".detail"))
    assert {"one.out", "two.out"} <= names


def test_apply_archives_verifies_then_removes(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    keep = _file(cls / "cited.out")
    drop = _file(cls / "orphan.out", body="payload")
    plan = plan_class(cls, "cited.out", now=NOW, min_age_s=86400)
    archive = tmp_path / "out.tar.gz"
    result = apply_plan(plan, cls, archive)
    assert result["candidates"] == 1
    assert result["removed"] == 1
    assert result["removed_bytes"] == len("payload")
    assert result["kept_unverified"] == 0
    assert not drop.exists()
    assert keep.exists()
    with tarfile.open(archive) as tar:
        names = [m.name for m in tar.getmembers()]
    assert names == ["orphan.out"]


def test_apply_of_empty_plan_writes_no_archive(tmp_path: Path) -> None:
    cls = tmp_path / "delivery"
    _file(cls / "cited.out")
    plan = plan_class(cls, "cited.out", now=NOW, min_age_s=86400)
    archive = tmp_path / "out.tar.gz"
    result = apply_plan(plan, cls, archive)
    assert result["candidates"] == 0
    assert result["removed"] == 0
    assert not archive.exists()



def _retention_apply(home: Path) -> "subprocess.CompletedProcess[str]":
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "checks/retention-apply.py"
    return subprocess.run([sys.executable, str(script), "--home", str(home),
                           "--class", "work/releases", "--apply"],
                          capture_output=True, text=True)


def test_apply_to_release_records_refuses_while_a_merge_holds_the_lock(tmp_path: Path) -> None:
    import time

    from mishe_tauftauf.release_promotion import review_records_lock

    home = tmp_path / "home"
    kept = _file(home / "work/releases/record.json", mtime=time.time() - 10 * 86400)
    with review_records_lock(home):
        run = _retention_apply(home)
    assert run.returncode == 2
    assert "holds the record lock" in run.stdout
    assert kept.exists()


def test_apply_to_release_records_still_removes_an_unreferenced_old_record(tmp_path: Path) -> None:
    import time

    home = tmp_path / "home"
    old = _file(home / "work/releases/record.json", mtime=time.time() - 10 * 86400)
    run = _retention_apply(home)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not old.exists()
