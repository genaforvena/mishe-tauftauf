"""Live-window naming for the ledger evidence-binding sense.

The sense reports two windows: the latest outcome per role (live compliance)
and the whole post-clause set (standing audit, append-only). These tests pin the
live window's own naming, so the pane's ``latest_bad`` count is readable beside
the roles it counts instead of being re-derived from ``violating``, which lists
the standing audit's newest rows — a different window.
"""
from __future__ import annotations

from pathlib import Path

from mishe_tauftauf import discovery
from tests.test_discovery import _binding_tape, _write_evidenced_outcome


def test_evidence_binding_clean_store_names_no_live_bad_role(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence_a = home / "artifacts" / "wake-a.md"
    evidence_a.write_text("write-once A\n")
    evidence_b = home / "artifacts" / "wake-b.md"
    evidence_b.write_text("write-once B\n")
    digest_a = _write_evidenced_outcome(home, "alice", evidence_a)
    digest_b = _write_evidenced_outcome(home, "bob", evidence_b)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest_a}.json sha256={digest_a}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{digest_b}.json sha256={digest_b}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "verified"
    assert result["latest_bad"] == 0
    assert result["latest_bad_roles"] == []
    assert "latest_bad_roles=none" in result["sample"]


def test_evidence_binding_names_live_bad_roles_not_the_standing_audit(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # bob's only violation is historical: a later clean outcome repaired it.
    stale = home / "artifacts" / "stale.md"
    stale.write_text("original\n")
    stale_digest = _write_evidenced_outcome(home, "bob", stale)
    stale.write_text("edited after record\n")
    fresh = home / "artifacts" / "fresh.md"
    fresh.write_text("write-once\n")
    fresh_digest = _write_evidenced_outcome(home, "bob", fresh)
    # alice's latest outcome is non-compliant: evidence outside artifacts/.
    wall_file = home / "walls" / "alice.md"
    wall_file.write_text("wall file\n")
    alice_digest = _write_evidenced_outcome(home, "alice", wall_file)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{stale_digest}.json sha256={stale_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{fresh_digest}.json sha256={fresh_digest}\n"),
        (3, "2026-10-06T18:00:00Z", "alice",
         f"[record] records/{alice_digest}.json sha256={alice_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert result["all_bad"] == 2  # bob's historical B plus alice's live A
    assert result["latest_bad"] == 1
    # The live set names only the live violation; bob's repaired row stays in
    # the standing audit and is not reported as currently non-compliant.
    assert result["latest_bad_roles"] == ["alice(A)"]
    assert "latest_bad_roles=alice(A)" in result["sample"]
    assert "latest_bad_roles=bob" not in result["sample"]
