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
