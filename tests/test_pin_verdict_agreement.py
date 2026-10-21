"""The pin-verdict detector must catch a fault the plant prints and the tree does not.

On 2026-10-20 the plant's dashboard read ``STATE: RED — runtime drift`` for
service roots that health had declared on purpose in
``health/declared-roots.json``. The pinned bytes predate that record, so the
declaration could not take effect and the plant printed a fault the reviewed
tree resolves. These tests pin the detector's behaviour on that class: it
reports the disagreement, it names the record the pinned bytes cannot read, and
it refuses to report a disagreement it cannot substantiate.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "pin-verdict-agreement.py"
PACKAGE = "mishe_tauftauf"
SLUG = "alpha"

# The service roots the capture returns: one unit on a root that is not the pin.
ROOTS = {"mishe-self-development-current-follow-through-watch.service": ["/site/bundle"]}

PACKAGE_SOURCE = '''\
import json
from pathlib import Path

VERDICT = {verdict!r}
LINE = "RUNTIME: pin=/pin services=/site/bundle " + VERDICT
ROOTS = {roots!r}


def _service_block(home: Path):
    return [], dict(ROOTS), "GREEN — listed services running"


def _runtime_state(home: Path, root: Path, service_roots=None):
    if service_roots is None:
        return "UNKNOWN", "RUNTIME: pin=/pin services=UNKNOWN"
    return VERDICT, LINE
'''


def write_package(src_root: Path, verdict: str, roots=ROOTS) -> Path:
    """A package whose runtime verdict answers as the given verdict."""
    package = src_root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "wall_view.py").write_text(
        PACKAGE_SOURCE.format(verdict=verdict, roots=dict(roots)))
    return src_root


def write_pin(home: Path, src_root: Path) -> Path:
    """The runtime pin record naming the bytes the plant runs."""
    record = {"version": 1, "source": str(src_root.parent), "sha": "0" * 40,
              "session": "mishe-self-development-current"}
    directory = home / "health"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "runtime-release.json").write_text(json.dumps(record))
    (directory / "declared-roots.json").write_text(json.dumps(
        {"version": 1, "owner": "health", "units": {}}))
    return src_root


def write_frame(home: Path, *, verdict="DRIFT", state="RED — runtime drift", digest=None,
                slug=SLUG) -> Path:
    """A published dashboard frame carrying a runtime verdict."""
    frame = "\n".join([
        "HEADLINE: GREEN — a synthetic plant",
        "RUNTIME: pin=/pin services=/site/bundle " + verdict,
        "STATE: " + state,
        "MIND WALL — edited notes; sensor truth is above",
        "notes",
    ]) + "\n"
    directory = home / "dashboards"
    directory.mkdir(parents=True, exist_ok=True)
    record = {"version": 1, "slug": slug, "home": str(home.resolve()),
              "created": "2026-10-20T23:40:00+00:00", "frame": frame, "ok": True,
              "sha256": digest if digest is not None
              else hashlib.sha256(frame.encode()).hexdigest()}
    path = directory / f"{slug}.json"
    path.write_text(json.dumps(record))
    return path


@pytest.fixture
def plant(tmp_path):
    home = tmp_path / "site"
    (home / "dashboards").mkdir(parents=True)
    return home


@pytest.fixture
def pin(tmp_path):
    """The pinned bytes, whose verdict is DRIFT: they predate the declaration."""
    return write_pin(tmp_path / "site", write_package(tmp_path / "pin" / "src", "DRIFT"))


@pytest.fixture
def repo(tmp_path):
    """A reviewed tree whose modules name the site's declaration record."""
    package = tmp_path / "reviewed" / "src" / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "runtime_source.py").write_text(
        'MANIFEST = "health/declared-roots.json"\n')
    return tmp_path / "reviewed"


def run_check(home: Path, pin: Path, reference: Path, extra=()):
    """Run the check the way a runner would, and return its result."""
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--slug", SLUG,
         "--reference-src", str(reference), *extra],
        capture_output=True, text=True)


