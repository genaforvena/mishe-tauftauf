"""The least-privilege boundary a contained supervisor restores through.

The shape of the boundary:

* **Root owns the trust anchor.**  The registered public keys live in a
  root-owned directory that the requester cannot write.  Only root decides
  who may ask.
* **The requester owns its own inbox.**  A requester writes its request into
  a directory it owns alone.  It cannot write into root's state, and it
  cannot write into another requester's inbox.  There is no world-writable
  privileged spool.
* **Authentication is the signature.**  The latch identity and the evidence
  digest are observable data; the signature is what proves who asked.  A
  request whose signature does not verify against the registered key is
  refused before its contents are trusted.
* **Replay is refused.**  Each request carries a nonce, and the nonce is
  consumed in a root-owned ledger.  A request that arrives twice is
  refused the second time.
* **The guard keeps sampling.**  Restoring never holds the sentinel's
  transition lock while it waits, and never holds it across a staged
  restoration.  The sentinel's own observation loop is what decides whether
  the host is healthy, and it keeps running throughout.

This module is **offline**.  It decodes, verifies and records requests; it
does not clear a latch, does not admit a workload, does not start a unit and
does not touch the sentinel.  The root-side caller that would act on an
accepted request is a separate, reviewed act, and nothing here is installed
or activated.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import restore_request as rr

#: The layout under the containment state directory.
TRUST_DIR = "trusted-keys"
INBOX_DIR = "inbox"
SEEN_DIR = "seen-nonces"

#: The mode an inbox directory carries: the requester and its group only.
INBOX_MODE = 0o700

#: The mode a trusted key carries: root reads it, nobody else needs it.
#: It is read-only for its owner too, because a key that the owner may
#: rewrite is a key that authentication can be moved onto.
KEY_MODE = 0o444


class BoundaryError(RuntimeError):
    """The boundary refused a request or could not be set up."""


def inbox_for(state: Path, requester: str) -> Path:
    """The one directory a requester may write into."""
    if not rr.valid_requester(requester):
        raise BoundaryError(f"requester {requester!r} is not a plain name")
    return Path(state) / INBOX_DIR / requester


def trusted_key_for(state: Path, requester: str) -> Path:
    """The registered public key of one requester."""
    if not rr.valid_requester(requester):
        raise BoundaryError(f"requester {requester!r} is not a plain name")
    return Path(state) / TRUST_DIR / f"{requester}.pub"


def seen_nonce_path(state: Path, nonce: str) -> Path:
    """Where a consumed nonce is recorded."""
    if not nonce or "/" in nonce or nonce in {".", ".."}:
        raise BoundaryError("a nonce must be a single path component")
    return Path(state) / SEEN_DIR / nonce


def describe_layout(state: Path) -> dict:
    """The boundary's layout, so a reviewer can check it without running it."""
    state = Path(state)
    return {
        "state": str(state),
        "trusted_keys": str(state / TRUST_DIR),
        "inbox": str(state / INBOX_DIR),
        "seen_nonces": str(state / SEEN_DIR),
        "trust_owner": "root",
        "inbox_owner": "the requester, one directory each",
        "world_writable_paths": [],
    }


def check_layout(state: Path, *, root_uid: int = 0) -> list[str]:
    """Report every way the layout on disk is weaker than the contract.

    The checks are about ownership and mode, because a boundary that is
    correct in code and wrong on disk is not a boundary.  A path that is
    world-writable, or that the requester owns where root must own it, is
    named.

    ``root_uid`` is the uid that must own the trust anchor, the replay
    ledger and the inbox root.  An inbox directory must be owned by
    somebody other than root, because the requester has to write into it.
    That last rule only applies when the privileged owner is real root: an
    unprivileged fixture cannot give one directory to two owners, so it
    checks the mode and world-writability rules instead.
    """
    problems: list[str] = []
    state = Path(state)
    inbox_root = state / INBOX_DIR
    trust_root = state / TRUST_DIR
    seen_root = state / SEEN_DIR

    for path, label in ((trust_root, "trust anchor"),
                        (seen_root, "replay ledger"),
                        (inbox_root, "inbox root")):
        if not path.exists():
            continue
        info = path.stat()
        if info.st_mode & stat.S_IWOTH:
            problems.append(f"{label} {path} is world-writable")
        # A group-writable directory lets a group member unlink and recreate
        # a file inside it, whatever mode the file itself carries.  For the
        # trust anchor that is replacing the key; for the replay ledger it
        # is erasing a consumed nonce.
        if info.st_mode & stat.S_IWGRP:
            problems.append(f"{label} {path} is writable by its group")
        if info.st_uid != root_uid:
            problems.append(f"{label} {path} is not owned by uid {root_uid}")

    if inbox_root.exists():
        for entry in sorted(inbox_root.iterdir()):
            if not entry.is_dir():
                problems.append(f"inbox entry {entry} is not a directory")
                continue
            info = entry.stat()
            if info.st_mode & stat.S_IWOTH:
                problems.append(f"inbox {entry} is world-writable")
            if info.st_mode & 0o077:
                problems.append(f"inbox {entry} is readable by other users")
            if root_uid == 0 and info.st_uid == 0:
                problems.append(f"inbox {entry} is owned by root, so the requester cannot write it")
    if trust_root.exists():
        for entry in sorted(trust_root.iterdir()):
            if not entry.is_file():
                problems.append(f"trust anchor {entry} is not a regular file")
                continue
            info = entry.stat()
            # The trust anchor is the root of the authentication: a key that
            # others may write is a key an attacker may replace.
            if info.st_mode & stat.S_IWOTH:
                problems.append(f"trust anchor {entry} is world-writable")
            if info.st_mode & stat.S_IWGRP:
                problems.append(f"trust anchor {entry} is writable by its group")
            if info.st_mode & stat.S_IWUSR:
                problems.append(f"trust anchor {entry} is writable by its owner")
    return problems


