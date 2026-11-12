"""Decode the quota-history dialect the budget producer reads.

The claude tap and the engine router append JSONL rows to operator/budget and
engine history files, and the engine labels its quota entries "account/window".
Those are external byte formats, so they are decoded here (a registered parsing
boundary) and the producer consumes Python objects rather than raw text.
"""

from __future__ import annotations

import json
from pathlib import Path


def read_jsonl_rows(path: Path) -> list:
    """Every decodable JSON object line of a JSONL file, in file order.

    An unreadable file yields no rows. A line that is not valid UTF-8 JSON, or
    that is not an object, is skipped, so one bad line never hides the rest.
    """
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return []
    rows = []
    for raw in lines:
        try:
            row = json.loads(raw.decode("utf-8"))
        except ValueError:  # includes UnicodeDecodeError
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def split_quota_label(label: object) -> tuple[str, str]:
    """Split an engine quota label "account/window" at its last slash.

    The account is empty when the label carries no slash.
    """
    account, _, window = str(label).rpartition("/")
    return account, window
