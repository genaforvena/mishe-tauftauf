"""Decode the site's declared off-pin service import roots."""
from __future__ import annotations

import json
from pathlib import Path

MANIFEST = "health/declared-roots.json"


def entries(home: Path) -> dict[str, str]:
    """The declared roots keyed by unit name; an absent or malformed record declares none.

    A service can only import from a snapshot that is not the pin when the pin does
    not carry the module it runs. The site records that decision — unit name, root,
    owning role, the activation record for the reviewed bytes and the retiring
    condition — because the runtime sensors read the pin, not the checkout, so the
    record must be readable from the site home to take effect without a release
    advance. An unreadable record declares nothing: a broken allowlist must not read
    as agreement, so every unit it fails to cover is left to the drift verdict.

    A root is reported the way ``wall_view._import_roots`` reports one, so a record
    naming ``<root>/src`` compares equal to a unit that names the same ``src``.
    """
    try:
        units = json.loads((Path(home) / MANIFEST).read_text(encoding="utf-8"))["units"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(units, dict):
        return {}
    declared: dict[str, str] = {}
    for unit, entry in units.items():
        root = entry.get("root") if isinstance(entry, dict) else None
        if isinstance(unit, str) and isinstance(root, str) and root:
            path = Path(root)
            declared[unit] = str(path.parent if path.name == "src" else path)
    return declared
