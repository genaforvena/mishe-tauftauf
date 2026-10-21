"""Live-window naming for the ledger evidence-binding sense.

The sense reports two windows: the latest outcome per role (live compliance)
and the whole post-clause set (standing audit, append-only). These tests pin the
live window's own naming, so the pane's ``latest_bad`` count is readable beside
the roles it counts instead of being re-derived from ``violating``, which lists
the standing audit's newest rows — a different window.
"""
import re
import tracemalloc

from pathlib import Path

from mishe_tauftauf import discovery
from mishe_tauftauf.seed_culture_views import _clip_sample, _observation_reading
from tests.test_discovery import _binding_tape, _tape_entry, _write_evidenced_outcome


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
    # The path is outside the owned site, so no later rule can make it
    # eligible: the row is measured and permanent, not repairable debt. The
    # verdict reads settled, and the row stays published in the standing audit.
    assert result["state"] == "settled"
    assert result["remediation_bands"]["permanent"] == 1
    assert result["owed_rows"] == 0
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
    (home / "records").mkdir(parents=True)
    edited = home / "artifacts" / "edited.md"
    edited.write_text("original\n")
    digest = _write_evidenced_outcome(home, "alice", edited)
    edited.write_text("edited after the record\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    cause = (f"owed={result['owed_rows']} all_bad={result['all_bad']} "
             f"permanent_loss={result['permanent_loss']}")
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

    The verdict is the standing audit's, so a clean live window with a
    standing row reads drift, and the cause that leads the sample is the
    standing count.
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
    assert result["state"] == "drift"
    assert result["latest_bad"] == 0
    # The append-only standing audit keeps the repaired row beside the verdict.
    assert result["all_bad"] == 1
    cause = (f"owed={result['owed_rows']} all_bad={result['all_bad']} "
             f"permanent_loss={result['permanent_loss']}")
    _state, line = _observation_reading(result, "recent", home)
    assert cause in line
    assert "read_at=" in line

def test_evidence_binding_verdict_follows_the_owed_gate(tmp_path):
    """The verdict follows the gate, and the standing audit stays beside it.

    Ruled 2026-10-21 (research-methods wake 77624, discover wake 77549): the
    live window self-heals — every role publishes again, so it empties while
    the append-only audit keeps the loss. A verdict read from it certifies a
    damaged ledger: the plant read ``verified`` over 212 violating rows, and
    the only thing holding it off that verdict was a coverage gap that binds
    no evidence path at all.

    Ruled again 2026-10-21 (research-methods wake 80245 and 81372): a verdict
    composed from the standing audit alone can never be green, because a
    class-B row cannot be unbound from an append-only store. So the verdict
    follows the **gate** — the class-B rows whose work was lost and that no
    later, intact outcome of the same role re-evidenced — and the standing
    audit stays published beside it as history. The 2026-10-16 objection
    (senses wake 57698) was that a drift verdict had no visible field to
    support it; the gate's count leads the sample, so the pane's clipped
    prefix carries the cause of the verdict it shows.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    # bob's only violation is historical: a later clean outcome followed it,
    # but nothing re-evidenced the lost work.
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
    assert result["state"] == "drift"
    assert result["latest_bad"] == 0
    assert result["latest_bad_roles"] == []
    assert "latest_bad_roles=none" in result["sample"]
    # The standing audit retains the loss beside the verdict.
    assert result["all_bad"] == 1
    assert result["permanent_loss"] == 1
    # The gate names the row that is still owed, and the row is owed because
    # nothing re-evidenced it.
    assert result["owed_rows"] == 1
    assert result["owed_roles"] == {"bob": 1}
    assert result["remediation_bands"] == {"versioned": 0, "permanent": 0,
                                           "disposed": 0, "rebound": 0, "cited": 0,
                                           "recoverable": 0, "owed": 1}
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
    """The mechanism is a name for the failure, not a second class.

    An appended file and a rewritten file both break the unchanged-bytes
    requirement, so both are class B and both drop the outcome from the
    evidenced count. Splitting them into separate classes would let a reader
    count a rewrite as a lesser failure than the clause treats it as.

    The mechanism does move the verdict, and that is the point of separating
    them: the gate counts work that was **lost**, and an appended file still
    carries its bound bytes, so nothing was lost. The row is recoverable, so
    no repairable work is owed and the verdict reads settled. A rewritten
    file, whose bound bytes are gone, stays owed and keeps the verdict at
    drift. The clause still treats the two alike; the reading does not
    pretend the loss was the same in both.
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
    assert result["remediation_bands"]["recoverable"] == 1
    assert result["owed_rows"] == 0
    assert result["state"] == "settled"


