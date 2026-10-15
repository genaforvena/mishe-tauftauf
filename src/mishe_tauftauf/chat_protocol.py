"""Boundary adapters for lifecycle and linked-site completion records.

Machine control records are decoded here; explanatory prose remains opaque.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from .feed import FeedEntry, FeedError

_WAKE_RE = re.compile(r"seed wake ([a-z0-9-]+) observation=([1-9][0-9]*)(?: event=([1-9][0-9]*))?(?: task=(\S+))?\Z")
_YIELD_RE = re.compile(r"seed yield ([a-z0-9-]+) wake=([1-9][0-9]*)( continue=1)?\Z")
_CLEAR_RE = re.compile(r"seed clear ([a-z0-9-]+) after=([1-9][0-9]*)\Z")
_RESERVED = re.compile(r"\s*seed (?:wake|yield|clear)(?:\s|\Z)")
_SETTLED = re.compile(r"Turn settled \(([a-z]+)\);")
_SITE_SYNC_HEADER = "[site-sync] "
_CORE_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_KERNEL_SYNC_TASK_RE = re.compile(r"kernel-sync-[0-9a-f]{12}-(.+)\Z")


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


@dataclass(frozen=True)
class SiteSyncCompletion:
    target: str
    core_sha: str


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


def decode_site_sync(entry: FeedEntry) -> SiteSyncCompletion | None:
    """Decode the coordinator's target-scoped successful-refresh marker."""
    if entry.source != "sync":
        return None
    header = entry.body.splitlines()[0] if entry.body else ""
    if not header.startswith("[site-sync]"):
        return None
    if not header.startswith(_SITE_SYNC_HEADER):
        raise ProtocolError(f"entry {entry.sequence}: malformed site-sync marker")
    try:
        data = json.loads(header[len(_SITE_SYNC_HEADER):])
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"entry {entry.sequence}: malformed site-sync payload") from exc
    if (not isinstance(data, dict) or set(data) != {"target", "core_sha"}
            or not isinstance(data["target"], str) or not data["target"]
            or len(data["target"]) > 255 or any(char in data["target"] for char in "\r\n\0")
            or not isinstance(data["core_sha"], str) or not _CORE_SHA_RE.fullmatch(data["core_sha"])):
        raise ProtocolError(f"entry {entry.sequence}: invalid site-sync payload")
    return SiteSyncCompletion(data["target"], data["core_sha"])


def decode_kernel_sync_target(identity: str) -> str | None:
    """Decode the target suffix in the coordinator's kernel-sync task identity."""
    match = _KERNEL_SYNC_TASK_RE.fullmatch(identity)
    return match[1] if match else None
