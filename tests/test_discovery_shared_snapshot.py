"""The scan's shared tape snapshot: one physical read, unchanged readings.

The readings scan read the tape physically three times (plan section 10.2a):
the cold verified read the recurrence reading needs, the census read that
collects the record digests the entries name, and the disposition read that
streams a six-hour window. All three can be served from one snapshot.

These tests fail if a reader stops accepting a supplied snapshot (the
sharing is undone), if a reader reads the tape again while a snapshot is
supplied (the sharing did not happen), or if the fallback changes a state
word when the shared read fails.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mishe_tauftauf import discovery, feed as feedmod, store_census
from tests.tape_binary import pin_tape_binary


def _tape_entry(sequence: int, timestamp: str, source: str, body: str) -> bytes:
    header = f"v1 {sequence:020d} {timestamp} {source} ::\n"
    lines = body.splitlines(keepends=True)
    if not lines:
        lines = [body]
    framed = []
    for line in lines:
        if line.endswith("\n"):
            framed.append("    | " + line)
        else:
            framed.append("    | " + line + "\n")
    terminator = "    .\n" if body.endswith("\n") else "    .-\n"
    return (header + "".join(framed) + terminator).encode("utf-8")


def _site(tmp_path: Path, entries) -> Path:
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    return home


def _counting_reads(monkeypatch):
    """Count the framer spawns: one spawn is one physical tape read."""
    calls = []
    original = feedmod._spawn_frames

    def counting(handle, *, start_sequence, offset, size, extra=()):
        calls.append({"size": size, "offset": offset})
        return original(handle, start_sequence=start_sequence, offset=offset,
                        size=size, extra=extra)

    monkeypatch.setattr(feedmod, "_spawn_frames", counting)
    return calls


def test_snapshot_serves_all_three_readings_without_a_second_read(monkeypatch, tmp_path):
    """One snapshot, three readings, no further physical tape read."""
    home = _site(tmp_path, [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=genome\n# hi genome\n"),
        (2, "2026-10-06T13:00:00Z", "genome", "[dm] to=health\n# hi health\n"),
    ])
    calls = _counting_reads(monkeypatch)
    snapshot = discovery._scan_snapshot(home)
    assert snapshot is not None
    assert len(calls) == 1, "the shared snapshot is one physical read"

    def explode(*args, **kwargs):
        raise AssertionError("a reader must not read the tape when it has a snapshot")

    monkeypatch.setattr(discovery, "Feed", explode)
    monkeypatch.setattr(discovery, "iter_feed_file", explode)
    monkeypatch.setattr(store_census, "tape_entries", explode)

    recurrence = discovery._mind_artifact_recurrence(home, entries=snapshot)
    census = discovery._ledger_evidence_binding(home, entries=snapshot)
    disposition = discovery._coord_dm_disposition_age(home, entries=snapshot)

    # A disposable site is not a git checkout, so the recurrence reading
    # reports that rather than a verdict; what matters here is that no reader
    # reached for the tape.
    assert recurrence["sample"] == "not a git checkout"
    assert census["id"] == "sense.ledger.evidence-binding"
    assert disposition["id"] == "sense.coord.dm-disposition-age"
    assert len(calls) == 1, "no reader may make a second physical read"


def test_shared_snapshot_matches_each_reading_s_own_read(monkeypatch, tmp_path):
    """The same reading, served two ways, is the same reading."""
    home = _site(tmp_path, [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=genome\n# hi genome\n"),
        (2, "2026-10-06T13:00:00Z", "genome", "[dm] to=health\n# hi health\n"),
    ])
    now = datetime.now(timezone.utc)
    snapshot = discovery._scan_snapshot(home)

    assert (discovery._mind_artifact_recurrence(home, now=now, entries=snapshot)
            == discovery._mind_artifact_recurrence(home, now=now))
    assert (discovery._coord_dm_disposition_age(home, now=now, entries=snapshot)
            == discovery._coord_dm_disposition_age(home, now=now))
    # The census stamps its own read instant, so the stamped fields differ by
    # design; every other field must be identical.
    shared = discovery._ledger_evidence_binding(home, entries=snapshot)
    own = discovery._ledger_evidence_binding(home)
    stamped = {"read_at", "sample"}
    differing = {k for k in set(shared) | set(own)
                 if shared.get(k) != own.get(k)} - stamped
    assert not differing, f"the census changed when it was served from a snapshot: {differing}"


def test_census_named_record_set_is_the_same_from_a_snapshot(tmp_path):
    """The digests a body names are a function of the body."""
    body = "[record] records/" + "a" * 64 + ".json sha256=" + "a" * 64 + "\n"
    home = _site(tmp_path, [(1, "2026-10-06T12:30:00Z", "health", body)])
    # The record is not in the store, so the verified read would raise; the
    # question here is about the bodies, so the entries are framed unverified.
    snapshot = feedmod.parse_feed((home / "chat.log").read_bytes(), home=home)
    from_snapshot = set()
    store_census.collect_named(snapshot, from_snapshot)
    from_tape = set()
    for entry in feedmod.iter_feed_file(home / "chat.log", home=home):
        store_census._add_named(entry.body, from_tape)
    assert from_snapshot == from_tape == {"a" * 64}


def test_a_failed_shared_read_leaves_each_reading_on_its_own_read(monkeypatch, tmp_path):
    """The fallback is the readers' own read, and no fault is swallowed."""
    home = _site(tmp_path, [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=genome\n# hi genome\n"),
    ])
    # The entries are framed before the framer is broken, so the snapshot
    # exists even though the tape can no longer be read.
    entries = feedmod.parse_feed((home / "chat.log").read_bytes(), home=home)

    # A missing framer is a fault every tape read hits, so the shared read
    # fails and the readers fall back to their own reads.
    monkeypatch.setenv("MISHE_TAPE_BIN", str(tmp_path / "missing-framer"))

    assert discovery._scan_snapshot(home) is None

    # Without a snapshot, the disposition reading still reads the tape and
    # reports the fault the way it does today.
    disposition = discovery._coord_dm_disposition_age(home)
    assert disposition["state"] == "unknown"
    assert "MISHE_TAPE_BIN names no executable file" in disposition["sample"]

    # With a snapshot, the reading never touches the tape, so the missing
    # framer cannot reach it: the shared read is the only tape access.
    served = discovery._coord_dm_disposition_age(home, entries=entries)
    assert served["state"] == "verified"
    assert served["dms"] == 0


