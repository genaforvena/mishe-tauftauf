"""The plant-wide lock that serializes tape and task writes (``.seed.lock``)."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

from .observations import validate_home
from .recovery_io import clear_seed_holder, stamp_seed_holder


@contextmanager
def lock(home: Path):
    validate_home(home)
    with (home / ".seed.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        stamp_seed_holder(handle)       # lets the recovery probe tell a live holder from a stuck one
        try:
            yield
        finally:
            clear_seed_holder(handle)
