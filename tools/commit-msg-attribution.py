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

#: Only lines that begin with the exact ``Key:`` token are moved, so prose that
#: merely mentions the words (as a commit *about* this format does) is never
#: touched.
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

    lines = text.splitlines()
    # Git reads a trailer's continuation lines only from the final paragraph, so
    # that is the only place a whitespace-prefixed line *is* a continuation. Mark
    # the final paragraph (the run after the last blank line, trailing blanks
    # ignored): a matched line inside it takes its continuation lines; a matched
    # line outside it still moves, but alone, and the indented line after it stays
    # in the body -- Git would not have folded it there.
    last_content = len(lines)
    while last_content and not lines[last_content - 1].strip():
        last_content -= 1
    final_start = 0
    for index in range(last_content):
        if not lines[index].strip():
            final_start = index + 1

    # Every matched trailer, in order, as a ``[key, value, raw_lines]`` unit. A
    # whitespace-prefixed line immediately after a trailer inside the final
    # paragraph travels with it (Git folds it into the value), so the value is not
    # truncated. This keeps a repeated ``Co-Authored-By`` and a multi-line value
    # intact without hoisting an unrelated indented body line.
    units = []
    body_lines = []
    last = None
    for index, line in enumerate(lines):
        match = _TRAILER_LINE.match(line)
        if match:
            units.append([match.group(1), match.group(2).strip(), [line]])
            last = units[-1]
        elif last is not None and index >= final_start and line[:1] in (" ", "\t"):
            last[2].append(line)
        else:
            body_lines.append(line)
            last = None

    def first(key):
        return next((value for k, value, _ in units if k == key), None)

    wake = first("Mishe-Wake") or (wake or "").strip()
    role = first("Mishe-Role") or (role or "").strip()
    if not wake or not role:
        return text, False  # cannot attribute; leave the author's message alone

    wake_unit = next((u for u in units if u[0] == "Mishe-Wake" and u[1] == wake), None)
    role_unit = next((u for u in units if u[0] == "Mishe-Role" and u[1] == role), None)
    block = [f"Mishe-Wake: {wake}", *(wake_unit[2][1:] if wake_unit else []),
             f"Mishe-Role: {role}", *(role_unit[2][1:] if role_unit else [])]
    for unit in units:
        if unit is wake_unit or unit is role_unit:
            continue  # already emitted as the canonical Mishe pair
        block.extend(unit[2])

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
