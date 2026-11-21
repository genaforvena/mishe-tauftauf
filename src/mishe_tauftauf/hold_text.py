"""Literal boundary for the site's ``hold`` file format.

Only this module interprets hold-file text. New parsing belongs here and is named
in parsing-policy.json; callers consume decoded values, never the raw dialect.
"""
from __future__ import annotations

PROMOTION_PREFIX = "promotion: short activation hold; wakes resume automatically"


def is_promotion_hold(text):
    """True for a hold written by promotion activation, current or legacy.

    A manual ``mishe hold on`` hold carries only its reason text, so the prefix
    is what distinguishes an auto-clearable promotion hold from an indefinite one.
    """
    return text.startswith(PROMOTION_PREFIX)


def fields(text):
    """Decode the ``key=value`` tokens a promotion hold appends to its prefix."""
    result = {}
    for part in text.split():
        key, sep, value = part.partition("=")
        if sep:
            result[key] = value
    return result


FLIP_PREFIX = "root migration flip "


def owner_pid(text):
    """The pid whose liveness decides whether this hold is still owned, or None.

    A promotion hold names it as ``pid=N``; a root-migration flip's legacy token is
    ``root migration flip <pid>``. Every other hold -- a manual ``mishe hold on``
    reason, plant_boot's boot guard -- names no writer, so no liveness check can
    say whether it outlived the work it was written for. Nothing clears these
    automatically; only ``mishe hold off`` does.
    """
    if is_promotion_hold(text):
        pid = fields(text).get("pid", "")
        return int(pid) if pid.isdigit() else None
    token = text.strip()
    if token.startswith(FLIP_PREFIX):
        rest = token[len(FLIP_PREFIX):].strip()
        return int(rest) if rest.isdigit() else None
    return None
