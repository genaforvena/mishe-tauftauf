"""Patch records above 1 MiB stay attributable: only the `files` payload is elided."""
import json

import pytest

from mishe_tauftauf.initiative import MAX_PATCH_RECORD_BYTES, elide_member_value


def _oversized_record() -> dict:
    blob = "x" * (2 * 1024 * 1024)
    return {
        "phase": "applied",
        "verification": {"at": 1793600000.0, "ok": True},
        "test": {"code": 0, "log": "/tmp/x"},
        "files": {"src/a.py": {"before": blob, "after": blob + "\\\"quoted\\\""}},
        "review": {"files": ["nested-kept"]},
    }


def test_oversized_record_parses_after_files_elided():
    raw = json.dumps(_oversized_record()).encode()
    assert len(raw) > 1024 * 1024
    assert len(raw) < MAX_PATCH_RECORD_BYTES
    record = json.loads(elide_member_value(raw, "files"))
    assert record["phase"] == "applied"
    assert record["verification"] == {"at": 1793600000.0, "ok": True}
    assert record["test"]["code"] == 0
    assert record["files"] is None
    assert record["review"] == {"files": ["nested-kept"]}


def test_no_member_returns_input_unchanged():
    raw = b'{"phase": "prepared", "x": [1, 2, {"y": "}]\\""}]}'
    assert elide_member_value(raw, "files") == raw


def test_key_inside_string_value_is_not_elided():
    raw = json.dumps({"phase": "files", "files": {"a": 1}}).encode()
    record = json.loads(elide_member_value(raw, "files"))
    assert record == {"phase": "files", "files": None}


def test_malformed_member_fails_closed():
    with pytest.raises(json.JSONDecodeError):
        json.loads(elide_member_value(b'{"files" 1}', "files"))
    with pytest.raises(ValueError):
        elide_member_value(b'{"files": {"a": "unterminated}', "files")
