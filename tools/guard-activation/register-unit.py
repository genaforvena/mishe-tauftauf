#!/usr/bin/env python3
"""Inspect the core unit's containment registration.

Mutation is disabled: an atomic replacement cannot safely compare and swap
against noncooperating writers, nor can it preserve all file metadata.  The
check reports only membership in life_support.units; it does not validate the
whole configuration or establish that the guard will admit the unit.

Usage:
  python3 register-unit.py check
  python3 register-unit.py [add|revert]  # refused
"""
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from mishe_tauftauf.core import containment_config as config_boundary  # noqa: E402

CONFIG = pathlib.Path("/etc/mishe-containment.json")
UNIT = "mishe-core-supervisor.service"


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "add"
    if action not in {"add", "revert", "check"}:
        print("usage: register-unit.py [add|revert|check]", file=sys.stderr)
        return 2
    if action != "check":
        print("refusing: config mutation is disabled; noncooperating writers "
              "and file metadata cannot be protected by this tool", file=sys.stderr)
        return 1
    try:
        current = CONFIG.read_bytes()
        data = config_boundary.parse_config(current)
        units = config_boundary.life_support_units(data)
    except (OSError, config_boundary.ConfigError) as exc:
        print(f"refusing: cannot inspect containment config: {exc}", file=sys.stderr)
        return 1
    print("config sha256:", hashlib.sha256(current).hexdigest())
    print("life_support.units:", units)
    print("core unit registered:", UNIT in units)
    print("membership only; guard configuration and admission not validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
