"""Role and identity slug validation.

A leaf module: standard library only, no package imports. Modules on the
dispatch-lock path (``feed``, ``wall``) need slug validation at call time, and
``observations`` imports ``feed``, so the validator cannot live there without a
cycle. ``observations`` re-exports these names for existing importers.
"""
from __future__ import annotations

import re

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


def validate_slug(slug: str) -> str:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("slug must match [a-z0-9][a-z0-9-]{0,63}")
    return slug
