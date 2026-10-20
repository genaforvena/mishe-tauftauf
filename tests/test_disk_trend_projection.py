"""Deterministic fixtures for the projection published by ``sense.disk.trend``.

A countdown to a disk floor is a statement about the future built from one
window's slope. These fixtures build a corpus whose windows disagree -- a disk
drained hard and then filling -- and one whose windows agree, so the published
projection can be checked against both.

The file is separate because the shared ``tests/test_discovery.py`` carries
other roles' uncommitted work.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mishe_tauftauf import discovery


def _scan(home: Path, created: str, free_bytes: int) -> None:
    """Write a minimal scan file with one verified disk.free observation."""
    root = home / "discovery"
    root.mkdir(exist_ok=True)
    doc = {"created": created,
           "observations": [{"id": "sense.disk.free", "state": "verified",
                             "sample": free_bytes}]}
    (root / f"scan-{created.replace(':', '')}.json").write_text(
        json.dumps(doc), encoding="utf-8")


def _corpus(home: Path, now: datetime, free_at) -> None:
    """Write one sample every 6 h over the last 21 days.

    ``free_at`` is the free space in GiB at a given age in days.
    """
    for step in range(85):
        age_days = step * 0.25
        stamp = now - timedelta(days=age_days)
        _scan(home, stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
              int(free_at(age_days) * 2**30))


def test_disk_trend_suppresses_a_countdown_when_the_windows_disagree(tmp_path):
    """A disk that drained for weeks and has been filling for days gives
    disagreeing windows. The countdown is not published from any one of them,
    and the robust reason still carries the verdict on its own."""
    home = tmp_path / "site"
    home.mkdir()
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    def free_at(age_days: float) -> float:
        if age_days <= 3:
            return 8 + 4 * (3 - age_days)      # filling for the last 3 days
        return 8 + 5 * (age_days - 3)          # drained at 5 GiB/day before

    _corpus(home, now, free_at)

    result = discovery._disk_trend(home, now=now)

    assert result["state"] == "drift"
    assert "floor=not-robust" in result["sample"]
    assert "projected floor" not in result["sample"]
    assert "trailing-24h minimum" in result["sample"]
    assert result["projection_robust"] is False
    assert result["days_to_floor"] is None
    assert result["unrobust_days_to_floor"] is not None
    # The signs of every window are published, so the disagreement is visible
    # rather than hidden behind the one window the figure came from.
    assert "slopes=" in result["sample"]
    assert result["slope_windows"]["2d"] < 0
    assert result["slope_windows"]["7d"] > 0


def test_disk_trend_publishes_a_countdown_when_the_windows_agree(tmp_path):
    """A steady drain agrees across every window, so the countdown is
    published and can still carry a reason."""
    home = tmp_path / "site"
    home.mkdir()
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    def free_at(age_days: float) -> float:
        return 27 + 3 * age_days               # steady 3 GiB/day drain

    _corpus(home, now, free_at)

    result = discovery._disk_trend(home, now=now)

    assert result["state"] == "drift"
    assert result["projection_robust"] is True
    assert "projected floor" in result["sample"]
    assert "floor=not-robust" not in result["sample"]
    assert result["days_to_floor"] is not None
    assert result["drain_window"] == "7d"
    assert result["drain_per_day"] == round(3 * 2**30, 1)
