#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Field-review lane: deterministic cell selection, embodiment sweep, contract.

The lane reads one real source per (field x angle) cell and lands one review
that either names a concrete change in THIS repository or is discarded in one
line. This instrument owns the parts that must not be a judgement call:

* the grid comes from a literal registry file, not from a model's memory;
* the next cell is the first uncovered cell in registry order, so two runs on
  the same tree name the same cell;
* the "already embodied?" sweep is a keyword search over the tracked tree, so
  a reading starts from what this plant already does;
* a landed review is checked against the landing contract, so a review that
  cites nothing, names no existing file, or omits its sections is a failure
  rather than a review;
* a landed review must be committed to the shared branch, so a review cannot
  sit on disk as an unlanded promise.

Coverage is derived from the review files themselves, so there is no second
ledger that can drift from the artifacts it describes.

Usage:
    python3 research/field-review/field_review.py next    [--repo DIR]
    python3 research/field-review/field_review.py coverage [--repo DIR]
    python3 research/field-review/field_review.py check    [--repo DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_MARKERS = ("src", "docs", "checks", "tools", "research", "coordination")
SCAN_SUFFIXES = (".py", ".md", ".json", ".ts")
MAX_HITS_PER_KEYWORD = 10
REQUIRED_SECTIONS = ("## What we read", "## What this plant already does",
                     "## What is new here")
APPLICATION_HEADING = "## Application"
DISCARD_HEADING = "## Discard"
ROLES = ("genome", "health", "witness", "discover", "senses", "docs",
         "research-methods", "pub", "body-research", "inference-research",
         "field-review")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FRONT_MATTER_END = "---"


@dataclass(frozen=True)
class Cell:
    """One (field, angle) pair with its question and the plant part it touches."""

    field_id: str
    field_title: str
    plant_part: str
    keywords: tuple[str, ...]
    angle_id: str
    angle_question: str
    angle_read: str

    @property
    def key(self) -> str:
        return f"{self.field_id}--{self.angle_id}"


@dataclass(frozen=True)
class Hit:
    """One place in the tracked tree where a field keyword already occurs."""

    path: str
    line: int
    text: str


def load_registry(path: Path) -> list[Cell]:
    """Read the literal grid. A malformed registry is an error, never a default grid."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("field registry must be version 1")
    fields = data.get("fields")
    angles = data.get("angles")
    if not isinstance(fields, list) or not fields or not isinstance(angles, list) or not angles:
        raise ValueError("field registry needs fields and angles")
    for name, group in (("field", fields), ("angle", angles)):
        for item in group:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError(f"every {name} needs an id")
    if len({item["id"] for item in angles}) != len(angles):
        raise ValueError("angle ids must be unique")
    if len({item["id"] for item in fields}) != len(fields):
        raise ValueError("field ids must be unique")
    cells: list[Cell] = []
    for field in fields:
        keywords = field.get("keywords")
        if not isinstance(keywords, list) or not keywords or not all(isinstance(k, str) and k for k in keywords):
            raise ValueError(f"field {field['id']} needs keyword strings")
        for angle in angles:
            cells.append(Cell(
                field_id=field["id"],
                field_title=str(field.get("title", field["id"])),
                plant_part=str(field.get("plant_part", "")),
                keywords=tuple(keywords),
                angle_id=angle["id"],
                angle_question=str(angle.get("question", "")),
                angle_read=str(angle.get("read", "")),
            ))
    return cells


def reviews_dir(repo: Path) -> Path:
    return repo / "research" / "field-review" / "reviews"


def front_matter(text: str) -> dict[str, str]:
    """The review's declared metadata, or an empty mapping when it has none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_END:
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == FRONT_MATTER_END:
            return values
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip()
    return {}


def review_files(repo: Path) -> list[Path]:
    """Every review file present in the working tree, committed or not."""
    directory = reviews_dir(repo)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix == ".md")


