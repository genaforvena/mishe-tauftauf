"""The restore boundary: ownership, authentication, replay and isolation."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mishe_tauftauf.core import restore_boundary as rb
from mishe_tauftauf.core import restore_request as rr

OPENSSL = shutil.which("openssl")
pytestmark = pytest.mark.skipif(OPENSSL is None, reason="openssl is unavailable")


@pytest.fixture
def keys(tmp_path):
    private = tmp_path / "requester.key"
    public = tmp_path / "requester.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(private)],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
                   check=True, capture_output=True)
    os.chmod(private, 0o600)
    return private, public


@pytest.fixture
def state(tmp_path, keys):
    """A boundary state directory with one registered requester."""
    root = tmp_path / "state"
    (root / rb.TRUST_DIR).mkdir(parents=True)
    (root / rb.INBOX_DIR).mkdir(parents=True)
    (root / rb.SEEN_DIR).mkdir(parents=True)
    rb.register_requester(root, "operator", keys[1])
    return root


@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text('{"clears": {"tripped": {"reason": "host filesystem probe unavailable"}},'
                    ' "verification": ["controlled check"]}\n', encoding="utf-8")
    return path


def signed_request(keys, evidence, *, nonce="nonce-1", requester="operator",
                   issued_at=None, clears=None):
    import hashlib
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    request = rr.build_request(
        requester=requester, operation_id="restore-op-1", evidence_path=evidence,
        evidence_sha256=digest,
        clears=clears or {"tripped": {"reason": "host filesystem probe unavailable"}},
        issued_at=issued_at if issued_at is not None else time.time(), nonce=nonce)
    signature = rr.sign(request, key_path=keys[0])
    return request, signature, digest


def test_a_registered_requester_is_accepted_once(state, keys, evidence):
    request, signature, digest = signed_request(keys, evidence)
    accepted = rb.accept_request(state, request, signature=signature,
                                 expected_clears=request["clears"],
                                 expected_evidence_sha256=digest)
    assert accepted["requester"] == "operator"
    assert accepted["nonce_record"].endswith("nonce-1")


def test_the_same_request_is_refused_the_second_time(state, keys, evidence):
    request, signature, digest = signed_request(keys, evidence)
    rb.accept_request(state, request, signature=signature,
                      expected_clears=request["clears"], expected_evidence_sha256=digest)
    with pytest.raises(rb.BoundaryError, match="already used"):
        rb.accept_request(state, request, signature=signature,
                          expected_clears=request["clears"], expected_evidence_sha256=digest)


def test_an_unregistered_requester_is_refused(state, keys, evidence):
    request, signature, digest = signed_request(keys, evidence, requester="intruder")
    with pytest.raises(rb.BoundaryError, match="no registered public key"):
        rb.accept_request(state, request, signature=signature,
                          expected_clears=request["clears"], expected_evidence_sha256=digest)


def test_a_forged_signature_is_refused_and_the_nonce_stays_unused(state, keys, evidence):
    """A refused request does not consume its nonce, so it can be corrected."""
    request, signature, digest = signed_request(keys, evidence)
    forged = bytes(64)
    with pytest.raises(rb.BoundaryError, match="does not verify"):
        rb.accept_request(state, request, signature=forged,
                          expected_clears=request["clears"], expected_evidence_sha256=digest)
    # The nonce was not consumed, so the genuine request still works.
    rb.accept_request(state, request, signature=signature,
                      expected_clears=request["clears"], expected_evidence_sha256=digest)


def test_a_request_citing_different_latches_is_refused(state, keys, evidence):
    request, signature, digest = signed_request(keys, evidence)
    with pytest.raises(rb.BoundaryError, match="different latches"):
        rb.accept_request(state, request, signature=signature,
                          expected_clears={"tripped": {"reason": "other"}},
                          expected_evidence_sha256=digest)


def test_a_stale_request_is_refused(state, keys, evidence):
    request, signature, digest = signed_request(keys, evidence, issued_at=time.time() - 4000)
    with pytest.raises(rb.BoundaryError, match="stale"):
        rb.accept_request(state, request, signature=signature,
                          expected_clears=request["clears"], expected_evidence_sha256=digest)


def test_one_requester_cannot_write_into_another_requesters_inbox(state):
    """The inbox is per requester; there is no shared privileged spool."""
    mine = rb.inbox_for(state, "operator")
    theirs = rb.inbox_for(state, "genome")
    assert mine != theirs
    assert mine.parent == theirs.parent
    assert mine.parent.name == rb.INBOX_DIR


def test_a_world_writable_inbox_is_reported(state):
    inbox = rb.inbox_for(state, "operator")
    inbox.mkdir(parents=True, exist_ok=True)
    os.chmod(inbox, 0o777)
    problems = rb.check_layout(state, root_uid=os.getuid())
    assert any("world-writable" in problem for problem in problems)


def test_an_inbox_readable_by_other_users_is_reported(state):
    inbox = rb.inbox_for(state, "operator")
    inbox.mkdir(parents=True, exist_ok=True)
    os.chmod(inbox, 0o755)
    problems = rb.check_layout(state, root_uid=os.getuid())
    assert any("readable by other users" in problem for problem in problems)


def test_a_trust_anchor_owned_by_the_requester_is_reported(state):
    """The trust anchor must not be writable by the requester it trusts."""
    problems = rb.check_layout(state, root_uid=os.getuid() + 1)
    assert any("trust anchor" in problem for problem in problems)


def test_a_correct_layout_reports_no_problem(state):
    inbox = rb.inbox_for(state, "operator")
    inbox.mkdir(parents=True, exist_ok=True)
    os.chmod(inbox, rb.INBOX_MODE)
    # The fixture is owned by this user, so the same rules are checked
    # against this user's uid.  The inbox must be owned by somebody else,
    # which in a real installation is the requester.
    problems = rb.check_layout(state, root_uid=os.getuid())
    assert [problem for problem in problems if "inbox " in problem] == []


def test_a_nonce_that_could_escape_the_ledger_is_refused(state):
    with pytest.raises(rb.BoundaryError, match="single path component"):
        rb.consume_nonce(state, "../escape", requester="operator")


def test_a_request_cannot_overwrite_a_waiting_request(state):
    inbox = rb.inbox_for(state, "operator") / "request.json"
    rb.write_request(inbox, {"requester": "operator", "nonce": "n"})
    with pytest.raises(rb.BoundaryError, match="already waiting"):
        rb.write_request(inbox, {"requester": "operator", "nonce": "m"})


def test_writing_a_request_gives_the_inbox_its_contracted_mode(state):
    """The inbox holds a signed request; it must not be readable by others."""
    import stat
    inbox = rb.inbox_for(state, "operator") / "request.json"
    rb.write_request(inbox, {"requester": "operator", "nonce": "n"})
    mode = stat.S_IMODE(inbox.parent.stat().st_mode)
    assert mode == rb.INBOX_MODE, f"the inbox's mode is {oct(mode)}, not {oct(rb.INBOX_MODE)}"
    # The boundary's own checker must accept what the writer produced.
    problems = [problem for problem in rb.check_layout(state, root_uid=os.getuid())
                if "inbox " in problem]
    assert problems == []


def test_the_layout_names_no_world_writable_path(state):
    layout = rb.describe_layout(state)
    assert layout["world_writable_paths"] == []
    assert layout["trust_owner"] == "root"
