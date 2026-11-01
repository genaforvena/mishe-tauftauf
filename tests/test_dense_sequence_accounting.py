"""Expected-vs-received sequence accounting for the Redmi10 dense baseline.

RFC 3550 Appendix A.3 style: expected is the producer's sequence span, received
is the distinct sequences seen, loss is their difference (never clamped), and
repeated rows are named as duplicates. The verdict must not change.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mishe_tauftauf import body_perception


NOW = 1_800_000_000.0


def _rows(seqs: list[int]) -> list[str]:
    rows: list[str] = []
    for index, seq in enumerate(seqs):
        epoch = int(NOW) - 300 + index * 5
        rows.append(f"{seq} {epoch} light lux=5.0 accuracy=3 age_ms=10")
    return rows


def _queue(tmp_path: Path, rows: list[str]) -> tuple[Path, float]:
    core = tmp_path / "core"
    android = tmp_path / "android-body"
    (core / "health").mkdir(parents=True)
    (core / "health" / "linked-sites.json").write_text(
        '{"version": 1, "sites": [{"home": "%s", "session": "android-body", '
        '"sha": "%s"}]}' % (android, "a" * 40), encoding="utf-8")
    queue = android / "body" / "queues" / body_perception.DENSE_QUEUE_NAME
    queue.parent.mkdir(parents=True)
    queue.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return core, NOW


def test_contiguous_window_reports_no_loss_and_no_duplicates(tmp_path: Path) -> None:
    core, now = _queue(tmp_path, _rows(list(range(100, 150))))

    row = body_perception.read_redmi10_dense_baseline(core, now=now + 1)

    assert row["seq_expected"] == 50
    assert row["seq_received"] == 50
    assert row["seq_lost"] == 0
    assert row["seq_dup"] == 0
    assert row["seq_unparsed"] == 0
    assert row["seq_steps_back"] == 0
    assert "seq-lost=0 seq-dup=0" in row["details"]


def test_removed_sequence_number_is_counted_as_one_lost(tmp_path: Path) -> None:
    seqs = [s for s in range(100, 150) if s != 120]
    core, now = _queue(tmp_path, _rows(seqs))

    row = body_perception.read_redmi10_dense_baseline(core, now=now + 1)

    assert row["seq_expected"] == 50
    assert row["seq_received"] == 49
    assert row["seq_lost"] == 1
    assert row["seq_dup"] == 0
    assert "seq-lost=1 seq-dup=0" in row["details"]


def test_repeated_row_is_named_as_duplicate_not_hidden_as_loss(tmp_path: Path) -> None:
    seqs = list(range(100, 150))
    seqs.insert(30, seqs[30])
    core, now = _queue(tmp_path, _rows(seqs))

    row = body_perception.read_redmi10_dense_baseline(core, now=now + 1)

    assert row["seq_expected"] == 50
    assert row["seq_received"] == 50
    assert row["seq_lost"] == 0
    assert row["seq_dup"] == 1
    assert "seq-lost=0 seq-dup=1" in row["details"]


def test_step_back_is_counted_so_a_restart_span_is_not_read_as_clean(tmp_path: Path) -> None:
    seqs = list(range(100, 120)) + list(range(10, 20))
    core, now = _queue(tmp_path, _rows(seqs))

    row = body_perception.read_redmi10_dense_baseline(core, now=now + 1)

    assert row["seq_steps_back"] == 1
    assert row["seq_lost"] > 0


def test_unparsed_sequence_is_counted_and_excluded(tmp_path: Path) -> None:
    rows = _rows(list(range(100, 110)))
    rows.append(f"live {int(NOW) - 1} light lux=6.0 accuracy=3 age_ms=10")
    core, now = _queue(tmp_path, rows)

    row = body_perception.read_redmi10_dense_baseline(core, now=now + 1)

    assert row["seq_unparsed"] == 1
    assert row["seq_expected"] == 10
    assert row["seq_lost"] == 0


def test_sequence_accounting_does_not_change_the_verdict(tmp_path: Path) -> None:
    clean, now = _queue(tmp_path / "a", _rows(list(range(100, 150))))
    gapped, _ = _queue(tmp_path / "b", _rows([s for s in range(100, 150) if s != 120]))

    # Anchor the clock to the last row so both windows are fresh.
    last_epoch = int(NOW) - 300 + 49 * 5
    clean_row = body_perception.read_redmi10_dense_baseline(clean, now=last_epoch + 1)
    gapped_row = body_perception.read_redmi10_dense_baseline(gapped, now=last_epoch + 1)

    assert clean_row["state"] == gapped_row["state"] == "verified"
    assert clean_row["motion_ratio"] == gapped_row["motion_ratio"]
    assert gapped_row["seq_lost"] == 1
