"""Boundary adapter for the chat tape's historical lifecycle dialect.

Only this module interprets wake/yield/clear sentences. Consumers receive
decoded values; explanatory prose never supplies lifecycle control fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .feed import FeedEntry, FeedError

_WAKE_RE = re.compile(r"seed wake ([a-z0-9-]+) observation=([1-9][0-9]*)(?: event=([1-9][0-9]*))?(?: task=(\S+))?\Z")
_YIELD_RE = re.compile(r"seed yield ([a-z0-9-]+) wake=([1-9][0-9]*)( continue=1)?\Z")
_CLEAR_RE = re.compile(r"seed clear ([a-z0-9-]+) after=([1-9][0-9]*)\Z")
_RESERVED = re.compile(r"\s*seed (?:wake|yield|clear)(?:\s|\Z)")
_SETTLED = re.compile(r"Turn settled \(([a-z]+)\);")


class ProtocolError(FeedError):
    """A reserved control sentence cannot be decoded safely."""


@dataclass(frozen=True)
class LifecycleEvent:
    kind: str
    role: str
    observation: int | None = None
    event: int | None = None
    task: str | None = None
    wake: int | None = None
    continue_work: bool = False
    result: str | None = None
    prose: str = ""


@lru_cache(maxsize=8192)
def _decode(body: str) -> LifecycleEvent | None:
    lines = body.splitlines()
    if not lines:
        return None
    header = lines[0]
    prose = "\n".join(lines[1:])
    if match := _WAKE_RE.fullmatch(header):
        return LifecycleEvent("wake", match[1], observation=int(match[2]),
                              event=int(match[3]) if match[3] else None,
                              task=match[4], prose=prose)
    if match := _YIELD_RE.fullmatch(header):
        result = next((found[1] for line in lines[1:] if (found := _SETTLED.match(line))), None)
        return LifecycleEvent("yield", match[1], wake=int(match[2]),
                              continue_work=bool(match[3]), result=result, prose=prose)
    if match := _CLEAR_RE.fullmatch(header):
        return LifecycleEvent("clear", match[1], wake=int(match[2]), prose=prose)
    if _RESERVED.match(header):
        raise ProtocolError("malformed lifecycle receipt")
    return None


def decode_lifecycle(entry: FeedEntry) -> LifecycleEvent | None:
    if entry.source != "seed":
        return None
    try:
        return _decode(entry.body)
    except ProtocolError as exc:
        raise ProtocolError(f"entry {entry.sequence}: {exc}") from exc
