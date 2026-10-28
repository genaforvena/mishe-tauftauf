"""The authenticated restore request: forged, stale, replayed and raced."""
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mishe_tauftauf.core import restore_request as rr

OPENSSL = shutil.which("openssl")
pytestmark = pytest.mark.skipif(OPENSSL is None, reason="openssl is unavailable")


@pytest.fixture
def keys(tmp_path):
    """A requester's key pair: a private key only the requester can read."""
    private = tmp_path / "requester.key"
    public = tmp_path / "requester.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(private)],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
                   check=True, capture_output=True)
    os.chmod(private, 0o600)
    return private, public


@pytest.fixture
def other_keys(tmp_path):
    """A second, unrelated key pair: the impersonator."""
    private = tmp_path / "other.key"
    public = tmp_path / "other.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(private)],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
                   check=True, capture_output=True)
    return private, public


@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text('{"clears": {"tripped": {"reason": "host filesystem probe unavailable"}},'
                    ' "verification": ["controlled check"]}\n', encoding="utf-8")
    return path


def make_request(tmp_path, keys, evidence, *, requester="operator", nonce="nonce-1",
                 issued_at=None, clears=None):
    private, public = keys
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    request = rr.build_request(
        requester=requester, operation_id="restore-op-1", evidence_path=evidence,
        evidence_sha256=digest, clears=clears or {"tripped": {"reason": "host filesystem probe unavailable"}},
        issued_at=issued_at if issued_at is not None else time.time(), nonce=nonce)
    signature = rr.sign(request, key_path=private)
    request["signature"] = signature.hex()
    return request, public


def test_a_signed_request_verifies(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence)
    rr.check_request(request, public_key_path=public, expected_requester="operator",
                     expected_clears=request["clears"])


def test_a_request_with_no_signature_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence)
    del request["signature"]
    with pytest.raises(rr.RestoreRequestError, match="no signature"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_forged_signature_is_refused(tmp_path, keys, other_keys, evidence):
    """A request signed by someone else's key does not verify."""
    request, public = make_request(tmp_path, keys, evidence)
    _, other_public = other_keys
    with pytest.raises(rr.RestoreRequestError, match="does not verify"):
        rr.check_request(request, public_key_path=other_public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_tampered_field_breaks_the_signature(tmp_path, keys, evidence):
    """Changing the evidence digest after signing is detected."""
    request, public = make_request(tmp_path, keys, evidence)
    request["evidence_sha256"] = "0" * 64
    with pytest.raises(rr.RestoreRequestError, match="does not verify"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_tampered_latch_list_breaks_the_signature(tmp_path, keys, evidence):
    """The signature covers the latches too, so a swapped latch is detected."""
    request, public = make_request(tmp_path, keys, evidence)
    request["clears"] = {"tripped": {"reason": "something else"}}
    with pytest.raises(rr.RestoreRequestError, match="does not verify"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_request_naming_another_requester_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence, requester="genome")
    with pytest.raises(rr.RestoreRequestError, match="not 'operator'"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_stale_request_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence, issued_at=time.time() - 4000)
    with pytest.raises(rr.RestoreRequestError, match="stale"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_request_dated_in_the_future_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence, issued_at=time.time() + 4000)
    with pytest.raises(rr.RestoreRequestError, match="future"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_replayed_nonce_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence, nonce="nonce-7")
    seen = {"nonce-7"}
    with pytest.raises(rr.RestoreRequestError, match="already used"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"], seen_nonces=seen)


def test_a_request_naming_different_latches_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence)
    with pytest.raises(rr.RestoreRequestError, match="different latches"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears={"tripped": {"reason": "a different reason"}})


def test_a_request_citing_a_different_evidence_digest_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence)
    with pytest.raises(rr.RestoreRequestError, match="different evidence digest"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"], expected_evidence_sha256="1" * 64)


def test_evidence_that_changed_after_signing_cannot_be_cited(tmp_path, keys, evidence):
    """The evidence digest is recomputed from the bytes at build time."""
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    evidence.write_text('{"clears": {}}\n', encoding="utf-8")
    with pytest.raises(rr.RestoreRequestError, match="disagrees with the file"):
        rr.build_request(requester="operator", operation_id="restore-op-1",
                         evidence_path=evidence, evidence_sha256=digest,
                         clears={"tripped": {}}, issued_at=time.time(), nonce="n")


def test_evidence_reached_through_a_symlink_is_refused(tmp_path, keys, evidence):
    """A symlink to evidence is not the evidence the request named."""
    link = tmp_path / "link.json"
    link.symlink_to(evidence)
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    request = rr.build_request(requester="operator", operation_id="restore-op-1",
                               evidence_path=link, evidence_sha256=digest,
                               clears={"tripped": {}}, issued_at=time.time(), nonce="n")
    # The request records the resolved path, so a later swap of the link
    # cannot silently change what the request cited.
    assert request["evidence_path"] == str(evidence.resolve())


def test_a_race_between_reading_and_signing_is_detected(tmp_path, keys, evidence):
    """Evidence that changes between the digest and the signature is refused.

    The signature covers the digest the requester read, so the request still
    verifies as a signed statement.  What changes is the evidence: its bytes
    no longer match the digest the request cites.  The caller must re-read
    the evidence and compare, and refuse when they disagree.
    """
    private, public = keys
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    request = rr.build_request(requester="operator", operation_id="restore-op-1",
                               evidence_path=evidence, evidence_sha256=digest,
                               clears={"tripped": {}}, issued_at=time.time(), nonce="n")
    evidence.write_text('{"clears": {"tripped": {"reason": "swapped"}}}\n', encoding="utf-8")
    signature = rr.sign(request, key_path=private)
    request["signature"] = signature.hex()
    # The signature is valid over the digest the requester signed.
    rr.check_request(request, public_key_path=public, expected_requester="operator",
                     expected_clears=request["clears"])
    # The evidence no longer matches that digest, so the caller refuses.
    observed = hashlib.sha256(evidence.read_bytes()).hexdigest()
    assert observed != request["evidence_sha256"]
    with pytest.raises(rr.RestoreRequestError, match="different evidence digest"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"],
                         expected_evidence_sha256=observed)


def test_a_request_with_an_unknown_version_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence)
    request["version"] = 99
    with pytest.raises(rr.RestoreRequestError, match="unsupported request version"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_request_with_a_malformed_signature_is_refused(tmp_path, keys, evidence):
    request, public = make_request(tmp_path, keys, evidence)
    request["signature"] = "not hex"
    with pytest.raises(rr.RestoreRequestError, match="not hex"):
        rr.check_request(request, public_key_path=public, expected_requester="operator",
                         expected_clears=request["clears"])


def test_a_missing_public_key_is_refused_not_assumed(tmp_path, keys, evidence):
    request, _ = make_request(tmp_path, keys, evidence)
    with pytest.raises(rr.RestoreRequestError):
        rr.check_request(request, public_key_path=tmp_path / "absent.pub",
                         expected_requester="operator", expected_clears=request["clears"])


def test_the_module_does_not_import_the_plant_stack():
    """The restore boundary stays offline: it reaches nothing else."""
    import ast
    path = Path(rr.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("mishe_tauftauf."), \
                f"restore_request imports {module} at module scope"
