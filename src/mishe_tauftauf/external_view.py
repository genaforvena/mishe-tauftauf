"""A bounded, typed view for opt-in external judgment of observations."""

from __future__ import annotations

import re
import json

VERSION = 1
DELTA_VERSION = "projected-pair-v1"
DELTA_PUBLISH_QUESTION = (
    "Compare the named `previous` and `current` validated projected views. "
    "Does the current view introduce a new error, unknown, actionable change, task event, "
    "or recovery that warrants entering the shared feed? Identical views are routine. "
    "Judge the transition, not whether the current state alone is RED."
)

_STATE = re.compile(r"STATE: (GREEN|RED|INTERMEDIATE)\n")
_SUMMARY = re.compile(
    r"[A-Z][A-Z0-9-]{0,31}: candidates=(\d+) held=(\d+) actionable=(\d+) "
    r"delete=(\d+) unknowns=(\d+) head=([0-9a-f]{12}) "
    r"task=(present|none) task-epoch=(\d+) task-event-count=(\d+) "
    r"task-events=(NONE|\d+:(?:open|active|complete|rejected|blocked)"
    r"(?:,\d+:(?:open|active|complete|rejected|blocked))*)\n"
)


# This is a separate protocol from the cleaner's projected publish view. The
# producer may attach bounded typed diagnostic fields, but the external judge
# receives only enums, never hashes, counts, private pane text, or predictions.
FLEET_VERSION = "fleet-projected-v1"
FLEET_ROLES = frozenset((
    "adint", "check", "cleaner", "discover", "genome", "haunt", "hire", "job",
    "minds", "pub", "senses", "sound", "tg", "tg-roz", "vpn", "wake", "witness",
))
FLEET_SEMANTICS = {
    "adint": "obligations", "check": "fleet-health", "discover": "field-study",
    "genome": "vitality", "haunt": "obligations", "hire": "obligations",
    "job": "job-liability", "minds": "mind-wall", "senses": "sense-map",
    "sound": "archivist", "tg": "tg-path", "tg-roz": "roz-intake",
    "vpn": "vpn-probe", "wake": "obligations",
}
_FLEET_STATE = re.compile(r"STATE: (GREEN|RED|INTERMEDIATE|UNKNOWN)\n")
_FLEET_FIELDS = re.compile(
    r"OBSERVATION: source=(top-pane|dashboard|unavailable)/([a-z-]+) "
    r"freshness=(fresh|stale|unknown)(?: ([^\n]+))?\n"
)
_FLEET_ENUMS = {
    "goal": frozenset(("fresh", "stale", "unknown")),
    "comments": frozenset(("fresh", "stale", "unknown")),
    "cleaner": frozenset(("fresh", "stale", "unknown")),
    "journal": frozenset(("fresh", "stale", "unknown")),
    "value-coverage": frozenset(("partial", "unknown")),
    "review": frozenset(("pass", "review_required", "unknown")),
    "rx": frozenset(("up", "down", "wedged", "unknown")),
    "textin": frozenset(("up", "down", "wedged", "unknown")),
    "send": frozenset(("ok", "blocked")),
    "pipeline": frozenset(("present", "empty")),
}
_FLEET_DIAGNOSTICS = {
    "signal": re.compile(r"[0-9a-f]{16}\Z"),
    "issue-digest": re.compile(r"[0-9a-f]{16}\Z"),
    "view-change": re.compile(r"[0-9a-f]{24}\Z"),
    "mishe-issues": re.compile(r"(?:0|[1-9][0-9]{0,5})\Z"),
    "peers-online": re.compile(r"(?:0|[1-9][0-9]{0,2})\Z"),
    "peers-offline": re.compile(r"(?:0|[1-9][0-9]{0,2})\Z"),
    "tasks-total": re.compile(r"(?:0|[1-9][0-9]{0,5})\Z"),
    "tasks-unfinished": re.compile(r"(?:0|[1-9][0-9]{0,5})\Z"),
    "tasks-unowned": re.compile(r"(?:0|[1-9][0-9]{0,5})\Z"),
}
_FLEET_ROLE_FIELDS = {
    "cleaner": frozenset(("review",)),
    "tg": frozenset(("rx", "textin")),
    "tg-roz": frozenset(("send",)),
    "vpn": frozenset(("peers-online", "peers-offline")),
    "job": frozenset(("pipeline",)),
    "witness": frozenset(("tasks-total", "tasks-unfinished", "tasks-unowned",
                          "mishe-issues", "issue-digest")),
}


