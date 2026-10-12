"""Decode the site's declared tmux window names."""

from __future__ import annotations

import json
from pathlib import Path


def read(home: Path) -> list[str]:
    """Return declared windows; absent manifests mean no declared utilities."""
    path = home / "health" / "windows.json"
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"cannot read window manifest: {path}") from exc
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError(f"malformed window manifest: {path}")
    return names
