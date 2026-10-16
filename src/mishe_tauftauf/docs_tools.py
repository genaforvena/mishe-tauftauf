"""Literal search of reader Markdown; document text is data, never instructions."""
from __future__ import annotations

from pathlib import Path


def search_docs(root: Path, query: str, *, limit: int = 20) -> dict:
    """Search README.md and immediate docs/*.md pages in path/line order.

    Matching uses Unicode casefolding, not regex or command syntax. Results
    contain exact lines without line endings. A complete no-hit result has
    empty ``matches`` and ``truncated=False``; unreadable corpus files instead
    raise their read/decode error, even if the result limit was already met.

    Symlink pages and a symlink docs directory are excluded entirely, including
    in-root aliases, so this reader corpus cannot redirect into private text.
    The root, README.md and a non-symlink docs directory must be readable.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty, non-whitespace string")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")

    root = root.resolve(strict=True)
    docs = root / "docs"
    paths = [root / "README.md"]
    if not docs.is_symlink():
        paths.extend(path for path in docs.iterdir() if path.name.endswith(".md"))

    needle = query.casefold()
    matches = []
    truncated = False
    for path in sorted(paths):
        if path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        with path.open(encoding="utf-8") as stream:
            for number, raw_line in enumerate(stream, start=1):
                text = raw_line.removesuffix("\n")
                if needle not in text.casefold():
                    continue
                if len(matches) < limit:
                    matches.append({"path": relative, "line": number, "text": text})
                else:
                    truncated = True
    return {"matches": matches, "truncated": truncated}