def safe_fleet_view(projection: str, slug: str) -> str | None:
    """Validate the whole producer record, then emit an enum-only model view.

    No unrecognized token is silently discarded: malformed or newly added
    producer fields must be reviewed before an external model can see them.
    """
    if not isinstance(projection, str) or not projection.isascii() or len(projection) > 1024:
        return None
    lines = projection.splitlines(keepends=True)
    if len(lines) != 2 or not (state := _FLEET_STATE.fullmatch(lines[0])):
        return None
    match = _FLEET_FIELDS.fullmatch(lines[1])
    if match is None:
        return None
    source, role, freshness, fields_text = match.groups()
    if role not in FLEET_ROLES or role != ("check" if slug == "health" else slug):
        return None
    if (source == "unavailable" and freshness != "unknown") or (freshness == "stale" and source != "top-pane"):
        return None
    if fields_text is None or fields_text != " ".join(fields_text.split()):
        return None
    fields: dict[str, str] = {}
    for token in fields_text.split(" "):
        key, sep, value = token.partition("=")
        if not sep or key in fields:
            return None
        if key in _FLEET_ENUMS:
            if value not in _FLEET_ENUMS[key]:
                return None
        elif key in _FLEET_DIAGNOSTICS:
            if not _FLEET_DIAGNOSTICS[key].fullmatch(value):
                return None
        elif key == "semantic":
            kind, sep, status = value.partition(":")
            if not sep or kind != FLEET_SEMANTICS.get(role) or status not in ("fresh", "stale", "unknown", "suspended"):
                return None
        else:
            return None
        fields[key] = value
    value_keys = set(fields) & {"goal", "comments", "cleaner", "journal"}
    expected_value = "journal" if role == "witness" else "comments" if role == "pub" else "cleaner" if role == "cleaner" else "goal"
    if (len(value_keys) > 1 or (value_keys and value_keys != {expected_value})
        or (role == "witness" and value_keys == {"journal"} and "value-coverage" in fields)
        or (not (role == "witness" and value_keys == {"journal"})
            and ("value-coverage" not in fields
                 or (value_keys and fields["value-coverage"] != "partial")
                 or (not value_keys and fields["value-coverage"] != "unknown")))):
        return None
    if value_keys and (source != "top-pane" or freshness != "fresh"):
        return None
    allowed = {"value-coverage", expected_value, "semantic", "signal", "view-change"} | _FLEET_ROLE_FIELDS.get(role, frozenset())
    if set(fields) - allowed or ("semantic" in fields and (source != "top-pane" or freshness != "fresh")):
        return None
    if ("review" in fields and role != "cleaner") or ("signal" in fields and (role == "cleaner" or freshness != "fresh")):
        return None
    if ("issue-digest" in fields) != ("mishe-issues" in fields):
        return None
    if ("peers-online" in fields) != ("peers-offline" in fields):
        return None
    if ("rx" in fields) != ("textin" in fields):
        return None
    if any(key in fields for key in ("tasks-total", "tasks-unfinished", "tasks-unowned")) and not all(
        key in fields for key in ("tasks-total", "tasks-unfinished", "tasks-unowned")
    ):
        return None
    if (("view-change" in fields and freshness != "fresh")
        or (freshness == "stale" and state.group(1) != "RED")
        or ((source == "unavailable" or freshness == "unknown") and state.group(1) != "UNKNOWN")
        or (fields.get("semantic", "").endswith(":stale") and state.group(1) != "RED")
        or (fields.get("semantic", "").endswith((":unknown", ":suspended")) and state.group(1) != "UNKNOWN")
        or (state.group(1) == "GREEN" and (source != "top-pane" or freshness != "fresh"
                                           or fields.get(expected_value) != "fresh"))):
        return None
    semantic = fields.get("semantic", "none")
    typed = " ".join(f"{key}={fields[key]}" for key in (
        expected_value, "value-coverage", "semantic", "review", "rx", "textin", "send", "pipeline"
    ) if key in fields)
    return (f"STATE: {state.group(1)}\nOBSERVATION: source={source}/{role} "
            f"freshness={freshness} {typed}" + ("" if "semantic" in fields else " semantic=none") + "\n")

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


def safe_publish_delta_view(previous: str | None, current: str) -> str | None:
    """Return only two separately validated, named safe views."""
    if previous is None:
        return None
    before, after = safe_publish_view(previous), safe_publish_view(current)
    if before is None or after is None:
        return None
    return json.dumps({"version": DELTA_VERSION, "previous": before, "current": after}, ensure_ascii=True)


def projected_delta_publish_controls() -> tuple[str, str]:
    positive = safe_publish_delta_view("STATE: GREEN\n", "STATE: RED\n")
    # A held-only count change has no new actionable work, error, task, or source head.
    # Unlike an identical pair, this exercises the model's negative decision.
    routine = ("STATE: GREEN\nCLEANER: candidates=0 held={held} actionable=0 delete=0 "
               "unknowns=0 head=000000000000 task=none task-epoch=0 "
               "task-event-count=0 task-events=NONE\n")
    negative = safe_publish_delta_view(routine.format(held=1), routine.format(held=2))
    assert positive is not None and negative is not None
    return positive, negative