def committed_paths(repo: Path, relative: str) -> set[str]:
    """The paths the shared branch already carries, relative to the repository.

    One listing, not one call per file: the coverage and the contract check
    both need it, and a per-file call would make the check quadratic in the
    number of reviews.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--", relative],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode:
        return set()
    return {line for line in result.stdout.decode("utf-8", "replace").splitlines() if line}


def landed_review_files(repo: Path) -> list[Path]:
    """Review files that are on the shared branch.

    A review on disk but not on the branch is an unlanded promise, which is
    how the retired mesh's review lane lost 245 of 376 artifacts. Coverage is
    therefore counted from the landed files only.
    """
    directory = reviews_dir(repo)
    if not directory.is_dir():
        return []
    prefix = str(directory.relative_to(repo))
    committed = committed_paths(repo, prefix)
    landed: list[Path] = []
    for path in review_files(repo):
        if str(path.relative_to(repo)) in committed:
            landed.append(path)
    return landed


def covered_cells(repo: Path) -> dict[str, Path]:
    """Covered cell key -> the landed review that covers it.

    A review covers a cell only when its front matter names one; a file
    without usable front matter covers nothing and is reported by the contract
    check rather than silently counting as progress.
    """
    covered: dict[str, Path] = {}
    for path in landed_review_files(repo):
        matter = front_matter(path.read_text(encoding="utf-8"))
        field = matter.get("field")
        angle = matter.get("angle")
        if field and angle:
            covered[f"{field}--{angle}"] = path
    return covered


def next_cell(cells: list[Cell], covered: dict[str, Path]) -> Cell | None:
    """First uncovered cell in registry order; None when the grid is exhausted."""
    for cell in cells:
        if cell.key not in covered:
            return cell
    return None


def tracked_files(repo: Path) -> list[Path]:
    """Files in the tracked tree that can carry a field keyword."""
    found: list[Path] = []
    for marker in REPO_MARKERS:
        base = repo / marker
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in SCAN_SUFFIXES:
                found.append(path)
    return sorted(found)


def embodiment_hits(repo: Path, keywords: tuple[str, ...]) -> list[Hit]:
    """Where this plant already uses the field's vocabulary.

    This is the sweep that keeps a review from rediscovering what the plant
    already does. It is a keyword search over the tree, so it reports
    occurrence, never meaning.
    """
    hits: list[Hit] = []
    for path in tracked_files(repo):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            for keyword in keywords:
                if keyword.lower() in lowered:
                    hits.append(Hit(str(path.relative_to(repo)), number, line.strip()[:200]))
                    break
    return hits


def hits_by_keyword(hits: list[Hit], keywords: tuple[str, ...]) -> dict[str, list[Hit]]:
    grouped: dict[str, list[Hit]] = {keyword: [] for keyword in keywords}
    for hit in hits:
        lowered = hit.text.lower()
        for keyword in keywords:
            if keyword.lower() in lowered and len(grouped[keyword]) < MAX_HITS_PER_KEYWORD:
                grouped[keyword].append(hit)
    return grouped


def is_committed(repo: Path, path: Path, committed: set[str] | None = None) -> bool:
    """Whether the shared branch already carries this file.

    A review on disk but not on the branch is an unlanded promise, which is
    how the retired mesh's review lane lost 245 of 376 artifacts.
    """
    if committed is not None:
        return str(path.relative_to(repo)) in committed
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--",
             str(path.relative_to(repo))],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def validate_review(path: Path, repo: Path, committed: set[str] | None = None) -> list[str]:
    """Every way this review breaks the landing contract.

    The contract: front matter names the cell, the date, the status and the
    owner; the body reads the source, states what the plant already does,
    states what is new, and either names one existing repository file as the
    application or discards the cell in one line; a source URL is present; the
    file is committed to the shared branch.
    """
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"unreadable review: {exc}"]
    matter = front_matter(text)
    for key in ("field", "angle", "date", "status", "owner"):
        if not matter.get(key):
            problems.append(f"front matter missing {key}")
    date = matter.get("date", "")
    if date and not DATE_RE.match(date):
        problems.append(f"date is not YYYY-MM-DD: {date}")
    status = matter.get("status", "")
    if status and status not in ("applied", "discarded"):
        problems.append(f"status must be applied or discarded: {status}")
    owner = matter.get("owner", "")
    if owner and owner not in ROLES:
        problems.append(f"owner is not a plant role: {owner}")
    for heading in REQUIRED_SECTIONS:
        if heading not in text:
            problems.append(f"missing section {heading}")
    if not URL_RE.search(text):
        problems.append("no source URL")
    has_application = APPLICATION_HEADING in text
    has_discard = DISCARD_HEADING in text
    if has_application == has_discard:
        problems.append("exactly one of the Application or Discard sections is required")
    if has_application:
        target = ""
        for line in text.split(APPLICATION_HEADING, 1)[1].splitlines():
            if line.startswith("target:"):
                target = line.split(":", 1)[1].strip()
                break
        if not target:
            problems.append("the Application section must name target: <repository path>")
        else:
            candidate = (repo / target).resolve()
            if not candidate.is_relative_to(repo.resolve()) or not candidate.exists():
                problems.append(f"the application target does not exist in the repository: {target}")
    if has_discard:
        body = [line for line in text.split(DISCARD_HEADING, 1)[1].splitlines()
                if line.strip() and not line.startswith("#")]
        if not body:
            problems.append("the Discard section must say why in one line")
    if not is_committed(repo, path, committed):
        problems.append("the review is not committed to the shared branch")
    return problems


def validate_tree(repo: Path) -> dict[str, list[str]]:
    """Contract check over every review file, keyed by file name.

    Both the committed and the uncommitted reviews are checked: an uncommitted
    review is a review that has not landed, and the contract says so.
    """
    directory = reviews_dir(repo)
    committed = committed_paths(repo, str(directory.relative_to(repo))) if directory.is_dir() else set()
    results: dict[str, list[str]] = {}
    for path in review_files(repo):
        problems = validate_review(path, repo, committed)
        if problems:
            results[path.name] = problems
    return results


def render_brief(cell: Cell, hits: list[Hit]) -> str:
    """The prompt material for one cell: question, sweep, and the contract."""
    grouped = hits_by_keyword(hits, cell.keywords)
    lines = [
        f"CELL {cell.key}",
        f"FIELD: {cell.field_title}",
        f"PLANT PART: {cell.plant_part}",
        f"ANGLE ({cell.angle_id}): {cell.angle_question}",
        f"READ: {cell.angle_read}",
        "",
        "ALREADY EMBODIED (keyword occurrence in the tree; occurrence, not meaning):",
    ]
    for keyword, found in grouped.items():
        if found:
            for hit in found[:MAX_HITS_PER_KEYWORD]:
                lines.append(f"  {keyword}: {hit.path}:{hit.line}: {hit.text}")
        else:
            lines.append(f"  {keyword}: (no occurrence in the tree)")
    lines += [
        "",
        "LAND ONE OF:",
        "  a review naming an existing repository file as the application, or",
        "  a one-line discard of this cell.",
        "",
        "REVIEW FILE: research/field-review/reviews/"
        f"{cell.field_id}--{cell.angle_id}--<YYYY-MM-DD>.md",
        "FRONT MATTER: field, angle, date, status (applied|discarded), owner (a plant role),"
        " target (for applied)",
        "SECTIONS: " + " | ".join(REQUIRED_SECTIONS) + " | " + APPLICATION_HEADING
        + " or " + DISCARD_HEADING,
        "Then commit the review on main, and route the proposal to the owner role by addressed message.",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("next", help="print the next uncovered cell and its sweep")
    sub.add_parser("coverage", help="print covered and pending cells")
    sub.add_parser("check", help="check every landed review against the landing contract")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    registry = repo / "research" / "field-review" / "fields.json"
    try:
        cells = load_registry(registry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"UNKNOWN field registry unusable: {exc}", file=sys.stderr)
        return 2
    covered = covered_cells(repo)
    command = args.command or "next"
    if command == "coverage":
        print(f"covered {len(covered)} of {len(cells)} cells")
        for cell in cells:
            state = covered.get(cell.key)
            print(f"  {'covered' if state else 'PENDING'} {cell.key}"
                  + (f" -> {state.relative_to(repo)}" if state else ""))
        return 0
    if command == "check":
        problems = validate_tree(repo)
        for name, found in sorted(problems.items()):
            print(f"FAIL {name}")
            for problem in found:
                print(f"  {problem}")
        if problems:
            return 2
        print(f"PASS — {len(review_files(repo))} landed reviews satisfy the landing contract")
        return 0
    cell = next_cell(cells, covered)
    if cell is None:
        print("GRID EXHAUSTED — every field x angle cell has a landed review")
        return 0
    print(render_brief(cell, embodiment_hits(repo, cell.keywords)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # A reader that stops reading (`| head`) closes the pipe early. That is
        # the reader's choice, not a tool failure, and the traceback would
        # read as one.
        try:
            sys.stdout.close()
        except OSError:
            pass
        raise SystemExit(0)
