from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .observations import validate_slug


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_check(home: Path, slug: str, argv: list[str]) -> Path:
    validate_slug(slug)
    if not argv:
        raise ValueError("check requires a program after --")
    started = stamp()
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True)
        code = result.returncode
        stdout = result.stdout.decode("utf-8", "replace")
        stderr = result.stderr.decode("utf-8", "replace")
        launch = "ok"
    except OSError as exc:
        code = 127
        stdout = ""
        stderr = str(exc)
        launch = "failed"
    ended = stamp()
    report = (
        f"check: {shlex.join(argv)}\n"
        f"started: {started}\nended: {ended}\nlaunch: {launch}\nexit: {code}\n"
        "stdout:\n" + stdout + ("" if stdout.endswith("\n") or not stdout else "\n") +
        "stderr:\n" + stderr + ("" if stderr.endswith("\n") or not stderr else "\n")
    )
    directory = home / "observations"
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{slug}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        destination = directory / slug
        os.replace(temporary, destination)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return destination
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
