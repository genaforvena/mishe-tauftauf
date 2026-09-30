"""Immutable structured evidence committed only by a canonical feed reference."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import secrets
from pathlib import Path

MAX_BYTES = 1024 * 1024
REFERENCE_RE = re.compile(r"\[record\] records/([0-9a-f]{64})\.json sha256=([0-9a-f]{64})\Z")


def reference(digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid record digest")
    return f"[record] records/{digest}.json sha256={digest}"


def _directory(home: Path) -> int:
    # The site root is trusted; its record subdirectory must never be followed.
    return os.open(home / "records", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _read(directory: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError("record must be a regular file of 1 byte to 1 MiB")
        data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("record exceeds 1 MiB")
        return data


def prepare(home: Path | str, payload: dict, *, kind: str = "event") -> str:
    """Durably install content-addressed evidence and return its feed reference."""
    if not isinstance(payload, dict) or not isinstance(kind, str) or not kind:
        raise ValueError("record requires an object payload and a kind")
    data = json.dumps({"version": 1, "kind": kind, "payload": payload}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("record exceeds 1 MiB")
    digest = hashlib.sha256(data).hexdigest()
    home = Path(home)
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / "records").mkdir(mode=0o700, exist_ok=True)
        directory = _directory(home)
    except OSError as exc:
        raise ValueError(f"record directory unavailable: {exc}") from exc
    name = digest + ".json"
    temporary = "." + digest + "." + secrets.token_hex(16)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o400)
            os.fsync(handle.fileno())
        try:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            if _read(directory, name) != data:
                raise ValueError("existing immutable record disagrees with content hash")
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
        parent = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError as exc:
        raise ValueError(f"record unavailable: {exc}") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return reference(digest)


def payload(entry) -> dict:
    """Resolve legacy first-line JSON or verify a committed immutable reference."""
    lines = entry.body.splitlines()
    refs = [line for line in lines if line.lstrip().startswith("[record]")]
    if len(lines) > 1 and lines[1].lstrip().startswith("{"):
        if refs:
            raise ValueError("mixed legacy JSON and immutable record reference")
        data = json.loads(lines[1])
        if not isinstance(data, dict):
            raise ValueError("control payload must be an object")
        return data
    if len(refs) != 1 or not (match := REFERENCE_RE.fullmatch(refs[0])):
        raise ValueError("missing or invalid immutable record reference")
    digest, expected = match.groups()
    if digest != expected or entry.home is None:
        raise ValueError("record digest mismatch or missing site context")
    directory = None
    try:
        directory = _directory(Path(entry.home))
        raw = _read(directory, digest + ".json")
    except OSError as exc:
        raise ValueError(f"record unavailable: {exc}") from exc
    finally:
        if directory is not None:
            os.close(directory)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("immutable record checksum mismatch")
    record = json.loads(raw)
    if (not isinstance(record, dict) or record.get("version") != 1 or
            not isinstance(record.get("kind"), str) or not record["kind"] or
            not isinstance(record.get("payload"), dict)):
        raise ValueError("invalid immutable record envelope")
    return record["payload"]
