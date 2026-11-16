"""Golden/differential test for the `mishe-cgroup-counters` binary.

The binary is the authoritative cgroup counter decoder in a delivered plant;
its answers must match the retained Python decoder recorded here. When no binary
is installed the binary cases SKIP (never silently pass) while the Python
golden assertions still run, the same shape as `tests/test_rust_unit_paths.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from mishe_tauftauf import cpu_stat_text
from mishe_tauftauf import cgroup_counters
from mishe_tauftauf.cgroup_counters import CgroupCountersUnknown, NoBinary

STAT = (
    "usage_usec 1\nuser_usec 0\nsystem_usec 0\nnr_periods 10\n"
    "nr_throttled 4\nthrottled_usec 99\nnr_bursts 0\nburst_usec 0\n"
)
THROTTLE_COUNTERS = ("nr_periods", "nr_throttled", "throttled_usec")
STAT_CASES = [
    (STAT, THROTTLE_COUNTERS, {"nr_periods": 10, "nr_throttled": 4, "throttled_usec": 99}),
    ("nr_periods 10\n", THROTTLE_COUNTERS, None),
    ("", THROTTLE_COUNTERS, None),
    ("nr_periods x\nnr_throttled 1\nthrottled_usec 1\n", THROTTLE_COUNTERS, None),
    (STAT, ("usage_usec",), {"usage_usec": 1}),
    ("usage_usec 7\nbadline_usec nonsense\n", ("usage_usec",), {"usage_usec": 7}),
]
PERIOD_CASES = [
    ("max 100000\n", 100000),
    ("200000 100000\n", 100000),
    ("max max\n", None),
    ("max 0\n", None),
    ("max -1\n", None),
    ("", None),
]


def _resolve() -> str | None:
    """Resolve only a verified binary; never return an unverified path."""
    override = os.environ.get("MISHE_CGROUP_COUNTERS_BIN")
    if override:
        path = Path(override)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    site = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not site:
        return None
    binary = Path(site) / "bin" / "mishe-cgroup-counters"
    record = binary.with_name(binary.name + ".install.json")
    if not binary.is_file() or not record.is_file():
        return None
    try:
        expected = json.loads(record.read_text(encoding="utf-8"))["sha256"]
    except (ValueError, KeyError, OSError, TypeError):
        return None
    if not isinstance(expected, str) or hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
        return None
    return str(binary)


BINARY = _resolve()
needs_binary = pytest.mark.skipif(BINARY is None, reason="mishe-cgroup-counters binary not installed")


def _binary_stat(body: str, counters: tuple[str, ...]):
    done = subprocess.run([BINARY, "stat", *counters], input=body.encode(), capture_output=True, timeout=5)
    if done.returncode == 2:
        return None
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    result = {}
    for line in done.stdout.decode().splitlines():
        name, value = line.split("\t")
        result[name] = int(value)
    return result


def _binary_period(body: str):
    done = subprocess.run([BINARY, "max-period"], input=body.encode(), capture_output=True, timeout=5)
    if done.returncode == 2:
        return None
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    name, value = done.stdout.decode().strip().split("\t")
    assert name == "period"
    return int(value)


def test_python_golden_answers_match_recorded() -> None:
    for body, counters, golden in STAT_CASES:
        assert cpu_stat_text._reference_parse_cpu_stat(body, counters) == golden
    for body, golden in PERIOD_CASES:
        assert cpu_stat_text._reference_parse_cpu_max_period(body) == golden


@needs_binary
@pytest.mark.parametrize("index", range(len(STAT_CASES)))
def test_binary_matches_recorded_stat_golden(index: int) -> None:
    body, counters, golden = STAT_CASES[index]
    assert _binary_stat(body, counters) == golden


@needs_binary
@pytest.mark.parametrize("index", range(len(PERIOD_CASES)))
def test_binary_matches_recorded_period_golden(index: int) -> None:
    body, golden = PERIOD_CASES[index]
    assert _binary_period(body) == golden


@needs_binary
def test_production_adapter_uses_the_binary() -> None:
    assert cpu_stat_text.parse_cpu_stat(STAT, THROTTLE_COUNTERS) == STAT_CASES[0][2]
    assert cpu_stat_text.parse_cpu_max_period("max 100000\n") == 100000


def test_missing_or_unexecutable_override_is_never_a_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MISHE_CGROUP_COUNTERS_BIN", "/nonexistent/mishe-cgroup-counters")
    with pytest.raises(CgroupCountersUnknown):
        cgroup_counters.resolve_binary()
    fake = tmp_path / "mishe-cgroup-counters"
    fake.write_text("not executable")
    monkeypatch.setenv("MISHE_CGROUP_COUNTERS_BIN", str(fake))
    with pytest.raises(CgroupCountersUnknown):
        cgroup_counters.resolve_binary()


def test_site_install_with_bad_sha_is_never_a_result(monkeypatch, tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "mishe-cgroup-counters"
    binary.parent.mkdir()
    binary.write_bytes(b"\x7fELF not really")
    binary.chmod(0o755)
    (tmp_path / "bin" / "mishe-cgroup-counters.install.json").write_text('{"sha256": "deadbeef"}')
    monkeypatch.delenv("MISHE_CGROUP_COUNTERS_BIN", raising=False)
    monkeypatch.setenv("MISHE_SEED_HOME", str(tmp_path))
    with pytest.raises(CgroupCountersUnknown):
        cgroup_counters.resolve_binary()


def test_no_binary_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("MISHE_CGROUP_COUNTERS_BIN", raising=False)
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    monkeypatch.delenv("MISHE_TAUFTAUF_HOME", raising=False)
    with pytest.raises(NoBinary):
        cgroup_counters.resolve_binary()
