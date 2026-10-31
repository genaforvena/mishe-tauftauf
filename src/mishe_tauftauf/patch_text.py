"""Decode unified-diff and file text for content-presence readings.

Git's patch output is an external dialect. It is decoded here, once, so a
reader consumes the added lines and line sets rather than diff syntax.
"""


def added_lines(patch: str) -> list[str]:
    """The stripped content of every added line in a unified diff.

    A ``+++`` line is a file header only before the first hunk of a file;
    inside a hunk ``+++x`` is an added ``++x``. A ``diff --git`` line starts
    the next file's header block, so the hunk state resets there and a second
    file's header is not counted as content. Blank additions are not content.
    """
    added = []
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif not in_hunk and line.startswith("+++ "):
            continue
        elif line.startswith("+") and line[1:].strip():
            added.append(line[1:].strip())
    return added


def stripped_lines(text: str) -> set[str]:
    """Each line of ``text`` with surrounding whitespace removed, as a set."""
    return {line.strip() for line in text.splitlines()}
