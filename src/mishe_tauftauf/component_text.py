"""Component deploy text: the approved adapter for per-component records.

Decodes the component name and release sha forms, the promotions ledger lines,
the release manifest, the pointer's target path, the promote check file and
git's one-line output. Used by ``components``; design in docs/component-deploy.md.
A malformed record raises ``ValueError`` (or ``json.JSONDecodeError``, a
``ValueError``): callers report RED or refuse. Nothing substitutes a default.
"""
from __future__ import annotations

import json
import re

NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"
SHA_PATTERN = r"^[0-9a-f]{40}$"
_NAME_RE = re.compile(NAME_PATTERN)
_SHA_RE = re.compile(SHA_PATTERN)


def name_ok(name) -> bool:
    return bool(_NAME_RE.match(name or ""))


def sha_ok(sha) -> bool:
    return bool(_SHA_RE.match(sha or ""))


def sha_from_parts(parts) -> str | None:
    """The sha of a ``.../releases/SHA`` target path, or None when it has another shape."""
    if len(parts) < 2 or parts[-2] != "releases" or not sha_ok(parts[-1]):
        return None
    return parts[-1]


def decode_ledger(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def decode_manifest(text: str) -> dict:
    return json.loads(text)


def decode_check(text: str):
    return json.loads(text)


def git_line(output: str) -> str:
    return output.strip()
