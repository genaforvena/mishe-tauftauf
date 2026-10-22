from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from mishe_tauftauf import feed, initiative, initiative_watch as watch, records
from mishe_tauftauf.feed import Feed, FeedEntry, _encode_entry, parse_feed


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture
def home(tmp_path, monkeypatch):
    for directory in ("patches", "records", "artifacts", "checks"):
        (tmp_path / directory).mkdir()
    (tmp_path / "chat.log").write_bytes(b"")
    monkeypatch.setattr(feed, "utc_now", lambda: stamp(NOW))
    monkeypatch.delenv("MISHE_SEED_HOME", raising=False)
    return tmp_path


def append(home, source, body, *, age=0, now=NOW):
    path = home / "chat.log"
    entries = parse_feed(path.read_bytes(), home=home)
    sequence = len(entries) + 1
    with path.open("ab") as stream:
        stream.write(_encode_entry(sequence, stamp(now - timedelta(seconds=age)), source, body))
    return sequence


def publish(home, *, now=NOW):
    entries = parse_feed((home / "chat.log").read_bytes(), home=home)
    report = initiative.collect(home, now=now, entries=entries)
    (home / "checks/initiative.json").write_text(json.dumps(report))
    return report


def kinds(row, kind):
    return [item for item in row["findings"] if item["kind"] == kind]


def messages(home, *, recipient=None, text=None):
    return [entry for entry in Feed(home).entries() if entry.source == watch.SOURCE
            and (recipient is None or entry.body.startswith(f"[dm] to={recipient}\n"))
            and (text is None or text in entry.body)]


def chain(home, *, role="discover", count=6, labels=("changed", "verified")):
    for index in range(count):
        age = 1200 - index * 100
        wake = append(home, "seed", f"seed wake {role} observation=1", age=age + 1)
        append(home, "seed", f"seed yield {role} wake={wake} continue=1\nTurn settled ({labels[index % len(labels)]}); bounded prose", age=age)


