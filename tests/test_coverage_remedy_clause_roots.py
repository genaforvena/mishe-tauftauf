"""The coverage reading's remedy rows must be read the same way by every root.

``checks/coverage-remedy-clause-roots.py`` answers a question the plant's
coverage reading depends on: the rows the published reading re-evidences by
the plant's remedy naming rest on a relation that lives in the reader's
*code*, and the plant runs more than one import root. A root whose package
does not carry the relation reads those rows broken, so the plant's green
coverage reading and the plant's pinned bytes disagree about the same rows.

These tests pin the detector's decisions on a synthetic site home, so the
readings are fixed by the fixture rather than by the machine and the same
assertions hold in CI and on the plant:

* a row the published reading re-evidences by the remedy relation is named,
  with the defect the row still carries and the successor that re-evidences it,
  and a pinned root without the relation makes the check exit 2;
* a pinned root that carries the relation is green;
* a published reading that re-evidences no row by the relation is green;
* a published reading that names no relation at all is UNKNOWN, not green: the
  reading cannot say which rows rest on the relation;
* a missing, unreadable or stale reading is UNKNOWN, never green;
* a pinned root whose module cannot be read is UNKNOWN, never green;
* the site entry point that pins the bytes is read, so the pin is the site's
  own statement of what it runs.

The detector reads published artifacts, process environments and package
source, and imports nothing from the plant package, so a change to an internal
helper's signature cannot make it fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "coverage-remedy-clause-roots.py"

RELATION_SOURCE = '''\
"""A module that carries the plant's remedy relation."""


def remedy_successor(lost_path, successor_path):
    """Whether the successor names the lost artifact's corrected form."""
    return False
'''

PLAIN_SOURCE = '''\
"""A module that predates the plant's remedy relation."""


def eligible_evidence_path(path):
    """Whether the path is eligible evidence."""
    return True
'''


def run_check(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home), *extra],
                          capture_output=True, text=True)


def write_report(home: Path, items: list[dict] | None = None, *, age: float = 0.0,
                 raw: str | None = None) -> None:
    """Publish a coverage reading with the given outcome rows."""
    directory = home / "checks"
    directory.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (directory / "initiative.json").write_text(raw, encoding="utf-8")
        return
    payload = {
        "generated_at": "2026-10-22T03:10:39.884607+00:00",
        "window": {"start": "2026-10-21T03:10:39.884607+00:00", "seconds": 86400},
        "roles": {},
        "outcomes": {"state": "OBSERVED", "items": items if items is not None else []},
    }
    path = directory / "initiative.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))


def remedy_row(sequence: int = 77622, role: str = "genome") -> dict:
    """One published row re-evidenced by the remedy relation."""
    return {"ref": "chat.log#%d" % sequence, "sequence": sequence, "role": role,
            "state": "OBSERVED", "link_kind": "remedy-name",
            "own_evidence_state": "UNKNOWN",
            "own_evidence_reason": "outcome evidence empty or changed",
            "re_evidenced_by": "chat.log#83490"}


def plain_row(sequence: int = 77055, role: str = "discover") -> dict:
    """One published row that the reading names, without resting on the relation."""
    return {"ref": "chat.log#%d" % sequence, "sequence": sequence, "role": role,
            "state": "OBSERVED", "link_kind": "own-evidence"}


def make_root(path: Path, *, carries: bool) -> Path:
    """An import root whose package carries or lacks the relation."""
    root = path / "src"
    module = root / "mishe_tauftauf" / "outcome_publication.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(RELATION_SOURCE if carries else PLAIN_SOURCE, encoding="utf-8")
    return root


def write_entry_point(home: Path, pin: Path) -> None:
    """The site's own entry point, naming the bytes every call runs."""
    binary = home / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    (binary / "mishe-tauftauf").write_text(
        "#!/bin/sh\nexport PYTHONPATH=%s${PYTHONPATH:+:$PYTHONPATH}\n"
        "exec python3 -m mishe_tauftauf \"$@\"\n" % pin,
        encoding="utf-8")


def test_a_remedy_row_is_named_and_the_pin_without_the_relation_is_red(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, [remedy_row()])
    pin = make_root(tmp_path / "pin", carries=False)
    write_entry_point(home, pin)
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "chat.log#77622" in result.stdout
    assert "genome" in result.stdout
    assert "own_evidence=UNKNOWN" in result.stdout
    assert "successor=chat.log#83490" in result.stdout


def test_a_pin_that_carries_the_relation_is_green(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, [remedy_row()])
    pin = make_root(tmp_path / "pin", carries=True)
    write_entry_point(home, pin)
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "carries remedy_successor: yes" in result.stdout


def test_a_reading_with_no_remedy_row_is_green(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, [plain_row()])
    pin = make_root(tmp_path / "pin", carries=False)
    write_entry_point(home, pin)
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "re-evidences no row" in result.stdout


def test_a_reading_that_names_no_relation_is_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, raw=json.dumps({
        "generated_at": "2026-10-22T03:10:39.884607+00:00",
        "outcomes": {"state": "OBSERVED", "items": [
            {"ref": "chat.log#77055", "role": "discover", "state": "OBSERVED"}]},
    }))
    pin = make_root(tmp_path / "pin", carries=True)
    write_entry_point(home, pin)
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_a_missing_reading_is_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    pin = make_root(tmp_path / "pin", carries=True)
    write_entry_point(home, pin)
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_a_stale_reading_is_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, [remedy_row()], age=3600)
    pin = make_root(tmp_path / "pin", carries=True)
    write_entry_point(home, pin)
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_an_unreadable_pin_module_is_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, [remedy_row()])
    pin = tmp_path / "pin" / "src"
    pin.mkdir(parents=True)
    write_entry_point(home, pin)
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_a_site_without_an_entry_point_is_unknown(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, [remedy_row()])
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_the_pin_can_be_given_explicitly(tmp_path: Path) -> None:
    home = tmp_path / "site"
    home.mkdir()
    write_report(home, [remedy_row()])
    pin = make_root(tmp_path / "pin", carries=True)
    result = run_check(home, "--pin", str(pin))
    assert result.returncode == 0, result.stdout
