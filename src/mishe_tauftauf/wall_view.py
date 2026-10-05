"""Deterministic observations and a first-class, edited docs pane."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import seed_culture_views, seed_witness_view
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


LEASE_AGE_RE = re.compile(
    r"^-- pane live (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) · refresh ([0-9.]+)s · ticks every frame --$")


def pane_lease_lines(home: Path) -> tuple[list[str], str]:
    """Name top-pane renderer leases that stopped advancing.

    A window can exist, be alive and pass a name/dead check while its renderer is
    frozen; only the footer lease timestamp shows it stopped. Read each resident
    renderer's lease without waiting and compare its age with the pane's own
    advertised refresh. A window with no renderer script (the operator shell, the
    log tail) carries no lease and is not a fault; a renderer whose lease is
    missing or unreadable is UNKNOWN, never silently GREEN.
    """
    session = os.environ.get("MISHE_SEED_SESSION", "")
    if not session:
        return [], "UNKNOWN"
    try:
        expected = json.loads((home / "health" / "windows.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], "UNKNOWN"
    if not isinstance(expected, list):
        return [], "UNKNOWN"
    renderers = sorted(window for window in expected
                       if isinstance(window, str) and (home / "top-pains" / window).is_file())
    if not renderers:
        return [], "UNKNOWN"
    stale: list[str] = []
    missing: list[str] = []
    unreadable = False
    advancing = 0
    for window in renderers:
        try:
            captured = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", f"{session}:{window}.0", "-S", "-5"],
                capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            unreadable = True
            continue
        lease = None
        if captured.returncode == 0:
            for line in reversed(captured.stdout.splitlines()):
                match = LEASE_AGE_RE.fullmatch(line)
                if match:
                    lease = match
                    break
        if lease is None:
            missing.append(window)
            continue
        try:
            stamp = datetime.fromisoformat(lease.group(1).replace("Z", "+00:00"))
            refresh = float(lease.group(2))
        except ValueError:
            missing.append(window)
            continue
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        limit = max(120.0, 6 * refresh)
        if age > limit:
            stale.append(f"{window}(age={age:.0f}s>limit={limit:.0f}s)")
        else:
            advancing += 1
    if stale:
        return [f"PANE LEASE: RED stale={','.join(stale)}"], "STALE"
    if missing or unreadable or not advancing:
        detail = ",".join(part for part in (
            ("no-lease=" + ",".join(missing)) if missing else "",
            "tmux-unreadable" if unreadable else "",
            "none-readable" if not missing and not unreadable else "") if part)
        return [f"PANE LEASE: UNKNOWN {detail}"], "UNKNOWN"
    return [f"PANE LEASE: GREEN {advancing} renderers advancing"], "GREEN"


def _runtime_state(home: Path, root: Path,
                   service_roots: dict[str, list[str]] | None = None) -> tuple[str, str]:
    """The runtime verdict and its line, comparing the pin with the service roots.

    The renderer's own root cannot show this drift: the panes and services import
    their own copies. ``service_roots`` maps each listed unit to the import roots
    it names; ``None`` when the environment could not be read, and empty when the
    manifest lists no service. A unit is on the pin when its roots equal the pin.
    The release coordinator is declared to import the development checkout
    (:func:`runtime_source.declared_checkout_root`), so that intended root reads
    ``DECLARED``; any other divergence still reads ``DRIFT`` and the state line
    must not hide it behind GREEN.
    """
    if not (home / "health/runtime-release.json").exists():
        return "UNPINNED", "RUNTIME: pin=UNPINNED"
    try:
        from .runtime_source import declared_checkout_root, source_for
        pinned = str(source_for(home, root).resolve())
    except (OSError, TypeError, ValueError) as exc:
        return "UNKNOWN", f"RUNTIME: pin=UNKNOWN — {exc}"
    if service_roots is None:
        return "UNKNOWN", f"RUNTIME: pin={pinned} services=UNKNOWN"
    distinct = sorted({item for roots in service_roots.values() for item in roots})
    if not distinct:
        return "NONE", f"RUNTIME: pin={pinned} services=none"
    line = f"RUNTIME: pin={pinned} services={','.join(distinct)}"
    declared: list[str] = []
    undeclared: list[str] = []
    for unit, roots in sorted(service_roots.items()):
        if not roots or sorted(set(roots)) == [pinned]:
            continue
        expected = declared_checkout_root(home, unit)
        if expected is not None and sorted(set(roots)) == [str(expected)]:
            declared.append(f"{unit}={expected}")
        else:
            undeclared.append(unit)
    if undeclared:
        return "DRIFT", f"{line} DRIFT"
    if declared:
        return "DECLARED", f"{line} DECLARED — {','.join(declared)} observes the checkout"
    return "MATCH", f"{line} MATCH"


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


def _service_block(home: Path) -> tuple[list[str], dict[str, list[str]] | None, str]:
    """Service rows, the import roots each unit runs, and the service-state note.

    ``None`` means the environment could not be read; an empty mapping means the
    manifest lists no service. The note is the STATE line's own verdict; the
    caller folds in the runtime verdict so a drift cannot hide behind GREEN.
    """
    try:
        units = json.loads((home / "health/services.json").read_text())
        if not isinstance(units, list) or any(not isinstance(u, str) or not u.endswith(".service") or u.startswith("-") or "/" in u or any(c.isspace() for c in u) for u in units):
            raise ValueError("invalid service manifest")
        if not units:
            # A site that installs no services gets an empty manifest. Asking
            # `systemctl show` with no unit arguments would print the manager's
            # own properties, which the row parser turns into a phantom unit.
            return [], {}, "RED — service failure or empty manifest"
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        result = subprocess.run(
            ["systemctl", "--user", "show", *units,
             "-p", "Id,ActiveState,SubState,NRestarts,Environment"],
            capture_output=True, text=True, timeout=5, env=env)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "service read failed")
        healthy = True
        roots: dict[str, list[str]] = {}
        rows = []
        for block in result.stdout.strip().split("\n\n"):
            values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
            good = values.get("ActiveState") == "active" and values.get("SubState") == "running"
            healthy &= good
            unit_roots = _import_roots(values.get("Environment", ""))
            if unit_roots:
                roots[values.get("Id", "unknown")] = unit_roots
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
    if role == "witness":
        return seed_witness_view.render(home)
    no_report = ("REPORT: NOT PRODUCED — this role has no deterministic observation writer; "
                 "legacy observation files are not refreshed."
                 if role in {"genome", "research-methods"} else None)
    service_lines, service_roots, service_note = _service_block(home)
    runtime_state, runtime_line = _runtime_state(home, Path(__file__).resolve().parents[2], service_roots)
    lines = ["OBSERVATION — deterministic facts; minds decide the next work", ci_line(home), *deployment_lines(home),
             runtime_line]
    if no_report:
        lines.append(no_report)
    for path in sorted((home / "patches").glob("*.json")):
        try:
            record = json.loads(path.read_text())
            phase = record["phase"]
            delivery = (" delivery=" + ("verified" if record.get("delivery_verified") else "incomplete")) if phase == "applied" else ""
            lines.append(f"PATCH {path.stem}: {phase}" + delivery + (f" failure={record['failure']}" if record.get("failure") else ""))
        except (OSError, ValueError, KeyError) as exc:
            lines.append(f"UNKNOWN patch {path.stem}: {exc}")
    lines.extend(service_lines)
    lease_state = None
    if role == "health":
        lease_lines, lease_state = pane_lease_lines(home)
        lines.extend(lease_lines)
    if runtime_state == "DRIFT" and service_note.startswith("GREEN"):
        service_note = "RED — runtime drift: service import roots differ from the pinned release"
    if lease_state == "STALE" and service_note.startswith("GREEN"):
        service_note = "RED — a top-pane renderer lease stopped advancing"
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
