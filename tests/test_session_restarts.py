"""Focused fixtures for the isolated restart-cadence reader.

Covers the operator restart-sense request at reader level only: steady
cadence, count drift, tight-gap drift, recovery, and unknown on malformed
inputs. No discovery wiring or activation.
"""
from mishe_tauftauf import session_restarts

SESSION = "mishe-self-development-current"

BIRTH = f"Started {SESSION}-session.scope - Mishe resident session {SESSION}."
OTHER_BIRTH = "Started android-body-session.scope - Mishe resident session android-body."
FOREIGN = "mishe-self-development-current-discover.service: Failed with result 'exit-code'."


def _entries(*pairs):
    return list(pairs)


def test_positive_widely_spaced_births_are_steady():
    # Two births 50 minutes apart: deliberate cadence, not churn.
    entries = _entries((100_0000000, BIRTH), (400_0000000, BIRTH))
    row = session_restarts.assess_restarts(entries, SESSION)
    assert row["verdict"] == "steady"
    assert row["births"] == 2
    assert row["under_30m"] == 0


def test_controlled_failure_three_births_reports_churn():
    entries = _entries((100_0000000, BIRTH), (200_0000000, BIRTH), (300_0000000, BIRTH))
    row = session_restarts.assess_restarts(entries, SESSION)
    assert row["verdict"] == "churn"
    assert row["reason"] == "count>=3-in-window"


def test_controlled_failure_tight_gap_reports_churn():
    # Only two births, but 2.7 minutes apart: rapid death-plus-rebirth.
    entries = _entries((100_0000000, BIRTH), (100_0000000 + 162_000000, BIRTH))
    row = session_restarts.assess_restarts(entries, SESSION)
    assert row["verdict"] == "churn"
    assert row["reason"] == "tight-gap-rebirth"


def test_recovery_single_birth_is_steady():
    churned = _entries((100_0000000, BIRTH), (110_0000000, BIRTH), (120_0000000, BIRTH))
    assert session_restarts.assess_restarts(churned, SESSION)["verdict"] == "churn"
    healed = _entries((500_0000000, BIRTH),)
    row = session_restarts.assess_restarts(healed, SESSION)
    assert row["verdict"] == "steady"
    assert row["reason"] == "cadence-nominal"


def test_foreign_lines_never_count():
    entries = _entries((100_0000000, OTHER_BIRTH), (200_0000000, FOREIGN),
                       (300_0000000, "Started other-session.scope - x."),
                       (400_0000000, None), (500_0000000, 42))
    row = session_restarts.assess_restarts(entries, SESSION)
    assert row["births"] == 0
    assert row["verdict"] == "unknown"
    assert row["reason"] == "no-births-in-window"


def test_unknown_on_missing_or_malformed_inputs():
    assert session_restarts.assess_restarts([], "")["verdict"] == "unknown"
    assert session_restarts.assess_restarts([], None)["verdict"] == "unknown"
    assert session_restarts.assess_restarts("not-a-list", SESSION)["verdict"] == "unknown"
    assert session_restarts.assess_restarts([(True, BIRTH)], SESSION)["reason"] == "malformed-entries"
    assert session_restarts.assess_restarts([("x", BIRTH)], SESSION)["reason"] == "malformed-entries"
    assert session_restarts.assess_restarts([(100_0000000, BIRTH), "junk"], SESSION)["reason"] == "malformed-entries"


def test_sample_text_names_cadence_shape():
    row = session_restarts.assess_restarts(
        _entries((100_0000000, BIRTH), (200_0000000, BIRTH), (300_0000000, BIRTH)), SESSION)
    text = session_restarts.sample_text(row)
    assert "births=3" in text
    assert "count>=3-in-window" in text
    steady = session_restarts.sample_text(
        session_restarts.assess_restarts(_entries((500_0000000, BIRTH),), SESSION))
    assert "births=1" in steady
    assert "count" not in steady

def _journal_line(cursor, stamp, boot, message):
    import json
    return json.dumps({"__CURSOR": cursor, "__REALTIME_TIMESTAMP": str(stamp),
                       "_BOOT_ID": boot, "SYSLOG_IDENTIFIER": "systemd",
                       "MESSAGE": message})


def test_decode_journal_entries_passes_decoded_pairs():
    raw = ("\n".join([
        _journal_line("c1", 200_0000000, "boot1", BIRTH),
        _journal_line("c2", 300_0000000, "boot1", FOREIGN),
        _journal_line("c3", 999_0000000, "boot1", BIRTH),  # outside window: dropped
    ]) + "\n").encode()
    entries = session_restarts.decode_journal_entries(raw, "boot1", "systemd", 100_0000000, 400_0000000)
    assert entries == [(200_0000000, BIRTH), (300_0000000, FOREIGN)]


def test_decode_journal_entries_malformed_metadata_is_none():
    good = _journal_line("c1", 200_0000000, "boot1", BIRTH)
    # Duplicate cursor: partial lists would undercount births, so fail closed.
    dup = "\n".join([good, good]) + "\n"
    assert session_restarts.decode_journal_entries(
        dup.encode(), "boot1", "systemd", 0, 999_0000000) is None
    # Wrong boot: foreign-boot lines never merge into this boot's cadence.
    foreign = _journal_line("c9", 200_0000000, "other-boot", BIRTH) + "\n"
    assert session_restarts.decode_journal_entries(
        foreign.encode(), "boot1", "systemd", 0, 999_0000000) is None
    # Non-JSON line and bad timestamp likewise fail the whole acquisition.
    assert session_restarts.decode_journal_entries(
        b"not json\n", "boot1", "systemd", 0, 999_0000000) is None
    bad_ts = _journal_line("c2", "yesterday", "boot1", BIRTH) + "\n"
    assert session_restarts.decode_journal_entries(
        bad_ts.encode(), "boot1", "systemd", 0, 999_0000000) is None
    assert session_restarts.decode_journal_entries(
        "not-bytes".encode().decode(), "boot1", "systemd", 0, 999_0000000) is None
