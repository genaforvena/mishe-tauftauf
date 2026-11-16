"""Retention of one class of plant-local output files.

A class is a directory of generated output (for example the moving-pin delivery
outputs under an operator plan, or past gate outputs).  A file in the class is a
*candidate* only when all of these hold:

* it is a regular file, not a symlink, and not a script (``.sh``/``.py``) that a
  unit may execute;
* its mtime is at least ``min_age_s`` seconds old;
* its basename does not appear anywhere in the evidence corpus (the text of the
  evidence files a record or a signal detail uses: patches, release records,
  signals and signal details).  The test is set membership over basenames
  extracted from the corpus, so it errs toward keeping a file.

``plan_class`` only plans and deletes nothing.  ``apply_plan`` is the removal
step: it archives every candidate, verifies each archived member's size against
the source, and only then unlinks.  A file that fails verification is kept, so a
partial archive never loses bytes.  ``summary``/``apply_plan``'s return give the
before/after counts a duty reports.

The decoded text this module needs (corpus text, filenames) is read at the
``retention_text`` boundary; here only decoded values are used.
"""
from __future__ import annotations

import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .retention_text import basenames, is_protected_name


@dataclass
class Plan:
    candidates: list[Path] = field(default_factory=list)
    kept_referenced: list[Path] = field(default_factory=list)
    kept_young: list[Path] = field(default_factory=list)
    kept_protected: list[Path] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "candidates": len(self.candidates),
            "candidate_bytes": sum(_size(p) for p in self.candidates),
            "kept_referenced": len(self.kept_referenced),
            "kept_young": len(self.kept_young),
            "kept_protected": len(self.kept_protected),
        }


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def corpus_basenames(corpus_dirs: Iterable[Path], suffixes: tuple[str, ...]) -> set[str]:
    """Extract all cited basenames from corpus files, streaming to cap memory.

    Each corpus file is read once, its text scanned for basename-like tokens
    (filenames with known suffixes), and those tokens are added to the result
    set.  The function never holds more than one file's text in memory at a
    time, and the final set is bounded by the number of unique basenames.
    """
    basename_set: set[str] = set()
    for root in corpus_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix not in suffixes:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            basename_set.update(basenames(text))
    return basename_set


def extract_basenames(text: str) -> set[str]:
    """Extract cited basenames from a pre-joined corpus text (tests)."""
    return set(basenames(text))


def corpus_text(corpus_dirs: Iterable[Path], suffixes: tuple[str, ...]) -> str:
    """Join the text of every corpus file whose suffix is listed.

    Unreadable files are skipped; a skipped file can only make the corpus
    smaller, so the rule would then keep less, never more.

    .. deprecated:: Use ``corpus_basenames`` for memory-efficient streaming.
       This function is kept for backward compatibility with tests.
    """
    chunks: list[str] = []
    for root in corpus_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix not in suffixes:
                continue
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(chunks)


def plan_class(
    class_dir: Path,
    corpus: str | set[str],
    *,
    now: float,
    min_age_s: float,
) -> Plan:
    """Return the retention plan for one class directory (no side effects).

    ``corpus`` may be either a set of basenames (from ``corpus_basenames``) or
    a pre-joined corpus text string (deprecated, for backward compatibility).
    If a string is given, basenames are extracted from it on the fly.
    """
    if isinstance(corpus, str):
        corpus_set = extract_basenames(corpus)
    else:
        corpus_set = corpus

    result = Plan()
    if not class_dir.is_dir():
        return result
    for path in sorted(class_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if is_protected_name(path.name):
            result.kept_protected.append(path)
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            result.kept_young.append(path)
            continue
        if age < min_age_s:
            result.kept_young.append(path)
            continue
        if path.name in corpus_set:
            result.kept_referenced.append(path)
            continue
        result.candidates.append(path)
    return result


def apply_plan(plan: Plan, class_dir: Path, archive_path: Path) -> dict[str, int]:
    """Archive the plan's candidates, verify sizes, then remove them.

    Every candidate is written to ``archive_path`` (a ``.tar.gz``), stored under
    its path relative to ``class_dir``.  Each member's size is then compared
    with the source file: a file is unlinked only when the archived member size
    equals the source size.  A file that fails is left in place and counted in
    ``kept_unverified``.  An empty candidate list writes no archive.
    """
    result = {
        "candidates": len(plan.candidates),
        "candidate_bytes": sum(_size(p) for p in plan.candidates),
        "archived": 0,
        "removed": 0,
        "removed_bytes": 0,
        "kept_unverified": 0,
        "archive_bytes": 0,
    }
    if not plan.candidates:
        return result

    planned_sizes = {path: _size(path) for path in plan.candidates}
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in plan.candidates:
            tar.add(path, arcname=path.relative_to(class_dir).as_posix())
    result["archived"] = len(plan.candidates)
    result["archive_bytes"] = _size(archive_path)

    with tarfile.open(archive_path) as tar:
        archived_sizes = {member.name: member.size for member in tar.getmembers() if member.isfile()}

    for path in plan.candidates:
        rel = path.relative_to(class_dir).as_posix()
        if archived_sizes.get(rel) != planned_sizes[path]:
            result["kept_unverified"] += 1
            continue
        try:
            path.unlink()
        except OSError:
            result["kept_unverified"] += 1
            continue
        result["removed"] += 1
        result["removed_bytes"] += planned_sizes[path]
    return result
