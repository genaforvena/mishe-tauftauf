"""System 0 owner routing: a deterministic lookup from a signal, project or retired role to its resolver.

The registry is data in the site (system1/owner-routing.json, gitignored). A lookup that finds
an entry is decided by code. A lookup that finds nothing is residue: the caller may ask a
System 1 decision model with an escape option, or hand the question to a mind.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

REGISTRY_NAME = "owner-routing.json"
SCHEMA = "owner-routing/1"


@dataclass(frozen=True)
class Route:
    kind: str
    key: str
    owner: str | None
    source: str  # "system0" when the registry decided; "residue" when it did not

    @property
    def decided(self) -> bool:
        return self.owner is not None


class RegistryError(ValueError):
    """The registry is missing or malformed. Callers must treat routing as UNKNOWN, never guess."""


def load(site_home: pathlib.Path) -> dict:
    path = pathlib.Path(site_home) / "system1" / REGISTRY_NAME
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"owner registry unreadable: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RegistryError("owner registry has an unexpected schema")
    for section in ("signals", "projects", "retired_roles"):
        if not isinstance(data.get(section), dict):
            raise RegistryError(f"owner registry section {section!r} missing")
    return data


def route(registry: dict, kind: str, key: str) -> Route:
    """Look up one signal, project or retired role. kind is 'signal', 'project' or 'role'."""
    if kind == "signal":
        entry = registry["signals"].get(key)
        owner = entry.get("resolver") if isinstance(entry, dict) else None
    elif kind == "project":
        owner = registry["projects"].get(key)
    elif kind == "role":
        owner = registry["retired_roles"].get(key)
    else:
        raise ValueError(f"unknown routing kind {kind!r}")
    if owner:
        return Route(kind, key, owner, "system0")
    return Route(kind, key, None, "residue")
