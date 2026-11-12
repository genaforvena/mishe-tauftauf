"""Boundary for the runtime's small JSON state files (signals, intentions, cursors).

The only place these files are decoded or encoded. Callers get decoded values;
a file that is absent is None, a file that is present but not JSON raises
ValueError so callers keep the evidence instead of treating it as empty.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def read_json(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeError as exc:
        raise ValueError(f"{path.name} is not text: {exc}") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc


def write_json(path: Path, payload) -> None:
    """Replace ``path`` atomically; a crash leaves the old or the new file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False)
    try:
        with handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def write_text_atomic(path: Path, text: str) -> None:
    """Durable atomic text replacement; an identical rewrite changes nothing."""
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_detail_lines(path: Path, limit: int) -> list[str]:
    """The raw lines of a signal's detail file, bounded to its last ``limit`` characters.

    Empty when the file is absent, unreadable or blank. A cut tail is marked so a reader sees it was shortened.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return []
    if not text:
        return []
    if len(text) > limit:
        text = "...(truncated)\n" + text[-limit:]
    return text.splitlines()
