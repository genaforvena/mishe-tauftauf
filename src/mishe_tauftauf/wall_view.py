"""Deterministic observations and a first-class, edited docs pane."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from . import seed_culture_views
from .ci_watch import line as ci_line
from .observations import validate_slug


def render(home: Path, role: str) -> str:
    validate_slug(role)
    if role == "docs":
        document = home.parent / "docs/mesh.md"
        try:
            text = document.read_text()
            return f"DOCS FILE: {document}\n" + text + "\nSTATE: GREEN — page readable; lease is not a content review\n"
        except OSError as exc:
            return f"DOCS FILE: {document}\nSTATE: UNKNOWN — page unavailable: {exc}\n"
    if role in {"discover", "senses"}:
        return getattr(seed_culture_views, role)(home)
    lines = ["OBSERVATION — deterministic facts; minds decide the next work", ci_line(home)]
    for path in sorted((home / "patches").glob("*.json")):
        try:
            record = json.loads(path.read_text())
            phase = record["phase"]
            lines.append(f"PATCH {path.stem}: {phase}" + (f" failure={record['failure']}" if record.get("failure") else ""))
        except (OSError, ValueError, KeyError) as exc:
            lines.append(f"UNKNOWN patch {path.stem}: {exc}")
    try:
        units = json.loads((home / "health/services.json").read_text())
        if not isinstance(units, list) or any(not isinstance(u, str) or not u.endswith(".service") or u.startswith("-") or "/" in u or any(c.isspace() for c in u) for u in units):
            raise ValueError("invalid service manifest")
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        result = subprocess.run(
            ["systemctl", "--user", "show", *units,
             "-p", "Id,ActiveState,SubState,NRestarts"],
            capture_output=True, text=True, timeout=5, env=env)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "service read failed")
        healthy = bool(units)
        rows = []
        for block in result.stdout.strip().split("\n\n"):
            values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
            good = values.get("ActiveState") == "active" and values.get("SubState") == "running"
            healthy &= good
            rows.append((values.get("Id", "unknown"), good, values.get("ActiveState"),
                         values.get("SubState"), values.get("NRestarts", "unknown")))
        notable = [row for row in rows if not row[1] or row[4] != "0"]
        if rows and not notable:
            lines.append(f"SERVICES: GREEN — {len(rows)} listed units active/running, 0 restarts")
        else:
            if rows:
                lines.append(f"SERVICES: {len(rows)} listed; showing {len(notable)} with restarts or failures")
            for ident, good, active, sub, restarts in notable:
                lines.append(f"SERVICE {ident}: {'GREEN' if good else 'RED'} {active}/{sub} restarts={restarts}")
        lines.append("STATE: " + ("GREEN — listed services running; CI reported separately" if healthy else "RED — service failure or empty manifest"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        lines.append(f"STATE: UNKNOWN — services unavailable: {exc}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--role", required=True)
    args = parser.parse_args()
    print(render(args.home, args.role), end="")


if __name__ == "__main__":
    main()
