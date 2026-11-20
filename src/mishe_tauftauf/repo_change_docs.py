"""System 0 seam for the repo-change initiative: which reader docs name a changed path.

The producer records changed repo paths; the docs listener needs the deterministic
part of "is this doc stale?" answered before any model call or wake. This module
reads only the reader corpus defined by :mod:`mishe_tauftauf.docs_tools`
(README.md and immediate docs/*.md, no symlinks) and treats doc text as data.
It never decides to wake anyone: it returns candidate docs and passages.
"""
from __future__ import annotations

import re
from pathlib import Path

from mishe_tauftauf.docs_tools import search_docs

# search_docs caps at 100 matches; ask for all of them, then apply the token rule.
_CORPUS_LIMIT = 100
_GENERIC_STEMS = frozenset({"__init__", "readme", "conftest", "test", "tests", "docs"})
_TOKEN_EDGE = r"[A-Za-z0-9_]"


def normalize_path(path: str) -> str:
    """Validate a repo-relative changed path and return its POSIX spelling."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a nonempty string")
    relative = path.strip().replace("\\", "/")
    while relative.startswith("./"):
        relative = relative[2:]
    if not relative or relative.startswith("/") or "\x00" in relative:
        raise ValueError("path must be a plain repo-relative path")
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError("path must have no empty or parent segments")
    return relative


def identifiers_for(path: str) -> list[str]:
    """Precise search keys for one changed path, longest first and deduplicated.

    A bare stem can be a common English word, so it is kept alongside the full
    path and (for src modules) the dotted import name rather than replacing them.
    Generic stems carry no information about a specific change and are dropped.
    """
    relative = normalize_path(path)
    candidates = [relative]
    if relative.startswith("src/") and relative.endswith(".py"):
        candidates.append(relative[4:-3].replace("/", "."))
    stem = Path(relative).stem
    if len(stem) >= 3 and stem.casefold() not in _GENERIC_STEMS:
        candidates.append(stem)
    seen: set[str] = set()
    result: list[str] = []
    for candidate in sorted(candidates, key=lambda value: (-len(value), value)):
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _token_pattern(identifier: str) -> re.Pattern[str]:
    # re.escape keeps paths and dots literal; IGNORECASE mirrors search_docs' casefold scan.
    return re.compile(rf"(?<!{_TOKEN_EDGE}){re.escape(identifier)}(?!{_TOKEN_EDGE})", re.IGNORECASE)


def _named_matches(root: Path, identifier: str, limit: int) -> dict:
    """One corpus read -> token-hits with doc path, line and text in corpus order."""
    pattern = _token_pattern(identifier)
    found = search_docs(root, identifier, limit=_CORPUS_LIMIT)
    kept: list[dict] = []
    hits = 0
    for match in found["matches"]:
        if not pattern.search(match["text"]):
            continue
        hits += 1
        if len(kept) < limit:
            kept.append(match)
    return {"passages": kept, "truncated": found["truncated"] or hits > len(kept)}


def passages_for(root: Path, identifier: str, *, limit: int = 3) -> dict:
    """Passages naming `identifier` as a token, with line numbers, no doc paths.

    A substring hit is not enough: ``budget`` in ``budget_producer.py`` must not
    name that module. Missing passages are an empty, complete result; an
    unreadable corpus propagates the read/decode error from search_docs.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("identifier must be a nonempty string")
    if type(limit) is not int or not 1 <= limit <= _CORPUS_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {_CORPUS_LIMIT}")
    found = _named_matches(root, identifier, limit)
    return {"passages": [{"line": p["line"], "text": p["text"]} for p in found["passages"]],
            "truncated": found["truncated"]}


def docs_naming(root: Path, changed_paths: list[str], *, passages: int = 3) -> dict:
    """Group the reader docs that name each changed path. Pure; no wake, no model.

    Results are deterministic: changed paths deduplicate in first-seen order,
    identifiers run longest first, and doc paths and passages keep the corpus
    reader's path/line order. A path with no token-hit has ``named: false``;
    that is evidence for the residual decision, never a skip instruction.
    """
    if not isinstance(changed_paths, (list, tuple)):
        raise ValueError("changed_paths must be a list of repo-relative paths")
    if type(passages) is not int or not 1 <= passages <= _CORPUS_LIMIT:
        raise ValueError(f"passages must be an integer from 1 to {_CORPUS_LIMIT}")
    changes: list[dict] = []
    seen_paths: set[str] = set()
    doc_paths: set[str] = set()
    truncated = False
    for raw in changed_paths:
        relative = normalize_path(raw)
        if relative in seen_paths:
            continue
        seen_paths.add(relative)
        per_doc: dict[str, list[dict]] = {}
        identifiers = identifiers_for(relative)
        for identifier in identifiers:
            found = _named_matches(root, identifier, passages)
            truncated = truncated or found["truncated"]
            for passage in found["passages"]:
                entry = {"line": passage["line"], "text": passage["text"]}
                bucket = per_doc.setdefault(passage["path"], [])
                if entry not in bucket:
                    bucket.append(entry)
        doc_paths.update(per_doc)
        docs = [{"path": path, "passages": sorted(per_doc[path], key=lambda p: p["line"])}
                for path in sorted(per_doc)]
        changes.append({"path": relative, "identifiers": identifiers, "docs": docs,
                        "named": bool(docs)})
    return {
        "changes": changes,
        "doc_paths": sorted(doc_paths),
        "summary": {
            "paths": len(changes),
            "named": sum(1 for change in changes if change["named"]),
            "unnamed": sum(1 for change in changes if not change["named"]),
            "candidate_docs": len(doc_paths),
            "truncated": truncated,
        },
    }
