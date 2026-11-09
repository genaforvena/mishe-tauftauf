"""Approved parsing adapter for the engine router and wake-concurrency JSON files."""
from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path):
    """Decode a UTF-8 JSON file; raises OSError/ValueError like json.loads."""
    return json.loads(path.read_text(encoding="utf-8"))


def split_cooling_key(key: str) -> tuple:
    """``role|candidate`` -> (role, candidate); a bare candidate has role None."""
    if "|" not in key:
        return None, key
    role, _, cand = key.partition("|")
    return role, cand


def file_lines(path: Path) -> list:
    return path.read_text(encoding="utf-8").splitlines()
