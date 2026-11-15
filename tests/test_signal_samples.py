"""Differential test: mishe-signal-sample against a Python reference reader.

The reference below is the oracle: it parses the same segment files with the same
field rules and must select the same records as the Rust `read` for every filter.
The binary comes from MISHE_SIGNAL_SAMPLE_BIN or the site install; without either
the tests are skipped with the reason, so the rest of the suite still runs.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mishe_tauftauf import signal_samples as ss


def _require_binary() -> None:
    """Skip the test with the reason when the binary is unreachable.

    Resolved at test time, not at import.  The suite's session fixture deletes
    MISHE_SEED_HOME on purpose so an in-mind run describes the tree as CI does
    (tests/conftest.py, ``private_tmux_server``), and import happens before
    that fixture runs.  A module-level probe therefore saw the site install,
    decided the binary was there, and never skipped -- then every body failed
    with "mishe-signal-sample unavailable: no MISHE_SIGNAL_SAMPLE_BIN and no
    site install".  Measured 2026-11-15: the delivery of 8249c692 failed its
    whole-suite check on five of these tests for exactly that reason.

    The delivery check exports MISHE_SIGNAL_SAMPLE_BIN (like MISHE_TAPE_BIN),
    so the differential test runs there; a CI-like run has neither the override
    nor a site binding and skips with this reason.  This is the pattern
    tests/test_feed.py uses.
    """
    try:
        ss._binary()
    except ss.SignalSampleUnknown as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"mishe-signal-sample binary not available: {exc}")


_SEG = re.compile(r"^\d{15}\.tsv$")
_NAME = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_SRC = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_VAL = re.compile(r"^[0-9+\-.eE]{1,32}$")


def _reference_valid(line: str) -> bool:
    f = line.split("\t")
    if len(f) != 5 or len(line) > 512:
        return False
    ts, name, state, value, source = f
    if not (re.fullmatch(r"\d{1,15}", ts) and int(ts) > 0):
        return False
    if not _NAME.match(name) or state not in {"ok", "warn", "red", "unknown"} or not _SRC.match(source):
        return False
    if value != "-":
        if not _VAL.match(value):
            return False
        try:
            return float(value) == float(value) and abs(float(value)) != float("inf")
        except ValueError:
            return False
    return True


def _reference_read(directory: Path, name=None, since=0, until=2**63) -> list[str]:
    out: list[str] = []
    for seg in sorted(p for p in directory.iterdir() if _SEG.match(p.name)):
        text = seg.read_bytes().decode("utf-8")
        if text and not text.endswith("\n"):
            text = text[: text.rfind("\n") + 1]  # torn tail is a fault, never a record
        for line in text.splitlines():
            if not _reference_valid(line):
                continue
            ts, rec_name = int(line.split("\t")[0]), line.split("\t")[1]
            if (name is None or rec_name == name) and since <= ts <= until:
                out.append(line)
    return out


def _corpus(n: int) -> list[str]:
    lines = []
    for i in range(n):
        ts = 1_794_700_000_000 + i * 60_000
        state = ["ok", "warn", "red", "unknown"][i % 4]
        value = "-" if state == "unknown" else f"{(i % 97) / 10:.3f}"
        name = "psi.some" if i % 3 else "budget.burn"
        lines.append(ss.format_record(ts, name, state, value, "sense"))
    return lines


def test_roundtrip_matches_reference_for_every_filter(tmp_path: Path) -> None:
    _require_binary()
    d = tmp_path / "samples"
    lines = _corpus(400)
    # One batch is one append, so rotation happens between batches, not inside one.
    for start in range(0, 400, 50):
        assert ss.append_samples(d, lines[start:start + 50], max_segment_bytes=4096, keep_segments=1000) == 50
    segs = sorted(p.name for p in d.iterdir())
    assert len(segs) > 1, "rotation should have produced several segments"
    for kw in ({}, {"name": "psi.some"}, {"since_ms": 1_794_700_120_000, "until_ms": 1_794_700_600_000}):
        got = ss.read_samples(d, **kw)
        assert got.complete
        ref_kw = {"name": kw.get("name"), "since": kw.get("since_ms", 0), "until": kw.get("until_ms", 2**63)}
        assert got.records == _reference_read(d, **ref_kw)


def test_gap_is_absent_not_zero(tmp_path: Path) -> None:
    _require_binary()
    d = tmp_path / "samples"
    ss.append_samples(d, [ss.format_record(1_794_700_000_000, "a.b", "ok", "1", "s"),
                          ss.format_record(1_794_700_180_000, "a.b", "ok", "2", "s")], keep_segments=5)
    got = ss.read_samples(d, name="a.b")
    assert got.records == ["1794700000000\ta.b\tok\t1\ts", "1794700180000\ta.b\tok\t2\ts"]


def test_torn_tail_keeps_valid_records_and_reports_fault(tmp_path: Path) -> None:
    _require_binary()
    d = tmp_path / "samples"
    ss.append_samples(d, [ss.format_record(1_794_700_000_000, "a.b", "ok", "1", "s")], keep_segments=5)
    seg = next(d.iterdir())
    with seg.open("ab") as f:
        f.write(b"1794700060000\ta.b\tok")
    got = ss.read_samples(d)
    assert not got.complete
    assert got.records == ["1794700000000\ta.b\tok\t1\ts"]
    assert any("torn tail" in f for f in got.faults)


def test_bad_batch_is_rejected_and_writes_nothing(tmp_path: Path) -> None:
    _require_binary()
    d = tmp_path / "samples"
    with pytest.raises(ValueError):
        ss.append_samples(d, ["1\tbad name\tok\t1\ts\n"], keep_segments=5)
    assert not d.exists() or not any(d.iterdir())


def test_empty_store_is_empty_result_not_error(tmp_path: Path) -> None:
    _require_binary()
    d = tmp_path / "samples"
    d.mkdir()
    got = ss.read_samples(d)
    assert got.complete and got.records == []


def test_missing_directory_is_unknown(tmp_path: Path) -> None:
    with pytest.raises(ss.SignalSampleUnknown):
        ss.read_samples(tmp_path / "absent")
