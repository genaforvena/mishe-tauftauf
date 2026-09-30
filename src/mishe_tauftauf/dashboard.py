"""The complete current report shared by a pane, its mind and its supervisor."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from .observations import validate_slug


def publish(home: Path, slug: str, frame: str, ok: bool, *, now=None):
    validate_slug(slug)
    now = now or datetime.now(timezone.utc)
    directory = home / "dashboards"
    directory.mkdir(parents=True, exist_ok=True)
    record = dict(version=1, slug=slug, home=str(home.resolve()), created=now.isoformat(),
                  frame=frame, ok=ok, sha256=hashlib.sha256(frame.encode()).hexdigest())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False, encoding="utf-8") as out:
            temporary = Path(out.name)
            json.dump(record, out)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(directory / f"{slug}.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read(home: Path, slug: str, *, now=None, max_age=30) -> tuple[str, bool]:
    validate_slug(slug)
    now = now or datetime.now(timezone.utc)
    try:
        record = json.loads((home / "dashboards" / f"{slug}.json").read_text())
        if record["version"] != 1 or record["slug"] != slug or record["home"] != str(home.resolve()):
            raise ValueError("dashboard identity mismatch")
        age = (now - datetime.fromisoformat(record["created"])).total_seconds()
        if not 0 <= age <= max_age:
            raise ValueError("dashboard stale or future")
        frame = record["frame"]
        if not isinstance(record["ok"], bool) or not isinstance(frame, str) or not frame:
            raise ValueError("dashboard frame invalid")
        if hashlib.sha256(frame.encode()).hexdigest() != record["sha256"]:
            raise ValueError("dashboard digest mismatch")
        return frame, record["ok"]
    except FileNotFoundError as exc:
        raise ValueError("dashboard missing; start its watcher") from exc
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"dashboard unreadable: {exc}") from exc
