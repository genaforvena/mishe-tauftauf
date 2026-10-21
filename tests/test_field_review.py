#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Tests for the field-review lane's deterministic parts.

The instrument under test is the tracked research tool
``research/field-review/field_review.py``, loaded by path because it is a
research instrument rather than an importable package module.

These tests cover the parts that must not be a judgement call: which cell is
selected, what the coverage ledger derives, and what the landing contract
rejects. The reading itself is the mind's work and cannot be tested here.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "research" / "field-review" / "field_review.py"
SPEC = importlib.util.spec_from_file_location("field_review", TOOL)
assert SPEC and SPEC.loader
field_review = importlib.util.module_from_spec(SPEC)
# The dataclasses in the instrument resolve their annotations through the
# module's own entry, so the module is registered before it executes.
sys.modules[SPEC.name] = field_review
SPEC.loader.exec_module(field_review)


REGISTRY = {
    "version": 1,
    "angles": [
        {"id": "recent", "question": "a recent result", "read": "the primary source"},
        {"id": "misread", "question": "an idea misread", "read": "both texts"},
    ],
    "fields": [
        {"id": "alpha", "title": "Alpha", "plant_part": "src", "keywords": ["alpha"]},
        {"id": "beta", "title": "Beta", "plant_part": "docs", "keywords": ["beta"]},
    ],
}

GOOD_REVIEW = """---
field: alpha
angle: recent
date: 2026-10-21
status: applied
owner: genome
target: src/example.py
---

# Alpha / recent

## What we read

Paper title, author, 2026, https://example.org/paper — sections 2 and 3.

## What this plant already does

src/example.py already tracks the word alpha in its name only.

## What is new here

The paper's decay coefficient is new to this plant.

## Application

target: src/example.py

Add the decay coefficient to the existing reading.
"""


