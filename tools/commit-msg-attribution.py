#!/usr/bin/env python3
"""Commit-msg hook: keep Mishe attribution in a valid final Git trailer block.

Git reads trailers only from the final paragraph. A ``Mishe-Wake``/``Mishe-Role``
line that runs straight into the body paragraph is body text, so
``git interpret-trailers --parse`` prints nothing and
``work_boundary.new_commits`` records the commit observed but *unattributed*
(measured on ``ac98aaf7`` and ``ba25a854``, 2026-11-15). This hook rewrites the
message so those lines sit in the final contiguous trailer block, taking the wake
key and role from the environment (``MISHE_WAKE_KEY``/``MISHE_ROLE``) when the
message carries no Mishe lines at all.

Installed as ``.git/hooks/commit-msg`` by ``tools/install-commit-msg-attribution.sh``.
It never blocks a commit and never removes or rewrites a trailer value: it only
relocates the Mishe/coauthor lines into the final block. A message it cannot
attribute (no lines and no environment) is left untouched.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

#: Hoisted into the final block, in this order. Only lines that begin with the
#: exact ``Key:`` token are moved, so prose that merely mentions the words (as a
#: commit *about* this format does) is never touched.
KEYS = ("Mishe-Wake", "Mishe-Role", "Co-Authored-By")
_TRAILER_LINE = re.compile(r"^(Mishe-Wake|Mishe-Role|Co-Authored-By):[ \t]*(.*)$")


def parsed_trailers(text):
    """The trailer lines Git itself reads from ``text`` (empty when the block is not the final paragraph)."""
    out = subprocess.run(["git", "interpret-trailers", "--parse"], input=text,
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def _already_attributed(text):
    lines = parsed_trailers(text)
    has_wake = any(line.startswith("Mishe-Wake: ") for line in lines)
    has_role = any(line.startswith("Mishe-Role: ") for line in lines)
    return has_wake and has_role


def normalize(text, wake, role):
    """``(new_text, changed)``: relocate Mishe/coauthor lines into the final trailer block.

    ``wake``/``role`` are the fallback values (the wake environment) used only when
    the message itself carries no such line. The function is idempotent: a message
    whose Mishe lines already parse as trailers is returned unchanged.
    """
    if _already_attributed(text):
        return text, False

    values = {}
    body_lines = []
    for line in text.splitlines():
        match = _TRAILER_LINE.match(line)
        if match:
            values.setdefault(match.group(1), match.group(2).strip())
        else:
            body_lines.append(line)

    wake = values.get("Mishe-Wake") or (wake or "").strip()
    role = values.get("Mishe-Role") or (role or "").strip()
    if not wake or not role:
        return text, False  # cannot attribute; leave the author's message alone

    block = [f"Mishe-Wake: {wake}", f"Mishe-Role: {role}"]
    for key in KEYS[2:]:
        if values.get(key):
            block.append(f"{key}: {values[key]}")

    body = "\n".join(body_lines).strip("\n")
    new = (body + "\n\n" if body else "") + "\n".join(block) + "\n"
    return (new, True) if new != text else (text, False)


def main(argv):
    if not argv:
        return 0
    path = Path(argv[0])
    try:
        text = path.read_text()
        new, changed = normalize(text, os.environ.get("MISHE_WAKE_KEY", ""),
                                 os.environ.get("MISHE_ROLE", ""))
    except Exception as exc:  # a hook must never block a commit
        print(f"commit-msg-attribution: skipped ({exc})", file=sys.stderr)
        return 0
    if changed:
        path.write_text(new)
        print("commit-msg-attribution: moved the Mishe trailers into the final trailer block",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
