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
  rather than a review.

What counts as landed is read from the branch, not from the index and not from
the working tree. ``git ls-files`` describes the index, so a staged-but-not-
committed review would read as landed; and a review that was edited after it
landed would read as the bytes on disk. Both are the retired mesh's failure
mode in miniature, so coverage is derived from the branch's own bytes:

* coverage = the reviews the branch carries, read from the branch;
* the scheduled check = the reviewed tree's reviews, the reviewed tree's
  grid, and the reviewed tree's targets;
* a review in the working tree that the branch does not carry is in flight
  until it has waited longer than the grace period, and after that it is a
  review that never landed.

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
import time
from dataclasses import dataclass
from pathlib import Path

REPO_MARKERS = ("src", "docs", "checks", "tools", "research", "coordination")
SCAN_SUFFIXES = (".py", ".md", ".json", ".ts")
MAX_HITS_PER_KEYWORD = 10
REQUIRED_SECTIONS = ("## What we read", "## What this plant already does",
                     "## What is new here")
READING_HEADING = "## What we read"
APPLICATION_HEADING = "## Application"
DISCARD_HEADING = "## Discard"
ROLES = ("genome", "health", "witness", "discover", "senses", "docs",
         "research-methods", "pub", "body-research", "inference-research",
         "field-review")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FRONT_MATTER_END = "---"
UNLANDED_GRACE_SECONDS = 1800
"""How long a review may wait on the working tree before it is a problem.

The mind writes a review and commits it; that gap is seconds. A review that
has waited this long was never landed, which is the retired mesh's 245-of-376
failure and the reason this lane counts the branch rather than the disk.
"""


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


def _git(repo: Path, args: list[str]) -> bytes:
    """Run a git command in the repository and return its stdout.

    The caller decides what an empty answer means; a git failure is an empty
    answer, never an exception, so a broken repository reads as "nothing
    landed" rather than as a crash.
    """
    try:
        result = subprocess.run(["git", "-C", str(repo), *args],
                                capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return b""
    if result.returncode:
        return b""
    return result.stdout


def head_paths(repo: Path, relative: str) -> set[str]:
    """The paths the branch carries under a directory, relative to the root.

    ``git ls-tree`` reads the branch. ``git ls-files`` would read the index,
    where a staged review that was never committed would already look landed.
    """
    out = _git(repo, ["ls-tree", "-r", "--name-only", "HEAD", "--", relative])
    return {line for line in out.decode("utf-8", "replace").splitlines() if line}


def head_bytes(repo: Path, relative: str) -> bytes | None:
    """The branch's bytes for one path, or None when the branch has none."""
    out = _git(repo, ["show", f"HEAD:{relative}"])
    return out if out else None


def head_has_file(repo: Path, relative: str) -> bool:
    """Whether the branch carries this path as a file (not a directory)."""
    out = _git(repo, ["cat-file", "-t", f"HEAD:{relative}"])
    return out.decode("utf-8", "replace").strip() == "blob"


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


def reviews_relative(repo: Path) -> str:
    return str(reviews_dir(repo).relative_to(repo))


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
    return values


def review_files(repo: Path) -> list[Path]:
    """Every review file present in the working tree, landed or not."""
    directory = reviews_dir(repo)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix == ".md")


def landed_review_names(repo: Path) -> list[str]:
    """The review files the branch carries, relative to the repository."""
    prefix = reviews_relative(repo)
    return sorted(p for p in head_paths(repo, prefix) if p.endswith(".md"))


def covered_cells(repo: Path) -> dict[str, str]:
    """Covered cell key -> the branch's review that covers it.

    A review covers a cell only when its front matter names one; a file
    without usable front matter covers nothing and is reported by the contract
    check rather than silently counting as progress.
    """
    covered: dict[str, str] = {}
    for name in landed_review_names(repo):
        blob = head_bytes(repo, name)
        if blob is None:
            continue
        matter = front_matter(blob.decode("utf-8", "replace"))
        field = matter.get("field")
        angle = matter.get("angle")
        if field and angle:
            covered[f"{field}--{angle}"] = name
    return covered


def next_cell(cells: list[Cell], covered: dict[str, str]) -> Cell | None:
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