def _repo(root: Path, reviews: dict[str, str] | None = None) -> Path:
    repo = root / "repo"
    (repo / "research" / "field-review" / "reviews").mkdir(parents=True)
    (repo / "research" / "field-review" / "fields.json").write_text(
        json.dumps(REGISTRY), encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("# alpha lives here\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("beta note\n", encoding="utf-8")
    for name, text in (reviews or {}).items():
        (repo / "research" / "field-review" / "reviews" / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.org",
                    "-c", "user.name=test", "commit", "-qm", "seed"], check=True)
    return repo


class RegistryTest(unittest.TestCase):
    def test_grid_is_the_cross_product_of_fields_and_angles(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            cells = field_review.load_registry(repo / "research" / "field-review" / "fields.json")
            self.assertEqual(len(cells), 4)
            self.assertEqual({cell.key for cell in cells},
                             {"alpha--recent", "alpha--misread", "beta--recent", "beta--misread"})

    def test_malformed_registry_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fields.json"
            path.write_text(json.dumps({"version": 2, "fields": [], "angles": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                field_review.load_registry(path)


class SelectionTest(unittest.TestCase):
    def test_next_cell_is_the_first_uncovered_in_registry_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            cells = field_review.load_registry(repo / "research" / "field-review" / "fields.json")
            cell = field_review.next_cell(cells, field_review.covered_cells(repo))
            self.assertEqual(cell.key, "alpha--recent")
            self.assertEqual(cell.field_title, "Alpha")
            brief = field_review.render_brief(cell, [])
            self.assertIn("research/field-review/reviews/alpha--recent--", brief)

    def test_a_landed_review_covers_its_cell_and_the_next_one_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": GOOD_REVIEW})
            cells = field_review.load_registry(repo / "research" / "field-review" / "fields.json")
            covered = field_review.covered_cells(repo)
            self.assertIn("alpha--recent", covered)
            cell = field_review.next_cell(cells, covered)
            self.assertEqual(cell.key, "alpha--misread")

    def test_an_uncommitted_review_does_not_cover_its_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            path = repo / "research" / "field-review" / "reviews" / "alpha--recent--2026-10-21.md"
            path.write_text(GOOD_REVIEW, encoding="utf-8")
            self.assertEqual(field_review.covered_cells(repo), {})

    def test_a_review_without_front_matter_covers_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"stray.md": "# no front matter\n"})
            self.assertEqual(field_review.covered_cells(repo), {})
            cells = field_review.load_registry(repo / "research" / "field-review" / "fields.json")
            self.assertEqual(field_review.next_cell(cells, field_review.covered_cells(repo)).key,
                             "alpha--recent")

    def test_exhausted_grid_reports_no_next_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            reviews = {}
            for key in ("alpha--recent", "alpha--misread", "beta--recent", "beta--misread"):
                field, angle = key.split("--")
                reviews[f"{key}--2026-10-21.md"] = GOOD_REVIEW.replace(
                    "field: alpha", f"field: {field}").replace(
                    "angle: recent", f"angle: {angle}")
            repo = _repo(Path(tmp), reviews)
            cells = field_review.load_registry(repo / "research" / "field-review" / "fields.json")
            self.assertIsNone(field_review.next_cell(cells, field_review.covered_cells(repo)))


class EmbodimentSweepTest(unittest.TestCase):
    def test_sweep_finds_where_the_plant_already_uses_the_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            hits = field_review.embodiment_hits(repo, ("alpha",))
            self.assertTrue(hits)
            self.assertTrue(any(hit.path == "src/example.py" for hit in hits))
            self.assertFalse(field_review.embodiment_hits(repo, ("nonexistent-term",)))

    def test_brief_reports_the_sweep_and_the_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            cells = field_review.load_registry(repo / "research" / "field-review" / "fields.json")
            cell = cells[0]
            brief = field_review.render_brief(cell, field_review.embodiment_hits(repo, cell.keywords))
            self.assertIn("ALREADY EMBODIED", brief)
            self.assertIn("research/field-review/reviews/alpha--recent--", brief)
            self.assertIn("## Application", brief)


class LandingContractTest(unittest.TestCase):
    def test_a_complete_committed_review_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": GOOD_REVIEW})
            self.assertEqual(field_review.validate_tree(repo), {})

    def test_missing_front_matter_fields_are_reported(self):
        broken = GOOD_REVIEW.replace("owner: genome\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": broken})
            problems = field_review.validate_tree(repo)
            self.assertTrue(any("front matter missing owner" in p for p in problems["alpha--recent--2026-10-21.md"]))

    def test_a_missing_source_url_is_reported(self):
        broken = GOOD_REVIEW.replace("https://example.org/paper", "a paper I read")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": broken})
            problems = field_review.validate_tree(repo)["alpha--recent--2026-10-21.md"]
            self.assertIn("no source URL", problems)

    def test_a_missing_section_is_reported(self):
        broken = GOOD_REVIEW.replace("## What is new here", "## Something else")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": broken})
            problems = field_review.validate_tree(repo)["alpha--recent--2026-10-21.md"]
            self.assertTrue(any("missing section" in p for p in problems))

    def test_both_application_and_discard_is_refused(self):
        broken = GOOD_REVIEW + "\n## Discard\n\nno\n"
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": broken})
            problems = field_review.validate_tree(repo)["alpha--recent--2026-10-21.md"]
            self.assertTrue(any("exactly one of the Application or Discard" in p for p in problems))

    def test_a_discard_with_one_line_passes(self):
        discard = """---
field: alpha
angle: recent
date: 2026-10-21
status: discarded
owner: genome
---

# Alpha / recent

## What we read

Paper title, author, 2026, https://example.org/paper — section 2.

## What this plant already does

src/example.py already carries the whole mechanism.

## What is new here

Nothing this plant lacks.

## Discard

The plant already implements this, so there is nothing to apply.
"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": discard})
            self.assertEqual(field_review.validate_tree(repo), {})

    def test_an_empty_discard_is_refused(self):
        discard = GOOD_REVIEW.replace("status: applied", "status: discarded").replace(
            "## Application", "## Discard").replace(
            "target: src/example.py\n", "").replace(
            "Add the decay coefficient to the existing reading.", "")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": discard})
            problems = field_review.validate_tree(repo)["alpha--recent--2026-10-21.md"]
            self.assertTrue(any("one line" in p for p in problems))

    def test_an_application_target_outside_the_repository_is_refused(self):
        broken = GOOD_REVIEW.replace("target: src/example.py", "target: ../outside.py")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": broken})
            problems = field_review.validate_tree(repo)["alpha--recent--2026-10-21.md"]
            self.assertTrue(any("does not exist" in p for p in problems))

    def test_an_uncommitted_review_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            path = repo / "research" / "field-review" / "reviews" / "alpha--recent--2026-10-21.md"
            path.write_text(GOOD_REVIEW, encoding="utf-8")
            problems = field_review.validate_tree(repo)["alpha--recent--2026-10-21.md"]
            self.assertTrue(any("not committed to the shared branch" in p for p in problems))

    def test_an_unknown_owner_is_refused(self):
        broken = GOOD_REVIEW.replace("owner: genome", "owner: the-operator")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": broken})
            problems = field_review.validate_tree(repo)["alpha--recent--2026-10-21.md"]
            self.assertTrue(any("owner is not a plant role" in p for p in problems))


class CommandTest(unittest.TestCase):
    def test_check_exits_zero_on_a_clean_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": GOOD_REVIEW})
            result = subprocess.run(
                ["python3", str(TOOL), "--repo", str(repo), "check"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_check_exits_two_on_a_broken_review(self):
        broken = GOOD_REVIEW.replace("https://example.org/paper", "a paper I read")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp), {"alpha--recent--2026-10-21.md": broken})
            result = subprocess.run(
                ["python3", str(TOOL), "--repo", str(repo), "check"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("no source URL", result.stdout)

    def test_next_prints_the_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            result = subprocess.run(
                ["python3", str(TOOL), "--repo", str(repo), "next"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CELL alpha--recent", result.stdout)

    def test_an_unreadable_grid_is_unknown_not_a_default_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "research" / "field-review" / "fields.json").write_text("{", encoding="utf-8")
            result = subprocess.run(
                ["python3", str(TOOL), "--repo", str(repo), "next"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("UNKNOWN", result.stderr)

    def test_a_reader_that_stops_reading_is_not_a_tool_failure(self):
        """`coverage | head` closes the pipe; the tool must not traceback."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            reader = subprocess.Popen(
                ["python3", str(TOOL), "--repo", str(repo), "coverage"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            reader.stdout.readline()
            reader.stdout.close()
            reader.wait(timeout=30)
            stderr = reader.stderr.read().decode("utf-8", "replace")
            self.assertNotIn("BrokenPipeError", stderr)
            self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
