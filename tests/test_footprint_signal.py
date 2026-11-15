"""footprint producer: anon beside charged beside PSI from the sample store; any missing reading is UNKNOWN."""
import time

import pytest

from mishe_tauftauf import producers, signal_samples


def _store(monkeypatch, records):
    monkeypatch.setattr(signal_samples, "read_samples",
                        lambda directory, since_ms=None: signal_samples.ReadResult(records=records))


def test_footprint_reads_the_latest_of_each_and_reports_age(tmp_path, monkeypatch):
    now = 1_000_000.0
    ts = int(now * 1000) - 5000
    _store(monkeypatch, [
        f"{ts - 180000}\tmemory.anon.kib\tok\t1\tmemory-samples",
        f"{ts}\tmemory.anon.kib\tok\t366000\tmemory-samples",
        f"{ts}\tmemory.charged.kib\tok\t13212160\tmemory-samples",
        f"{ts}\tmemory.psi.avg10\tok\t0.12\tmemory-samples",
    ])
    out = producers.footprint(tmp_path, now=now)
    assert out == "anon=0.35GiB charged=12.60GiB psi_avg10=0.12 sample_age=5s"


def test_footprint_missing_any_reading_is_unknown_not_zero(tmp_path, monkeypatch):
    now = 1_000_000.0
    ts = int(now * 1000)
    _store(monkeypatch, [f"{ts}\tmemory.anon.kib\tok\t366000\tmemory-samples"])
    with pytest.raises(signal_samples.SignalSampleUnknown):
        producers.footprint(tmp_path, now=now)
