"""A bounded, typed view for opt-in external judgment of observations."""

from __future__ import annotations

import re

VERSION = 1

_STATE = re.compile(r"STATE: (GREEN|RED|INTERMEDIATE)\n")
_SUMMARY = re.compile(
    r"[A-Z][A-Z0-9-]{0,31}: candidates=(\d+) held=(\d+) actionable=(\d+) "
    r"delete=(\d+) unknowns=(\d+) head=([0-9a-f]{12}) "
    r"task=(present|none) task-epoch=(\d+) task-event-count=(\d+) "
    r"task-events=(NONE|\d+:(?:open|active|complete|rejected|blocked)"
    r"(?:,\d+:(?:open|active|complete|rejected|blocked))*)\n"
)


def safe_publish_view(projection: str) -> str | None:
    """Accept only an enumerated state and fixed-shape numeric summary.

    The channel label is discarded. Free-form projector output is never sent to
    the external judge on this opt-in path.
    """
    try:
        size = len(projection.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if size > 4096:
        return None
    lines = projection.splitlines(keepends=True)
    if len(lines) not in (1, 2) or not _STATE.fullmatch(lines[0]):
        return None
    if len(lines) == 1:
        return lines[0]
    match = _SUMMARY.fullmatch(lines[1])
    if not match:
        return None
    values = [int(value) for value in match.groups()[:5]]
    epoch, count = int(match.group(8)), int(match.group(9))
    if max(*values, epoch, count) > 1_000_000:
        return None
    events = match.group(10)
    if events != "NONE":
        numbers = [int(item.split(":", 1)[0]) for item in events.split(",")]
        if len(numbers) > 16 or numbers != sorted(set(numbers)) or numbers[-1] != epoch or count < len(numbers):
            return None
    elif count != 0:
        return None
    return (lines[0] + "SUMMARY: candidates={0} held={1} actionable={2} delete={3} "
            "unknowns={4} head={5} task={6} task-epoch={7} task-event-count={8} "
            "task-events={9}\n").format(*match.groups())


def projected_publish_controls() -> tuple[str, str]:
    return "STATE: RED\n", "STATE: GREEN\n"
