"""checks/root-migration-bulk.py: a tree's rc decides the verdict, not the final line alone.

bulk-copy.sh aggregates per-tree rc into the terminal ``=== all done rc=`` line,
but the check is the detector of record for a pass whose log may come from an
earlier or modified producer. A tree that ended rc != 0 must never read as
completion merely because the terminal line says 0.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "root_migration_bulk", REPO / "checks/root-migration-bulk.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_ok_when_every_tree_and_the_pass_end_with_zero():
    verdict, detail, last = check.parse([
        "=== mishe-tauftauf start 2026-11-17T07:53:47Z",
        "=== mishe-tauftauf end rc=0 2026-11-17T08:01:58Z 30925374169 bytes copied",
        "=== .omp end rc=0 2026-11-17T08:10:00Z 34601686107 bytes copied",
        "=== all done rc=0 2026-11-17T08:19:46Z",
    ])
    assert verdict == "ok", detail
    assert last == 1794903586  # 2026-11-17T08:19:46Z


def test_a_failed_tree_is_red_even_when_the_pass_reports_zero():
    verdict, detail, _ = check.parse([
        "=== mishe-tauftauf start 2026-11-17T07:53:47Z",
        "=== mishe-tauftauf end rc=23 2026-11-17T08:01:58Z 30925374169 bytes copied",
        "=== .omp start 2026-11-17T08:02:00Z",
        "=== .omp end rc=0 2026-11-17T08:10:00Z 34601686107 bytes copied",
        "=== all done rc=0 2026-11-17T08:19:46Z",
    ])
    assert verdict == "fail", detail
    assert "mishe-tauftauf rc=23" in detail[0]
    assert "pass ended rc=0" in detail[0]


def test_a_nonzero_pass_rc_stays_red():
    verdict, detail, _ = check.parse([
        "=== mishe-tauftauf end rc=0 2026-11-17T08:01:58Z 30925374169 bytes copied",
        "=== all done rc=23 2026-11-17T08:19:46Z",
    ])
    assert verdict == "fail", detail
    assert "pass ended rc=23" in detail[0]


def test_a_later_successful_end_clears_an_earlier_failure_for_the_same_tree():
    verdict, detail, _ = check.parse([
        "=== mishe-tauftauf end rc=23 2026-11-17T08:01:58Z 1 bytes copied",
        "=== mishe-tauftauf start 2026-11-17T08:02:00Z",
        "=== mishe-tauftauf end rc=0 2026-11-17T08:05:00Z 2 bytes copied",
        "=== all done rc=0 2026-11-17T08:06:00Z",
    ])
    assert verdict == "ok", detail
    assert "mishe-tauftauf 2 B" in detail[0]


def test_an_in_progress_log_is_pending():
    verdict, detail, _ = check.parse([
        "=== mishe-tauftauf start 2026-11-17T07:53:47Z",
        "=== mishe-tauftauf end rc=0 2026-11-17T08:01:58Z 30925374169 bytes copied",
        "=== .omp start 2026-11-17T08:02:00Z",
    ])
    assert verdict == "pending", detail
    assert ".omp since 2026-11-17T08:02:00Z" in detail[0]
