from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .chat_protocol import OBS_RE, decode_lifecycle, receipt_line
from .feed_errors import FeedError
from . import records
from .records import VERIFIED_FLUSH_ROWS, cached_payload, verified_store
from .slugs import validate_slug
from .wall_text import addressed

SOURCE_RE = re.compile(r"[A-Za-z0-9._/-]+\Z")
FRAME_START_RE = re.compile(rb"(?m)^(?:v1 )?\d{20} \d{4}-\d{2}-\d{2}T[^\n]+ [A-Za-z0-9._/-]+ ::\n")
RECEIPT_RE = re.compile(r"wake (?:delivered|refused) top-pain [a-z0-9-]+ for entry [1-9][0-9]*(?: generation=[1-9][0-9]*)?(?: request-id=[A-Za-z0-9._:-]{1,128})?\Z")
INDEX_STRIDE = 128
STREAM_CHUNK_BYTES = 1024 * 1024
"""The block a streaming reader holds at once, independent of the tape length."""
_FRAME_START_TAIL = 256
"""The bytes a frame start can straddle, kept between streaming chunks."""
RESERVED_EXACT = {"mishe-tauftauf"}
RESERVED_PREFIXES = ("prediction/", "observation/")
TASK_CONTROL_TAGS = ("task-state", "task-event", "task-claim", "task-add", "task-close", "task-reopen", "landing")


_PUBLICATION_GUARDS = {}
_PUBLICATION_GUARDS_LOCK = threading.Lock()
_PUBLICATION_DEPTH = threading.local()

# Parsed full-feed reads cost ~0.6s on a multi-megabyte tape and every pane
# re-reads it per frame. Keep one parsed revision per feed path, keyed by the
# index's file metadata, so unchanged frames skip the parse. Any append or
# external rewrite changes inode/size/mtime/ctime and invalidates the entry.
# Entries are (metadata, parsed list, verified). A reader that asks for record
# verification takes only verified revisions; a reader that opts out of record
# verification (verify_records=False) may use either, and an unverified
# revision never replaces a verified one for the same tape revision.
_ENTRIES_CACHE: dict[str, tuple[tuple, list, bool]] = {}
_ENTRIES_CACHE_LOCK = threading.Lock()
_ROLE_ENTRIES_CACHE = OrderedDict()


