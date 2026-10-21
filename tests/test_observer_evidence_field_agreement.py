"""The field-agreement detector must name the fields the plant stops printing.

``checks/observer-view-agreement.py`` compares the plant's published evidence
with the reviewed tree's at the level of *projections* — which observations are
present. A line that is present but incomplete is invisible to it: the
projection is there, so the plant reads as agreeing. The observer bundle is
refreshed one module at a time, so that state is reachable — on 2026-10-21,
refreshing the three files that make the evidence leg run left the bundle's
``initiative.py`` stale, and every role row then lacked the
``checked-evidence-age`` field the reviewed tree prints, while the projection
check reported agreement.

These tests pin the detector's decisions on synthetic packages, so the readings
are fixed by the fixture rather than by the machine:

* a published line that lacks a field the reviewed tree publishes reads the
  field missing, by name;
* a published line that carries every field reads agreement, and a value that
  moved between the two samples is not a disagreement;
* a field the plant publishes and the reviewed tree does not reads extra;
* a reviewed line the plant does not publish reads missing;
* a frame, a package or a reviewed tree that cannot be read reads UNKNOWN,
  never a verdict.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "observer-evidence-field-agreement.py"
PACKAGE = "mishe_tauftauf"
SLUG = "research-methods"

_CHECKER = None


def _load_checker():
    global _CHECKER
    if _CHECKER is None:
        spec = importlib.util.spec_from_file_location(
            "_observer_evidence_field_agreement_checker", CHECK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CHECKER = module
    return _CHECKER


CHECKER = _load_checker()


def write_package(root: Path, *, field: str | None, extra_field: str | None = None) -> Path:
    """A package whose evidence leg prints a role row with given fields.

    ``field`` names the field the row binds; ``None`` means the row binds no
    field at all. ``extra_field`` adds a field the reviewed tree does not
    publish.
    """
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    bindings = []
    if field:
        bindings.append("%s=none" % field)
    if extra_field:
        bindings.append("%s=x" % extra_field)
    row = "wakes=1" + (" " + " ".join(bindings) if bindings else "")
    body = [
        "def lines(home):",
        "    return ['SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z',",
        "            'body-research: OBSERVED; %s evidence-UNKNOWN=0']" % row,
    ]
    (package / "follow_through.py").write_text("\n".join(body) + "\n")
    return root


def write_frame(home: Path, slug: str, body: list) -> Path:
    """A published dashboard frame carrying an evidence block."""
    frame = ("OBSERVATION — deterministic facts\n"
             "MIND WALL — shared follow-through evidence; not an authored wall\n"
             + "\n".join(body) + "\n"
             "MIND WALL — edited notes; sensor truth is above\n"
             "wall text\n")
    dashboards = home / "dashboards"
    dashboards.mkdir(parents=True, exist_ok=True)
    path = dashboards / (slug + ".json")
    path.write_text(json.dumps({
        "version": 1, "slug": slug, "home": str(home), "created": "2026-10-21T00:00:00Z",
        "frame": frame, "ok": True,
        "sha256": __import__("hashlib").sha256(frame.encode()).hexdigest(),
    }), encoding="utf-8")
    return path


def run_check(home: Path, reference: Path, slug: str = SLUG) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--slug", slug,
         "--reference-src", str(reference)],
        capture_output=True, text=True, timeout=120)


def test_missing_field_is_named(tmp_path):
    """A published row without the reviewed tree's field reads that field missing."""
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z",
                              "body-research: OBSERVED; wakes=1 evidence-UNKNOWN=0"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 2, result.stdout
    assert "MISSING FIELD" in result.stdout
    assert "checked-evidence-age" in result.stdout


def test_agreeing_fields_read_agreement(tmp_path):
    """A published row carrying every field reads agreement."""
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z",
                              "body-research: OBSERVED; wakes=1 checked-evidence-age=none "
                              "evidence-UNKNOWN=0"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 0, result.stdout
    assert "AGREE" in result.stdout


def test_a_moved_value_is_not_a_disagreement(tmp_path):
    """A counter that differs between the two samples is not a field difference."""
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z",
                              "body-research: OBSERVED; wakes=99 checked-evidence-age=none "
                              "evidence-UNKNOWN=0"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 0, result.stdout


def test_extra_field_is_named(tmp_path):
    """A field the plant publishes and the reviewed tree does not reads extra."""
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z",
                              "body-research: OBSERVED; wakes=1 checked-evidence-age=none "
                              "extra-field=x evidence-UNKNOWN=0"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 2, result.stdout
    assert "EXTRA FIELD" in result.stdout
    assert "extra-field" in result.stdout


