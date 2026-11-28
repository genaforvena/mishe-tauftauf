"""A cut run names the bound that ended it, so the work record can tell a cut from a clean finish.

Operator DM (2026-11-28): pub wake wake-pub-1795828299484870481 ran 2255 s with no cut field, so
a cut could not be told from a clean finish. Every EngineTimeout now carries ``cut`` and ``cut_at``.
"""
import os
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import engine_process as proc
from mishe_tauftauf import work_receipts
from mishe_tauftauf.wake import EngineTimeout

SILENT = "import time\ntime.sleep(60)\n"


def test_a_silent_engine_records_the_first_output_cut(tmp_path: Path):
    before = time.time()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", SILENT], env=dict(os.environ), cwd=tmp_path,
                         timeout=30, stdout_to=tmp_path / "stream", hard_timeout=60, idle_gap=5.0,
                         first_output_s=1.0, first_event=lambda: False)
    assert caught.value.cut == "first_output"
    assert before <= caught.value.cut_at <= time.time()


def test_a_hard_cap_cut_is_named(tmp_path: Path):
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", "import time,sys\nprint('x',flush=True)\ntime.sleep(60)\n"],
                         env=dict(os.environ), cwd=tmp_path, timeout=0.5, stdout_to=tmp_path / "stream",
                         hard_timeout=1.0, idle_gap=0.5)
    assert caught.value.cut in {"hard_cap", "idle_gap", "stall", "soft_deadline"}
    assert caught.value.cut is not None


def test_a_timeout_without_a_bound_has_no_cut_kind():
    assert EngineTimeout("exceeded").cut is None


def test_complete_writes_the_cut_into_the_work_record(tmp_path: Path, monkeypatch):
    root = tmp_path / "wakes"
    root.mkdir()
    (root / "k1.work-start.json").write_text('{"role":"genome","repo":"%s","head_start":"a"}' % tmp_path)
    seen = {}
    monkeypatch.setattr(work_receipts, "finish", lambda *a, **k: {"head_end": None, "commits_attributed": []})
    monkeypatch.setattr(work_receipts, "write_json", lambda path, data: seen.update(data=data))
    monkeypatch.setattr(work_receipts, "read_json", lambda path: (
        {"role": "genome", "repo": str(tmp_path), "head_start": "a"} if path.name.endswith("start.json") else {}))
    work_receipts.complete(tmp_path, "k1", now=1.0, engine_seconds=2.0,
                           cut={"kind": "stall", "at": 1794000000.0})
    assert seen["data"]["cut"] == {"kind": "stall", "at": 1794000000.0}
    work_receipts.complete(tmp_path, "k1", now=1.0, engine_seconds=2.0)
    assert seen["data"]["cut"] == {"kind": "none", "at": None}