def register_requester(state: Path, requester: str, public_key: Path | str) -> Path:
    """Register a requester's public key in the root-owned trust anchor.

    This is the root-side act.  It is not called by a requester and it is
    not called by this module's verification path.
    """
    target = trusted_key_for(state, requester)
    source = Path(public_key)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise BoundaryError(f"public key unreadable: {exc}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".new")
    temporary.write_bytes(data)
    os.chmod(temporary, KEY_MODE)
    os.replace(temporary, target)
    # The directory that holds the anchor is root's alone: nobody but root
    # may add or replace a key in it.
    os.chmod(target.parent, 0o755)
    return target


def accept_request(state: Path, request: Mapping[str, Any], *,
                   signature: bytes,
                   expected_clears: Mapping[str, Any],
                   expected_evidence_sha256: str | None = None,
                   now: float | None = None,
                   root_uid: int = 0) -> dict:
    """Verify a request and consume its nonce.

    The layout is checked first.  A boundary that is correct in code and
    wrong on disk is not a boundary: a trust anchor others may write is an
    authentication that can be moved onto another key, so the request is
    refused before the signature is even read.

    Returns the verified request when every check passes.  Raises when one
    does not, and a refusal leaves the nonce unconsumed so the same request
    can be corrected and retried.
    """
    state = Path(state)
    problems = check_layout(state, root_uid=root_uid)
    if problems:
        raise BoundaryError(
            "the boundary's layout is not sound: " + "; ".join(problems))
    requester = request.get("requester")
    if not isinstance(requester, str) or not requester:
        raise BoundaryError("the request names no requester")
    key = trusted_key_for(state, requester)
    if not key.is_file():
        raise BoundaryError(f"requester {requester!r} has no registered public key")
    # The signature travels with the request.  The check reads it from the
    # request, so it is placed there before the check runs — never after.
    checked = dict(request)
    checked["signature"] = signature.hex()
    try:
        rr.check_request(checked, public_key_path=key, expected_requester=requester,
                         expected_clears=expected_clears,
                         expected_evidence_sha256=expected_evidence_sha256,
                         now=now)
    except rr.RestoreRequestError as exc:
        raise BoundaryError(str(exc)) from exc
    nonce = checked["nonce"]
    consumed = consume_nonce(state, nonce, requester=requester,
                             operation_id=checked.get("operation_id", ""))
    return dict(checked, accepted_at=now, nonce_record=consumed)


def consume_nonce(state: Path, nonce: str, *, requester: str,
                  operation_id: str = "") -> str:
    """Record a nonce as used, refusing one that was used before.

    The record is created exclusively, so two requests carrying the same
    nonce cannot both be accepted even when they arrive together.
    """
    path = seen_nonce_path(state, nonce)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"nonce": nonce, "requester": requester,
                           "operation_id": operation_id, "at": _now()},
                          sort_keys=True) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BoundaryError(f"nonce {nonce!r} was already used") from exc
    except OSError as exc:
        raise BoundaryError(f"the replay ledger is unavailable: {exc}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return str(path)


def _now() -> float:
    import time
    return time.time()


def read_request(inbox: Path) -> dict:
    """Read one request from a requester's own inbox.

    The inbox belongs to the requester, so its contents are evidence, not
    instructions: the caller verifies the signature before it reads any
    field as meaningful.
    """
    path = Path(inbox)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BoundaryError(f"request unreadable: {exc}") from exc
    try:
        return rr.decode_request(raw)
    except rr.RestoreRequestError as exc:
        raise BoundaryError(str(exc)) from exc


def write_request(inbox: Path, request: Mapping[str, Any]) -> Path:
    """Write a request into the requester's own inbox.

    The write is exclusive and the file is written under the requester's
    own directory, so a request cannot overwrite another request's receipt.
    """
    path = Path(inbox)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The inbox carries a signed request, its evidence digest and its
    # nonce.  It belongs to the requester alone: the contracted mode is
    # applied after the creation, because mkdir's mode is masked by the
    # caller's umask.
    #
    # Only a directory inside this boundary's inbox root is narrowed.  A
    # caller that passes an unrelated path must not have its mode changed
    # as a side effect.
    if path.parent.parent.name == INBOX_DIR:
        try:
            os.chmod(path.parent, INBOX_MODE)
        except OSError as exc:
            raise BoundaryError(f"the inbox's mode could not be set: {exc}") from exc
    payload = rr.encode_request(request)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BoundaryError("a request is already waiting in this inbox") from exc
    except OSError as exc:
        raise BoundaryError(f"the inbox is unavailable: {exc}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return path
