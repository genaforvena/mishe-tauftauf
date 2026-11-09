"""Immutable structured evidence committed only by a canonical feed reference."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import secrets
import threading
import time
import zlib
from pathlib import Path

MAX_BYTES = 1024 * 1024
REFERENCE_RE = re.compile(r"\[record\] records/([0-9a-f]{64})\.json sha256=([0-9a-f]{64})\Z")

#: A record names its kind in its first field: the record is written with
#: sorted keys, so the head of the file begins ``{"kind":"``.
KIND_PREFIX = b'{"kind":"'

#: How many bytes of a record the kind is read from. The kind is the first
#: field, so the head is enough and the census never reads a whole record.
HEAD_BYTES = 64


def reference(digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid record digest")
    return f"[record] records/{digest}.json sha256={digest}"


def record_kind(head: bytes) -> str | None:
    """The kind a record's head names, or None when it names none.

    The record is written with sorted keys, so its kind is the first field
    and the head of the file says which kind it is. A head that does not
    begin with the kind field names no kind: the answer is None, never a
    guess.
    """
    if not head.startswith(KIND_PREFIX):
        return None
    end = head.find(b'"', len(KIND_PREFIX))
    if end < 0:
        return None
    try:
        return head[len(KIND_PREFIX):end].decode()
    except UnicodeDecodeError:
        return None


def record_digests(home: Path | str) -> list[str]:
    """The content digests of the records the store holds.

    A store file is named by the digest of its own content, so the names are
    the digests. The store is read through the record directory's own file
    descriptor, so the path is never followed.
    """
    directory = _directory(Path(home))
    try:
        names = os.listdir(directory)
    finally:
        os.close(directory)
    return sorted(name[:-5] for name in names if name.endswith(".json"))


def record_head(home: Path | str, digest: str) -> bytes | None:
    """The first bytes of a stored record, or None when it cannot be read."""
    directory = _directory(Path(home))
    try:
        fd = os.open(digest + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as handle:
            return handle.read(HEAD_BYTES)
    except OSError:
        return None
    finally:
        os.close(directory)


def _directory(home: Path) -> int:
    # The site root is trusted; its record subdirectory must never be followed.
    return os.open(home / "records", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def check_pass_directory(passfd: int, home: Path | str) -> None:
    """Fail closed when the live records path no longer names the pass's directory.

    A pass-long fd keeps reading the inode it opened. If ``home/records`` was swapped
    (symlink or replacement) during the pass, results from the old inode must not
    be reported or stored as current.
    """
    try:
        live = os.lstat(Path(home) / "records")
    except OSError as exc:
        raise ValueError("records directory unavailable during verification pass") from exc
    held = os.fstat(passfd)
    if not stat.S_ISDIR(live.st_mode) or (live.st_dev, live.st_ino) != (held.st_dev, held.st_ino):
        raise ValueError("records directory replaced during verification pass")


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
    return envelope(entry)["payload"]


def envelope(entry, directory: int | None = None) -> dict:
    """Verify and return an immutable envelope, including its publication kind.

    A caller-supplied ``directory`` fd is used and left open; it stays owned by the caller.
    """
    refs = [line for line in entry.body.splitlines()
            if line.lstrip().startswith("[record]")]
    if len(refs) != 1 or not (match := REFERENCE_RE.fullmatch(refs[0])):
        raise ValueError("missing or invalid immutable record reference")
    digest, expected = match.groups()
    if digest != expected or entry.home is None:
        raise ValueError("record digest mismatch or missing site context")
    owned = directory is None
    try:
        if owned:
            directory = _directory(Path(entry.home))
        raw = _read(directory, digest + ".json")
    except OSError as exc:
        raise ValueError(f"record unavailable: {exc}") from exc
    finally:
        if owned and directory is not None:
            os.close(directory)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("immutable record checksum mismatch")
    record = json.loads(raw)
    if (not isinstance(record, dict) or record.get("version") != 1 or
            not isinstance(record.get("kind"), str) or not record["kind"] or
            not isinstance(record.get("payload"), dict)):
        raise ValueError("invalid immutable record envelope")
    return record


#: Verified payloads persist per site so a fresh process does not re-hash every
#: referenced record on each render. A hit is served only while the record file
#: still has the identity it had when it was verified and the entry is younger
#: than VERIFIED_MAX_AGE; anything else runs the full envelope() verification.
VERIFIED_STORE = "records.verified.sqlite3"
VERIFIED_MAX_AGE = 6 * 3600
VERIFIED_FLUSH_ROWS = 500
_STORE_LOCK = threading.Lock()
_STORES: dict[str, "VerifiedStore"] = {}


class VerifiedStore:
    """Per-digest cache of payloads that passed envelope() verification.

    Identity is (inode, size, ctime_ns, mtime_ns) read from the record directory's
    own descriptor. ctime changes on every write, rename or metadata change, so a
    rewritten or replaced record never matches its stored identity. A missing,
    corrupt or unreadable store disables the cache for that call; it never
    substitutes a stale payload.
    """

    def __init__(self, home: Path):
        self.home = Path(home)
        self.path = self.home / VERIFIED_STORE
        self._lock = threading.Lock()
        self._connection = None
        self._broken = False

    def _connect(self):
        if self._connection is None and not self._broken:
            try:
                connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE IF NOT EXISTS verified ("
                                   "digest TEXT PRIMARY KEY, ino INTEGER, size INTEGER, "
                                   "ctime_ns INTEGER, mtime_ns INTEGER, verified_at REAL, "
                                   "payload BLOB NOT NULL)")
                connection.commit()
                self._connection = connection
            except sqlite3.Error:
                self._broken = True
        return self._connection

    @staticmethod
    def _identity(directory: int, name: str) -> tuple[int, int, int, int]:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("record is not a regular file")
        return (info.st_ino, info.st_size, info.st_ctime_ns, info.st_mtime_ns)

    def lookup(self, digest: str, directory: int, now: float, *, verify_only: bool = False) -> dict | bool | None:
        with self._lock:
            connection = self._connect()
            if connection is None:
                return None
            try:
                row = connection.execute(
                    "SELECT ino, size, ctime_ns, mtime_ns, verified_at, payload FROM verified WHERE digest=?",
                    (digest,)).fetchone()
            except sqlite3.Error:
                self._broken = True
                return None
        if (row is None or any(type(value) is not int for value in row[:4])
                or type(row[4]) not in (int, float)
                or not isinstance(row[5], bytes)
                or not (0 <= now - row[4] < VERIFIED_MAX_AGE)):
            return None
        try:
            current = self._identity(directory, digest + ".json")
        except (OSError, ValueError):
            return None
        if current != tuple(row[:4]):
            return None
        if verify_only:
            return True
        try:
            payload = json.loads(zlib.decompress(row[5]))
        except (ValueError, zlib.error):
            return None
        return payload if isinstance(payload, dict) else None

    def store(self, rows: list[tuple]) -> None:
        if not rows:
            return
        with self._lock:
            connection = self._connect()
            if connection is None:
                return
            try:
                with connection:
                    connection.executemany(
                        "INSERT OR REPLACE INTO verified VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            except sqlite3.Error:
                self._broken = True


def verified_store(home: Path | str) -> VerifiedStore:
    key = str(Path(home))
    with _STORE_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = _STORES[key] = VerifiedStore(Path(home))
        return store


#: Record kinds persist outside the plant, under the user cache directory, keyed by the
#: site path: classify runs from read-only probes, which must leave the plant untouched.
#: A kind is cached only with the identity the record file had when its head was read,
#: so a hit needs one stat and a changed file misses.
KIND_INDEX_DIR = "mishe-tauftauf"


def kind_index_path(home: Path | str) -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    key = hashlib.sha256(str(Path(home)).encode()).hexdigest()[:24]
    return Path(base) / KIND_INDEX_DIR / f"records-kinds-{key}.sqlite3"


class KindIndex:
    """Per-digest record kind (the kind named by the head), validated by file identity.

    A stored entry is served only when the record's (inode, size, ctime_ns, mtime_ns)
    still equal the identity captured before its head was read. The head is read
    through the descriptor that is fstat'ed, so the identity and the bytes belong to
    the same inode; a rewrite moves ctime and forces the direct read. Anything the
    index cannot answer (missing, corrupt or locked store, non-regular file) falls
    back to the direct head read, which is exactly the uncached answer.
    """

    def __init__(self, home: Path | str):
        self.path = kind_index_path(home)
        self._connection = None
        self._broken = False
        self._loaded = False
        self._known: dict[str, tuple] = {}
        self._pending: dict[str, tuple] = {}

    def _connect(self):
        if self._connection is None and not self._broken:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE IF NOT EXISTS kinds ("
                                   "digest TEXT PRIMARY KEY, ino INTEGER, size INTEGER, "
                                   "ctime_ns INTEGER, mtime_ns INTEGER, kind TEXT)")
                connection.commit()
                self._connection = connection
            except (sqlite3.Error, OSError):
                self._broken = True
        return self._connection

    def _load(self) -> None:
        self._loaded = True
        connection = self._connect()
        if connection is None:
            return
        try:
            for digest, ino, size, ctime_ns, mtime_ns, kind in connection.execute(
                    "SELECT digest, ino, size, ctime_ns, mtime_ns, kind FROM kinds"):
                self._known[digest] = (ino, size, ctime_ns, mtime_ns, kind)
        except sqlite3.Error:
            self._broken = True
            self._known = {}

    def kind(self, directory: int, digest: str) -> str | None:
        """The kind the record's head names, or None, exactly as record_kind(head)."""
        if not self._loaded:
            self._load()
        name = digest + ".json"
        cached = self._known.get(digest)
        if cached is not None:
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except OSError:
                info = None
            if (info is not None and stat.S_ISREG(info.st_mode)
                    and (info.st_ino, info.st_size, info.st_ctime_ns, info.st_mtime_ns) == cached[:4]):
                return cached[4]
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except OSError:
            return None
        try:
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                identity = (info.st_ino, info.st_size, info.st_ctime_ns, info.st_mtime_ns)
                kind = record_kind(handle.read(HEAD_BYTES))
        except OSError:
            return None
        if stat.S_ISREG(info.st_mode):
            entry = (*identity, kind)
            self._known[digest] = entry
            self._pending[digest] = entry
        return kind

    def flush(self) -> None:
        """Persist the entries read since the last flush; a failed write only costs future reads."""
        pending, self._pending = self._pending, {}
        if not pending or self._broken:
            return
        connection = self._connect()
        if connection is None:
            return
        try:
            with connection:
                connection.executemany(
                    "INSERT OR REPLACE INTO kinds VALUES (?, ?, ?, ?, ?, ?)",
                    [(digest, *entry) for digest, entry in pending.items()])
        except sqlite3.Error:
            self._broken = True

    def close(self) -> None:
        self.flush()
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def cached_payload(entry, pending: dict, store: VerifiedStore | None = None, *,
                   verify_only: bool = False, directory: int | None = None) -> dict | None:
    """Return the payload of a single-reference entry, serving verified hits.

    Anything other than one well-formed reference, a legacy JSON control line, or
    a missing site context takes the uncached payload() path, which raises as before.
    A miss runs envelope() in full; its identity is captured before the read so a
    record rewritten mid-read cannot be recorded with a newer identity.
    With verify_only, a fresh matching identity is sufficient: no caller uses the
    cached payload. Payload readers still decode it and reverify on cache damage.
    A caller-supplied ``directory`` fd (a pass-long handle on the record directory)
    is used and left open; the caller must check it still matches the live path.
    """
    lines = entry.body.splitlines()
    refs = [line for line in lines if line.lstrip().startswith("[record]")]
    if (len(lines) > 1 and lines[1].lstrip().startswith("{")) or len(refs) != 1 or entry.home is None:
        return payload(entry)
    match = REFERENCE_RE.fullmatch(refs[0])
    if match is None or match.group(1) != match.group(2):
        return payload(entry)
    digest = match.group(1)
    if digest in pending:
        # Same record referenced again in this pass: decode the blob already verified.
        if verify_only:
            return None
        return json.loads(zlib.decompress(pending[digest][-1]))
    store = store or verified_store(entry.home)
    now = time.time()
    owned = directory is None
    if owned:
        try:
            directory = _directory(Path(entry.home))
        except OSError:
            return payload(entry)
    try:
        hit = store.lookup(digest, directory, now, verify_only=verify_only)
        if hit is not None:
            return None if verify_only else hit
        try:
            before = store._identity(directory, digest + ".json")
        except (OSError, ValueError):
            before = None
    finally:
        if owned:
            os.close(directory)
    record = envelope(entry, None if owned else directory)
    if before is not None:
        blob = zlib.compress(json.dumps(record["payload"], sort_keys=True, separators=(",", ":")).encode(), 6)
        pending[digest] = (digest, *before, now, blob)
    return record["payload"]
