from __future__ import annotations

import fcntl
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCE_RE = re.compile(r"[A-Za-z0-9._/-]+\Z")
HEADER_RE = re.compile(r"(\d{20}) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z) ([A-Za-z0-9._/-]+) ::\n\Z")
RESERVED_EXACT = {"mishe-tauftauf"}
RESERVED_PREFIXES = ("prediction/", "observation/")


class FeedError(ValueError):
    pass


@dataclass(frozen=True)
class FeedEntry:
    sequence: int
    timestamp: str
    source: str
    body: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode_entry(sequence: int, timestamp: str, source: str, body: str) -> bytes:
    header = f"{sequence:020d} {timestamp} {source} ::\n"
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


def parse_feed(data: bytes) -> list[FeedEntry]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FeedError(f"feed is not UTF-8: {exc}") from exc
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    entries: list[FeedEntry] = []
    index = 0
    expected = 1
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
        entries.append(FeedEntry(sequence, timestamp, source, body))
        expected += 1
    return entries


class Feed:
    def __init__(self, home: Path | str):
        self.home = Path(home)
        self.path = self.home / "feed"

    def read_bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b""

    def entries(self) -> list[FeedEntry]:
        return parse_feed(self.read_bytes())

    def append(self, source: str, body: str, *, reserved: bool = False, once: bool = False) -> FeedEntry:
        if not SOURCE_RE.fullmatch(source):
            raise FeedError("source must match [A-Za-z0-9._/-]+")
        if not reserved and (source in RESERVED_EXACT or source.startswith(RESERVED_PREFIXES)):
            raise FeedError(f"source {source!r} is reserved for the runtime")
        if not isinstance(body, str) or not body:
            raise FeedError("body must be non-empty UTF-8 text")
        try:
            body.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise FeedError(f"body is not UTF-8 encodable: {exc}") from exc
        if "\r" in body:
            # Carriage returns are valid Unicode text, but canonical line framing is LF-only.
            # Preserve them as content rather than accepting CRLF as ambiguous framing.
            pass
        self.home.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with os.fdopen(fd, "r+b", buffering=0) as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.seek(0)
                existing = handle.read()
                entries = parse_feed(existing)
                if once:
                    for entry in entries:
                        if entry.source == source and entry.body == body:
                            return entry
                sequence = len(entries) + 1
                timestamp = utc_now()
                encoded = _encode_entry(sequence, timestamp, source, body)
                handle.seek(0, os.SEEK_END)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                return FeedEntry(sequence, timestamp, source, body)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def append_runtime(self, source: str, body: str) -> FeedEntry:
        return self.append(source, body, reserved=True)

    def append_runtime_once(self, source: str, body: str) -> FeedEntry:
        """Append one exact textual receipt atomically across cooperating writers."""
        return self.append(source, body, reserved=True, once=True)
