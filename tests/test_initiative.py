from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from mishe_tauftauf import initiative, outcome_events, records
from mishe_tauftauf.feed import FeedEntry, _encode_entry, parse_feed


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


@pytest.fixture
def home(tmp_path):
    for name in ("patches", "records", "artifacts"):
        (tmp_path / name).mkdir()
    return tmp_path


def entry(sequence, source, body, *, age=60):
    return FeedEntry(sequence, (NOW - timedelta(seconds=age)).isoformat(), source, body)


def outcome(home, sequence=1, *, role="discover", kind="accepted", age=60, name="proof.txt"):
    evidence = home / "artifacts" / name
    evidence.write_text("bounded observed result")
    binding = {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    text = "Observed the scoped result."
    reference = records.prepare(home, {"role": role, "kind": kind, "text": text, "evidence": binding}, kind="wall-outcome")
    return entry(sequence, role, f"Wall outcome {kind} by {role}\n{text}\n{reference}", age=age), evidence


def patch(home, name, phase, **fields):
    record = {"phase": phase, "prepared_at": (NOW - timedelta(days=2)).isoformat(), **fields}
    (home / "patches" / f"{name}.json").write_text(json.dumps(record))


def test_lifecycle_counts_results_not_prose_and_keeps_old_open_wake(home):
    tape = [
        entry(1, "seed", "seed wake health observation=1", age=100000),
        entry(2, "seed", "seed wake discover observation=1", age=200),
        entry(3, "seed", "seed yield discover wake=2\nTurn settled (changed); a bug is fixed", age=190),
        entry(4, "seed", "seed wake discover observation=1", age=180),
        entry(5, "seed", "seed yield discover wake=4\nTurn settled (verified); bounded check", age=170),
        entry(6, "seed", "seed wake discover observation=1", age=160),
        entry(7, "seed", "seed yield discover wake=6\nTurn settled (blocked); unavailable service", age=150),
        entry(8, "discover", "Turn settled (verified); ordinary prose is not a seed receipt", age=140),
    ]
    report = initiative.collect(home, now=NOW, entries=iter(tape))
    discover = report["roles"]["discover"]
    assert discover["wakes"] == 3
    assert discover["settlements"] == {"changed": 1, "verified": 1, "blocked": 1}
    assert discover["outcomes"] == {}
    assert discover["open_wakes"] == []
    assert report["roles"]["health"]["wakes"] == 0
    assert report["roles"]["health"]["open_wakes"] == [
        {"ref": "chat.log#1", "timestamp": tape[0].timestamp, "age_seconds": 100000.0}]
    assert report["window"]["start"] == (NOW - timedelta(days=1)).isoformat()
    assert report["history"]["window_entries"] == 7


def test_only_matching_pending_wake_can_settle_and_clear_is_not_delivery(home):
    tape = [
        entry(1, "seed", "seed wake health observation=1", age=200),
        entry(2, "seed", "seed yield discover wake=1\nTurn settled (verified); wrong role", age=190),
        entry(3, "seed", "seed clear health after=1", age=180),
        entry(4, "seed", "seed yield health wake=1\nTurn settled (changed); first receipt", age=170),
        entry(5, "seed", "seed yield health wake=1\nTurn settled (verified); duplicate receipt", age=160),
        entry(6, "seed", "seed wake health observation=1", age=150),
        entry(7, "seed", "seed clear health after=6", age=140),
    ]
    report = initiative.collect(home, now=NOW, entries=tape)
    assert report["roles"]["health"]["settlements"] == {"changed": 1}
    assert report["roles"]["health"]["open_wakes"][0]["ref"] == "chat.log#6"
    assert report["roles"]["discover"]["settlements"] == {}
    assert report["roles"]["discover"]["lifecycle_gaps"][0]["ref"] == "chat.log#2"
    assert report["roles"]["health"]["lifecycle_gaps"][0]["ref"] == "chat.log#5"
    assert report["state"] == "PARTIAL"


def test_missing_settlement_result_and_superseded_wake_remain_unknown(home):
    report = initiative.collect(home, now=NOW, entries=[
        entry(1, "seed", "seed wake discover observation=1", age=200),
        entry(2, "seed", "seed wake discover observation=1", age=190),
        entry(3, "seed", "seed yield discover wake=2\nNo structured result was recorded.", age=180),
    ])
    assert report["roles"]["discover"]["settlements"] == {"UNKNOWN": 1}
    assert report["roles"]["discover"]["lifecycle_gaps"][0]["ref"] == "chat.log#1"
    assert report["roles"]["discover"]["open_wakes"] == []
    assert report["roles"]["discover"]["state"] == "PARTIAL"


def test_outcomes_use_canonical_kind_role_and_live_evidence(home):
    old, _ = outcome(home, 1, age=100000, kind="blocker-resolved", name="old.txt")
    current, _ = outcome(home, 2, kind="hypothesis-changed")
    report = initiative.collect(home, now=NOW, entries=[old, current])
    assert report["roles"]["discover"]["outcomes"] == {"hypothesis-changed": 1}
    assert report["outcomes"]["items"][0]["ref"] == "chat.log#2"
    assert report["outcomes"]["state"] == "OBSERVED"

def test_checked_evidence_recency_uses_newest_digest_checked_outcome_only(home):
    older, _ = outcome(home, 1, age=3600, name="older.txt")
    newer, _ = outcome(home, 2, age=600, name="newer.txt")
    report = initiative.collect(home, now=NOW, entries=[older, newer])
    assert report["roles"]["discover"]["newest_checked_evidence"] == 600.0


def test_damaged_evidence_does_not_set_recency_and_none_is_reported(home):
    publication, evidence = outcome(home, age=600)
    evidence.unlink()
    report = initiative.collect(home, now=NOW, entries=[publication])
    assert report["roles"]["discover"]["newest_checked_evidence"] is None

def test_renderer_accepts_producer_report_lacking_recency_field(home):
    publication, _ = outcome(home, age=600)
    report = initiative.collect(home, now=NOW, entries=[publication])
    for row in report["roles"].values():
        row.pop("newest_checked_evidence")
    lines = initiative._lines(report)
    assert any("checked-evidence-age=none" in line for line in lines)

@pytest.mark.parametrize("damage", ["changed", "missing", "oversized"])
def test_missing_changed_or_bounded_outcome_evidence_is_unknown(home, monkeypatch, damage):
    publication, evidence = outcome(home)
    if damage == "missing":
        evidence.unlink()
    elif damage == "changed":
        evidence.write_text("different result")
    else:
        monkeypatch.setattr(initiative, "MAX_EVIDENCE_BYTES", 4)
    report = initiative.collect(home, now=NOW, entries=[publication])
    assert report["roles"]["discover"]["outcomes"] == {}
    assert report["roles"]["discover"]["outcome_gaps"][0]["state"] == "UNKNOWN"
    assert report["outcomes"]["items"][0]["state"] == "UNKNOWN"
    assert report["outcomes"]["state"] == "PARTIAL"
    assert report["state"] == "PARTIAL"


def test_corrupt_record_and_forged_role_do_not_become_outcomes(home):
    publication, _ = outcome(home)
    reference = publication.body.splitlines()[-1]
    record_path = home / reference.split()[1]
    record_path.chmod(0o600)
    record_path.write_text("{}")
    report = initiative.collect(home, now=NOW, entries=[publication])
    assert report["outcomes"]["items"] == []
    assert report["outcomes"]["gaps"][0]["sequence"] == 1
    assert report["roles"]["discover"]["outcome_gaps"][0]["state"] == "UNKNOWN"
    publication, _ = outcome(home, name="second.txt")
    forged = FeedEntry(1, publication.timestamp, "health", publication.body)
    report = initiative.collect(home, now=NOW, entries=[forged])
    assert report["roles"]["health"]["outcomes"] == {}
    assert "role mismatch" in report["outcomes"]["gaps"][0]["reason"]


def test_remedy_named_successor_re_evidences_a_row_whose_own_evidence_changed(home):
    first, evidence = outcome(home, 1, age=200, name="proof.txt")
    evidence.write_text("edited after publication")
    second, corrected = outcome(home, 2, age=100, name="proof-v2.txt")
    report = initiative.collect(home, now=NOW, entries=[first, second])
    row = report["roles"]["discover"]
    assert row["outcome_gaps"] == []
    assert row["outcome_coverage"] == "OBSERVED"
    re_evidenced = row["re_evidenced_outcomes"]
    assert [item["ref"] for item in re_evidenced] == ["chat.log#1"]
    assert re_evidenced[0]["re_evidenced_by"] == "chat.log#2"
    assert re_evidenced[0]["link_kind"] == "remedy-name"
    assert re_evidenced[0]["own_evidence_state"] == "UNKNOWN"
    reason = re_evidenced[0]["own_evidence_reason"]
    assert reason.startswith("outcome evidence digest changed: recorded ")
    assert hashlib.sha256(b"edited after publication").hexdigest() in reason
    assert hashlib.sha256(b"bounded observed result").hexdigest() in reason
    assert report["outcomes"]["state"] == "OBSERVED"
    assert report["state"] == "OBSERVED"


def test_a_re_evidenced_row_is_not_counted_as_digest_checked_evidence(home):
    first, evidence = outcome(home, 1, age=200, name="proof.txt")
    evidence.write_text("edited after publication")
    second, _ = outcome(home, 2, age=100, name="proof-v2.txt")
    report = initiative.collect(home, now=NOW, entries=[first, second])
    row = report["roles"]["discover"]
    assert row["outcomes"] == {"accepted": 1}
    assert row["newest_checked_evidence"] == 100.0


@pytest.mark.parametrize("damage", ["not-remedy-named", "broken-successor", "other-role", "earlier-successor"])
def test_only_a_later_intact_same_role_remedy_successor_re_evidences(home, damage):
    first, evidence = outcome(home, 1, age=200, name="proof.txt")
    evidence.write_text("edited after publication")
    if damage == "not-remedy-named":
        second, _ = outcome(home, 2, age=100, name="proof-copy.txt")
    elif damage == "broken-successor":
        second, corrected = outcome(home, 2, age=100, name="proof-v2.txt")
        corrected.write_text("edited after publication")
    elif damage == "other-role":
        second, _ = outcome(home, 2, age=100, role="health", name="proof-v2.txt")
    else:
        second, _ = outcome(home, 2, age=300, name="proof-v2.txt")
    report = initiative.collect(home, now=NOW, entries=[first, second])
    row = report["roles"]["discover"]
    assert "chat.log#1" in [gap["ref"] for gap in row["outcome_gaps"]]
    assert row["re_evidenced_outcomes"] == []
    assert row["outcome_coverage"] == "PARTIAL"
    assert report["outcomes"]["state"] == "PARTIAL"


def test_supplied_snapshot_reuses_canonical_classifier_without_tape_scan(home, monkeypatch):
    publication, _ = outcome(home)

    def rescan(*args, **kwargs):
        pytest.fail("supplied entries must not trigger another tape scan")

    monkeypatch.setattr(outcome_events, "parse_feed", rescan)
    monkeypatch.setattr(initiative, "parse_feed", rescan)
    report = initiative.collect(home, now=NOW, entries=iter([publication]))
    assert report["roles"]["discover"]["outcomes"] == {"accepted": 1}
    citation = FeedEntry(2, publication.timestamp, "health", publication.body.splitlines()[-1], home)
    canonical = outcome_events.classify_outcomes(home, NOW - timedelta(days=1), entries=[citation])
    assert canonical == {"events": [], "incomplete": [], "unavailable": None}


def test_patch_attempt_check_failure_and_revert_are_distinct_current_states(home):
    patch(home, "prepared", "prepared")
    patch(home, "test-failed", "test-failed", test={"code": 2, "log": "test.log"})
    patch(home, "reverted", "reverted", delivery_verified=False)
    patch(home, "recovery-failed", "revert-failed", delivery_verified=False, rollback_failure="runtime changed")
    patch(home, "merely-applied", "applied", delivery_verified=False)
    patch(home, "flag-only", "applied", delivery_verified=True)
    patch(home, "checked", "applied", role="discover", delivery_verified=True,
          verification={"at": (NOW - timedelta(minutes=5)).isoformat(), "patch_hash": "saved binding"})
    (home / "patches/broken.json").write_text("{")
    report = initiative.collect(home, now=NOW, entries=[])
    patches = report["patches"]
    assert patches["counts"] == {"attempt_recorded": 5, "checked_delivery": 1, "open_delivery_gap": 5,
                                  "reverted": 1, "failed": 2, "unknown": 1}
    by_id = {item["id"]: item for item in patches["items"]}
    assert by_id["prepared"]["attempt_recorded"] is False
    assert by_id["test-failed"]["test_code"] == 2
    assert by_id["merely-applied"]["verification"] == "NOT_VERIFIED"
    assert by_id["flag-only"]["verification"] == "UNKNOWN"
    assert by_id["checked"]["verification"] == "RECORDED"
    assert by_id["checked"]["role"] is None
    assert by_id["reverted"]["disposition"] == "REVERTED"
    assert by_id["reverted"]["open_delivery_gap"] is False
    assert by_id["recovery-failed"]["open_delivery_gap"] is True
    assert by_id["prepared"]["age_seconds"] == 172800
    assert by_id["broken"]["state"] == "UNKNOWN"
    rendered = initiative.lines(home, now=NOW, entries=[])
    assert any("patches/reverted.json" in line and "phase=reverted" in line for line in rendered)
    assert any("patches/prepared.json" in line and "age=172800s" in line for line in rendered)


def test_prediction_channels_are_not_invented_authors_and_terminal_status_is_preserved(home):
    check_at = (NOW - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tape = [
        entry(1, "prediction/discover", f"Observe the result\nCheck at: {check_at}", age=3600),
        entry(2, "mishe-tauftauf", "prediction 1: accepted", age=3500),
        entry(3, "prediction/project-not-a-role", f"Another result\nCheck at: {check_at}", age=3400),
        entry(4, "mishe-tauftauf", "prediction 3: accepted\nprediction 3: missed", age=3300),
        entry(5, "prediction/health", "No canonical check time", age=3200),
    ]
    report = initiative.collect(home, now=NOW, entries=tape)
    discover, project, damaged = report["predictions"]["items"]
    assert discover["role"] == "discover"
    assert discover["open"] is True
    assert discover["overdue_seconds"] == 1800
    assert report["roles"]["discover"]["predictions"] == [discover]
    assert project["role"] is None
    assert project["statuses"] == ["accepted", "missed"]
    assert project["terminal"] is True
    assert project["open"] is False
    assert damaged["state"] == "UNKNOWN"
    assert report["roles"]["health"]["state"] == "UNKNOWN"


@pytest.mark.parametrize("contents", [None, b"not a feed"])
def test_unavailable_tape_is_unknown_without_hiding_patch_evidence(home, contents):
    if contents is not None:
        (home / "chat.log").write_bytes(contents)
    patch(home, "open", "prepared")
    report = initiative.collect(home, now=NOW)
    assert report["state"] == "UNKNOWN"
    assert report["history"]["state"] == "UNKNOWN"
    assert report["outcomes"]["state"] == "UNKNOWN"
    assert report["predictions"]["state"] == "UNKNOWN"
    assert report["roles"]["discover"]["state"] == "UNKNOWN"
    assert report["patches"]["items"][0]["phase"] == "prepared"
    assert report["patches"]["items"][0]["open_delivery_gap"] is True


def test_entry_and_patch_bounds_do_not_become_empty_success(home, monkeypatch):
    monkeypatch.setattr(initiative, "MAX_ENTRIES", 1)
    report = initiative.collect(home, now=NOW, entries=[entry(1, "health", "one"), entry(2, "health", "two")])
    assert report["history"]["state"] == "UNKNOWN"
    assert "bound" in report["history"]["reason"]
    monkeypatch.setattr(initiative, "MAX_PATCHES", 1)
    patch(home, "one", "prepared")
    patch(home, "two", "prepared")
    report = initiative.collect(home, now=NOW, entries=[])
    assert report["patches"]["state"] == "UNKNOWN"
    assert report["patches"]["counts"] is None


def test_partial_history_and_future_entries_do_not_claim_complete_coverage(home):
    report = initiative.collect(home, now=NOW, entries=[
        entry(8, "seed", "seed yield discover wake=3\nTurn settled (verified); missing wake"),
        entry(9, "seed", "seed wake health observation=1", age=-1),
    ])
    assert report["history"]["state"] == "PARTIAL"
    assert report["history"]["gaps"][0]["ref"] == "chat.log#9"
    assert report["roles"]["discover"]["settlements"] == {}
    assert report["roles"]["health"]["open_wakes"] == []
    assert report["state"] == "PARTIAL"


def test_cached_projection_rejects_stale_future_corrupt_or_missing_snapshot(home):
    assert "UNKNOWN" in initiative.snapshot_lines(home, now=NOW)[0]
    (home / "checks").mkdir()
    path = home / "checks/initiative.json"
    patch(home, "unfinished", "prepared")
    report = initiative.collect(home, now=NOW, entries=[])
    path.write_text(json.dumps(report))
    assert any("patches/unfinished.json" in line and "age=172800s" in line
               for line in initiative.snapshot_lines(home, now=NOW))
    assert "UNKNOWN" in initiative.snapshot_lines(home, now=NOW + timedelta(seconds=181), max_age=180)[0]
    assert "UNKNOWN" in initiative.snapshot_lines(home, now=NOW - timedelta(seconds=1))[0]
    for invalid in ("{", "[]", "{}", '{"version": 1, "generated_at": 42}'):
        path.write_text(invalid)
        assert "UNKNOWN" in initiative.snapshot_lines(home, now=NOW)[0]


def test_oversized_prediction_plan_is_unknown_without_hiding_other_predictions(home, monkeypatch):
    (home / "plan.txt").write_text("a plan larger than the bounded budget")
    monkeypatch.setattr(initiative, "MAX_BYTES", 4)
    check_at = (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = initiative.collect(home, now=NOW, entries=[
        entry(1, "prediction/discover", f"Check at: {check_at}\nPlan: plan.txt"),
        entry(2, "mishe-tauftauf", "prediction 1: accepted"),
        entry(3, "prediction/health", f"Check at: {check_at}"),
        entry(4, "mishe-tauftauf", "prediction 3: accepted"),
    ])
    damaged, healthy = report["predictions"]["items"]
    assert damaged["state"] == "UNKNOWN"
    assert "exceeds" in damaged["reason"]
    assert healthy["open"] is True
    assert report["roles"]["health"]["predictions"][0]["open"] is True
    assert report["predictions"]["state"] == "PARTIAL"


@pytest.mark.parametrize("damage", [
    "three-fields", "state", "role-shape", "negative-count", "clean-patch-shape",
    "nonfinite-age", "missing-render-field", "window",
])
def test_cached_health_reader_rejects_fresh_malformed_reports(home, damage):
    patch(home, "unfinished", "prepared")
    report = initiative.collect(home, now=NOW, entries=[])
    if damage == "three-fields":
        report = {key: report[key] for key in ("version", "state", "generated_at")}
    elif damage == "state":
        report["state"] = "OK"
    elif damage == "role-shape":
        report["roles"]["health"] = []
    elif damage == "negative-count":
        report["roles"]["health"]["wakes"] = -1
    elif damage == "clean-patch-shape":
        report["patches"]["items"] = [{"state": "OBSERVED", "open_delivery_gap": False}]
    elif damage == "nonfinite-age":
        report["patches"]["items"][0]["age_seconds"] = float("nan")
    elif damage == "missing-render-field":
        del report["patches"]["items"][0]["age_seconds"]
    else:
        report["window"]["start"] = (NOW + timedelta(seconds=1)).isoformat()
    (home / "checks").mkdir()
    (home / "checks/initiative.json").write_text(json.dumps(report))
    with pytest.raises(ValueError):
        initiative.read_snapshot(home, now=NOW)
    assert "UNKNOWN" in initiative.snapshot_lines(home, now=NOW)[0]


@pytest.mark.parametrize("max_age", [float("nan"), float("inf"), -1, True, "900"])
def test_snapshot_freshness_rejects_invalid_limits(home, max_age):
    (home / "checks").mkdir()
    (home / "checks/initiative.json").write_text(json.dumps(initiative.collect(home, now=NOW, entries=[])))
    with pytest.raises(ValueError):
        initiative.read_snapshot(home, now=NOW, max_age=max_age)
    assert "UNKNOWN" in initiative.snapshot_lines(home, now=NOW, max_age=max_age)[0]


def test_validated_snapshot_reader_never_rescans_live_evidence(home, monkeypatch):
    patch(home, "unfinished", "prepared")
    (home / "checks").mkdir()
    (home / "checks/initiative.json").write_text(json.dumps(initiative.collect(home, now=NOW, entries=[])))

    def rescan(*args, **kwargs):
        pytest.fail("cached health read must not scan live evidence")

    monkeypatch.setattr(initiative, "collect", rescan)
    monkeypatch.setattr(initiative, "parse_feed", rescan)
    report = initiative.read_snapshot(home, now=NOW, max_age=180)
    assert report["patches"]["counts"]["open_delivery_gap"] == 1
    assert report["patches"]["items"][0]["age_seconds"] == 172800
    with pytest.raises(ValueError):
        initiative.read_snapshot(home, now=NOW.replace(tzinfo=None))


def write_tape(home, *lines):
    """Write a tape whose entries carry the given (source, body) pairs."""
    tape = home / "chat.log"
    tape.write_bytes(b"")
    with tape.open("ab") as stream:
        for index, (source, body) in enumerate(lines, start=1):
            moment = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            stream.write(_encode_entry(index, moment, source, body))
    return tape


def test_an_entry_appended_while_the_producer_reads_the_tape_is_not_a_future_event(home, monkeypatch):
    """The tape grows while the producer reads it, and a producer that judges
    the tape against the clock it took before the read calls that growth a
    future event. The entry is real, so the producer must read it.

    The live producer does not pin its clock, so this test does not either:
    the clock is the wall clock, and the entry carries a stamp the wall clock
    reaches during the read.
    """
    write_tape(home, ("seed", "seed wake discover observation=1"))
    original = initiative._read

    def appending_read(path, maximum):
        if path.name == "chat.log":
            # Another writer appends after the producer took its clock and
            # before the producer reads the tape. The stamp is the wall clock,
            # which is what the live producer reads.
            parsed = parse_feed(path.read_bytes(), home=home)
            moment = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            with path.open("ab") as stream:
                stream.write(_encode_entry(len(parsed) + 1, moment, "seed", "seed observation discover"))
        return original(path, maximum)

    monkeypatch.setattr(initiative, "_read", appending_read)
    report = initiative.collect(home)
    assert report["history"]["state"] == "OBSERVED", report["history"]
    assert not report["history"].get("gaps")
    assert all(row["outcome_coverage"] == "OBSERVED" for row in report["roles"].values())


def test_a_pinned_clock_still_judges_the_tape_against_that_instant(home):
    """A caller that pins the instant pins the whole cycle: the producer must
    not silently move the reference clock it was given."""
    write_tape(home, ("seed", "seed wake discover observation=1"))
    pinned = datetime.now(timezone.utc) - timedelta(seconds=1)
    report = initiative.collect(home, now=pinned)
    assert report["history"]["state"] == "PARTIAL", report["history"]
    assert report["history"]["reason"] == "invalid/future event times"


def test_empty_and_digest_changed_outcome_evidence_have_distinct_reasons(home):
    empty_entry, empty = outcome(home, 1, age=200, name="empty.txt")
    empty.write_text("")
    changed_entry, changed = outcome(home, 2, age=100, name="changed.txt")
    changed.write_text("edited after publication")
    report = initiative.collect(home, now=NOW, entries=[empty_entry, changed_entry])
    dump = json.dumps(report)
    assert "outcome evidence empty" in dump
    assert "outcome evidence empty or changed" not in dump
    recorded = hashlib.sha256(b"bounded observed result").hexdigest()
    observed = hashlib.sha256(b"edited after publication").hexdigest()
    assert f"outcome evidence digest changed: recorded {recorded} observed {observed}" in dump
