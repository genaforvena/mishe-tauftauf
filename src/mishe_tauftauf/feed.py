from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SOURCE_RE = re.compile(r"[A-Za-z0-9._/-]+\Z")
HEADER_RE = re.compile(r"(?:v1 )?(\d{20}) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z) ([A-Za-z0-9._/-]+) ::\n\Z")
FRAME_START_RE = re.compile(rb"(?m)^(?:v1 )?\d{20} \d{4}-\d{2}-\d{2}T[^\n]+ [A-Za-z0-9._/-]+ ::\n")
RECEIPT_RE = re.compile(r"wake (?:delivered|refused) top-pain [a-z0-9-]+ for entry [1-9][0-9]*(?: generation=[1-9][0-9]*)?(?: request-id=[A-Za-z0-9._:-]{1,128})?\Z")
INDEX_STRIDE = 128
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
_ENTRIES_CACHE: dict[str, tuple[tuple, list]] = {}
_ENTRIES_CACHE_LOCK = threading.Lock()


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


class FeedError(ValueError):
    pass


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


def parse_feed(data: bytes, *, start_sequence: int = 1, home: Path | str | None = None) -> list[FeedEntry]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FeedError(f"feed is not UTF-8: {exc}") from exc
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    entries: list[FeedEntry] = []
    index = 0
    expected = start_sequence
    while index < len(lines):
        match = HEADER_RE.fullmatch(lines[index])
        if not match:
            raise FeedError(f"malformed header at line {index + 1}")
        sequence = int(match.group(1))
        if sequence != expected:
            raise FeedError(f"non-contiguous sequence {sequence}, expected {expected}")
        timestamp, source = match.group(2), match.group(3)
        try:
            datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise FeedError(f"invalid timestamp at sequence {sequence}") from exc
        index += 1
        body_parts: list[str] = []
        final_newline: bool | None = None
        while index < len(lines):
            line = lines[index]
            if line == "    .\n":
                final_newline = True
                index += 1
                break
            if line == "    .-\n":
                final_newline = False
                index += 1
                break
            if not line.startswith("    | ") or not line.endswith("\n"):
                raise FeedError(f"malformed body framing at line {index + 1}")
            body_parts.append(line[6:])
            index += 1
        if final_newline is None:
            raise FeedError(f"unterminated entry {sequence}")
        body = "".join(body_parts)
        if not final_newline:
            if not body.endswith("\n"):
                raise FeedError(f"malformed no-newline entry {sequence}")
            body = body[:-1]
        if not body:
            raise FeedError(f"empty body at sequence {sequence}")
        entries.append(FeedEntry(sequence, timestamp, source, body, Path(home) if home is not None else None))
        expected += 1
    return entries


class Feed:
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

    def _save_index(self, handle, index: dict) -> None:
        index["metadata"] = self._metadata(handle)
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
        if os.stat(self.path).st_ino != os.fstat(handle.fileno()).st_ino:
            raise FeedError("feed path changed while locked")
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
        if wrapper is not None:
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
                if index["metadata"] == self._metadata(handle):
                    return index
            except (KeyError, TypeError, ValueError) as exc:
                raise FeedError(f"corrupt feed index: {exc}") from exc
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
        return index

    def entries(self, *, start: int = 1, limit: int | None = None) -> list[FeedEntry]:
        if start < 1 or limit is not None and limit < 0:
            raise ValueError("start must be positive and limit nonnegative")
        if not self.path.exists():
            if self.index_path.exists():
                raise FeedError("feed missing while checkpoint exists")
            return []
        if limit == 0:
            return []
        with self.path.open("rb") as handle:
            # A shared lock keeps concurrent readers concurrent; the writer's
            # LOCK_EX still excludes every reader. Index rebuilds use an atomic
            # replace, so racing readers waste a rebuild but cannot corrupt.
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            index = self._index(handle)
            if start > index["sequence"]:
                return []
            metadata = tuple(index["metadata"])
            if start == 1 and limit is None:
                with _ENTRIES_CACHE_LOCK:
                    cached = _ENTRIES_CACHE.get(str(self.path))
                if cached is not None and cached[0] == metadata:
                    return list(cached[1])
            checkpoints = index["checkpoints"]
            first_seq, first_offset = max((seq, offset) for seq, offset in checkpoints if seq <= start)
            target = index["sequence"] if limit is None else min(index["sequence"], start + limit - 1)
            end_offset = next((offset for seq, offset in checkpoints if seq > target), index["metadata"][1])
            handle.seek(first_offset)
            selected = parse_feed(handle.read(end_offset - first_offset), start_sequence=first_seq, home=self.home)
            result = [entry for entry in selected if start <= entry.sequence <= target]
            from .records import payload
            for entry in result:
                if any(line.lstrip().startswith("[record]") for line in entry.body.splitlines()):
                    payload(entry)
            if start == 1 and limit is None:
                with _ENTRIES_CACHE_LOCK:
                    _ENTRIES_CACHE[str(self.path)] = (metadata, list(result))
            return result

    def tail_sequence(self) -> int:
        """Return the verified canonical tail without replaying a current feed."""
        if not self.path.exists():
            if self.index_path.exists():
                raise FeedError("feed missing while checkpoint exists")
            return 0
        with self.path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            index = self._index(handle)
            if index["sequence"]:
                handle.seek(index["tail_offset"])
                tail = parse_feed(handle.read(), start_sequence=index["sequence"])
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