def outcome(home, *, age=60):
    evidence = home / "artifacts/proof.txt"
    evidence.write_text("Observed a bounded actual result")
    binding = {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    reference = records.prepare(home, {"role": "discover", "kind": "accepted", "text": "Observed result", "evidence": binding}, kind="wall-outcome")
    append(home, "discover", "Wall outcome accepted by discover\nObserved result\n" + reference, age=age)
    return evidence


def test_aged_wake_boundary_and_exact_matching_settlement(home):
    wake = append(home, "seed", "seed wake discover observation=1", age=3599)
    publish(home)
    before = watch.check(home, now=NOW)
    assert before["state"] == "OBSERVED"
    at = NOW + timedelta(seconds=1)
    publish(home, now=at)
    aged = watch.check(home, now=at)
    finding = kinds(aged, "aged-wake")[0]
    assert finding["id"] == f"aged-wake:discover:chat.log#{wake}"
    assert finding["primary"] == ["health", "discover"]
    append(home, "seed", "seed yield health wake=1\nTurn settled (verified); wrong role", now=at)
    publish(home, now=at)
    assert kinds(watch.check(home, now=at), "aged-wake")
    append(home, "seed", f"seed yield discover wake={wake}\nTurn settled (changed); no assertion the bug is fixed", now=at)
    publish(home, now=at)
    cleared = watch.check(home, now=at)
    assert not kinds(cleared, "aged-wake")
    assert kinds(cleared, "unknown")  # The unrelated malformed lifecycle is not hidden.


def test_administrative_clear_does_not_forge_settlement(home):
    wake = append(home, "seed", "seed wake health observation=1", age=3600)
    append(home, "seed", f"seed clear health after={wake}", age=1)
    publish(home)
    assert kinds(watch.check(home, now=NOW), "aged-wake")[0]["ref"] == f"chat.log#{wake}"


def test_continuation_review_ignores_changed_verified_labels_and_wall_edits(home):
    chain(home)
    publish(home)
    initial = watch.check(home, now=NOW)
    review = kinds(initial, "continuation-review")[0]
    assert review["primary"] == ["genome", "discover"]
    (home / "wall.md").write_text("All fixed, verified, changed; not outcome evidence")
    later = NOW + timedelta(seconds=30)
    append(home, "seed", "seed wake health observation=1", now=later)
    publish(home, now=later)
    repeated = watch.check(home, now=later)
    assert repeated["incidents"][review["id"]]["first_seen"] == initial["incidents"][review["id"]]["first_seen"]
    assert len(messages(home, recipient="genome", text="continuation-review:discover")) == 1


def test_genome_continuation_is_owned_by_health(home):
    chain(home, role="genome")
    publish(home)
    finding = kinds(watch.check(home, now=NOW), "continuation-review")[0]
    assert finding["primary"] == ["health", "genome"]


def test_a_role_lifecycle_gap_reaches_the_role_that_owns_it(home):
    # Two wakes for one role with no settlement between them make the second
    # wake supersede the first: the role's own lifecycle coverage is
    # incomplete. The gap branch emits "lifecycle:<role>", so the role that
    # owns the broken chain must be routed, exactly as for "outcomes:<role>".
    append(home, "seed", "seed wake discover observation=1", age=120)
    append(home, "seed", "seed wake discover observation=1", age=60)
    publish(home)
    row = watch.check(home, now=NOW)
    finding = kinds(row, "unknown")[0]
    assert finding["id"] == "unknown:lifecycle:discover"
    assert len(messages(home, recipient="discover", text="unknown:lifecycle:discover")) == 1
    # The resolver still holds the plant-wide view of the condition.
    assert len(messages(home, recipient="health", text="unknown:lifecycle:discover")) == 1


def test_only_matching_continuations_inside_window_count(home):
    chain(home, count=5)
    append(home, "discover", "seed yield discover wake=1 continue=1", age=1)
    publish(home)
    assert not kinds(watch.check(home, now=NOW), "continuation-review")


@pytest.mark.parametrize("age,suppressed", [(60, True), (1300, False)])
def test_checked_outcome_since_first_continuation_suppresses_review(home, age, suppressed):
    chain(home)
    outcome(home, age=age)
    publish(home)
    row = watch.check(home, now=NOW)
    assert bool(kinds(row, "continuation-review")) is not suppressed


class _Py310Datetime(datetime):
    """``datetime.fromisoformat`` under Python 3.10's accepted formats.

    Python 3.11 added the trailing "Z" to the formats ``fromisoformat``
    accepts; 3.10 rejects it, and the tape stamps every instant with it.
    """

    @classmethod
    def fromisoformat(cls, value):
        if isinstance(value, str) and value.endswith("Z"):
            raise ValueError("invalid isoformat string on Python 3.10: " + value)
        return super().fromisoformat(value)


def test_the_monitor_reads_the_tapes_z_stamps_under_python310_iso_rules(home, monkeypatch):
    """The monitor's own instants must decode on every supported Python.

    A rejected "Z" collapses the whole evaluation into one unknown finding,
    so the review signals vanish exactly where the plant cannot read them.
    """
    monkeypatch.setattr(watch, "datetime", _Py310Datetime)
    chain(home)
    outcome(home, age=1300)
    publish(home)
    row = watch.check(home, now=_Py310Datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    assert kinds(row, "continuation-review"), row["findings"]
    assert not kinds(row, "unknown"), row["findings"]


def test_changed_outcome_evidence_cannot_suppress_review(home):
    chain(home)
    evidence = outcome(home)
    evidence.write_text("Changed bytes")
    publish(home)
    row = watch.check(home, now=NOW)
    assert row["state"] == "UNKNOWN"
    assert kinds(row, "continuation-review")
    assert any(item["ref"] == "outcomes" for item in kinds(row, "unknown"))


def test_outcome_coverage_notice_names_the_failing_evidence_row(home):
    chain(home)
    evidence = outcome(home)
    published = hashlib.sha256(evidence.read_bytes()).hexdigest()
    evidence.write_text("Changed bytes")
    publish(home)
    row = watch.check(home, now=NOW)
    assert any(item["ref"] == "outcomes:discover" for item in kinds(row, "unknown"))
    owner_notice = messages(home, recipient="discover", text="Evidence rows for discover")
    assert owner_notice, "the owner must learn which bound evidence row failed"
    assert str(evidence) in owner_notice[0].body
    assert published in owner_notice[0].body
    plant_notice = messages(home, recipient="health", text="Evidence rows for discover")
    assert plant_notice, "the plant-level owner must learn the same rows"
    assert str(evidence) in plant_notice[0].body


def test_producer_outcome_coverage_must_cover_new_continuations(home):
    publish(home)
    chain(home)
    row = watch.check(home, now=NOW)
    assert row["state"] == "UNKNOWN"
    assert kinds(row, "continuation-review")
    assert any(item["ref"] == "outcomes:discover" for item in kinds(row, "unknown"))


def test_overdue_accepted_open_prediction_and_unknown_prediction(home):
    append(home, "seed", "seed wake discover observation=1", age=300)
    append(home, "seed", "seed yield discover wake=1\nTurn settled (changed); result", age=290)
    prediction = append(home, "prediction/discover", "Expected bounded result\nCheck at: " + stamp(NOW - timedelta(seconds=60)), age=200)
    append(home, "mishe-tauftauf", f"prediction {prediction}: accepted", age=190)
    publish(home)
    row = watch.check(home, now=NOW)
    finding = kinds(row, "prediction")[0]
    assert finding["id"] == f"prediction:chat.log#{prediction}"
    assert finding["primary"] == ["discover", "genome"]
    append(home, "mishe-tauftauf", f"prediction {prediction}: met", age=10)
    append(home, "prediction/health", "No canonical check time", age=5)
    publish(home)
    later = watch.check(home, now=NOW)
    assert not kinds(later, "prediction")
    assert later["state"] == "UNKNOWN"


@pytest.mark.parametrize("damage", ["missing", "corrupt", "stale", "future"])
def test_unavailable_producer_is_unknown_and_routes_health(home, damage):
    publish(home)
    path = home / "checks/initiative.json"
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("{")
    else:
        report = json.loads(path.read_text())
        report["generated_at"] = (NOW + timedelta(seconds=1) if damage == "future" else NOW - timedelta(seconds=181)).isoformat()
        path.write_text(json.dumps(report))
    row = watch.check(home, now=NOW)
    assert row["state"] == "UNKNOWN"
    assert messages(home, recipient="health", text="unknown:producer")


def test_patch_only_partial_does_not_become_whole_plant_health(home):
    (home / "patches/old.json").write_text("{")
    report = publish(home)
    assert report["state"] == "PARTIAL"
    row = watch.check(home, now=NOW)
    assert row["state"] == "OBSERVED"  # No monitored review condition, not whole-plant health.
    assert row["producer_state"] == "PARTIAL"


def test_initial_dedup_one_escalation_and_owner_directed_reminders(home):
    append(home, "seed", "seed wake discover observation=1", age=3600)
    publish(home)
    initial = watch.check(home, now=NOW)
    key = kinds(initial, "aged-wake")[0]["id"]
    for seconds in (59, 599):
        at = NOW + timedelta(seconds=seconds)
        publish(home, now=at)
        watch.check(home, now=at)
    assert len(messages(home, recipient="health")) == 1
    assert len(messages(home, recipient="discover")) == 1
    assert not messages(home, recipient="genome")
    at = NOW + timedelta(seconds=600)
    publish(home, now=at)
    escalation = watch.check(home, now=at)
    assert escalation["incidents"][key]["stage"] == "escalation"
    assert escalation["incidents"][key]["first_seen"] == initial["incidents"][key]["first_seen"]
    assert len(messages(home, recipient="witness")) == 1
    assert len(messages(home, recipient="genome")) == 1
    at = NOW + timedelta(seconds=4200)
    publish(home, now=at)
    reminder = watch.check(home, now=at)
    assert reminder["incidents"][key]["stage"] == "reminder"
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 1
    # The reminder addresses the owner only: the escalation peers are not re-notified.
    assert len(messages(home, recipient="health")) == 2
    assert len(messages(home, recipient="witness")) == 1
    assert len(messages(home, recipient="genome")) == 1
    at = NOW + timedelta(seconds=6000)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 1
    at = NOW + timedelta(seconds=11400)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 2


def test_the_reminder_schedule_backs_off_from_thirty_minutes(home):
    # The first reminder waits one interval (30 minutes) after the escalation,
    # then one hour, then two; the initial notice and the escalation are not
    # reminders and must not delay the first one.
    append(home, "seed", "seed wake discover observation=1", age=3600)
    publish(home)
    watch.check(home, now=NOW)
    at = NOW + timedelta(seconds=600)
    publish(home, now=at)
    watch.check(home, now=at)
    at = NOW + timedelta(seconds=2399)
    publish(home, now=at)
    watch.check(home, now=at)
    assert not messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")
    at = NOW + timedelta(seconds=2400)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 1
    # The second reminder waits an hour, not the two hours the old count gave.
    at = NOW + timedelta(seconds=5999)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 1
    at = NOW + timedelta(seconds=6000)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 2
    # The third waits two hours.
    at = NOW + timedelta(seconds=13199)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 2
    at = NOW + timedelta(seconds=13200)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW reminder")) == 3


def test_changed_condition_is_routed_once_to_its_owner(home, monkeypatch):
    append(home, "seed", "seed wake discover observation=1", age=3600)
    publish(home)
    watch.check(home, now=NOW)
    original = watch.evaluate

    def changed(report, entries, *, now=None):
        findings = original(report, entries, now=now)
        return [dict(item, summary=item["summary"] + " The condition changed.")
                if item["kind"] == "aged-wake" else item for item in findings]

    monkeypatch.setattr(watch, "evaluate", changed)
    at = NOW + timedelta(seconds=60)
    publish(home, now=at)
    row = watch.check(home, now=at)
    key = kinds(row, "aged-wake")[0]["id"]
    assert row["incidents"][key]["stage"] == "changed"
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW changed")) == 1
    assert len(messages(home, recipient="health", text="INITIATIVE REVIEW changed")) == 1
    at = NOW + timedelta(seconds=120)
    publish(home, now=at)
    watch.check(home, now=at)
    assert len(messages(home, recipient="discover", text="INITIATIVE REVIEW changed")) == 1


def test_a_pending_notice_is_not_stacked_with_a_new_reminder(home, monkeypatch):
    append(home, "seed", "seed wake discover observation=1", age=3600)
    publish(home)
    real_send = watch._send

    def fail(home, recipient, text):
        if recipient == "discover":
            raise OSError("recipient unavailable")
        return real_send(home, recipient, text)

    monkeypatch.setattr(watch, "_send", fail)
    failed = watch.check(home, now=NOW)
    assert failed["pending"]
    assert failed["state"] == "UNKNOWN"
    at = NOW + timedelta(seconds=600)
    publish(home, now=at)
    watch.check(home, now=at)
    at = NOW + timedelta(seconds=4200)
    publish(home, now=at)
    stacked = watch.check(home, now=at)
    assert not messages(home, recipient="discover")
    assert stacked["pending"]
    monkeypatch.setattr(watch, "_send", real_send)
    recovered = watch.check(home, now=at)
    assert not recovered["pending"]
    assert len(messages(home, recipient="discover")) == 2


def test_failed_delivery_survives_newer_cleared_findings(home, monkeypatch):
    wake = append(home, "seed", "seed wake discover observation=1", age=3600)
    publish(home)
    real_send = watch._send

    def fail(home, recipient, text):
        if recipient == "health":
            raise OSError("recipient unavailable")
        return real_send(home, recipient, text)

    monkeypatch.setattr(watch, "_send", fail)
    failed = watch.check(home, now=NOW)
    assert failed["state"] == "UNKNOWN"
    assert failed["pending"][0]["recipients"] == ["health"]
    append(home, "seed", f"seed yield discover wake={wake}\nTurn settled (verified); closed exact wake")
    publish(home)
    monkeypatch.setattr(watch, "_send", real_send)
    recovered = watch.check(home, now=NOW)
    assert not recovered["pending"]
    assert not kinds(recovered, "aged-wake")
    assert len(messages(home, recipient="health", text=f"aged-wake:discover:chat.log#{wake}")) == 1
    assert len(messages(home, recipient="discover", text=f"aged-wake:discover:chat.log#{wake}")) == 1


def test_crash_after_append_retries_exactly_once(home, monkeypatch):
    append(home, "seed", "seed wake health observation=1", age=3600)
    publish(home)
    real_send = watch._send

    class Crash(BaseException):
        pass

    def crash(home, recipient, text):
        real_send(home, recipient, text)
        raise Crash()

    monkeypatch.setattr(watch, "_send", crash)
    with pytest.raises(Crash):
        watch.check(home, now=NOW)
    assert len(messages(home, recipient="health")) == 1
    monkeypatch.setattr(watch, "_send", real_send)
    recovered = watch.check(home, now=NOW + timedelta(seconds=1))
    assert recovered["state"] == "ATTENTION"
    assert not recovered["pending"]
    assert len(messages(home, recipient="health")) == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_established_state_loss_is_unknown_retains_and_dedups(home, damage):
    append(home, "seed", "seed wake health observation=1", age=3600)
    publish(home)
    watch.check(home, now=NOW)
    path = home / "checks/initiative-watch.json"
    raw = b"{broken state"
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(raw)
    repaired = watch.check(home, now=NOW + timedelta(seconds=1))
    assert repaired["state"] == "UNKNOWN"
    assert repaired["state_damage"]
    assert len(messages(home, recipient="health", text="aged-wake:health")) == 1
    if damage == "corrupt":
        retained = home / "checks/initiative-watch-corrupt" / (hashlib.sha256(raw).hexdigest() + ".bin")
        assert retained.read_bytes() == raw


def test_retention_failure_leaves_original_untouched(home, monkeypatch):
    publish(home)
    watch.check(home, now=NOW)
    path = home / "checks/initiative-watch.json"
    path.write_bytes(b"{corrupt")
    monkeypatch.setattr(watch, "_retain", lambda *args: (_ for _ in ()).throw(OSError("read-only retention")))
    with pytest.raises(OSError):
        watch.check(home, now=NOW)
    assert path.read_bytes() == b"{corrupt"


@pytest.mark.parametrize("seconds", [-1, 181])
def test_reader_rejects_future_and_stale_cache(home, seconds):
    publish(home)
    watch.check(home, now=NOW)
    with pytest.raises(ValueError):
        watch.read_snapshot(home, now=NOW + timedelta(seconds=seconds))
    assert "UNKNOWN" in watch.snapshot_lines(home, now=NOW + timedelta(seconds=seconds))[0]


@pytest.mark.parametrize("damage", ["missing", "checksum", "incident", "receipt", "pending", "healthy", "nan", "duplicate"])
def test_strict_cache_reader_rejects_malformed_state(home, damage):
    append(home, "seed", "seed wake discover observation=1", age=3600)
    publish(home)
    row = watch.check(home, now=NOW)
    path = home / "checks/initiative-watch.json"
    if damage == "missing":
        path.unlink()
    elif damage == "checksum":
        row["state"] = "OBSERVED"
        path.write_text(json.dumps(row))
    elif damage == "nan":
        path.write_text('{"version": NaN}')
    elif damage == "duplicate":
        path.write_text('{"version":1,"version":1}')
    else:
        if damage == "incident":
            row["incidents"] = {}
        elif damage == "receipt":
            row["deliveries"][next(iter(row["deliveries"]))]["health"] = True
        elif damage == "pending":
            row["pending"] = [{"event_id": "x", "recipients": ["health"]}]
        else:
            row["state"] = "OBSERVED"
        watch._save(path, row)
    with pytest.raises((OSError, ValueError)):
        watch.read_snapshot(home, now=NOW)
    assert "UNKNOWN" in watch.snapshot_lines(home, now=NOW)[0]


def test_projection_never_reads_tape_or_observes(home, monkeypatch):
    publish(home)
    watch.check(home, now=NOW)
    (home / "chat.log").unlink()
    monkeypatch.setattr(watch, "check", lambda *args, **kwargs: pytest.fail("projection observed"))
    monkeypatch.setattr(watch, "parse_feed", lambda *args, **kwargs: pytest.fail("projection scanned tape"))
    assert watch.snapshot_lines(home, now=NOW)[0].startswith("INITIATIVE: OBSERVED")
    assert not (home / "chat.log").exists()


@pytest.mark.parametrize("damage, named", [
    ("missing", "producer report missing"),
    ("corrupt", "producer report unreadable"),
    ("stale", "producer report stale"),
    ("future", "producer report future-dated"),
])
def test_the_producer_fault_names_the_failing_evidence(home, damage, named):
    publish(home)
    path = home / "checks/initiative.json"
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("{")
    else:
        report = json.loads(path.read_text())
        report["generated_at"] = (NOW + timedelta(seconds=1) if damage == "future"
                                  else NOW - timedelta(seconds=181)).isoformat()
        path.write_text(json.dumps(report))
    row = watch.check(home, now=NOW, interval=120)
    summary = kinds(row, "unknown")[0]["summary"]
    assert named in summary
    assert "liveness is not observed" in summary
    assert "ValueError" not in summary


def test_a_stale_report_after_the_monitor_missed_cycles_is_not_a_running_producer(home):
    """The plant was down between cycles: the report is as old as the monitor's own gap."""
    publish(home, now=NOW - timedelta(seconds=3000))
    watch.check(home, now=NOW - timedelta(seconds=3000), interval=120)
    row = watch.check(home, now=NOW, interval=120)
    summary = kinds(row, "unknown")[0]["summary"]
    assert "producer report stale" in summary
    assert "missed its own cycles" in summary
    assert "liveness is not yet observed" in summary


def test_a_stale_report_within_the_monitor_tolerance_band_does_not_claim_a_stopped_producer(home):
    """The reading names the failing evidence, not a producer verdict it cannot support.

    A monitor gap above its interval means it missed a cycle, so "was running"
    would assert something the watch cannot know across the whole tolerance
    band -- and a plant outage shorter than the band is exactly the class of
    event this reading exists for.
    """
    publish(home)
    watch.check(home, now=NOW, interval=120)
    row = watch.check(home, now=NOW + timedelta(seconds=200), interval=120)
    summary = kinds(row, "unknown")[0]["summary"]
    assert "producer report stale" in summary
    assert "cycle continuity was within tolerance" in summary
    assert "this monitor was running" not in summary
    assert "stopped" not in summary


def test_a_monitor_gap_inside_the_tolerance_band_is_read_as_within_tolerance(home):
    """The band between one interval and the tolerance still misses a cycle.

    At the live interval of 120 s a monitor gap of 200 s means one cycle was
    missed, yet it is inside the tolerance band, so the reading must state the
    tolerance and not claim the monitor was running.
    """
    publish(home, now=NOW - timedelta(seconds=200))
    watch.check(home, now=NOW - timedelta(seconds=200), interval=120)
    row = watch.check(home, now=NOW, interval=120)
    summary = kinds(row, "unknown")[0]["summary"]
    assert "producer report stale" in summary
    assert "cycle continuity was within tolerance" in summary
    assert "missed its own cycles" not in summary


def test_an_unchanged_producer_fault_is_not_routed_again(home):
    """A live age in the reading would re-route an unchanged condition every cycle."""
    publish(home)
    watch.check(home, now=NOW, interval=120)
    first = watch.check(home, now=NOW + timedelta(seconds=200), interval=120)
    assert len(messages(home, recipient="health", text="unknown:producer")) == 1
    second = watch.check(home, now=NOW + timedelta(seconds=320), interval=120)
    assert kinds(second, "unknown")[0] == kinds(first, "unknown")[0]
    assert len(messages(home, recipient="health", text="unknown:producer")) == 1


def test_a_fault_without_a_known_cycle_interval_does_not_claim_the_monitor_was_running(home):
    publish(home)
    watch.check(home, now=NOW)
    row = watch.check(home, now=NOW + timedelta(seconds=200))
    summary = kinds(row, "unknown")[0]["summary"]
    assert "cycle interval is unknown" in summary
    assert "this monitor was running" not in summary
    assert "cycle continuity was within tolerance" not in summary


def test_an_entry_appended_while_the_monitor_reads_the_tape_is_not_a_future_event(home, monkeypatch):
    """The tape grows while the monitor reads it, and a reader that judges the
    tape against the clock it took before the read calls that growth a future
    event. The entry is real, so the monitor must read it.

    The live monitor does not pin its clock, so this test does not either: the
    clock is the wall clock, and the entry carries a stamp the wall clock
    reaches during the read.
    """
    wall = datetime.now(timezone.utc)
    publish(home, now=wall)
    original = watch._read

    def appending_read(path, maximum):
        if path.name == "chat.log":
            # Another writer appends after the monitor took its clock and
            # before the monitor reads the tape. The stamp is the wall clock,
            # which is what the live monitor reads.
            entries = feed.parse_feed(path.read_bytes(), home=home)
            moment = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            with path.open("ab") as stream:
                stream.write(feed._encode_entry(len(entries) + 1, moment, "seed",
                                                "seed observation discover"))
        return original(path, maximum)

    monkeypatch.setattr(watch, "_read", appending_read)
    row = watch.check(home)
    assert "unknown:tape-time" not in [item["id"] for item in kinds(row, "unknown")]
    assert row["state"] != "UNKNOWN", kinds(row, "unknown")


def test_a_pinned_clock_still_judges_the_tape_against_that_instant(home):
    """A caller that pins the instant pins the whole cycle: the monitor must
    not silently move the reference clock it was given."""
    publish(home)
    append(home, "seed", "seed observation discover", now=NOW + timedelta(seconds=1))
    publish(home)
    row = watch.check(home, now=NOW)
    assert "unknown:tape-time" in [item["id"] for item in kinds(row, "unknown")]