def test_phantom_fault_is_reported(plant, pin, tmp_path):
    """The plant prints a fault the reviewed tree resolves: that is a finding."""
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    write_frame(plant, verdict="DRIFT", state="RED — runtime drift")
    result = run_check(plant, pin, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "DISAGREE" in result.stdout


def test_agreement_reads_agree(plant, pin, tmp_path):
    """When both byte sets read the same, the check is quiet."""
    reference = write_package(tmp_path / "tree" / "src", "DRIFT")
    write_frame(plant, verdict="DRIFT", state="RED — runtime drift")
    result = run_check(plant, pin, reference)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AGREE" in result.stdout


def test_disagreement_names_the_record_the_pin_cannot_read(plant, pin, repo, tmp_path):
    """The cause is named: the pinned bytes cannot read the declaration."""
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    write_frame(plant, verdict="DRIFT", state="RED — runtime drift")
    result = run_check(plant, pin, reference, extra=["--repo", str(repo)])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "health/declared-roots.json" in result.stdout


def test_printed_line_the_pin_cannot_reproduce_is_undecidable(plant, pin, tmp_path):
    """A printed verdict the pinned bytes cannot reproduce is not a disagreement."""
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    write_frame(plant, verdict="MATCH", state="GREEN — listed services running")
    result = run_check(plant, pin, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN —" in result.stdout
    assert "do not reproduce" in result.stdout


def test_unreadable_service_roots_read_unknown(plant, pin, tmp_path):
    """A capture that cannot read the roots is not evidence of agreement."""
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    write_frame(plant, verdict="DRIFT", state="RED — runtime drift")
    broken = write_package(tmp_path / "broken" / "src", "DRIFT")
    (broken / PACKAGE / "wall_view.py").write_text(
        "def _service_block(home):\n    return [], None, 'UNKNOWN — services unavailable'\n"
        "def _runtime_state(home, root, service_roots=None):\n    return 'UNKNOWN', 'RUNTIME: UNKNOWN'\n")
    write_pin(plant, broken)
    result = run_check(plant, broken, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("UNKNOWN")


def test_missing_frame_reads_unknown(plant, pin, tmp_path):
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    result = run_check(plant, pin, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("UNKNOWN")


def test_malformed_frame_reads_unknown(plant, pin, tmp_path):
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    (plant / "dashboards" / f"{SLUG}.json").write_text("{not json")
    result = run_check(plant, pin, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "malformed" in result.stdout


def test_tampered_frame_reads_unknown(plant, pin, tmp_path):
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    write_frame(plant, verdict="DRIFT", digest="0" * 64)
    result = run_check(plant, pin, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "digest" in result.stdout


def test_missing_pin_record_reads_unknown(plant, tmp_path):
    """Without a pin record the check has no bytes to compare."""
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    result = run_check(plant, tmp_path / "absent", reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("UNKNOWN")


def test_frame_without_a_runtime_verdict_reads_unknown(plant, pin, tmp_path):
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    frame = "HEADLINE: GREEN — a synthetic plant\nMIND WALL — edited notes\nnotes\n"
    record = {"version": 1, "slug": SLUG, "home": str(plant.resolve()),
              "created": "2026-10-20T23:40:00+00:00", "frame": frame, "ok": True,
              "sha256": hashlib.sha256(frame.encode()).hexdigest()}
    (plant / "dashboards" / f"{SLUG}.json").write_text(json.dumps(record))
    result = run_check(plant, pin, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "no runtime verdict" in result.stdout


def test_invalid_slug_reads_unknown(plant, pin, tmp_path):
    reference = write_package(tmp_path / "tree" / "src", "DECLARED")
    write_frame(plant, verdict="DRIFT")
    result = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(plant), "--slug", "../escape",
         "--reference-src", str(reference)],
        capture_output=True, text=True)
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("UNKNOWN")
