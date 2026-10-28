"""Read the unit membership field of the containment configuration.

This is not the guard's full configuration validator.  Callers must not infer
that the guard accepts a configuration merely because this field is readable.
"""
from __future__ import annotations

import json


class ConfigError(ValueError):
    """The config is not JSON or lacks a readable unit list."""


def parse_config(raw: bytes) -> dict:
    """Parse one byte snapshot of the config as a JSON object."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(f"the containment config is not UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("the containment config must be a JSON object")
    return data


def life_support_units(data: dict) -> list:
    """Read the names listed under life_support.units."""
    life = data.get("life_support")
    if not isinstance(life, dict):
        raise ConfigError("the config has no life_support section")
    units = life.get("units")
    if not isinstance(units, list) or any(not isinstance(unit, str) for unit in units):
        raise ConfigError("the config has no life_support.units list of names")
    return units
