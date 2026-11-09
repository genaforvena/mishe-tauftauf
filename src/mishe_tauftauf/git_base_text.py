"""Committed-bytes reader for a base ref: the approved adapter for git tree text.

A wall patch whose working tree already equals HEAD needs the bytes a file had
at a base ref as its "before". Reading that means parsing ``git ls-tree`` output
(mode, object kind) and turning a blob into the patch record's encoded form.
That parsing lives here as a boundary, and ``wall_patch`` acts on the plain
record it returns.
"""
from __future__ import annotations

import base64
import subprocess


def committed_snapshot(root, base, name):
    """Return ``{"bytes": base64, "mode": int}`` for ``name`` at ``base``, or None if absent.

    Raises ``ValueError`` for an unreadable ref, a non-file path, or an unsafe ref name.
    """
    if base.startswith("-"):
        raise ValueError(f"invalid base ref: {base}")
    listing = subprocess.run(["git", "-C", str(root), "ls-tree", base, "--", name],
                             capture_output=True, text=True)
    if listing.returncode:
        raise ValueError(f"base ref not readable: {base}")
    if not listing.stdout.strip():
        return None
    mode, kind, _ = listing.stdout.split(None, 2)
    if kind != "blob":
        raise ValueError(f"base path is not a file: {name}")
    data = subprocess.run(["git", "-C", str(root), "show", f"{base}:{name}"],
                          capture_output=True, check=True).stdout
    return {"bytes": base64.b64encode(data).decode(), "mode": int(mode, 8) & 0o777}
