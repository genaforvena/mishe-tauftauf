"""The restore request's own text format: its fields, its signature, its checks.

A restore request is a signed statement by a named requester.  The signature
is what makes it a request; everything else in it — the latch identity, the
evidence digest, the expiry — is observable data that any reader can see.

That distinction is the point of this module.  A request that carried only a
latch id and an evidence hash could be written by anyone who can read the
latch, so it proves nothing about who asked.  Here, the requester holds a
private key; the verifier holds only the public key; and a request whose
signature does not verify is refused no matter how well-formed its data is.

The module is offline.  It decodes and verifies requests; it does not restore
anything, does not clear a latch, does not admit a workload and does not
talk to the sentinel.  Activating it is a separate, reviewed act.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

#: The request format this module understands.
VERSION = 1

#: The digest of the bytes a signature covers.
_MESSAGE_PREFIX = b"mishe-restore-request v1\n"

#: A requester identity: a role or service name.
REQUESTER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}\Z")

#: An operation id, as the effect ledger spells them.
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

#: A lowercase hex sha256 digest.
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

#: How long a request stays valid.
MAX_AGE_SECONDS = 300


class RestoreRequestError(ValueError):
    """The request is malformed, unauthenticated, stale or replayed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RestoreRequestError(message)


def _now() -> float:
    import time
    return time.time()


def valid_requester(requester: Any) -> bool:
    """Whether ``requester`` is a plain requester name."""
    return isinstance(requester, str) and bool(REQUESTER_RE.fullmatch(requester))


def valid_operation_id(operation_id: Any) -> bool:
    """Whether ``operation_id`` may name an operation."""
    return isinstance(operation_id, str) and bool(OPERATION_ID_RE.fullmatch(operation_id))


def valid_digest(digest: Any) -> bool:
    """Whether ``digest`` is a lowercase hex sha256 digest."""
    return isinstance(digest, str) and bool(DIGEST_RE.fullmatch(digest))


def canonical_message(*, requester: str, operation_id: str, nonce: str,
                       evidence_sha256: str, issued_at: float,
                       clears: Mapping[str, Any]) -> bytes:
    """The exact bytes a signature covers.

    Every field that gives the request its meaning is in here, including the
    latches it names and the evidence it cites.  A signature over these
    bytes therefore authenticates the whole request, not just its identity.
    """
    body = {
        "version": VERSION,
        "requester": requester,
        "operation_id": operation_id,
        "nonce": nonce,
        "evidence_sha256": evidence_sha256,
        "issued_at": issued_at,
        "clears": dict(clears),
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return _MESSAGE_PREFIX + encoded


def build_request(*, requester: str, operation_id: str, evidence_path: Path | str,
                  evidence_sha256: str, clears: Mapping[str, Any],
                  issued_at: float, nonce: str) -> dict:
    """The request's fields, before it is signed.

    The evidence digest is recomputed from the file's bytes when the file is
    readable; a digest that disagrees with the file is a caller error, not a
    request that can be signed.
    """
    _require(valid_requester(requester), "requester must be a plain name")
    _require(valid_operation_id(operation_id),
             "operation_id must match [A-Za-z0-9_.:-]+")
    _require(valid_digest(evidence_sha256),
             "evidence_sha256 must be a lowercase hex sha256 digest")
    path = Path(evidence_path)
    try:
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RestoreRequestError(f"evidence unreadable: {exc}") from exc
    _require(observed == evidence_sha256,
             f"evidence digest disagrees with the file: recorded {evidence_sha256}, "
             f"observed {observed}")
    _require(isinstance(clears, Mapping) and clears,
             "a request must name the latches it clears")
    return {
        "version": VERSION,
        "requester": requester,
        "operation_id": operation_id,
        "nonce": nonce,
        "evidence_path": str(path.resolve()),
        "evidence_sha256": evidence_sha256,
        "issued_at": float(issued_at),
        "clears": dict(clears),
    }


def sign(request: Mapping[str, Any], *, key_path: Path | str) -> bytes:
    """Sign a request with the requester's private key.

    The key is a file the requester alone can read.  It is never read into
    this process: ``openssl`` does the signing, so the private key does not
    have to be handled by the code that builds a request.
    """
    key = Path(key_path)
    message = canonical_message(**_message_fields(request))
    with tempfile.TemporaryDirectory() as directory:
        message_path = Path(directory) / "message"
        signature_path = Path(directory) / "signature"
        message_path.write_bytes(message)
        try:
            result = subprocess.run(
                ["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin",
                 "-in", str(message_path), "-out", str(signature_path)],
                capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RestoreRequestError(f"signing unavailable: {exc}") from exc
        if result.returncode != 0:
            raise RestoreRequestError(
                "signing refused: " + _openssl_message(result))
        return signature_path.read_bytes()


def verify(request: Mapping[str, Any], signature: bytes, *, public_key_path: Path | str,
           now: float | None = None) -> None:
    """Verify a request's signature and its freshness.

    Raises when anything is wrong.  A request that passes has a signature
    made by the holder of the private key behind ``public_key_path``, over
    exactly the fields the request carries, and it is not stale.
    """
    fields = _message_fields(request)
    message = canonical_message(**fields)
    key = Path(public_key_path)
    with tempfile.TemporaryDirectory() as directory:
        message_path = Path(directory) / "message"
        signature_path = Path(directory) / "signature"
        message_path.write_bytes(message)
        signature_path.write_bytes(signature)
        try:
            result = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key),
                 "-rawin", "-in", str(message_path), "-sigfile", str(signature_path)],
                capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RestoreRequestError(f"verification unavailable: {exc}") from exc
        if result.returncode != 0:
            raise RestoreRequestError(
                "the request's signature does not verify against the named key")
    issued = fields["issued_at"]
    moment = float(now if now is not None else _now())
    age = moment - issued
    if age < -MAX_AGE_SECONDS:
        raise RestoreRequestError("the request is dated in the future")
    if age > MAX_AGE_SECONDS:
        raise RestoreRequestError("the request is stale")


