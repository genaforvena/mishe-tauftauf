"""Decode the repository paths a documentation page names.

A release supplies its reviewer only the bytes of the files it changes. A page
that documents a rule implemented elsewhere therefore arrives without the code
it describes, and the documented rules cannot be traced to code: measured on the
docs releases 19fb7646 and c87cd884, where ``docs/network-view.md`` describes
``checks/network-care.py``'s RED/UNKNOWN arms and the reviewer answered
"unknown" for want of that file (see ``docs/protocol-boundaries.md``).

This adapter owns that one textual interpretation. It reads only the Markdown
page directory it names and returns the repo-relative path tokens a page spells;
whether a token exists, and whether it joins a review, is the caller's decision.
Prose that merely resembles a path stays a token like any other — the caller
resolves it against the release tree and drops what does not exist.
"""
from __future__ import annotations

import re

SUFFIX = ".md"
"""The only page suffix this adapter reads."""

REFERENCE = re.compile(r"[\w][\w./-]*\.(?:py|md|json|sh|rs|toml)")
"""A repo-relative path token: a word character, then a run of name characters,
ending in a suffix a repository file carries. Leading ``./`` cannot match, so a
token is already relative and needs no trimming."""


def is_page(name: str) -> bool:
    """Whether ``name`` is a page this adapter reads."""
    return name.endswith(SUFFIX)


def referenced_paths(text: str) -> list[str]:
    """The repo-relative path tokens ``text`` spells, in first-appearance order."""
    return REFERENCE.findall(text)