def test_missing_line_is_named(tmp_path):
    """A reviewed line the plant does not publish reads missing."""
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 2, result.stdout
    assert "MISSING LINE" in result.stdout
    assert "body-research" in result.stdout


def test_the_plant_report_segment_is_outside_the_evidence_block(tmp_path):
    """The plant's own check report is not a projection of the reviewed tree.

    The published frame puts the plant's report segment after the evidence
    block.  A reader that stopped the block at the next ``MIND WALL`` heading
    swallowed that report, so the plant's live observation read as an extra
    line of the reviewed tree — a RED that said nothing about the tree.
    """
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z",
                              "body-research: OBSERVED; wakes=1 checked-evidence-age=none "
                              "evidence-UNKNOWN=0",
                              "SYSTEM ZERO",
                              "FAIL witness overdue pending wake docs"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 0, result.stdout
    assert "AGREE" in result.stdout
    assert "OUTSIDE" in result.stdout
    assert "SYSTEM ZERO" in result.stdout


def test_an_extra_line_inside_the_block_is_named(tmp_path):
    """A line the reviewed tree does not publish, inside the block, reads extra.

    The plant's own projection-level check reports such a line, so this
    detector must not disagree with it about the same frame: a check that
    stays silent where a peer check reports a difference is a false green.
    """
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z",
                              "body-research: OBSERVED; wakes=1 checked-evidence-age=none "
                              "evidence-UNKNOWN=0",
                              "GHOST: a line the reviewed tree does not publish"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 2, result.stdout
    assert "EXTRA LINE" in result.stdout
    assert "GHOST" in result.stdout


def test_a_renderer_failure_line_is_outside_the_block(tmp_path):
    """A failed renderer is the plant's report, not a line of the block."""
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z",
                              "body-research: OBSERVED; wakes=1 checked-evidence-age=none "
                              "evidence-UNKNOWN=0",
                              "RENDERER: RED the renderer failed"])
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 0, result.stdout
    assert "OUTSIDE" in result.stdout
    assert "RENDERER: RED" in result.stdout


def test_absent_frame_reads_unknown(tmp_path):
    """No published frame reads UNKNOWN, never a verdict."""
    home = tmp_path / "home"
    home.mkdir()
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_malformed_frame_reads_unknown(tmp_path):
    """A frame that is not a dashboard record reads UNKNOWN."""
    home = tmp_path / "home"
    (home / "dashboards").mkdir(parents=True)
    (home / "dashboards" / (SLUG + ".json")).write_text("{not json", encoding="utf-8")
    reference = write_package(tmp_path / "ref", field="checked-evidence-age")
    result = run_check(home, reference)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_unreadable_reference_reads_unknown(tmp_path):
    """A reviewed tree that cannot be read reads UNKNOWN."""
    home = tmp_path / "home"
    home.mkdir()
    write_frame(home, SLUG, ["SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z"])
    reference = tmp_path / "empty"
    reference.mkdir()
    result = run_check(home, reference)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_field_names_are_compared_not_values():
    """The unit of comparison is the field name, never the value."""
    checker = CHECKER
    assert checker.fields("wakes=42 checked-evidence-age=none") == {
        "wakes", "checked-evidence-age"}
    assert checker.fields("settled[changed=1,verified=2]") == {"settled", "changed", "verified"}
    assert checker.fields("SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z") == {
        "checked_at"}


def test_the_projection_check_cannot_see_a_missing_field():
    """The gap this detector covers is real: the projection check reads AGREE.

    The plant's own projection-level check compares which observations are
    present. A published line that lacks a field still carries its projection,
    so that check reads agreement while the field is missing. This pins that
    the two checks do not read the same thing.
    """
    view = REPO / "checks" / "observer-view-agreement.py"
    spec = importlib.util.spec_from_file_location("_observer_view_agreement", view)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    published = ["body-research: OBSERVED; wakes=1 evidence-UNKNOWN=0"]
    reviewed = ["body-research: OBSERVED; wakes=2 checked-evidence-age=none evidence-UNKNOWN=0"]
    shown = module.projections(published)
    wanted = module.projections(reviewed)
    assert not [key for key in wanted if key not in shown]
    assert not [key for key in shown if key not in wanted]
    # The field-level comparison sees what the projection-level one cannot.
    checker = CHECKER
    shown_groups = checker.groups(published)
    wanted_groups = checker.groups(reviewed)
    missing = [name for key, group in wanted_groups.items()
               for name in group[2] - shown_groups[key][2]]
    assert missing == ["checked-evidence-age"]
