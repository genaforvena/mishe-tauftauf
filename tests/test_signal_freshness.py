"""Differential test: rust/mishe-signal-freshness against the Python comparison.

The Python side is ``now - seen > max_age``, the rule in ``signal_store.read``.
Run with MISHE_SIGNAL_FRESHNESS_BIN pointing at a built binary; without it the
test is skipped and says so (a skip is not a pass). Fixed boundary values and a
seeded random set must match exactly, and the reader must fail closed (UNKNOWN)
on a missing binary and on a binary that exits non-zero.
"""

import os
import random
import stat
from pathlib import Path

import pytest

from mishe_tauftauf import signal_freshness as sf

BIN = os.environ.get("MISHE_SIGNAL_FRESHNESS_BIN")
needs_bin = pytest.mark.skipif(not BIN, reason="MISHE_SIGNAL_FRESHNESS_BIN not set: differential test not run")

FIXED = [
    (10.0, 15.0, 5.0),        # equal age is FRESH
    (10.0, 15.000001, 5.0),   # just over is EXPIRED
    (0.0, 0.0, 0.0),
    (1.5, 1.25, 0.0),         # negative age is FRESH
    (1700000000.25, 1700000100.0, 99.75),
    (float("nan"), 15.0, 5.0),
    (10.0, float("inf"), 5.0),
    (float("-inf"), 1.0, 5.0),
    (1e16, 1e16 + 2.0, 1.0),
]


def _python(seen, now, max_age):
    return sf.EXPIRED if now - seen > max_age else sf.FRESH


def _random_triples(count, seed=20261115):
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        seen = rng.uniform(0, 2e9)
        now = seen + rng.uniform(-100, 4000)
        max_age = rng.choice([0.0, 1.0, 300.0, 1800.5, rng.uniform(0, 5000)])
        out.append((seen, now, max_age))
    return out


@needs_bin
def test_fixed_boundaries_match_python():
    got = sf.verdicts(FIXED, binary=BIN)
    assert got == [_python(*t) for t in FIXED]


@needs_bin
def test_seeded_random_triples_match_python():
    triples = _random_triples(2000)
    got = sf.verdicts(triples, binary=BIN)
    assert got == [_python(*t) for t in triples]


@needs_bin
def test_empty_batch_runs_nothing():
    assert sf.verdicts([], binary=BIN) == []


def test_missing_binary_is_unknown(tmp_path):
    with pytest.raises(sf.FreshnessUnknown):
        sf.verdicts([(1.0, 2.0, 3.0)], binary=str(tmp_path / "absent"))


def test_no_binary_at_all_is_unknown(monkeypatch):
    monkeypatch.delenv(sf.ENV_OVERRIDE, raising=False)
    with pytest.raises(sf.FreshnessUnknown):
        sf.verdicts([(1.0, 2.0, 3.0)])


def test_exit_two_is_unknown_not_fresh(tmp_path):
    bad = tmp_path / "fail.sh"
    bad.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    bad.chmod(bad.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(sf.FreshnessUnknown):
        sf.verdict(1.0, 2.0, 3.0, binary=str(bad))


def test_exit_zero_is_fresh_only_by_status(tmp_path):
    ok = tmp_path / "ok.sh"
    ok.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ok.chmod(ok.stat().st_mode | stat.S_IXUSR)
    assert sf.verdict(1.0, 2.0, 3.0, binary=str(ok)) == sf.FRESH


def test_hanging_binary_is_unknown(tmp_path):
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(sf.FreshnessUnknown):
        sf.verdict(1.0, 2.0, 3.0, binary=str(slow), timeout=0.2)
