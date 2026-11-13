"""Decode unified-diff and file text for content-presence readings.

Git's patch output is an external dialect. It is decoded here, once, so a
reader consumes the added lines and line sets rather than diff syntax.
"""

import re

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def added_lines(patch: str) -> list[str]:
    """The stripped content of every added line in a unified diff.

    A hunk header's counts say how many old and new lines its body holds, so
    the body ends exactly where the counts run out. Inside a body ``+++x`` is
    an added ``++x`` and ``---x`` is a removed line. Outside a body a ``+++ ``
    line is a file header, so a second file's header in a plain ``diff -u``
    is not counted, as in git output. Blank additions are not content.
    """
    added = []
    old_left = new_left = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            old_left = new_left = 0
            continue
        if old_left or new_left:
            if line.startswith("\\"):
                continue
            if line.startswith("+"):
                new_left -= 1
                if line[1:].strip():
                    added.append(line[1:].strip())
            elif line.startswith("-"):
                old_left -= 1
            else:
                old_left -= 1
                new_left -= 1
            continue
        header = _HUNK_HEADER.match(line)
        if header:
            old_left = int(header.group(1)) if header.group(1) is not None else 1
            new_left = int(header.group(2)) if header.group(2) is not None else 1
        elif line.startswith("+++ "):
            continue
        elif line.startswith("+") and line[1:].strip():
            added.append(line[1:].strip())
    return added


def patch_ids(patch_id_output: str) -> dict[str, str]:
    """Map commit sha to patch-id from ``git patch-id --stable`` output.

    Each output line is ``<patch-id> <commit-sha>``; lines that do not carry
    exactly those two fields are skipped. A repeated sha keeps its last id.
    """
    ids: dict[str, str] = {}
    for line in patch_id_output.splitlines():
        fields = line.split()
        if len(fields) == 2:
            ids[fields[1]] = fields[0]
    return ids


def stripped_lines(text: str) -> set[str]:
    """Each line of ``text`` with surrounding whitespace removed, as a set."""
    return {line.strip() for line in text.splitlines()}


def import_candidates(name, text):
    """Dotted in-package module names that a changed Python file imports (one hop)."""
    import ast
    from pathlib import Path
    parts = Path(name).relative_to("src").parts[:-1] if name.startswith("src/") else ()
    found = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, False) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(parts[:len(parts) - (node.level - 1)]) if node.level - 1 <= len(parts) else None
                if base is None:
                    continue
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            # `from . import sub` names no module of its own; only its aliases can be modules.
            if node.module:
                found.append((module, False))
            # `from pkg import sub` may import a submodule; the alias is a candidate too.
            found += [(module + "." + alias.name, True) for alias in node.names]
    return found


def module_file(src, dotted):
    parts = dotted.split(".")
    if parts[0] != "mishe_tauftauf":
        return None
    for candidate in (src.joinpath(*parts).with_suffix(".py"), src.joinpath(*parts, "__init__.py")):
        if candidate.is_file():
            return candidate
    return None


def in_package(dotted):
    """True for a dotted name inside the mishe_tauftauf package."""
    return dotted.startswith("mishe_tauftauf")


def is_python(name):
    return name.endswith(".py")


def decode_b64_text(encoded):
    """UTF-8 text of a base64 file body recorded in a patch record."""
    import base64
    return base64.b64decode(encoded).decode("utf-8")


def decode_lossy(data):
    """Text of raw bytes, undecodable bytes replaced rather than refused."""
    return data.decode("utf-8", errors="replace")
