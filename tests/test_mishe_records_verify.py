"""Differential checks for the ``mishe-records`` binary.

While the Python path is live it is the authority: ``feed._VerificationPass`` and
``records.cached_payload`` must reach the same decisions, the same payloads and the same
fault text as the binary. The contract is ``rust/mishe-records/CONTRACT.md``. Runs when a
binary resolves (``MISHE_RECORDS_BIN``, then the site install), else skips with the reason.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from mishe_tauftauf import feed, records
from tests.records_binary import RECORDS_BIN, RECORDS_SKIP_REASON

pytestmark = pytest.mark.skipif(RECORDS_BIN is None, reason=RECORDS_SKIP_REASON)

STAMP = "2026-11-10T00:00:00Z"
PAYLOADS = ({"text": "one", "n": 1}, {"text": "two", "n": 2})


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
            out.append((int(rest), head))
    return out


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
    """A site for the binary and an identical one for the Python authority.

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


def python_sink(site: Path, entries: list[tuple[int, str]]) -> dict[int, dict]:
    """What the Python pass yields for the same entries: the authority, run in full."""
    objects = [feed.FeedEntry(sequence, STAMP, "test", body, site) for sequence, body in entries]
    verification = feed._VerificationPass()
    sink: dict[int, dict] = {}
    try:
        verification.verify(objects, sink)
        verification.commit()
    finally:
        verification.close()
    return sink


def digest_of(reference: str) -> str:
    return reference.split("/", 1)[1].split(".", 1)[0]


def test_cold_pass_matches_python_and_leaves_a_store_python_serves(tmp_path):
    site, py_site, entries = fixtures(tmp_path)
    expected = python_sink(py_site, entries)
    assert set(expected) == {1, 2, 3}

    stdout, status, stderr = run_verify(site, entries, "--payloads")
    assert status == 0, stderr
    assert decisions(stdout) == [(1, "N"), (2, "N"), (3, "V"), (4, "S"), (5, "S")]
    assert emitted_payloads(stdout) == expected

    # The store the binary wrote is one Python's own reader serves from.
    store = records.VerifiedStore(site)
    directory = records._directory(site)
    try:
        now = time.time()
        for sequence in (1, 2):
            digest = digest_of(dict(entries)[sequence])
            assert store.lookup(digest, directory, now, verify_only=True) is True
    finally:
        os.close(directory)
    records._STORES.clear()
    assert python_sink(site, entries) == expected


def test_warm_store_is_served_again(tmp_path):
    site, py_site, entries = fixtures(tmp_path)
    expected = python_sink(py_site, entries)

    assert run_verify(site, entries, "--payloads")[1] == 0
    stdout, status, stderr = run_verify(site, entries, "--payloads")
    assert status == 0, stderr
    assert decisions(stdout)[:3] == [(1, "V"), (2, "V"), (3, "V")]
    assert emitted_payloads(stdout) == expected


def test_missing_record_fault_text_matches_python(tmp_path):
    site, _, _ = fixtures(tmp_path)
    digest = hashlib.sha256(b"never written").hexdigest()
    body = records.reference(digest)

    stdout, status, stderr = run_verify(site, [(7, body)])
    assert status == 2
    assert stdout == ""

    entry = feed.FeedEntry(7, STAMP, "test", body, site)
    with pytest.raises(ValueError) as raised:
        records.cached_payload(entry, {}, records.VerifiedStore(site), verify_only=True)
    assert stderr == str(raised.value)


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

    entry = feed.FeedEntry(1, STAMP, "test", body, site)
    with pytest.raises(ValueError) as raised:
        records.cached_payload(entry, {}, records.VerifiedStore(site), verify_only=True)
    assert str(raised.value) == "immutable record checksum mismatch"
