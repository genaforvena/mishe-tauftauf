"""memory-samples producer: one row per reading, gaps stay gaps, a missing store fails the unit."""
import time
from pathlib import Path

import pytest

from mishe_tauftauf import memory_reading, producers, signal_samples
from mishe_tauftauf.memory_reading import meminfo_kib as _meminfo_kb, psi_some_avg10 as _psi_avg10


def test_parsers_read_the_kernel_shapes():
    psi = "some avg10=1.25 avg60=0.5 avg300=0.1 total=12345\nfull avg10=0.0 avg60=0 avg300=0 total=0\n"
    assert _psi_avg10(psi) == 1.25
    assert _psi_avg10("garbage") is None
    meminfo = "MemTotal:       100 kB\nAnonPages:      2398200 kB\n"
    assert _meminfo_kb(meminfo, "AnonPages") == 2398200
    assert _meminfo_kb("MemTotal: 1 kB\n", "AnonPages") is None


def test_charged_is_cached_plus_slab_and_gaps_when_either_is_missing():
    meminfo = "MemTotal: 100 kB\nCached:  8600000 kB\nSlab:  4000000 kB\n"
    assert memory_reading.charged_kib(meminfo) == 12600000
    assert memory_reading.charged_kib("Cached: 1 kB\n") is None


def test_gap_writes_no_row_for_an_unreadable_reading(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(signal_samples, "append_samples", lambda d, lines, **kw: seen.append(lines) or len(lines))
    monkeypatch.setattr(memory_reading, "read_memory", lambda proc=None: (None, 2398200))
    monkeypatch.setattr(memory_reading, "read_charged", lambda proc=None: None)
    out = producers.memory_samples(tmp_path, now=time.time())
    assert out == "samples=1"
    assert len(seen[0]) == 1 and "memory.anon.kib" in seen[0][0]


def test_missing_store_raises_unknown_not_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_SIGNAL_SAMPLE_BIN", raising=False)
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    monkeypatch.delenv("MISHE_TAUFTAUF_HOME", raising=False)
    with pytest.raises(signal_samples.SignalSampleUnknown):
        producers.memory_samples(tmp_path, now=time.time())
