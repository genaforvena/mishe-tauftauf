"""Malformed/unknown-input tests for the two pending literal adapters."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mishe_tauftauf import clear_journal, window_manifest


def _home(tmp_path: Path) -> Path:
    (tmp_path / "checks").mkdir()
    (tmp_path / "health").mkdir()
    return tmp_path


@pytest.mark.parametrize("body", ["{not json", "[1, 2]", "42", '"str"', "null"])
def test_clear_journal_rejects_non_object_or_broken_json(tmp_path, body):
    home = _home(tmp_path)
    path = home / "checks" / "wall-clear-genome-7.json"
    path.write_text(body, encoding="utf-8")
    assert clear_journal.read(path) is None


def test_clear_journal_missing_file_is_unreadable(tmp_path):
    home = _home(tmp_path)
    assert clear_journal.read(home / "checks" / "wall-clear-missing.json") is None


def test_clear_journal_accepts_object(tmp_path):
    home = _home(tmp_path)
    path = home / "checks" / "wall-clear-genome-6.json"
    path.write_text(json.dumps({"phase": "rotated", "role": "genome"}), encoding="utf-8")
    assert clear_journal.read(path) == {"phase": "rotated", "role": "genome"}


def test_window_manifest_missing_means_no_declared_utilities(tmp_path):
    assert window_manifest.read(_home(tmp_path)) == []


@pytest.mark.parametrize("body", ["{not json", '{"a": 1}', "42", '"x"', "null", '["ok", 42]', '[null]'])
def test_window_manifest_rejects_malformed(tmp_path, body):
    home = _home(tmp_path)
    (home / "health" / "windows.json").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        window_manifest.read(home)


def test_window_manifest_accepts_string_list(tmp_path):
    home = _home(tmp_path)
    (home / "health" / "windows.json").write_text(json.dumps(["genome", "ledger"]), encoding="utf-8")
    assert window_manifest.read(home) == ["genome", "ledger"]