@contextmanager
def publication_lock(home):
    """Serialize context capture, private admission and publication across writers.

    Reentrant within a thread so one handoff transaction checks both receipts
    before installing its authoritative files. The feed lock remains inference-free.
    """
    key = str(Path(home).resolve())
    with _PUBLICATION_GUARDS_LOCK:
        guard = _PUBLICATION_GUARDS.setdefault(key, threading.RLock())
    with guard:
        depths = getattr(_PUBLICATION_DEPTH, 'depths', None)
        if depths is None:
            depths = _PUBLICATION_DEPTH.depths = {}
        depth = depths.get(key, 0)
        fd = None
        if depth == 0:
            fd = os.open(Path(home) / '.publication.lock', os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        depths[key] = depth + 1
        try:
            yield
        finally:
            if depth:
                depths[key] = depth
            else:
                depths.pop(key, None)
                os.close(fd)


def _reject_record_tag_lines(body: str) -> None:
    """Reject a body that begins a line with the record tag.

    ``_verify_records`` re-resolves tag-prefixed lines on every tape read, so a
    non-canonical such line must never reach the tape. ``append_record`` calls
    this before durably preparing the record, so a rejected publication cannot
    leak an orphan record file.
    """
    for line in body.splitlines():
        if line.lstrip().startswith("[record]"):
            raise FeedError(
                "record explanation must not begin a line with the [record] tag; "
                f"the feed appends the immutable reference itself: {line.strip()!r}")


@dataclass(frozen=True)
class FeedEntry:
    sequence: int
    timestamp: str
    source: str
    body: str
    home: Path | None = field(default=None, compare=False, repr=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode_entry(sequence: int, timestamp: str, source: str, body: str) -> bytes:
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


def entry_timestamp(entry: object) -> datetime | None:
    """Decode a ``FeedEntry``'s ISO timestamp to an aware UTC datetime.

    ``None`` means the stamp is absent or unparseable; a caller that cannot
    bound its interval from the tape must read that as not-clean. Textual
    timestamp interpretation lives on this boundary file, not in consumers.
    """
    timestamp = getattr(entry, "timestamp", None)
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    except (AttributeError, ValueError):
        return None


def parse_feed(data: bytes, *, start_sequence: int = 1, home: Path | str | None = None) -> list[FeedEntry]:
    """Frame whole-tape bytes with mishe-tape.

    The bytes ride an anonymous memfd, so the framer reads them through the same
    ``/proc/self/fd`` path as a tape file and no temporary file is written.
    """
    with _tape_memfd(data) as handle:
        return list(_iter_streamed_feed(handle, start_sequence=start_sequence, home=home, size=len(data)))


@contextmanager
def _tape_memfd(data: bytes):
    fd = os.memfd_create("mishe-tape-feed")
    handle = os.fdopen(fd, "w+b", closefd=True)
    try:
        handle.write(data)
        handle.flush()
        yield handle
    finally:
        handle.close()


class TapeUnknown(RuntimeError):
    """mishe-tape gave a result outside its contract, or no verified binary exists.

    UNKNOWN, never GREEN: a reader that sees this must not fold any entries from
    the read that raised it.
    """


def _tape_binary() -> str:
    """Resolve the mishe-tape framer. Fails closed; never falls back to Python.

    ``MISHE_TAPE_BIN`` is an explicit override (tests, scratch builds). Otherwise
    the site-local install ``$SITE/bin/mishe-tape`` is used, and it must match the
    sha256 recorded beside it in ``mishe-tape.install.json``.
    """
    override = os.environ.get("MISHE_TAPE_BIN")
    if override:
        if not os.path.isfile(override) or not os.access(override, os.X_OK):
            raise TapeUnknown("MISHE_TAPE_BIN names no executable file")
        return override
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        raise TapeUnknown("mishe-tape unavailable: no site home and no MISHE_TAPE_BIN")
    binary = Path(home) / "bin" / "mishe-tape"
    try:
        record = json.loads((binary.parent / "mishe-tape.install.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise TapeUnknown(f"mishe-tape install missing or unreadable: {exc}") from exc
    if not os.access(binary, os.X_OK) or record.get("sha256") != digest:
        raise TapeUnknown("mishe-tape install is not executable or does not match its recorded sha256")
    return str(binary)


def _spawn_frames(handle, *, start_sequence: int, offset: int, size: int, extra=()):
    fd = handle.fileno()
    argv = [_tape_binary(), "frames", f"/proc/self/fd/{fd}",
            "--start", str(start_sequence), "--offset", str(offset), "--size", str(size), *extra]
    # pass_fds keeps the same fd number in the child, so /proc/self/fd/N names the
    # inode the caller captured under the lock, not whatever the path now points to.
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, pass_fds=(fd,), close_fds=True)


def _close_tape(proc, *, complete: bool) -> None:
    """Reap the framer. A reader that stopped early kills it; its exit is not a result."""
    if not complete:
        proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()
        return
    proc.stdout.close()
    rc = proc.wait()
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    proc.stderr.close()
    if rc == 0:
        return
    if rc == 2:
        # Framing fault: the entries before it were already yielded. Same FeedError text as Python.
        raise FeedError(err)
    raise TapeUnknown(f"mishe-tape exit {rc}: {err}")


def _iter_streamed_feed(handle, *, start_sequence: int = 1, home=None,
                        offset: int = 0, size: int | None = None):
    """Yield the tape's entries from a byte range, framed by mishe-tape.

    The framing responsibility lives in ``rust/mishe-tape`` (contract:
    ``rust/mishe-tape/CONTRACT.md``). This reader only parses the framer's
    output: ``E <sequence> <timestamp> <source> <nbytes>`` then the body bytes
    and a newline. The range ends at ``size`` as observed under the tape's
    shared lock, and the bytes come from the caller's open handle, so the inode
    the caller captured is the one framed.
    """
    if size is None:
        size = os.fstat(handle.fileno()).st_size
    proc = _spawn_frames(handle, start_sequence=start_sequence, offset=offset, size=size)
    complete = False
    try:
        stdout = proc.stdout
        while True:
            line = stdout.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                raise TapeUnknown("mishe-tape output truncated mid-header")
            parts = line[:-1].split(b" ", 4)
            if len(parts) != 5 or parts[0] != b"E":
                raise TapeUnknown("mishe-tape emitted a non-contract header")
            try:
                sequence, nbytes = int(parts[1]), int(parts[4])
                timestamp, source = parts[2].decode("ascii"), parts[3].decode("ascii")
            except (ValueError, UnicodeDecodeError) as exc:
                raise TapeUnknown(f"mishe-tape header field invalid: {exc}") from exc
            body = stdout.read(nbytes)
            if len(body) != nbytes or stdout.read(1) != b"\n":
                raise TapeUnknown("mishe-tape body length disagrees with header")
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                # The contract makes non-UTF-8 a framer fault (exit 2), so a body
                # that reaches here is a contract violation: UNKNOWN.
                raise TapeUnknown(f"mishe-tape body is not UTF-8: {exc}") from exc
            yield FeedEntry(sequence, timestamp, source, text,
                            Path(home) if home is not None else None)
        complete = True
    finally:
        _close_tape(proc, complete=complete)


def _frame_offsets(handle, offset: int, size: int, *, start_sequence: int = 1) -> list[int]:
    """The byte offsets of every entry frame in a range, framed by mishe-tape.

    The checkpoint's frame index is the list of entry starts. ``start_sequence``
    must be the sequence the range's first frame carries: a mid-tape range
    (``offset > 0``) starts at the cached sequence, or the framer reports a
    non-contiguous sequence fault.
    """
    proc = _spawn_frames(handle, start_sequence=start_sequence, offset=offset, size=size,
                         extra=("--offsets",))
    offsets: list[int] = []
    complete = False
    try:
        for raw in proc.stdout:
            if not raw.endswith(b"\n"):
                raise TapeUnknown("mishe-tape offset line truncated")
            parts = raw[:-1].split(b" ")
            if len(parts) != 3 or parts[0] != b"O":
                raise TapeUnknown("mishe-tape emitted a non-contract offset line")
            try:
                offsets.append(int(parts[2]))
            except ValueError as exc:
                raise TapeUnknown(f"mishe-tape offset invalid: {exc}") from exc
        complete = True
    finally:
        _close_tape(proc, complete=complete)
    return offsets


def read_text_tail(path: Path | str, chars: int) -> str:
    """The last ``chars`` characters of a UTF-8 text file, read in bounded bytes.

    Equal to ``path.read_text(encoding="utf-8")[-chars:]``: universal newlines
    apply, so ``\\r\\n`` and lone ``\\r`` read as ``\\n``. UTF-8 spends at most
    four bytes per character, so the final ``4 * chars`` bytes hold every raw
    character of the tail; newline translation can only shorten that, so the
    window widens until the translated text holds ``chars`` characters or the
    file start is reached. The first character of a window that does not start
    at the file start may be a split sequence, so it is dropped before slicing.
    An unreadable file reads as empty.
    """
    if chars <= 0:
        return ""
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, 2)
            window = 4 * chars
            while True:
                start = max(0, size - window)
                handle.seek(start)
                raw = handle.read(size - start)
                text = raw.decode("utf-8", errors="replace")
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                if start == 0:
                    return text[-chars:]
                if len(text) > chars:
                    return text[1:][-chars:]
                window *= 2
    except OSError:
        return ""


def iter_feed_file(path: Path | str, *, home: Path | str | None = None):
    """Yield the tape's entries without holding the tape in memory.

    The prefix it reads is captured under the tape's shared lock, so it ends
    at an entry boundary even while a writer appends, and the bytes it reads
    are the ones that were on the tape when it looked. The lock is released
    before framing, so a writer's exclusive lock excludes this reader only for
    the capture. Framing is done by mishe-tape (``rust/mishe-tape``).
    """
    path = Path(path)
    with path.open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            size = os.fstat(handle.fileno()).st_size
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        yield from _iter_streamed_feed(handle, home=home, size=size)


def _role_fold(previous, entries, role):
    old = previous or {}
    row = {"control": list(old.get("control", [])), "inbox": deque(old.get("inbox", ()), maxlen=12),
           "recent": deque(old.get("recent", ()), maxlen=12),
           "sources": dict(old.get("sources", {})), "yield": old.get("yield")}
    for entry in entries:
        if entry.source == "seed":
            try:
                match = OBS_RE.fullmatch(receipt_line(entry.body))
                control = None if match else decode_lifecycle(entry)
                keep = match.group(1) == role if match else control.role == role if control else True
            except (ValueError, IndexError):
                keep, control = True, None  # preserve original malformed-control behavior
            if keep:
                row["control"].append(entry)
            if control and control.kind == "yield":
                stamp = entry_timestamp(entry)
                if stamp is None:
                    row["control"].append(entry)
                else:
                    candidate = (stamp, f"chat.log {entry.sequence} {entry.source}", entry)
                    if row["yield"] is None or candidate[:2] > row["yield"][:2]:
                        row["yield"] = candidate
        else:
            row["recent"].append(entry)
            stamp = entry_timestamp(entry)
            if stamp is None:
                row["control"].append(entry)
            else:
                candidate = (stamp, f"chat.log {entry.sequence} {entry.source}", entry)
                previous_source = row["sources"].get(entry.source)
                if previous_source is None or candidate[:2] > previous_source[:2]:
                    row["sources"][entry.source] = candidate
        if addressed(entry, role):
            row["inbox"].append(entry)
    return row


def _role_view(row):
    selected = {e.sequence: e for e in (*row["control"], *row["inbox"], *row["recent"])}
    for item in row["sources"].values():
        selected[item[2].sequence] = item[2]
    if row["yield"]:
        selected[row["yield"][2].sequence] = row["yield"][2]
    return [selected[n] for n in sorted(selected)]


class Feed:
    """Observation-driven coordination substrate — Pugachev 2025 CodeCRDT §3.2.

    This feed provides the three substrate requirements for observation-driven
    coordination (not explicit message passing):

    (i) observable updates — every ``append`` is observed via ``entries()`` /
        ``role_entries()`` / ``chat.log`` tail (``wall.mind_block`` / ``wall.context``
        poll this substrate; ``wall.message()`` DMs are secondary);
    (ii) deterministic convergence — append-only, sequence-total-ordered per host,
        digest-normalized via ``_digest`` / ``is_peer_copy``
        (``wall_text.document_digest`` SHA-256), no merge divergence;
    (iii) monotonic progress — audit log never rolls back (``_audit_log`` /
        ``record_dispatch_receipt`` never removes committed entries).

    ``wall.py:publication_lock`` (``threading.RLock`` + ``fcntl.flock``) is the
    local OT-like centralized arbiter for writers on one host, not SEC; its
    liveness failure is ``all render slots occupied → LIMITED`` shedding as seen
    on the dashboard cgroup high. SEC convergence here is character-level (feed
    frames) — semantic conflicts (duplicate patch lanes, type mismatches) remain
    5–10% and need reconciliation, per §6.1.
    """
    def __init__(self, home: Path | str):
        self.home = Path(home)
        # Existing sites retain the old tape until their active writers exit.
        # New and migrated sites use their own local chat.log.
        self.path = self.home / ("chat.log" if (self.home / "chat.log").exists()
                                 or not (self.home / "feed").exists() else "feed")
        self.index_path = self.home / "feed.index.json"
        self.identity_path = self.home / "feed.identities.sqlite3"

    @staticmethod
    def _digest(source: str, body: str) -> bytes:
        return hashlib.sha256(source.encode("utf-8") + b"\0" + body.encode("utf-8")).digest()

    @staticmethod
    def _malformed_receipt(source: str, body: str) -> bool:
        return (source == "mishe-tauftauf" and body.startswith(("wake delivered ", "wake refused "))
                and RECEIPT_RE.fullmatch(body) is None)

    def _rebuild_identities(self, handle, index: dict) -> None:
        handle.seek(0)
        entries = parse_feed(handle.read())
        if len(entries) != index["sequence"]:
            raise FeedError("identity rebuild disagrees with feed checkpoint")
        fd, temporary = tempfile.mkstemp(prefix=".feed.identities.", suffix=".sqlite3", dir=self.home)
        os.close(fd)
        try:
            connection = sqlite3.connect(temporary)
            try:
                with connection:
                    connection.execute("PRAGMA synchronous=FULL")
                    connection.execute("CREATE TABLE identities (digest BLOB PRIMARY KEY, sequence INTEGER NOT NULL)")
                    connection.execute("CREATE TABLE meta (sequence INTEGER NOT NULL, metadata TEXT NOT NULL, malformed INTEGER NOT NULL)")
                    for entry in entries:
                        connection.execute("INSERT OR IGNORE INTO identities VALUES (?, ?)",
                                           (self._digest(entry.source, entry.body), entry.sequence))
                    malformed = sum(self._malformed_receipt(e.source, e.body) for e in entries)
                    connection.execute("INSERT INTO meta VALUES (?, ?, ?)",
                                       (index["sequence"], json.dumps(index["metadata"]), malformed))
            finally:
                connection.close()
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.identity_path)
            directory = os.open(self.home, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _identity_connection(self, handle, index: dict) -> sqlite3.Connection:
        connection = None
        try:
            if not self.identity_path.exists():
                self._rebuild_identities(handle, index)
            # A zero-byte file is a torn or aborted rebuild: the writer that created
            # it crashed (or a reader merely opened the path) before any table existed.
            # Recover by rebuilding from the canonical feed instead of failing every append.
            elif self.identity_path.stat().st_size == 0:
                self.identity_path.unlink()
                self._rebuild_identities(handle, index)
            connection = sqlite3.connect(self.identity_path)
            row = connection.execute("SELECT sequence, metadata, malformed FROM meta").fetchone()
            if row is None or not isinstance(row[2], int) or row[2] < 0:
                raise sqlite3.DatabaseError("identity metadata missing")
            if row[0] != index["sequence"] or row[1] != json.dumps(index["metadata"]):
                connection.close()
                self._rebuild_identities(handle, index)
                connection = sqlite3.connect(self.identity_path)
            return connection
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise FeedError(f"corrupt feed identity index: {exc}") from exc

    def _indexed_entry(self, handle, index: dict, sequence: int) -> FeedEntry:
        if not isinstance(sequence, int) or not 1 <= sequence <= index["sequence"]:
            raise FeedError("identity index sequence outside canonical feed")
        first_seq, first_offset = max((seq, offset) for seq, offset in index["checkpoints"] if seq <= sequence)
        end_offset = next((offset for seq, offset in index["checkpoints"] if seq > sequence), index["metadata"][1])
        handle.seek(first_offset)
        entries = parse_feed(handle.read(end_offset - first_offset), start_sequence=first_seq, home=self.home)
        found = next((entry for entry in entries if entry.sequence == sequence), None)
        if found is None:
            raise FeedError("identity index points outside canonical feed")
        return found

    def _lookup_identity(self, connection, handle, index: dict, source: str, body: str) -> FeedEntry | None:
        row = connection.execute("SELECT sequence FROM identities WHERE digest=?", (self._digest(source, body),)).fetchone()
        if row is None:
            return None
        entry = self._indexed_entry(handle, index, row[0])
        if entry.source != source or entry.body != body:
            raise FeedError("identity index disagrees with canonical feed")
        return entry

    def read_bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b""

    @staticmethod
    def _metadata(handle) -> list[int]:
        stat = os.fstat(handle.fileno())
        return [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]

    def _save_index(self, handle, index: dict, metadata: list[int] | None = None) -> None:
        # A checkpoint rebuilt outside the tape lock describes the bytes that
        # were captured, not the file as it is now. Record that captured
        # metadata so the checkpoint keeps describing one exact revision.
        index["metadata"] = list(metadata) if metadata is not None else self._metadata(handle)
        payload = json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
        wrapper = {"payload": index, "sha256": hashlib.sha256(payload).hexdigest()}
        fd, temporary = tempfile.mkstemp(prefix=".feed.index.", dir=self.home)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(json.dumps(wrapper, sort_keys=True).encode())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.index_path)
            directory = os.open(self.home, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _index(self, handle) -> dict:
        index, _ = self._load_index(handle)
        return index

    def _assert_locked_path(self, handle) -> None:
        """Refuse a tape whose path was replaced while the handle was open."""
        if os.stat(self.path).st_ino != os.fstat(handle.fileno()).st_ino:
            raise FeedError("feed path changed while locked")

    def _read_checkpoint(self) -> dict | None:
        """Return the on-disk checkpoint when it is well formed, else None.

        None means the checkpoint is absent or was torn by an interrupted write,
        so the canonical tape has to be parsed to rebuild it. A checkpoint that
        is present but inconsistent is a corruption and raises, so a damaged
        checkpoint still fails closed instead of being silently replaced.
        """
        try:
            wrapper = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            wrapper = None
        except (ValueError, OSError):
            # A zero-byte or truncated checkpoint is a torn or aborted rebuild: the
            # writer that created it crashed before any payload was written, or an
            # out-of-band reader merely opened the path. The canonical tape is intact,
            # so rebuild instead of failing every read and append on that home.
            wrapper = None
        if wrapper is None:
            return None
        try:
            index = wrapper["payload"]
            payload = json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
            if wrapper["sha256"] != hashlib.sha256(payload).hexdigest() or index["version"] != 1:
                raise ValueError("checksum or version mismatch")
            if not isinstance(index["sequence"], int) or not isinstance(index["tail_offset"], int):
                raise ValueError("invalid cursor")
            checkpoints = index["checkpoints"]
            if (not isinstance(checkpoints, list) or
                checkpoints != [[seq, offset] for seq, offset in checkpoints] or
                (index["sequence"] == 0) != (len(checkpoints) == 0) or
                (checkpoints and checkpoints[0] != [1, 0]) or
                any(seq != 1 + pos * INDEX_STRIDE or not isinstance(offset, int) or offset < 0
                    for pos, (seq, offset) in enumerate(checkpoints)) or
                index["tail_offset"] < 0 or index["tail_offset"] > index["metadata"][1]):
                raise ValueError("invalid checkpoints")
            return index
        except (KeyError, TypeError, ValueError) as exc:
            raise FeedError(f"corrupt feed index: {exc}") from exc

    def _load_index(self, handle) -> tuple[dict, bool]:
        """Return the checkpoint and whether it already described this exact file.

        A reused checkpoint is the writer's own record that the tape reached its
        current size through the canonical append that rewrote it. A rebuild means
        the bytes changed without that record, so growth is not necessarily an
        append and a cached read must not be extended.
        """
        index = self._read_checkpoint()
        self._assert_locked_path(handle)
        if index is not None and index["metadata"] == self._metadata(handle):
            return index, True
        handle.seek(0)
        data = handle.read()
        entries = parse_feed(data)
        offsets = [match.start() for match in FRAME_START_RE.finditer(data)]
        if len(offsets) != len(entries):
            raise FeedError("feed frame index disagrees with canonical parser")
        index = {"version": 1, "sequence": len(entries),
                 "tail_offset": offsets[-1] if offsets else 0,
                 "checkpoints": [[entry.sequence, offsets[n]] for n, entry in enumerate(entries) if n % INDEX_STRIDE == 0]}
        self._save_index(handle, index)
        return index, False

    def _publish_checkpoint(self, index: dict, metadata: list[int]) -> None:
        """Record a rebuilt checkpoint without regressing a newer revision.

        The rebuild happens outside the tape lock, so the file may have grown
        since the bytes were captured, or the path may have been replaced. Save
        under the exclusive lock, and only when the checkpoint on disk does not
        already describe a later revision and the file is still the exact
        revision whose bytes were parsed. A corrupt checkpoint raises instead
        of being silently replaced: it fails closed, as a reader that held the
        shared lock across its own checkpoint save did.
        """
        with self.path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            current = self._read_checkpoint()
            if current is not None:
                described = current["metadata"]
                if described[0] != metadata[0] or described[1] > metadata[1]:
                    return
            if self._metadata(handle) != list(metadata):
                # The tape grew or was replaced while the reader parsed: the
                # rebuilt index describes bytes that are no longer the whole
                # file, so it must not be published as this file's checkpoint.
                return
            self._save_index(handle, index, metadata)

    @staticmethod
    def _verify_records(entries, sink: dict | None = None) -> None:
        # Verified payloads persist per site (records.VerifiedStore). A hit is served
        # only while the referenced file keeps its verified identity; a miss runs the
        # full envelope verification and is stored after the whole pass succeeds.
        # One record-directory fd serves the whole pass for the first entry's home, so the
        # directory is not reopened per entry. Entries under any other home open their own.
        # Every flush and the pass end check that the live path still names that directory;
        # a swapped records directory fails the pass instead of storing the old inode.
        pending: dict = {}
        store = None
        home = None
        passfd = None
        try:
            for entry in entries:
                if any(line.lstrip().startswith("[record]") for line in entry.body.splitlines()):
                    if store is None and entry.home is not None:
                        store = verified_store(entry.home)
                        home = Path(entry.home)
                        try:
                            passfd = records._directory(home)
                        except OSError:
                            passfd = None
                    shared = passfd if passfd is not None and entry.home is not None and Path(entry.home) == home else None
                    data = cached_payload(entry, pending, store, verify_only=sink is None, directory=shared)
                    if sink is not None:
                        sink[entry.sequence] = data
                    if store is not None and len(pending) >= VERIFIED_FLUSH_ROWS:
                        # Flush as the pass goes: a render killed by its supervisor keeps
                        # the verifications it finished, so the next render starts ahead.
                        if passfd is not None:
                            records.check_pass_directory(passfd, home)
                        store.store(list(pending.values()))
                        pending.clear()
            if store is not None:
                if passfd is not None:
                    records.check_pass_directory(passfd, home)
                store.store(list(pending.values()))
        finally:
            if passfd is not None:
                os.close(passfd)

    @staticmethod
    def _store_entries(path: Path | str, metadata, entries: list, verified: bool) -> None:
        key = str(path)
        with _ENTRIES_CACHE_LOCK:
            held = _ENTRIES_CACHE.get(key)
            if (not verified and held is not None and held[2]
                    and tuple(held[0]) == tuple(metadata)):
                return
            _ENTRIES_CACHE[key] = (metadata, entries, verified)

    def _extend_entries(self, cached, data: bytes, metadata,
                        verify_records: bool = True) -> list[FeedEntry] | None:
        """Continue a cached full read with the frames appended since it was taken.

        A live pane re-reads the whole tape every tick, so replaying the entire
        history makes its cost grow with the plant. The tape is append-only under
        the feed lock, so reading only the new suffix keeps that cost proportional
        to what changed. Return None unless the suffix is a contiguous continuation
        of the cached revision, so a rewrite or truncation is never read as an
        append and the caller falls back to a full parse. The suffix bytes are
        captured under the tape lock and parsed after it is released.
        """
        previous, entries = cached[0], cached[1]
        if not entries or previous[0] != metadata[0] or previous[1] >= metadata[1]:
            return None
        try:
            parsed = parse_feed(data, start_sequence=entries[-1].sequence + 1, home=self.home)
        except FeedError:
            return None
        if not parsed or parsed[0].sequence != entries[-1].sequence + 1:
            return None
        if verify_records:
            self._verify_records(parsed)
        return entries + parsed

    def _capture(self, handle, start: int, limit: int | None, allow_extend: bool = True,
                 verified: bool = True):
        """Capture the bytes a reader will parse, under the tape's shared lock.

        The tape is append-only and the writer's exclusive lock excludes every
        reader, so the captured bytes are a consistent revision of the tape. The
        shared lock covers this capture only: parsing, verification and a
        checkpoint rebuild happen after it is released, so a cold full-tape read
        no longer holds the writer off for the length of a parse.

        Returns (mode, data, span). The modes are "extend" (only the appended
        suffix changed since a cached revision), "range" (the byte range the
        checkpoint names) and "rebuild" (the checkpoint cannot describe the file,
        so the whole tape was captured).
        """
        self._assert_locked_path(handle)
        checkpoint = self._read_checkpoint()
        metadata = tuple(self._metadata(handle))
        if checkpoint is None or tuple(checkpoint["metadata"]) != metadata:
            handle.seek(0)
            return ("rebuild", handle.read(), (1, None))
        index = checkpoint
        if start > index["sequence"]:
            return ("empty", b"", (1, index["sequence"]))
        cached = None
        if start == 1 and limit is None:
            with _ENTRIES_CACHE_LOCK:
                cached = _ENTRIES_CACHE.get(str(self.path))
            # An unverified revision cannot serve a reader that requires verification.
            if cached is not None and verified and not cached[2]:
                cached = None
            if cached is not None and tuple(cached[0]) == metadata:
                return ("cached", b"", cached)
        if allow_extend and cached is not None and cached[0][0] == metadata[0] and cached[0][1] < metadata[1]:
            handle.seek(cached[0][1])
            return ("extend", handle.read(metadata[1] - cached[0][1]), cached)
        checkpoints = index["checkpoints"]
        first_seq, first_offset = max((seq, offset) for seq, offset in checkpoints if seq <= start)
        target = index["sequence"] if limit is None else min(index["sequence"], start + limit - 1)
        end_offset = next((offset for seq, offset in checkpoints if seq > target), metadata[1])
        handle.seek(first_offset)
        return ("range", handle.read(end_offset - first_offset), (first_seq, target))

    def role_entries(self, role: str, *, _force_full=False):
        """Verified role control/view projection, without retaining the whole tape.

        Every captured record is verified before pruning. Keep the role's full
        lifecycle, unknown seed evidence, twelve addressed/recent messages and
        global activity witnesses. Full history remains available via entries().
        """
        validate_slug(role)
        key = (str(self.path), role)
        if not self.path.exists():
            return _role_view(_role_fold(None, self.entries(), role))
        handle = self.path.open("rb")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                self._assert_locked_path(handle)
                checkpoint = self._read_checkpoint()
                metadata = tuple(self._metadata(handle))
                valid = checkpoint is not None and tuple(checkpoint["metadata"]) == metadata
                with _ENTRIES_CACHE_LOCK:
                    cached = _ROLE_ENTRIES_CACHE.get(key) if not _force_full else None
                    if cached is not None and valid and cached["metadata"] == metadata:
                        _ROLE_ENTRIES_CACHE.move_to_end(key)
                        full = _ENTRIES_CACHE.get(str(self.path))
                        if full and tuple(full[0]) == metadata:
                            _ENTRIES_CACHE.pop(str(self.path), None)
                        return list(cached["view"])
                extending = (cached is not None and valid and cached["metadata"][0] == metadata[0]
                             and cached["metadata"][1] < metadata[1])
                start = cached["tail"] + 1 if extending else 1
                offset = cached["metadata"][1] if extending else 0
                size = metadata[1]
            finally:
                # The lock covers the capture only. The bytes this reader
                # parses are bounded by the size it captured, so a writer
                # appending now cannot shift them.
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            tail = start - 1
            # The tape is read as a bounded stream, not as one string. Reading
            # it whole held the raw bytes and every decoded copy alive at
            # once, so a resident supervisor's peak grew with the tape: a
            # 184 MB tape cost 21 s and 466 MB of peak RSS on this plant, and
            # two such supervisors sharing the 3 GiB lifeline slice were
            # killed by the cgroup OOM (2026-11-03 13:53Z, oom_kill 47->48).
            # The stream keeps one chunk and one entry at a time, so the peak
            # depends on the chunk size rather than the tape length.
            def verified():
                nonlocal tail
                for entry in _iter_streamed_feed(handle, start_sequence=start, home=self.home,
                                                 offset=offset, size=size):
                    self._verify_records((entry,))
                    tail = entry.sequence
                    yield entry
            try:
                projection = _role_fold(cached["projection"] if extending else None, verified(), role)
            except FeedError:
                if extending:
                    return self.role_entries(role, _force_full=True)
                raise
            if valid and tail != checkpoint["sequence"]:
                raise FeedError("role view disagrees with canonical checkpoint")
            if not valid:
                offsets = _frame_offsets(handle, offset, size, start_sequence=start)
                if len(offsets) != tail:
                    raise FeedError("role view frame index disagrees with canonical parser")
                index = {"version": 1, "sequence": tail, "tail_offset": offsets[-1] if offsets else 0,
                         "checkpoints": [[n + 1, offsets[n]] for n in range(0, tail, INDEX_STRIDE)]}
                self._publish_checkpoint(index, list(metadata))
        finally:
            handle.close()
        view = tuple(_role_view(projection))
        with _ENTRIES_CACHE_LOCK:
            current = _ROLE_ENTRIES_CACHE.get(key)
            if not (current and current["metadata"][0] == metadata[0] and current["metadata"][1] > metadata[1]):
                _ROLE_ENTRIES_CACHE[key] = {"metadata": metadata, "tail": tail,
                                            "projection": projection, "view": view}
                _ROLE_ENTRIES_CACHE.move_to_end(key)
                while len(_ROLE_ENTRIES_CACHE) > 8:
                    _ROLE_ENTRIES_CACHE.popitem(last=False)
            full = _ENTRIES_CACHE.get(str(self.path))
            if full and tuple(full[0]) == metadata:
                _ENTRIES_CACHE.pop(str(self.path), None)
        return list(view)

    def view_entries(self, *, start: int = 1, limit: int = 200) -> list[FeedEntry]:
        """Bounded indexed presentation read; never rebuild or publish an index.

        Missing/stale checkpoints are unavailable evidence. The canonical writer
        repairs its own index; a presentation must not mutate evidence to read it.
        """
        if start < 1 or not 0 < limit <= 1000:
            raise ValueError("view range must be positive and limited to 1000 entries")
        with self.path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            self._assert_locked_path(handle)
            checkpoint = self._read_checkpoint()
            if checkpoint is None or tuple(checkpoint["metadata"]) != tuple(self._metadata(handle)):
                raise FeedError("view checkpoint missing or stale; mail UNKNOWN")
            if start > checkpoint["sequence"]:
                return []
            first, offset = max((seq, offset) for seq, offset in checkpoint["checkpoints"] if seq <= start)
            target = min(checkpoint["sequence"], start + limit - 1)
            end = next((offset for seq, offset in checkpoint["checkpoints"] if seq > target),
                       checkpoint["metadata"][1])
            handle.seek(offset)
            data = handle.read(end - offset)
        entries = [entry for entry in parse_feed(data, start_sequence=first, home=self.home)
                   if start <= entry.sequence <= target]
        self._verify_records(entries)
        return entries

    def entries(self, *, start: int = 1, limit: int | None = None,
                payloads: dict | None = None, verify_records: bool = True) -> list[FeedEntry]:
        """Parse the tape. verify_records=False skips record payload verification
        for a caller that reads only plain-text entries and verifies any record it
        consumes itself; the parse and tape-level checks still run."""
        if start < 1 or limit is not None and limit < 0:
            raise ValueError("start must be positive and limit nonnegative")
        if not self.path.exists():
            if self.index_path.exists():
                raise FeedError("feed missing while checkpoint exists")
            return []
        if limit == 0:
            return []
        allow_extend = True
        while True:
            with self.path.open("rb") as handle:
                # A shared lock keeps concurrent readers concurrent; the writer's
                # LOCK_EX still excludes every reader. The lock is held only for
                # the capture: parsing and verification follow after it is
                # released, so a cold full-tape read does not starve the writer.
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                mode, data, span = self._capture(handle, start, limit, allow_extend, verify_records)
                metadata = tuple(self._metadata(handle))
            if mode == "cached":
                return list(span[1])
            if mode == "empty":
                return []
            if mode == "extend":
                extended = self._extend_entries(span, data, metadata, verify_records)
                if extended is not None:
                    self._store_entries(self.path, metadata, extended, verify_records)
                    return list(extended)
                # The suffix was not a contiguous continuation of the cached
                # revision. Read the whole revision instead of splicing it.
                allow_extend = False
                continue
            if mode == "rebuild":
                entries = parse_feed(data, home=self.home)
                offsets = [match.start() for match in FRAME_START_RE.finditer(data)]
                if len(offsets) != len(entries):
                    raise FeedError("feed frame index disagrees with canonical parser")
                index = {"version": 1, "sequence": len(entries),
                         "tail_offset": offsets[-1] if offsets else 0,
                         "checkpoints": [[entry.sequence, offsets[n]] for n, entry in enumerate(entries) if n % INDEX_STRIDE == 0]}
                self._publish_checkpoint(index, list(metadata))
                target = index["sequence"] if limit is None else min(index["sequence"], start + limit - 1)
                result = [entry for entry in entries if start <= entry.sequence <= target]
            else:
                first_seq, target = span
                selected = parse_feed(data, start_sequence=first_seq, home=self.home)
                result = [entry for entry in selected if start <= entry.sequence <= target]
            # Fill the sink only on a full verify pass; a cache hit leaves it
            # empty so the caller's projection falls back to per-entry
            # resolution, exactly as before.
            if verify_records:
                self._verify_records(result, payloads)
            if start == 1 and limit is None:
                self._store_entries(self.path, metadata, list(result), verify_records)
            return result

    def tail_sequence(self) -> int:
        """Return the verified canonical tail without replaying a current feed."""
        if not self.path.exists():
            if self.index_path.exists():
                raise FeedError("feed missing while checkpoint exists")
            return 0
        with self.path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            self._assert_locked_path(handle)
            checkpoint = self._read_checkpoint()
            metadata = tuple(self._metadata(handle))
            if checkpoint is None or tuple(checkpoint["metadata"]) != metadata:
                handle.seek(0)
                data = handle.read()
                index = None
            else:
                index = checkpoint
                data = b""
                if index["sequence"]:
                    handle.seek(index["tail_offset"])
                    data = handle.read()
        if index is None:
            entries = parse_feed(data, home=self.home)
            offsets = [match.start() for match in FRAME_START_RE.finditer(data)]
            if len(offsets) != len(entries):
                raise FeedError("feed frame index disagrees with canonical parser")
            index = {"version": 1, "sequence": len(entries),
                     "tail_offset": offsets[-1] if offsets else 0,
                     "checkpoints": [[entry.sequence, offsets[n]] for n, entry in enumerate(entries) if n % INDEX_STRIDE == 0]}
            self._publish_checkpoint(index, list(metadata))
            return index["sequence"]
        if index["sequence"]:
            tail = parse_feed(data, start_sequence=index["sequence"])
            if len(tail) != 1:
                raise FeedError("feed tail disagrees with checkpoint")
        return index["sequence"]

    def append(self, source: str, body: str, **kwargs) -> FeedEntry:
        from .observations import validate_home
        validate_home(self.home)
        with publication_lock(self.home):
            return self._append(source, body, **kwargs)

    def _append(self, source: str, body: str, *, reserved: bool = False, once: bool = False,
               request: str | None = None, conflicting: str | None = None,
               task_control: bool = False, context: dict | None = None, commit_guard=None) -> FeedEntry:
        if not SOURCE_RE.fullmatch(source):
            raise FeedError("source must match [A-Za-z0-9._/-]+")
        if not reserved and (source in RESERVED_EXACT or source.startswith(RESERVED_PREFIXES)):
            raise FeedError(f"source {source!r} is reserved for the runtime")
        if not isinstance(body, str) or not body:
            raise FeedError("body must be non-empty UTF-8 text")
        if any(body.lstrip().startswith(f"[{tag}]") for tag in TASK_CONTROL_TAGS):
            if not task_control:
                raise FeedError("reserved task control; use the task CLI for structured operations")
            from .task_state import validate_control
            from .records import payload
            if any(line.lstrip().startswith("[record]") for line in body.splitlines()):
                first = body.splitlines()[0]
                data = payload(FeedEntry(0, "", source, body, self.home))
                validate_control(source, first + "\n" + json.dumps(data))
            else:
                validate_control(source, body)
        try:
            body.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise FeedError(f"body is not UTF-8 encodable: {exc}") from exc
        if "\r" in body:
            # Carriage returns are valid Unicode text, but canonical line framing is LF-only.
            # Preserve them as content rather than accepting CRLF as ambiguous framing.
            pass
        from .observations import validate_home
        validate_home(self.home)
        record_data = None
        if any(line.lstrip().startswith("[record]") for line in body.splitlines()):
            from .records import payload
            record_data = payload(FeedEntry(0, "", source, body, self.home))
            if context is None:
                context = record_data
        # Walls and chat do not need semantic publication admission. Historical
        # checker configuration cannot turn authorized work into a model gate.
        from .wall import settings
        settings(self.home)
        from .post_check import require_prose
        require_prose(self.home, source, body)
        if commit_guard is not None:
            commit_guard()
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with os.fdopen(fd, "r+b", buffering=0) as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                index = self._index(handle)
                connection = self._identity_connection(handle, index)
                try:
                    if request is not None:
                        malformed = connection.execute("SELECT malformed FROM meta").fetchone()[0]
                        if malformed:
                            raise FeedError("malformed dispatch receipt in feed")
                        if self._lookup_identity(connection, handle, index, source, request) is None:
                            raise FeedError("dispatch receipt requires an existing wake request")
                        if conflicting is not None and self._lookup_identity(connection, handle, index, source, conflicting):
                            raise FeedError("conflicting dispatch receipt for request")
                    if once:
                        existing = self._lookup_identity(connection, handle, index, source, body)
                        if existing is not None:
                            return existing
                    if index["sequence"]:
                        handle.seek(index["tail_offset"])
                        parse_feed(handle.read(), start_sequence=index["sequence"])
                    if record_data is not None:
                        from .records import payload
                        if payload(FeedEntry(0, "", source, body, self.home)) != record_data:
                            raise FeedError("record changed during publication checks; reconcile before posting")
                    sequence = index["sequence"] + 1
                    timestamp = utc_now()
                    encoded = _encode_entry(sequence, timestamp, source, body)
                    handle.seek(0, os.SEEK_END)
                    end_offset = handle.tell()
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                    if (sequence - 1) % INDEX_STRIDE == 0:
                        index["checkpoints"].append([sequence, end_offset])
                    index["tail_offset"] = end_offset
                    index["sequence"] = sequence
                    index["metadata"] = self._metadata(handle)
                    try:
                        with connection:
                            connection.execute("INSERT OR IGNORE INTO identities VALUES (?, ?)",
                                               (self._digest(source, body), sequence))
                            connection.execute("UPDATE meta SET sequence=?, metadata=?, malformed=malformed+?",
                                               (sequence, json.dumps(index["metadata"]),
                                                int(self._malformed_receipt(source, body))))
                    except sqlite3.DatabaseError as exc:
                        raise FeedError(f"feed persisted but identity index update failed: {exc}") from exc
                    self._save_index(handle, index)
                    return FeedEntry(sequence, timestamp, source, body, self.home)
                finally:
                    connection.close()
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def append_runtime(self, source: str, body: str) -> FeedEntry:
        return self.append(source, body, reserved=True)

    def append_task_control(self, source: str, body: str, *, commit_guard=None) -> FeedEntry:
        """Structured task operations own these tags; ordinary chat cannot emit them."""
        from .task_state import validate_control
        from .records import payload
        validate_control(source, body)
        lines = body.splitlines()
        if len(lines) > 1 and lines[1].lstrip().startswith("{"):
            data = payload(FeedEntry(0, "", source, body))
            explanation = "\n".join(lines[2:]).strip()
            if not explanation:
                explanation = str(data.get("reason") or data.get("progress") or data.get("next_step") or "Recorded task operation.")
                if data.get("evidence"):
                    explanation += f" Evidence: {data['evidence']}."
            return self.append_record(source, lines[0] + "\n" + explanation, data,
                                      kind=lines[0].split()[0].strip("[]"), task_control=True, commit_guard=commit_guard)
        return self.append(source, body, task_control=True, commit_guard=commit_guard)

    def append_record(self, source: str, body: str, payload: dict, *, kind: str = "event",
                      **append_kwargs) -> FeedEntry:
        """Persist structured evidence before publishing its readable reference."""
        from .records import prepare
        if not isinstance(body, str) or not body.strip():
            raise FeedError("record explanation must be non-empty text")
        _reject_record_tag_lines(body)
        ref = prepare(self.home, payload, kind=kind)
        append_kwargs.setdefault("context", payload)
        return self.append(source, body.rstrip("\n") + "\n" + ref, **append_kwargs)

    def append_runtime_once(self, source: str, body: str) -> FeedEntry:
        """Append one exact textual receipt atomically across cooperating writers."""
        return self.append(source, body, reserved=True, once=True)

    def record_dispatch_receipt(self, slug: str, sequence: int, outcome: str,
                                *, request_id: str | None = None, generation: int | None = None) -> FeedEntry:
        if outcome not in ("delivered", "refused"):
            raise FeedError("invalid dispatch outcome")
        if request_id is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            raise FeedError("invalid dispatch request ID")
        if (request_id is None) != (generation is None):
            raise FeedError("generation and request ID must be supplied together")
        if generation is not None and generation < 1:
            raise FeedError("dispatch generation must be positive")
        suffix = (f" generation={generation}" if generation is not None else "") + (f" request-id={request_id}" if request_id is not None else "")
        request = f"wake requested top-pain {slug} for entry {sequence}"
        opposite = "refused" if outcome == "delivered" else "delivered"
        return self.append(
            "mishe-tauftauf", f"wake {outcome} top-pain {slug} for entry {sequence}{suffix}",
            reserved=True, once=True, request=request,
            conflicting=f"wake {opposite} top-pain {slug} for entry {sequence}{suffix}",
        )
def observation_overhead_estimate(n_agents: int, updates: int) -> int:
    """O(N×U) observation overhead — Pugachev 2025 §4.2.

    Each of ``U`` CRDT/feed updates triggers callbacks in all ``N`` agents.
    Returns ``N * U`` for dashboard annotation; not a limit or SLO. Pure, no I/O.
    """
    if not isinstance(n_agents, int) or not isinstance(updates, int):
        raise ValueError("n_agents and updates must be integers")
    if n_agents < 0 or updates < 0:
        raise ValueError("n_agents and updates must be non-negative")
    return n_agents * updates


def coupling_score(claims: list[dict]) -> float:
    """Heuristic coupling — fraction of claims touching shared state, §5.2.1.

    ``coupling = shared_state_claims / total_claims`` where two claims share
    state if they target the same wall file / same ``target:`` path / same
    board row. Paper threshold: <30% → parallel wins, >50% → sequential wins.
    Pure, no I/O. Empty input → 0.0.
    Operationalizes the paper's post-hoc manual coupling inspection as a
    target-path overlap gauge; not a static data-flow proof.
    Example: ``coupling_score([{\"target\":\"a.py\"},{\"target\":\"a.py\"}]) == 0.5``
    (one duplicate / two claims).
    """
    if not claims:
        return 0.0
    def _target(claim: dict) -> str | None:
        if not isinstance(claim, dict):
            return None
        for key in ("target", "wall", "path", "board_row", "file", "row"):
            value = claim.get(key)
            if isinstance(value, str) and value:
                return value
        return None
    targets = [_target(c) for c in claims]
    n = len(claims)
    from collections import Counter
    counts: Counter = Counter(t for t in targets if t is not None)
    duplicates = sum(cnt - 1 for cnt in counts.values() if cnt > 1)
    return duplicates / n if n else 0.0


def claim_with_verify(entries: list, todo_id: str, agent: str) -> bool:
    """Scan–claim–verify LWW — at-most-one claim, Pugachev 2025 §3.5.

    Groups ``entries`` by ``todo_id`` (substring of ``body`` or ``source`` when
    present; otherwise the caller-passed filtered list is used as-is), picks
    winner = max ``(sequence, source)`` lexicographic — the paper's
    ``(logical clock, clientID)`` LWW register — and reports whether ``agent``
    is that winner. All replicas converge to the same winner deterministically
    regardless of arrival order; losers retry. Verify delay is 50 ms median,
    p95 200 ms per paper — caller should re-read after that delay before
    proceeding iff this returns True. Pure, no I/O, no lock change.
    Used only for claimable TODO-like lanes (e.g. ``kernel-sync-*``, patch
    lanes, wall regions marked ``TODO:``), not free-form prose where blanket
    ``is_peer_copy`` refusal stays.
    """
    if not isinstance(todo_id, str) or not todo_id:
        raise ValueError("todo_id must be non-empty string")
    if not isinstance(agent, str) or not agent:
        raise ValueError("agent must be non-empty string")
    if not entries:
        return False
    filtered = [e for e in entries if todo_id in getattr(e, "body", "") or todo_id in getattr(e, "source", "")]
    pool = filtered if filtered else list(entries)
    if not pool:
        return False
    def _key(entry) -> tuple:
        seq = getattr(entry, "sequence", 0)
        src = getattr(entry, "source", "")
        try:
            seq_i = int(seq)
        except (TypeError, ValueError):
            seq_i = 0
        return (seq_i, str(src))
    winner = max(pool, key=_key)
    return str(getattr(winner, "source", "")) == agent




def read_scan_acquisitions(home: Path, *, window: int = 100) -> list[tuple[datetime, datetime]]:
    """Ordered ``(acquisition start, created)`` pairs per scan receipt (boundary: scan JSON -> datetime).

    Yates et al. §II: monitor age ``Δ(t)=t−u(t)`` where ``u(t)`` is the instant
    the data was observed. A receipt's ``created`` is stamped at acquisition
    *end*, so the observation instant is ``acquisition.start.utc_ns``. Returns
    the last ``window`` pairs sorted by filename, skipping unreadable receipts
    and legacy receipts without a UTC acquisition start (warmup stays unknown).
    Kept in ``feed.py`` because it is the boundary adapter for scan JSON parsing.
    """
    discovery = home / "discovery"
    if not discovery.is_dir():
        return []
    pairs: list[tuple[datetime, datetime]] = []
    for name in sorted(discovery.glob("scan-*.json")):
        try:
            doc = json.loads(name.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(str(doc["created"]).replace("Z", "+00:00"))
            start_ns = doc["acquisition"]["start"]["utc_ns"]
            if isinstance(start_ns, bool) or not isinstance(start_ns, int):
                continue
            started = datetime.fromtimestamp(start_ns / 1_000_000_000, timezone.utc)
        except (OSError, ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError):
            continue
        if created.tzinfo is None:
            continue
        pairs.append((started, created))
    if window is not None and window > 0 and len(pairs) > window:
        pairs = pairs[-window:]
    return pairs


def read_scan_series(home: Path, sense_id: str) -> list[tuple[datetime, int]]:
    """Read a sense's verified samples from the retained discovery scan corpus.

    Returns (timestamp, sample) tuples sorted by time. This is a boundary
    function: scan files are JSON and parsed here, not in the caller.
    """
    discovery = home / "discovery"
    if not discovery.is_dir():
        return []
    rows: list[tuple[datetime, int]] = []
    for name in sorted(discovery.glob("scan-*.json")):
        try:
            doc = json.loads(name.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            when = datetime.fromisoformat(doc["created"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        for obs in doc.get("observations", []):
            if obs.get("id") == sense_id and obs.get("state") == "verified":
                try:
                    rows.append((when, int(obs["sample"])))
                except (KeyError, TypeError, ValueError):
                    pass
    rows.sort(key=lambda row: row[0])
    return rows
