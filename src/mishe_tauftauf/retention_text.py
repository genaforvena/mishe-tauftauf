"""Decode the plant-local text the retention rule reads (named boundary).

The retention rule decides whether a generated output file is still cited by the
evidence corpus.  Two things it reads are *text*: the corpus files (patches,
release records, signal details) from which it extracts cited basenames, and a
candidate's own filename, which it checks for an executable suffix.  Both live
here so ``site_retention`` acts only on decoded values (``tools/check_parsing.py``).

The basename pattern errs toward a large set: any token that looks like a
filename with one of the known output suffixes is treated as a citation, so an
ambiguous corpus keeps a file rather than deleting it.
"""
from __future__ import annotations

import re

PROTECTED_SUFFIXES = (".sh", ".py")
"""Scripts are live code: never retention candidates, whatever their age."""

_BASENAME_RE = re.compile(r"[a-zA-Z0-9_.-]+\.(?:json|detail|out|log|sh|py|txt|superseded|jsonl)")


def basenames(text: str) -> list[str]:
    """Return the basename-like tokens in one corpus text."""
    return _BASENAME_RE.findall(text)


def is_protected_name(name: str) -> bool:
    """True for a name a unit may execute; such a file is never a candidate."""
    return name.endswith(PROTECTED_SUFFIXES)
