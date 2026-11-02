"""Dourish & Bellotti 1992 shared feedback: wall provenance as persistent record (wall_view.py / wall.py)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mishe_tauftauf import wall
from mishe_tauftauf.wall_text import document_digest


def _setup_wall(home: Path) -> None:
    (home / "coordination-mode.json").write_text(
        json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()}),
        encoding="utf-8",
    )


def test_wall_provenance_shows_age_for_old_entry(tmp_path, monkeypatch):
    _setup_wall(tmp_path)
    # Write via wall.write so audit entry exists.
    wall.write(tmp_path, "senses", "# plate\nUseful work.\n")
    # Backdate the audit entry's ts to 10h ago so the rendered margin shows an old age.
    audit = tmp_path / "logs" / "wall-write-audit.jsonl"
    entries = audit.read_text(encoding="utf-8").splitlines()
    last = json.loads(entries[-1])
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=10, minutes=2)).isoformat()
    last["ts"] = old_ts
    # Keep mtime as original wall mtime; only audit ts drives the age display.
    audit.write_text("\n".join(entries[:-1] + [json.dumps(last)]) + "\n", encoding="utf-8")
    pane = wall.pane(tmp_path, "senses")
    # Must be inside MIND WALL block (after the header) and show age.
    assert "WALL PROVENANCE: walls/senses.md @" in pane
    assert " persistent record" in pane
    # Age should reflect ~10h.
    assert "10." in pane and "h" in pane
    # Digest short must be present.
    digest = document_digest("# plate\nUseful work.\n")
    assert digest[:12] in pane
    # Must be after MIND WALL header, not before.
    assert pane.index("MIND WALL —") < pane.index("WALL PROVENANCE:")


def test_wall_provenance_unknown_when_no_audit_entry(tmp_path):
    _setup_wall(tmp_path)
    # Create wall file directly without audit entry.
    (tmp_path / "walls").mkdir(parents=True, exist_ok=True)
    (tmp_path / "walls" / "senses.md").write_text("# plate\nFresh notes.\n", encoding="utf-8")
    # Ensure no audit log.
    assert not (tmp_path / "logs" / "wall-write-audit.jsonl").exists()
    pane = wall.pane(tmp_path, "senses")
    assert "WALL PROVENANCE: walls/senses.md @" in pane
    assert "UNKNOWN" in pane
    assert "no audit entry" in pane or "no file" not in pane.lower()
    # Digest still present.
    digest = document_digest("# plate\nFresh notes.\n")
    assert digest[:12] in pane


def test_wall_provenance_unknown_when_no_file(tmp_path):
    _setup_wall(tmp_path)
    pane = wall.pane(tmp_path, "senses")
    assert "WALL PROVENANCE: walls/senses.md" in pane
    assert "UNKNOWN" in pane
    assert "no file" in pane.lower()


def test_observation_digest_stable_despite_provenance(tmp_path):
    """The OBSERVATION digest must not change when the provenance margin appears.

    The margin lives inside the MIND WALL block (after the header), which
    observations.sensor_from_frame / wall.observation_text split on, so the
    meaningful sensor text is byte-identical.
    """
    _setup_wall(tmp_path)
    from mishe_tauftauf.observations import sensor_from_frame

    wall.write(tmp_path, "senses", "# plate\nStable.\n")
    pane = wall.pane(tmp_path, "senses")
    sensor = sensor_from_frame(pane)
    meaningful_with = wall.observation_text("senses", sensor)
    # The sensor must not contain the provenance line.
    assert "WALL PROVENANCE" not in sensor
    assert "WALL PROVENANCE" not in meaningful_with
    # Removing the provenance line from the pane must not change the sensor.
    pane_without = pane.replace(
        next(line for line in pane.splitlines() if line.startswith("WALL PROVENANCE:")), ""
    )
    sensor_without = sensor_from_frame(pane_without)
    meaningful_without = wall.observation_text("senses", sensor_without)
    assert meaningful_with == meaningful_without
