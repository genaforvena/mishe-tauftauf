"""The per-scan index beside the discovery receipts.

``read_scan_series`` used to parse every receipt on every call; the index is a
derived cache that answers the same series without opening them. These fixtures
pin the two properties that could break silently: the index must reproduce the
receipt reading exactly, and any gap in it (missing, torn, stale or short) must
fall back to the receipts rather than drop a scan.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from mishe_tauftauf import feed as feed_module


def _receipt(home: Path, stamp: str, samples: dict[str, object], *, scan_id: str,
             created: str | None = None, extra: dict[str, object] | None = None) -> Path:
    root = home / "discovery"
    root.mkdir(exist_ok=True)
    doc: dict[str, object] = {"created": created or stamp,
                              "observations": [{"id": key, "state": "verified", "sample": value}
                                               for key, value in samples.items()]}
    if extra is not None:
        doc.update(extra)
    path = root / f"scan-{stamp.replace(':', '').replace('-', '')}-{scan_id}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _rows(home: Path, sense: str = "sense.disk.free"):
    return feed_module.read_scan_series(home, sense)


def test_index_reproduces_the_receipt_reading_and_is_written(tmp_path):
    _receipt(tmp_path, "2026-11-18T00:00:00Z", {"sense.disk.free": 10, "sense.x": "7"}, scan_id="a" * 32)
    _receipt(tmp_path, "2026-11-18T01:00:00Z", {"sense.disk.free": "20"}, scan_id="b" * 32)

    rows = _rows(tmp_path)

    assert [sample for _, sample in rows] == [10, 20], "a string sample coerces, the same as the receipt reader"
    assert [when.strftime("%H") for when, _ in rows] == ["00", "01"]
    index = (tmp_path / "discovery" / feed_module.SCAN_INDEX_NAME).read_text(encoding="utf-8")
    assert len(index.splitlines()) == 2


def test_index_is_used_without_opening_receipts(tmp_path):
    receipts = [_receipt(tmp_path, "2026-11-18T00:00:00Z", {"sense.disk.free": 10}, scan_id="a" * 32),
                _receipt(tmp_path, "2026-11-18T01:00:00Z", {"sense.disk.free": 12}, scan_id="b" * 32)]
    assert [sample for _, sample in _rows(tmp_path)] == [10, 12]

    # The names still exist, so the index accounts for every receipt; their
    # contents must not matter to the indexed answer.
    for path in receipts:
        path.write_text("not json", encoding="utf-8")

    assert [sample for _, sample in _rows(tmp_path)] == [10, 12]


def test_missing_short_and_torn_index_lines_rebuild_from_receipts(tmp_path):
    _receipt(tmp_path, "2026-11-18T00:00:00Z", {"sense.disk.free": 10}, scan_id="a" * 32)
    _receipt(tmp_path, "2026-11-18T01:00:00Z", {"sense.disk.free": 12}, scan_id="b" * 32)
    index_path = tmp_path / "discovery" / feed_module.SCAN_INDEX_NAME
    expected = [10, 12]

    assert [sample for _, sample in _rows(tmp_path)] == expected
    lines = index_path.read_text(encoding="utf-8").splitlines()

    index_path.write_text("\n".join(lines[:1]) + "\n", encoding="utf-8")
    assert [sample for _, sample in _rows(tmp_path)] == expected, "a short index rebuilds instead of dropping a scan"
    assert len(index_path.read_text(encoding="utf-8").splitlines()) == 2

    index_path.write_text("\n".join(lines) + '\n{"id": "c", "created": "2026-', encoding="utf-8")
    assert [sample for _, sample in _rows(tmp_path)] == expected, "a torn append rebuilds instead of failing"

    index_path.write_text("{}\nnot an index line\n", encoding="utf-8")
    assert [sample for _, sample in _rows(tmp_path)] == expected
    assert len(index_path.read_text(encoding="utf-8").splitlines()) == 2


def test_index_line_without_a_receipt_is_ignored(tmp_path):
    receipt = _receipt(tmp_path, "2026-11-18T00:00:00Z", {"sense.disk.free": 10}, scan_id="a" * 32)
    assert [sample for _, sample in _rows(tmp_path)] == [10]
    receipt.unlink()

    assert _rows(tmp_path) == [], "the receipt is the record; its index line alone cannot keep a sample alive"


def test_unreadable_receipt_stays_absent_from_the_series(tmp_path):
    _receipt(tmp_path, "2026-11-18T00:00:00Z", {"sense.disk.free": 10}, scan_id="a" * 32)
    broken = tmp_path / "discovery" / f"scan-20261118T010000Z-{'b' * 32}.json"
    broken.write_text("{ truncated", encoding="utf-8")

    rows = _rows(tmp_path)

    assert [sample for _, sample in rows] == [10]
    index = (tmp_path / "discovery" / feed_module.SCAN_INDEX_NAME).read_text(encoding="utf-8")
    assert broken.stem not in index, "a receipt that cannot be read is not indexed as a gap"


def test_append_scan_index_keeps_only_verified_integer_samples(tmp_path):
    receipt = tmp_path / "discovery" / f"scan-20261118T000000Z-{'a' * 32}.json"
    receipt.parent.mkdir()
    snapshot = {"created": "2026-11-18T00:00:00Z",
                "observations": [{"id": "sense.a", "state": "verified", "sample": 5},
                                 {"id": "sense.b", "state": "unverified", "sample": 6},
                                 {"id": "sense.c", "state": "verified", "sample": "not a number"},
                                 {"id": "sense.d", "state": "verified", "sample": "8"}]}

    feed_module.append_scan_index(tmp_path, receipt, snapshot)
    feed_module.append_scan_index(tmp_path, receipt, snapshot)

    lines = (receipt.parent / feed_module.SCAN_INDEX_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    line = json.loads(lines[0])
    assert line["id"] == receipt.stem
    assert line["created"] == "2026-11-18T00:00:00Z"
    assert line["samples"] == {"sense.a": 5, "sense.d": 8}


def test_series_is_sorted_by_time_not_by_filename(tmp_path):
    _receipt(tmp_path, "2026-11-18T02:00:00Z", {"sense.disk.free": 3}, scan_id="a" * 32)
    _receipt(tmp_path, "2026-11-18T01:00:00Z", {"sense.disk.free": 2}, scan_id="b" * 32,
             created="2026-11-18T03:00:00Z")

    rows = _rows(tmp_path)

    assert [sample for _, sample in rows] == [3, 2]
    assert rows[1][0] == datetime(2026, 11, 18, 3, tzinfo=timezone.utc)
