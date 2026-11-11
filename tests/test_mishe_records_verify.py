"""Contract checks for the ``mishe-records`` binary and the feed reader that calls it.

Record verification and the verified store live in ``rust/mishe-records`` (the
artefact the feed reader composes); the contract is ``rust/mishe-records/CONTRACT.md``.
Since the cutover there is no Python authority left to differential against, so these
cases drive the binary directly *and* through ``Feed._verify_records``/``Feed.entries``,
which is the caller. Runs when a binary resolves (``MISHE_RECORDS_BIN``, then the site
install), else skips with the reason.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from mishe_tauftauf import feed, records
from tests.records_binary import RECORDS_BIN, RECORDS_SKIP_REASON, pin_records_binary

pytestmark = [pytest.mark.skipif(RECORDS_BIN is None, reason=RECORDS_SKIP_REASON),
              pytest.mark.usefixtures("pin_records_binary")]

STAMP = "2026-11-10T00:00:00Z"
PAYLOADS = ({"text": "one", "n": 1}, {"text": "two", "n": 2})
#: The store file name the binary and the contract share.
STORE_NAME = "records.verified.sqlite3"


def frame(entries: list[tuple[int, str]]) -> bytes:
    """The framed entry stream ``mishe-tape frames`` writes."""
    out = bytearray()
    for sequence, body in entries:
        raw = body.encode("utf-8")
        out += f"E {sequence} {STAMP} test {len(raw)}\n".encode() + raw + b"\n"
    return bytes(out)


def run_verify(home: Path, entries: list[tuple[int, str]], *extra: str):
    proc = subprocess.run([RECORDS_BIN, "verify", "--home", str(home), *extra],
                          input=frame(entries), capture_output=True)
    return proc.stdout.decode("utf-8"), proc.returncode, proc.stderr.decode("utf-8").strip()


def decisions(stdout: str) -> list[tuple[int, str]]:
    out = []
    for line in stdout.splitlines():
        head, _, rest = line.partition(" ")
        if head in ("V", "N", "S", "F"):
            # `V`/`N` carry the cited kind after the sequence (contract 0.4); the
            # sequence is the first token either way.
            out.append((int(rest.partition(" ")[0]), head))
    return out


def decision_rows(stdout: str) -> list[tuple[str, int, str | None]]:
    """The decision rows with their kind field: `(head, sequence, kind or None)`."""
    rows = []
    for line in stdout.splitlines():
        head, _, rest = line.partition(" ")
        if head in ("V", "N", "S"):
            sequence, _, kind = rest.partition(" ")
            rows.append((head, int(sequence), kind or None))
    return rows


def emitted_payloads(stdout: str) -> dict[int, dict]:
    lines = stdout.split("\n")
    out: dict[int, dict] = {}
    index = 0
    while index < len(lines):
        if lines[index].startswith("P "):
            _, sequence, size = lines[index].split(" ")
            body = lines[index + 1].encode("utf-8")
            assert len(body) == int(size), "payload body length disagrees with its header"
            out[int(sequence)] = json.loads(body)
            index += 2
        else:
            index += 1
    return out


def fixtures(tmp_path: Path):
    """A site for the binary and an identical one for the wrapper.

    The record content is a pure function of the payload, so both sites hold the same
    digests and the same entries.
    """
    entries: list[tuple[int, str]] = []
    for site in (tmp_path / "site", tmp_path / "py-site"):
        site.mkdir()
        references = [records.prepare(site, payload) for payload in PAYLOADS]
        if not entries:
            entries = [(1, references[0]), (2, references[1]), (3, references[0]),
                       (4, "plain\nmessage"), (5, 'note\n{"a": 1}')]
    return tmp_path / "site", tmp_path / "py-site", entries


def objects(site: Path, entries: list[tuple[int, str]]) -> list[feed.FeedEntry]:
    return [feed.FeedEntry(sequence, STAMP, "test", body, site) for sequence, body in entries]


def wrapper_sink(site: Path, entries: list[tuple[int, str]]) -> dict[int, dict]:
    """What the feed reader yields for the same entries: the caller, run in full."""
    sink: dict[int, dict] = {}
    feed.Feed._verify_records(objects(site, entries), sink)
    return sink


def wrapper_fault(site: Path, bodies: list[str]) -> str:
    """The text ``Feed._verify_records`` raises for a pass that faults, and nothing else."""
    with pytest.raises(ValueError) as raised:
        feed.Feed._verify_records([feed.FeedEntry(n, STAMP, "test", body, site)
                                    for n, body in enumerate(bodies, 1)])
    return str(raised.value)


def digest_of(reference: str) -> str:
    return reference.split("/", 1)[1].split(".", 1)[0]


def test_cold_pass_decides_and_leaves_a_store_the_wrapper_serves(tmp_path):
    site, py_site, entries = fixtures(tmp_path)
    expected = wrapper_sink(py_site, entries)
    assert set(expected) == {1, 2, 3}

    stdout, status, stderr = run_verify(site, entries, "--payloads")
    assert status == 0, stderr
    assert decisions(stdout) == [(1, "N"), (2, "N"), (3, "V"), (4, "S"), (5, "S")]
    assert emitted_payloads(stdout) == expected
    assert store_rows(site) == sorted(digest_of(dict(entries)[n]) for n in (1, 2))

    # A second wrapper pass reads the store the binary wrote and serves it.
    assert wrapper_sink(site, entries) == expected


def test_warm_store_is_served_again(tmp_path):
    site, py_site, entries = fixtures(tmp_path)
    expected = wrapper_sink(py_site, entries)

    assert run_verify(site, entries, "--payloads")[1] == 0
    stdout, status, stderr = run_verify(site, entries, "--payloads")
    assert status == 0, stderr
    assert decisions(stdout)[:3] == [(1, "V"), (2, "V"), (3, "V")]
    assert emitted_payloads(stdout) == expected


def test_missing_record_fault_text(tmp_path):
    site, _, _ = fixtures(tmp_path)
    digest = hashlib.sha256(b"never written").hexdigest()
    body = records.reference(digest)

    stdout, status, stderr = run_verify(site, [(7, body)])
    assert status == 2
    assert stdout == ""
    assert wrapper_fault(site, [body]) == stderr


def test_rewritten_record_is_verified_again_not_served(tmp_path):
    site, _, entries = fixtures(tmp_path)
    assert run_verify(site, entries, "--payloads")[1] == 0  # the store now holds all three

    body = entries[0][1]
    digest = digest_of(body)
    rewritten = site / "records" / f"{digest}.json"
    rewritten.chmod(0o600)  # records.prepare installs them read-only
    rewritten.write_bytes(b'{"version":1,"kind":"x","payload":{"t":1}}')

    stdout, status, stderr = run_verify(site, entries)
    assert status == 2
    assert stdout == "", "a rewritten record must not be served from the store"
    assert stderr == "immutable record checksum mismatch"
    assert wrapper_fault(site, [body]) == "immutable record checksum mismatch"


# --- the structural faults of CONTRACT.md ---


def plain_site(tmp_path: Path):
    """A site with one record and one site-level reference, no twin needed."""
    site = tmp_path / "site"
    site.mkdir()
    return site, records.prepare(site, PAYLOADS[0])


def store_rows(site: Path) -> list[str]:
    """The digests the shared store holds, or ``[]`` while it is not readable yet."""
    path = site / STORE_NAME
    if not path.exists():
        return []
    try:
        connection = sqlite3.connect(path)
        try:
            return [row[0] for row in connection.execute("SELECT digest FROM verified ORDER BY digest")]
        finally:
            connection.close()
    except sqlite3.Error:
        return []


def bytes_read(pid: int) -> int | None:
    """``rchar`` from ``/proc/<pid>/io``: how many bytes the process has read."""
    try:
        with open(f"/proc/{pid}/io") as handle:
            for line in handle:
                if line.startswith("rchar:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


@pytest.mark.parametrize("kind", ["mixed-legacy", "invalid-reference", "two-references",
                                  "unequal-digests", "indented-reference"])
def test_structural_fault_text_and_earlier_results(tmp_path, kind):
    site, reference = plain_site(tmp_path)
    digest = digest_of(reference)
    other = digest_of(records.prepare(site, PAYLOADS[1]))
    bodies = {
        # A `[record]` line and a legacy JSON control line in the same entry.
        "mixed-legacy": f'{reference}\n{{"text": "legacy"}}',
        # A reference line that exists but does not fullmatch the pattern.
        "invalid-reference": f"[record] records/{'0' * 63}.json sha256={'0' * 63}",
        # Two agreeing references: the pattern matches one line, not the entry.
        "two-references": f"{reference}\n{reference}",
        # One well-formed reference whose two digests disagree.
        "unequal-digests": f"[record] records/{digest}.json sha256={other}",
        # A reference the pass selects (lstrip) that the pattern must reject as written.
        "indented-reference": f" {reference}",
    }
    expected = {
        "mixed-legacy": "mixed legacy JSON and immutable record reference",
        "invalid-reference": "missing or invalid immutable record reference",
        "two-references": "missing or invalid immutable record reference",
        "unequal-digests": "record digest mismatch or missing site context",
        "indented-reference": "missing or invalid immutable record reference",
    }[kind]

    stdout, status, stderr = run_verify(site, [(1, reference), (2, bodies[kind])])
    assert status == 2
    assert stderr == expected
    assert decisions(stdout) == [(1, "N")], "the results before the fault are on stdout"
    assert wrapper_fault(site, [reference, bodies[kind]]) == expected


def test_a_corrupt_store_still_verifies_the_pass(tmp_path):
    site, _, entries = fixtures(tmp_path)
    expected = wrapper_sink(site, entries)
    (site / STORE_NAME).write_bytes(b"not a database" * 100)  # a corrupt store is a miss

    # The cache is disabled for the pass; the pass verifies every record again.
    assert wrapper_sink(site, entries) == expected


def test_two_homes_in_one_pass_each_verify(tmp_path):
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    payloads = {"mine": {"site": "mine"}, "theirs": {"site": "theirs"}}
    references = {"mine": records.prepare(mine, payloads["mine"]),
                  "theirs": records.prepare(theirs, payloads["theirs"])}
    entries = [feed.FeedEntry(1, STAMP, "test", references["mine"], mine),
               feed.FeedEntry(2, STAMP, "test", references["theirs"], theirs)]
    sink: dict[int, dict] = {}

    feed.Feed._verify_records(entries, sink)

    assert sink == {1: payloads["mine"], 2: payloads["theirs"]}
    for key, site in (("mine", mine), ("theirs", theirs)):
        assert store_rows(site) == [digest_of(references[key])], "each home has its own child and store"


def test_missing_records_directory_fails_the_pass_closed(tmp_path):
    site, reference = plain_site(tmp_path)
    (site / "records").rename(site / "records.moved")

    with pytest.raises(ValueError, match="record unavailable"):
        feed.Feed._verify_records(objects(site, [(1, reference)]))


def test_a_stale_store_row_is_not_served(tmp_path):
    site, _, entries = fixtures(tmp_path)
    assert run_verify(site, entries)[1] == 0  # cold: everything verified and stored
    connection = sqlite3.connect(site / STORE_NAME)
    try:
        with connection:
            connection.execute("UPDATE verified SET verified_at = 0")  # older than the 6 h bound
    finally:
        connection.close()

    stdout, status, stderr = run_verify(site, entries)
    assert status == 0, stderr
    assert decisions(stdout) == [(1, "N"), (2, "N"), (3, "V"), (4, "S"), (5, "S")]


def test_a_typed_but_corrupt_blob_is_served_only_without_payloads(tmp_path):
    site, _, entries = fixtures(tmp_path)
    assert run_verify(site, entries)[1] == 0
    connection = sqlite3.connect(site / STORE_NAME)
    try:
        with connection:
            connection.execute("UPDATE verified SET payload = ?", (b"not zlib",))
    finally:
        connection.close()

    # A verify-only caller trusts the identity and never decompresses the blob.
    stdout, status, stderr = run_verify(site, entries)
    assert status == 0, stderr
    assert decisions(stdout)[:3] == [(1, "V"), (2, "V"), (3, "V")]

    # A payload reader cannot decode it, so it is a miss: verify again and emit the payload.
    stdout, status, stderr = run_verify(site, entries, "--payloads")
    assert status == 0, stderr
    assert decisions(stdout)[:3] == [(1, "N"), (2, "N"), (3, "V")], "entry 3 reuses entry 1's digest"
    assert emitted_payloads(stdout) == {1: PAYLOADS[0], 2: PAYLOADS[1], 3: PAYLOADS[0]}


def test_the_decision_line_carries_the_cited_kind_and_a_kindless_row_is_a_miss(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    reference = records.prepare(site, {"text": "one"}, kind="wall-outcome")
    entries = [(1, reference)]

    stdout, status, stderr = run_verify(site, entries)
    assert status == 0, stderr
    assert decision_rows(stdout) == [("N", 1, "wall-outcome")], "the verified record's kind is reported"

    stdout, status, stderr = run_verify(site, entries)
    assert status == 0, stderr
    assert decision_rows(stdout) == [("V", 1, "wall-outcome")], "a store hit answers the kind without rereading"

    # A store written before the kind column existed holds no kind, so its row is a
    # miss: the record is verified again and the row rewritten. No kind is guessed.
    connection = sqlite3.connect(site / STORE_NAME)
    try:
        stored = connection.execute(
            "SELECT digest, ino, size, ctime_ns, mtime_ns, verified_at, payload FROM verified").fetchone()
        assert stored is not None, "the cold pass stored the row"
        with connection:
            connection.execute("DROP TABLE verified")
            connection.execute("CREATE TABLE verified (digest TEXT PRIMARY KEY, ino INTEGER, "
                               "size INTEGER, ctime_ns INTEGER, mtime_ns INTEGER, verified_at REAL, "
                               "payload BLOB NOT NULL)")
            connection.execute("INSERT INTO verified VALUES (?, ?, ?, ?, ?, ?, ?)", stored)
    finally:
        connection.close()

    stdout, status, stderr = run_verify(site, entries)
    assert status == 0, stderr
    assert decision_rows(stdout) == [("N", 1, "wall-outcome")], "a row without a kind is a miss"

    stdout, status, stderr = run_verify(site, entries)
    assert status == 0, stderr
    assert decision_rows(stdout) == [("V", 1, "wall-outcome")], "the rewritten row heals"


def test_records_directory_swap_before_a_flush_fails_closed(tmp_path):
    site, _, entries = fixtures(tmp_path)
    reference = entries[0][1]

    # The binary holds the records descriptor the caller opened, so a record read through
    # it still verifies; the swap is noticed at the flush, after the last decision.
    directory = os.open(site / "records", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        probe = subprocess.Popen(
            [RECORDS_BIN, "verify", "--home", str(site), "--records-fd", str(directory)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(directory,))
        probe.stdin.write(frame([(1, reference), (2, "plain"), (3, "plain")]))
        probe.stdin.flush()
        (site / "records").rename(site / "records.moved")
        (site / "records").mkdir()
        probe.stdin.write(frame([(n, "plain") for n in range(4, 501)]))
        probe.stdin.close()
        probe.stdin = None  # communicate() flushes stdin; it must not flush a closed file
        stdout, stderr = probe.communicate(timeout=120)
    finally:
        os.close(directory)

    assert probe.returncode == 2
    assert stderr.decode("utf-8").strip() == "records directory replaced during verification pass"
    lines = stdout.decode("utf-8").splitlines()
    assert lines[0] == "N 1 event", "the record read through the held descriptor still verified"
    assert lines[-1] == "S 500", "the swap was noticed at the flush, after the last decision"
    assert len(lines) == 500

    assert store_rows(site) == [], "nothing from the replaced directory was stored"
    connection = sqlite3.connect(site / STORE_NAME)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_kill_between_flushes_keeps_the_store_consistent(tmp_path):
    site, _, entries = fixtures(tmp_path)
    first_reference, second_reference = entries[0][1], entries[1][1]
    first_digest, second_digest = digest_of(first_reference), digest_of(second_reference)

    probe = subprocess.Popen([RECORDS_BIN, "verify", "--home", str(site)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Entry 500 is the pass's first flush: it stores what was verified before it.
    written = frame([(1, first_reference), *((n, "plain") for n in range(2, 501))])
    probe.stdin.write(written)
    probe.stdin.flush()
    deadline = time.time() + 60
    while store_rows(site) != [first_digest] and time.time() < deadline:
        time.sleep(0.01)
    assert store_rows(site) == [first_digest], "the first flush stored the verified record"

    # One more record, verified into the pending batch and not yet stored: the next flush
    # is 500 entries away, so the kill lands between the two however fast the reader runs.
    second = frame([(501, second_reference)])
    probe.stdin.write(second)
    probe.stdin.flush()
    consumed = len(written) + len(second)
    deadline = time.time() + 10
    while (bytes_read(probe.pid) or 0) < consumed and time.time() < deadline:
        time.sleep(0.01)
    probe.kill()
    probe.communicate(timeout=60)

    assert probe.returncode == -signal.SIGKILL
    assert store_rows(site) == [first_digest], "only the flushed row survived the kill"
    connection = sqlite3.connect(site / STORE_NAME)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()

    # The next pass serves the flushed record and verifies the unflushed one again.
    stdout, status, stderr = run_verify(site, [(1, first_reference), (501, second_reference)],
                                       "--payloads")
    assert status == 0, stderr
    assert decisions(stdout) == [(1, "V"), (501, "N")]
    assert emitted_payloads(stdout) == {1: PAYLOADS[0], 501: PAYLOADS[1]}
    assert store_rows(site) == sorted([first_digest, second_digest])