def _candidate_tape(home: Path, entries: list[tuple[int, str, str, str]]) -> None:
    """A tape of publication candidates that fail validation, bodies verbatim.

    ``_binding_tape`` frames every entry as a well-formed publication, so a
    test that needs a *damaged* candidate writes the body itself: what makes
    a candidate a gap is which parts of the publication are missing or
    malformed.
    """
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))


def test_evidence_binding_unknown_rests_on_a_gap_that_can_hide_a_binding(tmp_path):
    """UNKNOWN is entered for a gap that can still hide a bound evidence path.

    A candidate whose header is a real outcome header but whose publication
    never resolves to a record may be hiding a bound evidence path, so the
    bound set cannot be derived and the reading is UNKNOWN — the count it
    carries is a lower bound. This is the branch that makes the verdict's
    indeterminacy about the ledger it measures.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    _candidate_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         "Wall outcome accepted by alice\noutcome without a record reference\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "unknown"
    assert result["binding_gaps"] == [1]
    assert result["irrelevant_gaps"] == []
    assert result["coverage_incomplete"] == 1


def test_evidence_binding_gap_that_binds_nothing_is_not_the_verdict_reason(tmp_path):
    """A gap that binds nothing is reported beside the verdict, not as its reason.

    This is the plant's own live gap: a plain chat message whose first line
    mimics an outcome publication. It binds no evidence path, so it cannot
    make the bound set undeterminable, and it must not be the reason a
    reading is UNKNOWN — otherwise repairing it would certify whatever else
    the ledger holds.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    _candidate_tape(home, [
        (1, "2026-10-06T17:00:00Z", "witness",
         "Wall outcome hypothesis-changed by witness # prose after the header\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["irrelevant_gaps"] == [1]
    assert result["binding_gaps"] == []
    assert result["state"] == "verified"
    assert "binding_gaps=0" in result["sample"]


def test_evidence_binding_publishes_the_standing_loss_and_its_attribution(tmp_path):
    """The standing loss is published with the roles that hold it.

    The count alone says the ledger is damaged; the attribution says who
    holds the damage, so a reader can route it. The gate's own count is
    published beside it, with the row detail, so the debt is actionable: a
    role clears its own row by publishing an outcome that declares that row's
    record digest.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    stale = home / "artifacts" / "stale.md"
    stale.write_text("original\n")
    stale_digest = _write_evidenced_outcome(home, "bob", stale)
    stale.write_text("edited after record\n")
    wall_file = home / "walls" / "alice.md"
    wall_file.write_text("wall file\n")
    alice_digest = _write_evidenced_outcome(home, "alice", wall_file)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{stale_digest}.json sha256={stale_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "alice",
         f"[record] records/{alice_digest}.json sha256={alice_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert result["standing_loss_roles"] == {"alice": 1, "bob": 1}
    # The gate holds the destroyed class-B row and not the class-A row: an
    # ineligible path is a defect in the binding, not lost work.
    assert result["owed_rows"] == 1
    assert result["owed_roles"] == {"bob": 1}
    assert result["owed_detail"][0]["role"] == "bob"
    assert result["owed_detail"][0]["record_sha256"] == stale_digest
    # The class-B subset keeps its own attribution, and the two counts differ.
    assert result["permanent_loss_roles"] == {"bob": 1}

def test_evidence_binding_declared_supersession_clears_the_owed_row(tmp_path):
    """A declared supersession link takes a lost row out of the gate.

    The plant's work-identity relation is a structured field: an outcome that
    re-evidences an earlier one names that outcome's record digest. The link
    is a decoded value, so it is admissible under the parsing policy where a
    digest mentioned in prose is not, and the gate's count falls when a role
    re-evidences its own row — which is what makes the verdict reachable.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    repair = home / "artifacts" / "repair.md"
    repair.write_text("the same work, re-evidenced\n")
    repair_digest = _write_evidenced_outcome(home, "bob", repair, supersedes=[lost_digest])
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{repair_digest}.json sha256={repair_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    # Nothing is owed, and the standing audit still holds the row: the verdict
    # is settled, not verified — the ledger's history is not clean.
    assert result["state"] == "settled"
    assert result["owed_rows"] == 0
    assert result["remediation_bands"] == {"versioned": 0, "permanent": 0,
                                           "disposed": 1, "rebound": 0, "cited": 0,
                                           "recoverable": 0, "owed": 0}
    assert result["rebind_link_kinds"] == {"supersedes": 1}
    # The standing audit keeps the loss: the row was broken, whatever repaired it.
    assert result["all_bad"] == 1
    assert result["permanent_loss"] == 1


def test_evidence_binding_remedy_naming_clears_the_owed_row(tmp_path):
    """A successor named after the lost artifact's stem clears the row.

    The clause's own remedy is to write a new artifact for the corrected work
    and cite it in a new outcome. When the new artifact is named after the
    lost one with a remedy suffix, the relation is decidable from the
    filename, so the row leaves the gate without anything being read as prose.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "work.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    remedy = home / "artifacts" / "work-final.md"
    remedy.write_text("the same work, re-bound as a final\n")
    remedy_digest = _write_evidenced_outcome(home, "bob", remedy)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{remedy_digest}.json sha256={remedy_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["owed_rows"] == 0
    assert result["rebind_link_kinds"] == {"remedy-name": 1}
    assert result["state"] == "settled"


def test_evidence_binding_a_ci_status_note_does_not_clear_the_owed_row(tmp_path):
    """A CI disposition note names a status about the work, not the work.

    The remedy naming clears a lost row when a successor artifact is the lost
    artifact's corrected form. A ``-ci`` note carries the run URL, the head SHA
    and the outcome — none of the lost artifact's content — so it cannot be
    evidence that the work was re-evidenced. Measured over the plant's live
    ledger, the single ``-ci`` link was a citation that satisfied the naming
    convention without carrying the work, and the plant published it as
    re-evidenced. So the suffix is out of the vocabulary, and the row stays
    owed: the gate overstates the debt rather than hiding it.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "work.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    status = home / "artifacts" / "work-ci.md"
    status.write_text("CI run https://example.invalid/run/1 head=abc123 completed/success\n")
    status_digest = _write_evidenced_outcome(home, "bob", status)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{status_digest}.json sha256={status_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    # No relation clears the row: the status note is not the corrected artifact.
    assert result["rebind_link_kinds"] == {}
    assert result["owed_rows"] == 1
    assert result["remediation_bands"]["rebound"] == 0
    assert result["state"] == "drift"


def test_evidence_binding_a_broken_successor_does_not_clear_the_row(tmp_path):
    """A successor whose own bound file was amended is not a link.

    It moves the loss instead of repairing it: the row it would have cleared
    is still lost, and the successor's own row is now lost too. Counting it
    as a repair would hide both.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    repair = home / "artifacts" / "repair.md"
    repair.write_text("the same work, re-evidenced\n")
    repair_digest = _write_evidenced_outcome(home, "bob", repair, supersedes=[lost_digest])
    repair.write_text("edited after its own record\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{repair_digest}.json sha256={repair_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    # Two rows are owed: the lost work, and the successor's own row, whose
    # bound file was amended after it was bound.
    assert result["owed_rows"] == 2
    assert result["rebind_link_kinds"] == {}
    assert result["state"] == "drift"


def test_evidence_binding_another_role_cannot_clear_the_row(tmp_path):
    """Only the role that holds the row can re-evidence it.

    A later outcome of a different role that names the lost row's digest
    clears nothing: the work is the first role's, and the link is the
    role's own claim about its own work.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    other = home / "artifacts" / "other.md"
    other.write_text("someone else's work\n")
    other_digest = _write_evidenced_outcome(home, "alice", other, supersedes=[lost_digest])
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "alice",
         f"[record] records/{other_digest}.json sha256={other_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["owed_rows"] == 1
    assert result["owed_roles"] == {"bob": 1}
    assert result["rebind_link_kinds"] == {}


def test_evidence_binding_policy_version_rows_are_labelled_not_damage(tmp_path):
    """A row bound before the allowlist amendment is a version, not a debt.

    The clause keeps a row bound under the earlier text in the standing audit
    under the rule then in force, and says a reassessment under the new rule
    is labelled as such rather than presented as repaired compliance. So the
    row stays in the standing audit, is counted as a policy-version row, and
    never enters the gate: the plant does not owe work for a binding that was
    compliant when it was made.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    wall_file = home / "walls" / "alice.md"
    wall_file.write_text("wall file\n")
    versioned_digest = _write_evidenced_outcome(home, "alice", wall_file)
    _binding_tape(home, [
        (1, "2026-10-10T17:00:00Z", "alice",
         f"[record] records/{versioned_digest}.json sha256={versioned_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["all_bad"] == 1
    assert result["policy_version_rows"] == 1
    assert result["policy_version_current_rows"] == 0
    assert result["permanent_defect_rows"] == 0
    # The row is not lost work, so it is not owed: the verdict is settled.
    assert result["owed_rows"] == 0
    assert result["state"] == "settled"


def test_evidence_binding_a_path_bound_after_the_amendment_is_a_current_defect(tmp_path):
    """An ineligible path bound under the current rule is a permanent defect.

    It is not lost work, so it is not a version and it is not recoverable
    content; and no later rule can make the path eligible. It is measured and
    permanent, so it is banded as permanent and published with its count
    rather than counted as repairable debt: the verdict reads settled, not
    drift, and the word settled says the ledger's history is not clean.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    wall_file = home / "walls" / "alice.md"
    wall_file.write_text("wall file\n")
    current_digest = _write_evidenced_outcome(home, "alice", wall_file)
    _binding_tape(home, [
        (1, "2026-10-20T17:00:00Z", "alice",
         f"[record] records/{current_digest}.json sha256={current_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["all_bad"] == 1
    assert result["policy_version_rows"] == 0
    assert result["policy_version_current_rows"] == 1
    assert result["permanent_defect_rows"] == 1
    assert result["remediation_bands"]["permanent"] == 1
    # No repairable work is owed, so the verdict is settled — not verified.
    assert result["owed_rows"] == 0
    assert result["state"] == "settled"
    assert "permanent=1" in result["verdict_basis"]


def test_evidence_binding_publishes_the_verdict_basis(tmp_path):
    """The verdict's own claim is published with it.

    A state word is only useful if the reader knows what it claims about this
    ledger, so the reading carries its basis: what the gate counts, and how
    much of the standing loss is recoverable, re-evidenced, permanent or a
    policy version.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    basis = result["verdict_basis"]
    assert "owed=1" in basis
    assert "recoverable=0" in basis
    assert "versioned=0" in basis
    assert "disposed=0" in basis
    assert "rebound=0" in basis


def test_evidence_binding_bands_partition_the_standing_audit(tmp_path):
    """Every violating row is in exactly one band, and the bands sum to it.

    The verdict reads the gate, so a row that fell out of the accounting
    would silently disappear from the reading. The bands are the accounting:
    their counts must equal the standing audit's row count, and the gate must
    be exactly the two bands the plant still owes work on.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    appended = home / "artifacts" / "appended.md"
    appended.write_text("original\n")
    appended_digest = _write_evidenced_outcome(home, "bob", appended)
    with appended.open("a", encoding="utf-8") as stream:
        stream.write("more\n")
    wall_file = home / "walls" / "alice.md"
    wall_file.write_text("wall file\n")
    wall_digest = _write_evidenced_outcome(home, "alice", wall_file)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:10:00Z", "bob",
         f"[record] records/{appended_digest}.json sha256={appended_digest}\n"),
        (3, "2026-10-06T17:20:00Z", "alice",
         f"[record] records/{wall_digest}.json sha256={wall_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    bands = result["remediation_bands"]
    assert sum(bands.values()) == result["all_bad"] == 3
    # The appended row's content survives, so it is recoverable, not lost.
    assert bands["recoverable"] == 1
    # The wall row was bound before the allowlist amendment, so it is a version.
    assert bands["versioned"] == 1
    assert bands["owed"] == 1
    # The gate is the owed band alone: recoverable content lost no work, so it
    # does not keep the verdict at drift, and the published detail names
    # exactly the gate's rows.
    assert result["owed_rows"] == bands["owed"]
    assert len(result["owed_detail"]) == result["owed_rows"]
    assert result["state"] == "drift"


def test_evidence_binding_a_digest_citation_clears_the_owed_row(tmp_path):
    """A later artifact carrying the lost row's digest clears the row.

    The citation relation is the third way a row leaves the gate: a later,
    intact outcome of the same role binds an artifact whose bytes carry the
    lost row's record digest. It reads bytes, not prose, but it cannot tell a
    re-evidence from an audit that cites the row it diagnosed — measured over
    the live ledger, three of the fifteen citation hits are audits that name
    the row and leave it standing. So a citation clears the row, and every
    cleared row is published with the successor that cleared it, so the
    residual stays auditable.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    repair = home / "artifacts" / "repair.md"
    repair.write_text(f"the same work, re-evidenced; row {lost_digest}\n")
    repair_digest = _write_evidenced_outcome(home, "bob", repair)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{repair_digest}.json sha256={repair_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["owed_rows"] == 0
    assert result["remediation_bands"]["cited"] == 1
    assert result["rebind_link_kinds"] == {"digest-cited": 1}
    # The cleared row is published with the successor that cleared it, so the
    # weakest relation's rows can be audited by any reader.
    assert result["linked_detail"][0]["kind"] == "digest-cited"
    assert result["linked_detail"][0]["sequence"] == 1
    assert result["linked_detail"][0]["successor"] == 2
    assert result["linked_detail"][0]["successor_evidence"] == "artifacts/repair.md"
    assert result["state"] == "settled"


def test_evidence_binding_another_role_citation_clears_nothing(tmp_path):
    """Only the role that holds the row can re-evidence it.

    A later outcome of a different role whose artifact carries the lost row's
    digest clears nothing: the work is the first role's, and the link is the
    role's own claim about its own work.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    other = home / "artifacts" / "other.md"
    other.write_text(f"bob's row {lost_digest} was diagnosed\n")
    other_digest = _write_evidenced_outcome(home, "alice", other)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "alice",
         f"[record] records/{other_digest}.json sha256={other_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["owed_rows"] == 1
    assert result["owed_roles"] == {"bob": 1}
    assert result["rebind_link_kinds"] == {}


def test_evidence_binding_a_citation_beyond_the_byte_budget_leaves_the_row_owed(tmp_path, monkeypatch):
    """An artifact too large to search is not read for a citation.

    The citation search is bounded per file and per scan. A successor over the
    budget is not searched, so a row whose only link is a citation inside it
    stays owed. That is the safe direction: the gate overstates the debt
    instead of hiding it, and the skipped count is published so a reader knows
    the gate is an upper bound.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    monkeypatch.setattr(discovery, "EVIDENCE_CITATION_SEARCH_BYTES", 0)
    lost = home / "artifacts" / "lost.md"
    lost.write_text("original\n")
    lost_digest = _write_evidenced_outcome(home, "bob", lost)
    lost.write_text("edited after record\n")
    repair = home / "artifacts" / "repair.md"
    repair.write_text(f"the same work, re-evidenced; row {lost_digest}\n")
    repair_digest = _write_evidenced_outcome(home, "bob", repair)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{lost_digest}.json sha256={lost_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{repair_digest}.json sha256={repair_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["owed_rows"] == 1
    assert result["rebind_link_kinds"] == {}
    assert result["citation_files_skipped"] == 1
    assert result["state"] == "drift"


def test_evidence_binding_recoverable_content_does_not_keep_the_verdict_at_drift(tmp_path):
    """Content that survives its broken binding is not lost work.

    An appended file still carries the bound bytes, so nothing was lost: the
    row is recoverable and no repairable work is owed. The verdict reads
    settled — not verified, because the binding is still broken.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    appended = home / "artifacts" / "appended.md"
    appended.write_text("original\n")
    digest = _write_evidenced_outcome(home, "alice", appended)
    with appended.open("a", encoding="utf-8") as stream:
        stream.write("more\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["all_bad"] == 1
    assert result["remediation_bands"]["recoverable"] == 1
    assert result["owed_rows"] == 0
    assert result["state"] == "settled"


#: The chunk size the gate reads a bound artifact in. A whole-file read made
#: one retained evidence file the largest thing the live observer held in
#: memory, and the prefix-search budgets bound time, not memory.
GATE_CHUNK_BYTES = 1024 * 1024


def _large_artifact(home: Path, name: str, size: int, filler: bytes = b"a") -> Path:
    """A bound artifact of a size that dwarfs the gate's read chunk."""
    path = home / "artifacts" / name
    path.write_bytes(filler * size)
    return path


def test_evidence_binding_bounds_the_memory_a_large_bound_artifact_costs(tmp_path, monkeypatch):
    """A large retained artifact is checked without being materialized.

    The gate compares each bound artifact's current bytes with the digest
    recorded at binding time and, when they differ, searches the file's
    prefixes for that digest. Both reads are chunked, so the memory the gate
    spends on an artifact is bounded by the chunk rather than by the artifact:
    the search budgets bound time, not memory, and a whole-file read let one
    retained evidence file end the live observer.

    The edit here is in the file's **last** byte, so the row is only class B
    if the whole file was hashed — a digest taken over the first chunk alone
    would match the bound digest and read the intact artifact as amended.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    size = 16 * GATE_CHUNK_BYTES
    large = _large_artifact(home, "large.md", size)
    digest = _write_evidenced_outcome(home, "alice", large)
    large.write_bytes(b"a" * (size - 1) + b"z")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    # The prefix search is capped, so the search stays cheap; the file is far
    # larger than the budget and is left unclassified rather than guessed at.
    monkeypatch.setattr(discovery, "EVIDENCE_PREFIX_SEARCH_BYTES", 64 * 1024)
    reads: list[Path] = []
    original = Path.read_bytes

    def spy(self):
        reads.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    tracemalloc.start()
    try:
        result = discovery._ledger_evidence_binding(home)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["class_counts"]["B"] == 1
    assert result["permanent_loss_kind"] == {"append": 0, "rewrite": 0, "unknown": 1}
    assert result["state"] == "drift"
    # The gate holds a chunk, not a file: the peak it allocates is a small
    # multiple of the chunk, and far below the artifact it checked.
    assert peak < 4 * GATE_CHUNK_BYTES
    assert peak < size
    # The artifact was never read whole.
    assert large.resolve() not in [path.resolve() for path in reads]


def test_evidence_binding_keeps_a_large_unchanged_artifact_intact(tmp_path):
    """The whole-file digest comparison survives the chunked read.

    The bound bytes of a large artifact are still the bytes that are there
    now, so the row is not a violation. A digest taken over the first chunk
    alone would also pass here, so the pair of tests — an intact large file
    and one edited in its last byte — is what pins the comparison as a
    whole-file comparison.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    large = _large_artifact(home, "large.md", 16 * GATE_CHUNK_BYTES)
    digest = _write_evidenced_outcome(home, "alice", large)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["all_bad"] == 0
    assert result["permanent_loss"] == 0
    assert result["state"] == "verified"


def test_evidence_binding_classifies_large_amendments_across_chunk_boundaries(tmp_path):
    """The mechanism names the failure for artifacts larger than one chunk.

    The prefix search runs across chunk boundaries: an appended artifact is
    an append when its bound digest is the digest of a prefix that ends past
    the first chunk, and a rewritten artifact is a rewrite when no prefix
    matches.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    appended = _large_artifact(home, "appended.md", GATE_CHUNK_BYTES)
    alice_digest = _write_evidenced_outcome(home, "alice", appended)
    appended.write_bytes(b"a" * GATE_CHUNK_BYTES + b"b" * GATE_CHUNK_BYTES)
    rewritten = _large_artifact(home, "rewritten.md", GATE_CHUNK_BYTES, b"c")
    bob_digest = _write_evidenced_outcome(home, "bob", rewritten)
    rewritten.write_bytes(b"d" * GATE_CHUNK_BYTES)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{alice_digest}.json sha256={alice_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{bob_digest}.json sha256={bob_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["permanent_loss"] == 2
    assert result["permanent_loss_kind"] == {"append": 1, "rewrite": 1, "unknown": 0}
    assert result["remediation_bands"]["recoverable"] == 1
    assert result["remediation_bands"]["owed"] == 1
