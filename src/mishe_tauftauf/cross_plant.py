"""Read-only detector for sibling plants whose pinned release would collide with ours.

The 2026-11-20 03:54Z collision was a source-level fork error before it was a
unit-level drift: nginx2root's pinned release had its own generated marker but
its promotion still hardcoded ``prefix="mishe"``, so it rewrote 179 of our units
with foreign bytes. ``units.doctor`` then saw the effect within minutes; this
module reads the sibling's pinned source so the cause is visible before its next
promotion. No sibling state is written and no foreign code is imported: only
module-level string assignments and string constants are decoded with ``ast``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SITE_DIR = ".mishe-tauftauf"


def _tree(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return None


def _assignment(tree: ast.Module, name: str) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return None


def _promotion_prefix(tree: ast.Module) -> str | None:
    """The ``prefix=`` literal of the Namespace activation uses, or None if undecidable."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Namespace":
            for keyword in node.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        return keyword.value.value
    return None


def _start_prefixes(tree: ast.Module, *, unit: bool, timer: bool) -> set[str]:
    """Unit/timer prefixes a release asks systemd to start, from string constants."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if unit:
            match = re.search(r"([A-Za-z0-9_-]+)-wake@", node.value)
            if match:
                found.add(match.group(1))
        if timer:
            match = re.fullmatch(r"([A-Za-z0-9_-]+)-next-due", node.value)
            if match:
                found.add(match.group(1))
    return found


def _siblings(home: Path) -> list[tuple[str, Path]]:
    """Other plants beside this site home, identified by their own pointer."""
    try:
        site = home.resolve()
        root = site.parent.parent          # <plant>/<SITE_DIR> -> <host>/<plant> -> <host>
        return sorted((p.name, p) for p in root.iterdir()
                      if p.is_dir() and p != site.parent
                      and (p / SITE_DIR / "runtime" / "current").is_symlink())
    except OSError:
        return []


def release_problems(home: Path, *, our_prefix: str, our_marker: str) -> list[str]:
    """Every sibling release whose source would write or start our units.

    Returns [] when a sibling cannot be read or does not carry this runtime's
    source layout: an unknown fork is not claimed to be safe. Only the two
    collision-causing facts are judged — the unit prefix used at promotion and
    the names passed to systemd on start. A sibling's duty manifest is its own
    plant policy (fork change 2) and is deliberately out of scope here.
    """
    problems = []
    for name, root in _siblings(home):
        try:
            release = (root / SITE_DIR / "runtime" / "current").resolve()
        except OSError:
            continue
        pkg = release / "src" / "mishe_tauftauf"
        promotion = _tree(pkg / "release_promotion.py")
        units = _tree(pkg / "units.py")
        wake = _tree(pkg / "wake.py")
        arm = _tree(pkg / "arm.py")
        if promotion is None or units is None:
            continue                      # not our runtime's layout; no claim
        prefix = _promotion_prefix(promotion)
        marker = _assignment(units, "MARKER")
        if prefix == our_prefix:
            owner = (marker or "an unknown marker").split("-generated:", 1)[0].strip("# ")
            problems.append(f"{name}: pinned release promotes {prefix}-named units ({owner}); "
                            "its next promotion will rewrite our units")
        started = set()
        if wake is not None:
            started |= _start_prefixes(wake, unit=True, timer=False)
        if arm is not None:
            started |= _start_prefixes(arm, unit=False, timer=True)
        foreign = sorted(p for p in started if p == our_prefix)
        if foreign:
            problems.append(f"{name}: pinned release starts our {' and '.join(f'{p}-*' for p in foreign)} units; "
                            "restore its own plant prefix")
    return problems