def _openssl_message(result) -> str:
    """What openssl said, as one line."""
    return result.stderr.decode("utf-8", "replace").strip()


def _message_fields(request: Mapping[str, Any]) -> dict:
    for field in ("requester", "operation_id", "nonce", "evidence_sha256",
                  "issued_at", "clears"):
        if field not in request:
            raise RestoreRequestError(f"the request has no {field}")
    return {
        "requester": request["requester"],
        "operation_id": request["operation_id"],
        "nonce": request["nonce"],
        "evidence_sha256": request["evidence_sha256"],
        "issued_at": float(request["issued_at"]),
        "clears": dict(request["clears"]),
    }


def check_request(request: Mapping[str, Any], *, public_key_path: Path | str,
                  expected_requester: str, expected_clears: Mapping[str, Any],
                  expected_evidence_sha256: str | None = None,
                  seen_nonces: set[str] | None = None,
                  now: float | None = None) -> None:
    """Every check a request must pass before anyone acts on it.

    The signature is checked first: an unsigned or wrongly signed request is
    refused before its contents are read, so a forged request never reaches
    the point where its data would be trusted.
    """
    _require(request.get("version") == VERSION,
             f"unsupported request version {request.get('version')!r}")
    _require(request.get("requester") == expected_requester,
             f"request names requester {request.get('requester')!r}, "
             f"not {expected_requester!r}")
    signature = request.get("signature")
    _require(isinstance(signature, str) and signature,
             "the request carries no signature")
    try:
        raw = bytes.fromhex(signature)
    except ValueError as exc:
        raise RestoreRequestError("the request's signature is not hex") from exc
    verify(request, raw, public_key_path=public_key_path, now=now)
    if expected_evidence_sha256 is not None:
        _require(request.get("evidence_sha256") == expected_evidence_sha256,
                 "the request cites a different evidence digest")
    _require(dict(request.get("clears") or {}) == dict(expected_clears),
             "the request names different latches than the ones present")
    nonce = request.get("nonce")
    _require(isinstance(nonce, str) and nonce,
             "the request carries no nonce")
    if seen_nonces is not None:
        _require(nonce not in seen_nonces,
                 "this request's nonce was already used")


def decode_request(raw: Any) -> dict:
    """Decode a request's bytes into its fields.

    A request that is not JSON, or is JSON but not an object, is refused
    with a reason.  The contents are still only evidence until the
    signature over them verifies.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RestoreRequestError(f"request is not UTF-8: {exc}") from exc
    if not isinstance(raw, str):
        raise RestoreRequestError("request must be text")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RestoreRequestError(f"request is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RestoreRequestError("request must be a JSON object")
    return data


def encode_request(request: Mapping[str, Any]) -> str:
    """Encode a request's fields as its canonical text."""
    return json.dumps(dict(request), sort_keys=True, ensure_ascii=False) + "\n"
