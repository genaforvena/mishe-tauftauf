"""The already-settled path finds its yield receipt by a bounded tail search.

Health measured the unbounded full-tape read under the seed lock at 8.9 to
20 seconds on the live tape. The tail search must return the same receipt as
the full scan, including when the receipt is far back and when it is absent.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from mishe_tauftauf import seed, wall
from mishe_tauftauf.chat_protocol import decode_lifecycle
from mishe_tauftauf.feed import Feed


def _setup(home):
    (home / "coordination-mode.json").write_text(json.dumps({
        "mode": "wall",
        "until": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
    }))


def _full_scan(home, role, wake):
    return next(e for e in reversed(Feed(home).entries())
                if (c := decode_lifecycle(e)) and c.kind == "yield"
                and c.role == role and c.wake == wake)


def test_receipt_found_near_tail_matches_full_scan(tmp_path):
    _setup(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    note = tmp_path / "plan.md"
    note.write_text("plan\n")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, continue_task=False, result="verified")
    found = wall._find_yield_receipt(tmp_path, "genome", wake.sequence)
    assert found.sequence == _full_scan(tmp_path, "genome", wake.sequence).sequence


def test_receipt_found_after_many_later_entries(tmp_path):
    _setup(tmp_path)
    feed = Feed(tmp_path)
    wake = feed.append("seed", "seed wake genome observation=1\nRead your wall.")
    note = tmp_path / "plan.md"
    note.write_text("plan\n")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, continue_task=False, result="verified")
    for i in range(1500):
        feed.append("health", f"filler entry {i}")
    found = wall._find_yield_receipt(tmp_path, "genome", wake.sequence)
    assert found.sequence == _full_scan(tmp_path, "genome", wake.sequence).sequence


def test_absent_receipt_still_raises_stop_iteration_like_full_scan(tmp_path):
    _setup(tmp_path)
    Feed(tmp_path).append("health", "no receipts here")
    with pytest.raises(StopIteration):
        wall._find_yield_receipt(tmp_path, "genome", 999999)
