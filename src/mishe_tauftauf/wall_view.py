"""Deterministic observations and a first-class, edited docs pane."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

from . import seed_culture_views
from .ci_watch import line as ci_line
from .observations import validate_slug


def _import_roots(environment: str) -> list[str]:
    """Import roots named by a systemd ``Environment=`` value's PYTHONPATH entries."""
    roots: list[str] = []
    try:
        items = shlex.split(environment)
    except ValueError:
        return roots
    for item in items:
        if not item.startswith("PYTHONPATH="):
            continue
        for component in item[len("PYTHONPATH="):].split(os.pathsep):
            if not component:
                continue
            path = Path(component)
            roots.append(str(path.parent if path.name == "src" else path))
    return roots


def _runtime_state(home: Path, root: Path, service_roots: list[str] | None = None) -> tuple[str, str]:
    """The runtime verdict and its line, comparing the pin with the service roots.

    The renderer's own root cannot show this drift: the panes and services import
    their own copies. ``service_roots`` is ``None`` when the environment could not
    be read, and empty when the manifest lists no service. The verdict is
    ``MATCH``, ``DRIFT``, ``UNPINNED``, ``UNKNOWN`` or ``NONE``; only ``DRIFT``
    is running code that differs from the pin, and the state line must not hide
    it behind GREEN.
    """
    if not (home / "health/runtime-release.json").exists():
        return "UNPINNED", "RUNTIME: pin=UNPINNED"
    try:
        from .runtime_source import source_for
        pinned = str(source_for(home, root).resolve())
    except (OSError, TypeError, ValueError) as exc:
        return "UNKNOWN", f"RUNTIME: pin=UNKNOWN — {exc}"
    if service_roots is None:
        return "UNKNOWN", f"RUNTIME: pin={pinned} services=UNKNOWN"
    distinct = sorted(set(service_roots))
    if not distinct:
        return "NONE", f"RUNTIME: pin={pinned} services=none"
    state = "MATCH" if distinct == [pinned] else "DRIFT"
    return state, f"RUNTIME: pin={pinned} services={','.join(distinct)} {state}"


def deployment_lines(home: Path) -> list[str]:
    """Show checkout identity separately from the actual imported runtime bytes."""
    try:
        sha = subprocess.run(["git", "-C", str(home.parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "-C", str(home.parent), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5)
        source = (f"SOURCE: checkout={sha.stdout.strip()} changed_paths={len(dirty.stdout.splitlines())}; CI reported separately"
                  if sha.returncode == dirty.returncode == 0 else "SOURCE: UNKNOWN — checkout identity unavailable")
    except (OSError, subprocess.SubprocessError):
        source = "SOURCE: UNKNOWN — checkout identity unavailable"
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    try:
        files = sorted((root / "src/mishe_tauftauf").glob("*.py"))
        if not files:
            raise ValueError("runtime modules missing")
        for path in files:
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
        deployed = f"DEPLOYED: root={root} sha256={digest.hexdigest()} (Python modules; separate from checkout CI)"
    except (OSError, ValueError) as exc:
        deployed = f"DEPLOYED: UNKNOWN — runtime fingerprint unavailable: {exc}"
    return [source, deployed]


def _service_block(home: Path) -> tuple[list[str], list[str] | None, str]:
    """Service rows, the import roots those units run, and the service-state note.

    ``None`` roots means the environment could not be read; an empty list means
    the manifest lists no service. The note is the STATE line's own verdict; the
    caller folds in the runtime verdict so a drift cannot hide behind GREEN.
    """
    try:
        units = json.loads((home / "health/services.json").read_text())
        if not isinstance(units, list) or any(not isinstance(u, str) or not u.endswith(".service") or u.startswith("-") or "/" in u or any(c.isspace() for c in u) for u in units):
            raise ValueError("invalid service manifest")
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        result = subprocess.run(
            ["systemctl", "--user", "show", *units,
             "-p", "Id,ActiveState,SubState,NRestarts,Environment"],
            capture_output=True, text=True, timeout=5, env=env)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "service read failed")
        healthy = bool(units)
        roots: list[str] = []
        rows = []
        for block in result.stdout.strip().split("\n\n"):
            values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
            good = values.get("ActiveState") == "active" and values.get("SubState") == "running"
            healthy &= good
            if units:
                roots.extend(_import_roots(values.get("Environment", "")))
            rows.append((values.get("Id", "unknown"), good, values.get("ActiveState"),
                         values.get("SubState"), values.get("NRestarts", "unknown")))
        lines = []
        notable = [row for row in rows if not row[1] or row[4] != "0"]
        if rows and not notable:
            lines.append(f"SERVICES: GREEN — {len(rows)} listed units active/running, 0 restarts")
        else:
            if rows:
                lines.append(f"SERVICES: {len(rows)} listed; showing {len(notable)} with restarts or failures")
            for ident, good, active, sub, restarts in notable:
                lines.append(f"SERVICE {ident}: {'GREEN' if good else 'RED'} {active}/{sub} restarts={restarts}")
        note = ("GREEN — listed services running; CI reported separately" if healthy
                else "RED — service failure or empty manifest")
        return lines, roots, note
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return [], None, f"UNKNOWN — services unavailable: {exc}"


def render(home: Path, role: str) -> str:
    validate_slug(role)
    if role == "docs":
        from .wall import docs_document
        try:
            document = docs_document(home)
        except (OSError, ValueError) as exc:
            return f"STATE: UNKNOWN — docs repository selection unavailable: {exc}\n"
        try:
            text = document.read_text()
            return f"DOCS FILE: {document}\n" + text + "\nSTATE: GREEN — page readable; lease is not a content review\n"
        except OSError as exc:
            return f"DOCS FILE: {document}\nSTATE: UNKNOWN — page unavailable: {exc}\n"
    if role in {"discover", "senses"}:
        return getattr(seed_culture_views, role)(home)
    service_lines, service_roots, service_note = _service_block(home)
    runtime_state, runtime_line = _runtime_state(home, Path(__file__).resolve().parents[2], service_roots)
    lines = ["OBSERVATION — deterministic facts; minds decide the next work", ci_line(home), *deployment_lines(home),
             runtime_line]
    for path in sorted((home / "patches").glob("*.json")):
        try:
            record = json.loads(path.read_text())
            phase = record["phase"]
            delivery = (" delivery=" + ("verified" if record.get("delivery_verified") else "incomplete")) if phase == "applied" else ""
            lines.append(f"PATCH {path.stem}: {phase}" + delivery + (f" failure={record['failure']}" if record.get("failure") else ""))
        except (OSError, ValueError, KeyError) as exc:
            lines.append(f"UNKNOWN patch {path.stem}: {exc}")
    lines.extend(service_lines)
    if runtime_state == "DRIFT" and service_note.startswith("GREEN"):
        service_note = "RED — runtime drift: service import roots differ from the pinned release"
    lines.append("STATE: " + service_note)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--role", required=True)
    args = parser.parse_args()
    print(render(args.home, args.role), end="")


if __name__ == "__main__":
    main()
