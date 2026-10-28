"""The containment config's own format.

`/etc/mishe-containment.json` is the host safety boundary's configuration.
It is JSON, and this module is where that JSON is read and written, so a
caller that needs to change one name in it does not become a parsing site
of its own.

The changes this module supports are narrow on purpose: one unit name may
be added to `life_support.units` or removed, and the previous bytes are
kept so the change can be undone byte for byte.  Nothing else about the
config is editable here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """The config is missing, unreadable or not the shape this expects."""


def read_config(path: str | Path) -> dict:
    """The config's fields, as ordinary Python values."""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"the containment config is unreadable: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"the containment config is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("the containment config must be a JSON object")
    return data


def write_config(path: str | Path, data: dict) -> bytes:
    """Write the config's fields, and return the bytes written."""
    target = Path(path)
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = target.with_name(target.name + ".new")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)
    return payload


def life_support_units(data: dict) -> list:
    """The units the guard admits for the protected session."""
    life = data.get("life_support")
    if not isinstance(life, dict):
        raise ConfigError("the config has no life_support section")
    units = life.get("units")
    if not isinstance(units, list) or any(not isinstance(unit, str) for unit in units):
        raise ConfigError("the config has no life_support.units list of names")
    return units


def with_unit(data: dict, unit: str) -> dict:
    """The config's fields with ``unit`` added to the protected session's units."""
    units = life_support_units(data)
    if unit in units:
        return data
    updated = dict(data)
    life = dict(updated["life_support"])
    life["units"] = [*units, unit]
    updated["life_support"] = life
    return updated


def without_unit(data: dict, unit: str) -> dict:
    """The config's fields with ``unit`` removed from the protected session's units."""
    units = life_support_units(data)
    if unit not in units:
        return data
    updated = dict(data)
    life = dict(updated["life_support"])
    life["units"] = [name for name in units if name != unit]
    updated["life_support"] = life
    return updated
