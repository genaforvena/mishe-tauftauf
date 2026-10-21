"""Live-window naming for the ledger evidence-binding sense.

The sense reports two windows: the latest outcome per role (live compliance)
and the whole post-clause set (standing audit, append-only). These tests pin the
live window's own naming, so the pane's ``latest_bad`` count is readable beside
the roles it counts instead of being re-derived from ``violating``, which lists
the standing audit's newest rows — a different window.
"""
import re

from pathlib import Path

from mishe_tauftauf import discovery
from mishe_tauftauf.seed_culture_views import _clip_sample, _observation_reading
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

def test_evidence_binding_sample_stamps_its_read_watermark(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence = home / "artifacts" / "wake.md"
    evidence.write_text("write-once\n")
    digest = _write_evidenced_outcome(home, "alice", evidence)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert re.search(r"read_at=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                     result["sample"])


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

def test_evidence_binding_violating_rows_show_site_relative_paths(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # alice's evidence lives outside artifacts/: the row must show where.
    wall_file = home / "walls" / "alice.md"
    wall_file.write_text("wall file\n")
    alice_digest = _write_evidenced_outcome(home, "alice", wall_file)
    # bob's evidence is bound by an absolute path under the home, then edited.
    absolute = home / "artifacts" / "bob.md"
    absolute.write_text("write-once\n")
    bob_digest = _write_evidenced_outcome(home, "bob", absolute)
    absolute.write_text("edited after record\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{alice_digest}.json sha256={alice_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{bob_digest}.json sha256={bob_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert "alice@2026-10-06T17:00:00Z:walls/alice.md(A)" in result["sample"]
    assert "bob@2026-10-06T17:30:00Z:artifacts/bob.md(B)" in result["sample"]
    assert result["violations"] == [
        "alice@2026-10-06T17:00:00Z:walls/alice.md(A)",
        "bob@2026-10-06T17:30:00Z:artifacts/bob.md(B)",
    ]


def test_evidence_binding_violating_row_outside_home_shows_stored_path(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "secret.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("outside the site home\n")
    digest = _write_evidenced_outcome(home, "alice", outside)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert f"alice@2026-10-06T17:00:00Z:{outside}(A)" in result["sample"]

def test_evidence_binding_names_permanent_loss_beside_all_bad(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # alice's bound file was edited after record (B); bob's evidence sits
    # outside artifacts/ (A). The trial count drops only the B row.
    edited = home / "artifacts" / "edited.md"
    edited.write_text("original\n")
    edited_digest = _write_evidenced_outcome(home, "alice", edited)
    edited.write_text("edited after record\n")
    wall_file = home / "walls" / "bob.md"
    wall_file.write_text("wall file\n")
    bob_digest = _write_evidenced_outcome(home, "bob", wall_file)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{edited_digest}.json sha256={edited_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{bob_digest}.json sha256={bob_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert result["all_bad"] == 2
    assert result["permanent_loss"] == 1
    assert "all_bad=2 permanent_loss=1" in result["sample"]


def test_evidence_binding_reports_the_class_mix_as_structured_fields(tmp_path):
    """A reader learns the class mix from a field, not from parsing strings.

    The mix is what tells the permanent loss apart from the eligibility
    findings: class B is an amended file (the outcome cannot be rebound),
    while A and D are eligibility findings the clause audits without
    dropping the outcome from the evidenced count.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # alice's file was edited after record (B).
    edited = home / "artifacts" / "edited.md"
    edited.write_text("original\n")
    edited_digest = _write_evidenced_outcome(home, "alice", edited)
    edited.write_text("edited after record\n")
    # bob's evidence sits outside artifacts/ (A) and is cited by two
    # outcomes, so the same path breaks the citation-once rule (D).
    wall_file = home / "walls" / "bob.md"
    wall_file.write_text("wall file\n")
    bob_digest = _write_evidenced_outcome(home, "bob", wall_file)
    bob_second = _write_evidenced_outcome(home, "carol", wall_file)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{edited_digest}.json sha256={edited_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{bob_digest}.json sha256={bob_digest}\n"),
        (3, "2026-10-06T18:00:00Z", "carol",
         f"[record] records/{bob_second}.json sha256={bob_second}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["class_counts"] == {"A": 2, "B": 1, "D": 2}
    # The exact class strings partition the violating rows.
    assert result["class_rows"] == {"AD": 2, "B": 1}
    assert sum(result["class_rows"].values()) == result["all_bad"]


def test_evidence_binding_separates_permanent_loss_from_the_live_window(tmp_path):
    """A class-B row is permanently lost; whether it is still current is other.

    A role that amends its bound file loses that outcome's evidence forever,
    but publishing a later clean outcome restores its current standing. The
    two numbers answer different questions, so both are reported.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # bob's first outcome was amended after record: permanently lost.
    stale = home / "artifacts" / "stale.md"
    stale.write_text("original\n")
    stale_digest = _write_evidenced_outcome(home, "bob", stale)
    stale.write_text("edited after record\n")
    # bob's later outcome is clean, so bob's current standing is intact.
    fresh = home / "artifacts" / "fresh.md"
    fresh.write_text("write-once\n")
    fresh_digest = _write_evidenced_outcome(home, "bob", fresh)
    # alice's only outcome was amended: still current, so still non-compliant.
    current = home / "artifacts" / "current.md"
    current.write_text("original\n")
    current_digest = _write_evidenced_outcome(home, "alice", current)
    current.write_text("edited after record\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{stale_digest}.json sha256={stale_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{fresh_digest}.json sha256={fresh_digest}\n"),
        (3, "2026-10-06T18:00:00Z", "alice",
         f"[record] records/{current_digest}.json sha256={current_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert result["permanent_loss"] == 2
    assert result["permanent_loss_current"] == 1
    assert result["permanent_loss_superseded"] == 1
    assert result["permanent_loss_roles"] == {"alice": 1, "bob": 1}
    assert result["latest_bad_roles"] == ["alice(B)"]


def test_evidence_binding_reports_the_class_b_rows_spread_over_roles(tmp_path):
    """The permanent loss is spread, not concentrated in one role."""
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    citations = []
    for index, role in enumerate(("alice", "bob", "carol")):
        evidence = home / "artifacts" / f"{role}.md"
        evidence.write_text("original\n")
        digest = _write_evidenced_outcome(home, role, evidence)
        evidence.write_text("edited after record\n")
        citations.append((index + 1, f"2026-10-06T17:{index:02d}:00Z", role,
                           f"[record] records/{digest}.json sha256={digest}\n"))
    _binding_tape(home, citations)
    result = discovery._ledger_evidence_binding(home)
    assert result["permanent_loss"] == 3
    assert result["permanent_loss_roles"] == {"alice": 1, "bob": 1, "carol": 1}
    assert result["permanent_loss_current"] == 3


def test_evidence_binding_stamps_live_bad_roles_last_outcome_time(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # codex's only outcome is three days old: the live window is the latest
    # outcome per role over the whole post-clause set, so the row stands and
    # the stamp is what reads it as stale rather than current.
    wall_file = home / "walls" / "codex.md"
    wall_file.write_text("wall file\n")
    digest = _write_evidenced_outcome(home, "codex", wall_file)
    _binding_tape(home, [
        (1, "2026-10-07T18:33:03Z", "codex",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["latest_bad_roles"] == ["codex(A)"]
    assert result["latest_bad_last"] == ["codex@2026-10-07T18:33:03Z"]
    assert "latest_bad_last=codex@2026-10-07T18:33:03Z" in result["sample"]


def test_evidence_binding_absent_file_flagged_as_C_not_B(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence = home / "artifacts" / "absent.md"
    evidence.write_text("write-once\n")
    digest = _write_evidenced_outcome(home, "alice", evidence)
    evidence.unlink()
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert result["all_bad"] == 1
    assert result["permanent_loss"] == 0
    assert "alice@2026-10-06T17:00:00Z:artifacts/absent.md(C)" in result["sample"]
    assert result["latest_bad_roles"] == ["alice(C)"]
    assert result["violations"] == [
        "alice@2026-10-06T17:00:00Z:artifacts/absent.md(C)"]


def test_evidence_binding_drift_cause_survives_the_pane_clip(tmp_path):
    """The pane clips a sample to 120 cells, so a drift verdict must carry its
    standing-audit cause inside that prefix.

    ``93c1f3f`` put the read watermark first in this sample. On the live store
    (``bound=1430 latest_roles=10``) those 29 extra cells pushed ``all_bad=``
    past the clip and the pane rendered ``DRIFT … latest_bad_last=none…`` — a
    verdict with every visible field clean and its cause gone. The cause fields
    lead the sample so no clip budget can drop them; ``read_at`` stays in the
    sample and the observation dict.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    wall_file = home / "walls" / "alice.md"
    wall_file.write_text("wall file\n")
    digest = _write_evidenced_outcome(home, "alice", wall_file)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    cause = f"all_bad={result['all_bad']} permanent_loss={result['permanent_loss']}"
    assert result["sample"].startswith(cause)
    assert cause in _clip_sample(result["sample"])
    assert "read_at=" in result["sample"]
    assert "read_at" in result
    _state, line = _observation_reading(result, "recent", home)
    assert cause in line


def test_evidence_binding_pane_keeps_the_read_watermark_when_the_live_window_is_clean(tmp_path):
    """The watermark fits the same prefix while no role is live-bad.

    The sample leads with the drift cause, so what follows it competes for the
    pane's 120 cells: a clean live window (`latest_bad=0`, its roles and stamps
    all `none`) leaves room for `read_at=`, and a live-bad window's role names
    and stamps take that room instead. The cause is what must always survive.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # bob's B violation is historical: a later clean outcome repairs the live
    # window while the append-only standing audit keeps the row.
    stale = home / "artifacts" / "stale.md"
    stale.write_text("original\n")
    stale_digest = _write_evidenced_outcome(home, "bob", stale)
    stale.write_text("edited after record\n")
    fresh = home / "artifacts" / "fresh.md"
    fresh.write_text("write-once\n")
    fresh_digest = _write_evidenced_outcome(home, "bob", fresh)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{stale_digest}.json sha256={stale_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{fresh_digest}.json sha256={fresh_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "verified"
    assert result["latest_bad"] == 0
    # The append-only standing audit keeps the repaired row beside the verdict.
    assert result["all_bad"] == 1
    cause = f"all_bad={result['all_bad']} permanent_loss={result['permanent_loss']}"
    _state, line = _observation_reading(result, "recent", home)
    assert cause in line
    assert "read_at=" in line

def test_evidence_binding_state_follows_the_live_window(tmp_path):
    """State follows the live window, not the standing audit.

    Observed live 2026-10-16 (senses wake 57698, DM 57700): ``state`` read
    ``drift`` whenever any standing violation existed, so a store whose every
    role had repaired its latest outcome still read DRIFT with
    ``latest_bad=0`` — a verdict no visible field supported. The standing
    audit is append-only and keeps the row; the verdict follows the latest
    outcome per role.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # bob's only violation is historical: a later clean outcome repaired it.
    stale = home / "artifacts" / "stale.md"
    stale.write_text("original\n")
    stale_digest = _write_evidenced_outcome(home, "bob", stale)
    stale.write_text("edited after record\n")
    fresh = home / "artifacts" / "fresh.md"
    fresh.write_text("write-once\n")
    fresh_digest = _write_evidenced_outcome(home, "bob", fresh)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{stale_digest}.json sha256={stale_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{fresh_digest}.json sha256={fresh_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "verified"
    assert result["latest_bad"] == 0
    assert result["latest_bad_roles"] == []
    assert "latest_bad_roles=none" in result["sample"]
    # The standing audit retains the repaired violation beside the verdict.
    assert result["all_bad"] == 1
    assert result["permanent_loss"] == 1
    assert result["violations"] == [
        "bob@2026-10-06T17:00:00Z:artifacts/stale.md(B)"]


def test_evidence_binding_names_how_the_bound_file_was_amended(tmp_path):
    """An appended file and a rewritten file are different failures.

    Both break the same requirement (the bound bytes stay unchanged), so both
    are class B and the verdict does not move. But a mind that extended its
    notes and a mind that re-authored a published file are different
    behaviours, and a reader cannot tell them apart from the class letter
    alone. The mechanism counts say which.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # alice extended her bound file: the bound bytes are still a prefix.
    appended = home / "artifacts" / "appended.md"
    appended.write_text("original\n")
    alice_digest = _write_evidenced_outcome(home, "alice", appended)
    appended.write_text("original\nappended later\n")
    # bob re-authored his bound file: the bound bytes are gone.
    rewritten = home / "artifacts" / "rewritten.md"
    rewritten.write_text("original\n")
    bob_digest = _write_evidenced_outcome(home, "bob", rewritten)
    rewritten.write_text("something else entirely\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{alice_digest}.json sha256={alice_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{bob_digest}.json sha256={bob_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["permanent_loss"] == 2
    assert result["permanent_loss_kind"] == {"append": 1, "rewrite": 1, "unknown": 0}
    # The mechanism names the failure; it does not move the class or the verdict.
    assert result["class_counts"]["B"] == 2
    assert result["state"] == "drift"
    # The rewritten rows are the ones whose evidence is destroyed, so they are
    # spread over the roles that hold them.
    assert result["permanent_loss_rewrite_roles"] == {"bob": 1}


def test_evidence_binding_mechanism_counts_sum_to_the_permanent_loss(tmp_path):
    """Every class-B row gets a mechanism, or an honest ``unknown``.

    A row whose bound file cannot be read, or whose prefix search ran out of
    its byte budget, is ``unknown`` — never counted as a rewrite on the
    strength of a truncated search.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    edited = home / "artifacts" / "edited.md"
    edited.write_text("original\n")
    digest = _write_evidenced_outcome(home, "alice", edited)
    edited.write_text("edited after record\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert sum(result["permanent_loss_kind"].values()) == result["permanent_loss"]


def test_evidence_binding_leaves_a_file_over_the_search_budget_unclassified(tmp_path, monkeypatch):
    """A file too large to search is ``unknown``, not a guessed rewrite.

    The prefix search is bounded per file and per scan. A file over the budget
    is left unclassified so the reading never claims a rewrite it did not
    measure.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    big = home / "artifacts" / "big.md"
    big.write_bytes(b"x" * 4096)
    digest = _write_evidenced_outcome(home, "alice", big)
    big.write_bytes(b"y" * 4096)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    monkeypatch.setattr(discovery, "EVIDENCE_PREFIX_SEARCH_BYTES", 16)
    result = discovery._ledger_evidence_binding(home)
    assert result["permanent_loss"] == 1
    assert result["permanent_loss_kind"] == {"append": 0, "rewrite": 0, "unknown": 1}
    # The class and the verdict do not depend on the mechanism.
    assert result["class_counts"]["B"] == 1
    assert result["state"] == "drift"


def test_evidence_binding_treats_a_file_bound_while_empty_as_an_append(tmp_path):
    """The empty prefix is a prefix of every file.

    A file bound while it was empty and extended since has its bound bytes
    (none) still present, so it is an append, not a rewrite. Reading it as a
    rewrite would claim the evidence was destroyed when nothing was lost.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    empty = home / "artifacts" / "empty.md"
    empty.write_bytes(b"")
    digest = _write_evidenced_outcome(home, "alice", empty)
    empty.write_bytes(b"written after the binding\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["permanent_loss_kind"]["append"] == 1
    assert result["permanent_loss_kind"]["rewrite"] == 0


def test_evidence_binding_mechanism_does_not_move_the_class_or_the_verdict(tmp_path):
    """The mechanism is a name for the failure, not a second verdict.

    An appended file and a rewritten file both break the unchanged-bytes
    requirement, so both are class B and both drop the outcome from the
    evidenced count. Splitting them into separate classes would let a reader
    count a rewrite as a lesser failure than the clause treats it as.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    appended = home / "artifacts" / "appended.md"
    appended.write_text("original\n")
    digest = _write_evidenced_outcome(home, "alice", appended)
    appended.write_text("original\nmore\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["permanent_loss_kind"]["append"] == 1
    assert result["class_counts"] == {"B": 1}
    assert result["class_rows"] == {"B": 1}
    assert result["permanent_loss"] == 1
    assert result["state"] == "drift"
