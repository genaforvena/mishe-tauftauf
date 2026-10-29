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
from .wall_patch import verified_delivery


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


def _live_import_roots(pid: int) -> list[str] | None:
    """Import roots a live process runs, or ``None`` when its environ is unreadable.

    Decoding belongs to ``proc_table``; only the read/unreadable distinction is
    kept here, because the adapter's empty list cannot tell a process that names
    no PYTHONPATH from one whose ``/proc`` entry is gone.
    """
    from .proc_table import pane_environ_pythonpath, pane_root
    try:
        raw = Path("/proc", str(pid), "environ").read_bytes()
    except OSError:
        return None
    return [pane_root(component) for component in pane_environ_pythonpath(raw)]


def _renderer_pids(session: str, *, socket: str | None = None) -> dict[str, int] | None:
    """Upper-pane pid per window, or ``None`` when the session cannot be read."""
    from .proc_table import pane_windows
    argv = ["tmux"]
    if socket:
        argv.extend(["-S", socket])
    argv.extend(["list-panes", "-s", "-t", session, "-F",
                 "#{window_name} #{pane_index} #{pane_dead} #{pane_pid}"])
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return {window: int(pid) for window, index, _dead, pid in pane_windows(result.stdout)
            if index == "0"}


def _all_renderer_pids(home: Path) -> dict[str, int] | None:
    """Merged 0-pane pids from every session that records ``home`` as owner."""
    try:
        from .tmux import home_sessions as _home_sessions
        owned = _home_sessions(home)
    except Exception:
        return None
    if not owned:
        return None
    merged: dict[str, int] = {}
    any_ok = False
    for sess, sock in owned:
        part = _renderer_pids(sess, socket=sock)
        if part is None:
            continue
        if part:
            any_ok = True
        merged.update(part)
    return merged if any_ok or merged else None




def renderer_bytes_lines(home: Path) -> tuple[list[str], str]:
    """Name top-pane renderers whose live import root is not the pin."""
    from .window_manifest import read as read_window_manifest
    try:
        expected = read_window_manifest(home)
    except (OSError, ValueError):
        return [], "UNKNOWN"
    renderers = sorted(window for window in expected
                       if (home / "top-pains" / window).is_file())
    if not renderers:
        return [], "UNKNOWN"
    try:
        from .runtime_source import source_for
        pinned = str(source_for(home, Path(__file__).resolve().parents[2]).resolve())
    except (OSError, TypeError, ValueError) as exc:
        return [f"RENDERER BYTES: UNKNOWN — {exc}"], "UNKNOWN"
    # Merge every owned session so a site that spans plant + life-support
    # sees all 15 windows.  Fall back to the single MISHE_SEED_SESSION
    # when the home owns nothing (tests / legacy).
    merged = _all_renderer_pids(home)
    if merged is not None:
        pids: dict[str, int] | None = merged
        unreadable_session = "merged"
    else:
        session = os.environ.get("MISHE_SEED_SESSION", "")
        if not session:
            return [], "UNKNOWN"
        pids = _renderer_pids(session)
        unreadable_session = session
        if pids is None:
            return [f"RENDERER BYTES: UNKNOWN — session {unreadable_session} unreadable"], "UNKNOWN"
    if pids is None:
        return [f"RENDERER BYTES: UNKNOWN — session {unreadable_session} unreadable"], "UNKNOWN"
    stale: list[str] = []
    unreadable: list[str] = []
    for window in renderers:
        pid = pids.get(window)
        roots = None if pid is None else _live_import_roots(pid)
        if roots is None:
            unreadable.append(window)
            continue
        live = [str(Path(root).resolve()) for root in roots]
        if live and all(root == pinned for root in live):
            continue
        labeled = [f"nonexistent:{r}" if not Path(r).is_dir() else r for r in live]
        stale.append(f"{window}(live={','.join(labeled) if labeled else 'none'})")
    if stale:
        return [f"RENDERER BYTES: RED pin={pinned} stale={';'.join(stale)}"], "DRIFT"
    if unreadable:
        return [f"RENDERER BYTES: UNKNOWN pin={pinned} unreadable={','.join(unreadable)}"], "UNKNOWN"
    return [f"RENDERER BYTES: GREEN pin={pinned} renderers={len(renderers)}"], "GREEN"


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
        return "DRIFT", f"{line} DRIFT — undeclared: {','.join(undeclared)}"
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
            delivery = (" delivery=" + ("verified" if verified_delivery(record) else "incomplete")) if phase == "applied" else ""
            lines.append(f"PATCH {path.stem}: {phase}" + delivery + (f" failure={record['failure']}" if record.get("failure") else ""))
        except (OSError, ValueError, KeyError) as exc:
            lines.append(f"UNKNOWN patch {path.stem}: {exc}")
    lines.extend(service_lines)
    lease_state = None
    bytes_state = None
    bytes_lines = None
    if role == "health":
        lease_lines, lease_state = pane_lease_lines(home)
        lines.extend(lease_lines)
        bytes_lines, bytes_state = renderer_bytes_lines(home)
        lines.extend(bytes_lines)
    if service_note.startswith("GREEN"):
        if runtime_state == "DRIFT":
            service_note = "RED — runtime drift: service import roots differ from the pinned release"
        elif lease_state == "STALE":
            service_note = "RED — a top-pane renderer lease stopped advancing"
        elif bytes_state == "DRIFT":
            service_note = "RED — a top-pane renderer imports a superseded release"
        elif bytes_state == "UNKNOWN" and bytes_lines:
            # Only an evidence line makes a renderer unreadable. An empty
            # result means there was no renderer to inspect at all, which is
            # not an unreadable renderer; seed_culture_views guards the same
            # state with (bytes_lines or bytes_error) for that reason.
            service_note = "UNKNOWN — a top-pane renderer is unreadable"
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