def section_body(text: str, heading: str) -> str:
    """The text under a heading, up to the next heading of the same level."""
    if heading not in text:
        return ""
    after = text.split(heading, 1)[1]
    lines: list[str] = []
    for line in after.splitlines():
        if line.startswith("## "):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def validate_review_text(name: str, text: str, repo: Path, *,
                         target_exists=None) -> list[str]:
    """Every way this review breaks the content contract.

    The contract: front matter names the cell, the date, the status and the
    owner; the body reads the source, states what the plant already does,
    states what is new, and either names one existing repository FILE as the
    application or discards the cell in one line; the source URL is in the
    reading section, not somewhere else in the file.

    ``target_exists`` decides what "an existing file" means: a callable for
    the reviewed tree, or None for the working tree.
    """
    problems: list[str] = []
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
    reading = section_body(text, READING_HEADING)
    if not reading:
        problems.append("the What we read section is empty")
    elif not URL_RE.search(reading):
        problems.append("the What we read section cites no source URL")
    has_application = APPLICATION_HEADING in text
    has_discard = DISCARD_HEADING in text
    if has_application == has_discard:
        problems.append("exactly one of the Application or Discard sections is required")
    if has_application:
        target = ""
        for line in section_body(text, APPLICATION_HEADING).splitlines():
            if line.startswith("target:"):
                target = line.split(":", 1)[1].strip()
                break
        if not target:
            problems.append("the Application section must name target: <repository path>")
        else:
            if target_exists is None:
                candidate = (repo / target).resolve()
                exists = (candidate.is_relative_to(repo.resolve())
                          and candidate.is_file())
            else:
                exists = target_exists(target)
            if not exists:
                problems.append(f"the application target is not an existing file: {target}")
    if has_discard:
        body = [line for line in section_body(text, DISCARD_HEADING).splitlines()
                if line.strip() and not line.startswith("#")]
        if not body:
            problems.append("the Discard section must say why in one line")
    return problems


def validate_review(path: Path, repo: Path) -> list[str]:
    """The content contract for a review in the working tree."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"unreadable review: {exc}"]
    # The target must be a file the branch carries, the same test the
    # landing contract checker applies to the reviewed tree.
    return validate_review_text(path.name, text, repo,
                                target_exists=lambda target: head_has_file(repo, target))


def validate_tree(repo: Path) -> dict[str, list[str]]:
    """The content contract over every review in the working tree."""
    results: dict[str, list[str]] = {}
    for path in review_files(repo):
        problems = validate_review(path, repo)
        if problems:
            results[path.name] = problems
    return results


def unlanded_reviews(repo: Path, grace_seconds: int = UNLANDED_GRACE_SECONDS,
                      now: float | None = None) -> list[tuple[str, float]]:
    """Reviews the working tree holds that the branch does not, and their age.

    A review that has waited past the grace period was never landed. That is
    the retired mesh's failure and the reason the branch, not the disk, is
    the authority on what has landed.
    """
    landed = set(landed_review_names(repo))
    moment = time.time() if now is None else now
    waiting: list[tuple[str, float]] = []
    for path in review_files(repo):
        name = str(path.relative_to(repo))
        if name in landed:
            continue
        try:
            age = moment - path.stat().st_mtime
        except OSError:
            continue
        if age > grace_seconds:
            waiting.append((name, age))
    return sorted(waiting)


def edited_landed_reviews(repo: Path) -> list[str]:
    """Landed reviews whose working-tree bytes differ from the branch's."""
    edited: list[str] = []
    for name in landed_review_names(repo):
        blob = head_bytes(repo, name)
        path = repo / name
        if blob is None or not path.is_file():
            continue
        try:
            current = path.read_bytes()
        except OSError:
            continue
        if current != blob:
            edited.append(name)
    return edited


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
        "The source URL belongs in the What we read section. The application target"
        " must be an existing FILE in the repository.",
        "Then commit the review on main, and route the proposal to the owner role by"
        " addressed message. A review that is not committed has not landed.",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("next", help="print the next uncovered cell and its sweep")
    sub.add_parser("coverage", help="print covered and pending cells")
    sub.add_parser("check", help="check every review against the landing contract")
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
        print(f"covered {len(covered)} of {len(cells)} cells (the branch's reviews)")
        for cell in cells:
            state = covered.get(cell.key)
            print(f"  {'covered' if state else 'PENDING'} {cell.key}"
                  + (f" -> {state}" if state else ""))
        waiting = unlanded_reviews(repo)
        if waiting:
            print(f"unlanded: {len(waiting)} review(s) in the working tree")
            for name, age in waiting:
                print(f"  waiting {age:.0f}s: {name}")
        return 0
    if command == "check":
        problems = validate_tree(repo)
        for name in sorted(problems):
            print(f"FAIL {name}")
            for problem in problems[name]:
                print(f"  {problem}")
        if problems:
            return 2
        landed = len(landed_review_names(repo))
        waiting = unlanded_reviews(repo)
        edited = edited_landed_reviews(repo)
        print(f"PASS — {len(review_files(repo))} review(s) satisfy the landing contract;"
              f" {landed} landed on the branch")
        if waiting:
            print(f"  {len(waiting)} review(s) have not landed yet")
            for name, age in waiting:
                print(f"  waiting {age:.0f}s: {name}")
        for name in edited:
            print(f"  landed review edited after landing: {name}")
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