def test_a_missing_verifier_degrades_the_readings_not_the_scan(
        monkeypatch, tmp_path, pin_tape_binary):
    """A verifier fault is UNKNOWN, not a failed scan.

    ``RecordsUnknown`` is the verifier's sibling of ``TapeUnknown``: a reading
    that degrades a framer fault to UNKNOWN must degrade a verifier fault the
    same way. A missing or sha-mismatched ``mishe-records`` (the
    activation-order case) must not turn one broken binary into a failed scan.
    """
    home = _site(tmp_path, [
        (1, "2026-10-06T12:30:00Z", "health",
         "[record] records/" + "a" * 64 + ".json sha256=" + "a" * 64 + "\n"),
    ])
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                    "commit", "--allow-empty", "-qm", "baseline"], cwd=tmp_path, check=True)
    # The site has no usable verifier: the override names no executable file.
    monkeypatch.setenv("MISHE_RECORDS_BIN", str(tmp_path / "missing-records"))

    assert discovery._scan_snapshot(home) is None

    # Each reading falls back to its own read and reports the fault as UNKNOWN
    # rather than letting RecordsUnknown escape the scan.
    mix = discovery._seed_settle_result_mix(home)
    assert mix["state"] == "unknown"
    assert "MISHE_RECORDS_BIN names no executable file" in mix["sample"]
    recurrence = discovery._mind_artifact_recurrence(home)
    assert recurrence["state"] == "unknown"
    assert recurrence["sample"] == "feed unreadable"
