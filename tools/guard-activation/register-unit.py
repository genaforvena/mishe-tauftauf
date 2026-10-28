#!/usr/bin/env python3
"""Register the core supervisor's unit in the containment config, or remove it.

The guard admits a protected-session unit only when that unit is named in
`/etc/mishe-containment.json` under `life_support.units`.  The core's
supervisor must be named there before it can run, so this adds or removes
that one name.

The config's JSON is read and written by the core's own config boundary, so
this tool is not a parsing site of its own.  The change is exact and
reversible: the config's bytes before the change are kept, and `revert`
restores them byte for byte.  A config that someone else has changed since
is refused rather than overwritten.

Usage:
  sudo python3 register-unit.py            # add the core's unit
  sudo python3 register-unit.py revert     # restore the previous bytes
  sudo python3 register-unit.py check      # report what is registered
"""
import hashlib
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from mishe_tauftauf.core import containment_config as config_boundary  # noqa: E402

CONFIG = pathlib.Path("/etc/mishe-containment.json")
BACKUP_DIR = pathlib.Path("/var/lib/mishe-containment/config-backups")
UNIT = "mishe-core-supervisor.service"


def installed_bytes() -> bytes | None:
    if not CONFIG.exists():
        return None
    return CONFIG.read_bytes()


def restore_point() -> pathlib.Path:
    """Where the config's bytes before a change are kept."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / (CONFIG.name + ".before-core-unit")


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "add"
    if action not in {"add", "revert", "check"}:
        print("usage: register-unit.py [add|revert|check]", file=sys.stderr)
        return 2
    current = installed_bytes()
    if action == "check":
        data = config_boundary.read_config(CONFIG)
        units = config_boundary.life_support_units(data)
        print("config sha256:", hashlib.sha256(current).hexdigest() if current else None)
        print("life_support.units:", units)
        print("core unit registered:", UNIT in units)
        return 0
    if action == "revert":
        point = restore_point()
        if not point.exists():
            print("refusing: there is no saved config to restore", file=sys.stderr)
            return 1
        saved = point.read_bytes()
        original = config_boundary.read_config(point)
        if UNIT in config_boundary.life_support_units(original):
            print("refusing: saved config already contains the core unit", file=sys.stderr)
            return 1
        expected = config_boundary.encode_config(config_boundary.with_unit(original, UNIT))
        if current != expected:
            print("refusing: installed config differs from the registered bytes", file=sys.stderr)
            return 1
        CONFIG.write_bytes(saved)
        print("restored", CONFIG)
        print("sha256:", hashlib.sha256(saved).hexdigest())
        return 0
    if current is None:
        print("refusing: there is no containment config", file=sys.stderr)
        return 1
    # The previous bytes are kept before anything is written.
    point = restore_point()
    if point.exists() and point.read_bytes() != current:
        print("refusing: the saved config no longer matches the installed one;"
              " reconcile it before changing anything", file=sys.stderr)
        return 1
    data = config_boundary.read_config(CONFIG)
    if UNIT in config_boundary.life_support_units(data):
        print("already registered:", UNIT)
        return 0
    point.write_bytes(current)
    os.chmod(point, 0o600)
    updated = config_boundary.with_unit(data, UNIT)
    payload = config_boundary.write_config(CONFIG, updated)
    subprocess.run(["sudo", "-n", "chown", "0:0", str(CONFIG)], check=True)
    print("registered", UNIT)
    print("config sha256:", hashlib.sha256(payload).hexdigest())
    print("restore point:", point)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
