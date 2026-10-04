"""Stable text panes for discovery, sensing, health, and operator decisions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path

from .access import list_requests, recoveries, retired_requests
from .ci_watch import line as ci_line
from .discovery import latest


def _report(home: Path, slug: str, verdict: str) -> None:
    path = home / "observations" / slug
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(verdict + "\n", encoding="utf-8")


def _snapshot_age(snapshot: dict[str, object]) -> float | None:
    try:
        created = datetime.fromisoformat(str(snapshot["created"]).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - created).total_seconds()
    except (ValueError, KeyError):
        return None


def discover(home: Path) -> str:
    lines = ["DESIRED STATE: useful reads have a real sample, honest status, and an owned next step"]
    snapshot = latest(home)
    if snapshot is None:
        lines.append("STATE: UNKNOWN — no discovery scan yet")
        verdict = "UNKNOWN discover has no scan"
    else:
        age = _snapshot_age(snapshot)
        lines.append(f"SCAN: {snapshot.get('created', 'unknown')} freshness={'stale' if age is None or age > 900 else 'recent'}")
        observations = snapshot.get("observations", [])
        for item in observations:
            lines.append(f"{item['state'].upper()} {item['id']}: {str(item['sample'])[:120]}")
        if age is None or age > 900:
            lines.append("STATE: UNKNOWN — scan stale; renew the read")
            verdict = "UNKNOWN discover scan stale"
        else:
            lines.append("STATE: GREEN — recent bounded read-only scan")
            verdict = "PASS discover recent scan"
    pending = [(item, state) for item, state in list_requests(home) if state == "pending"]
    lines.append(f"PERMISSION REQUESTS: {len(pending)} pending")
    for item, _ in pending[-5:]:
        lines.append(f"REQUEST {item.identity} unblocks={','.join(item.unblocks)}")
    lines.extend(["GOAL: discover useful new directions for Mishe and this plant's goals",
                  "PURSUIT: explore capabilities and crossed senses; use related literature when useful, assess fit, and check ideas",
                  "NEXT: renew an absent or stale scan; otherwise choose one observation, reading, or experiment and record its outcome"])
    _report(home, "discover", verdict)
    return "\n".join(lines) + "\n"


def senses(home: Path) -> str:
    lines = ["GOAL: turn readings into honest senses with real samples and visible unknowns",
             "PURSUIT: wire useful reads, reproduce hollow or failed checks, and own reusable fixes through review, branch CI and ready integration",
             "NEXT: pursue a new useful read when an UNKNOWN has a documented retry condition; verify its live sample"]
    snapshot = latest(home)
    if snapshot is None:
        verdict = "UNKNOWN senses no discovery sample"
        lines.append("STATE: UNKNOWN — no sample exists")
    else:
        age = _snapshot_age(snapshot)
        stale = age is None or age > 900
        senses_rows = [item for item in snapshot.get("observations", []) if str(item.get("id", "")).startswith("sense.")]
        unknown = 0
        unavailable = 0
        for item in senses_rows:
            state = "unknown" if stale else item["state"]
            unknown += state not in {"verified", "unavailable"}
            unavailable += state == "unavailable"
            lines.append(f"{state.upper()} {item['id']}: {str(item['sample'])[:120]}")
        lines.append(f"SCAN: {snapshot.get('created', 'unknown')} freshness={'stale' if stale else 'recent'}")
        if unknown:
            verdict = f"UNKNOWN senses {unknown} unverified or stale"
            lines.append(f"STATE: UNKNOWN — {unknown} senses need a checked read or honest unavailable claim")
        else:
            verdict = f"PASS senses {len(senses_rows)} verified samples"
            lines.append("STATE: GREEN — all wired sample reads verified")
        if unavailable:
            lines.append(f"UNAVAILABLE senses {unavailable} named an absent source, not a failed read")
    _report(home, "senses", verdict)
    return "\n".join(lines) + "\n"

def _service_readings(units: list[str], env: dict[str, str]) -> dict[str, tuple[str, str, int | None]]:
    """One coherent systemctl sample for every listed unit, keyed by unit name.

    Per-unit ``is-active`` loops sample different instants, so a unit that crashes
    and is auto-restarted between two calls reads healthy each time: the restart
    window and the durable ``NRestarts`` counter never reach the pane. A failed
    read yields no readings, leaving every unit unknown rather than fabricating a
    healthy state.
    """
    if not units:
        return {}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", *units, "-p", "Id,ActiveState,SubState,NRestarts"],
            capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode:
        return {}
    readings: dict[str, tuple[str, str, int | None]] = {}
    for block in result.stdout.strip().split("\n\n"):
        values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
        name = values.get("Id")
        if not name:
            continue
        count = values.get("NRestarts", "")
        readings[name] = (values.get("ActiveState", "unknown"), values.get("SubState", "unknown"),
                          int(count) if count.isdigit() else None)
    return readings



def health(home: Path) -> str:
    lines = ["GOAL: keep this plant's panes, feed, and resident services working",
             "PURSUIT: repair internal failures from the live check; own reusable code delivery through ready integration",
             "NEXT: investigate the first RED or UNKNOWN internal check and verify its live recovery"]
    doctor = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "doctor"],
                            capture_output=True, text=True, timeout=15)
    lines.append("DOCTOR: " + ("PASS" if doctor.returncode == 0 else "RED"))
    if doctor.returncode:
        lines.extend(doctor.stdout.splitlines()[-8:])
    session = os.environ.get("MISHE_SEED_SESSION", "")
    expected_path = home / "health" / "windows.json"
    try:
        expected = set(json.loads(expected_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        expected = set()
    windows_unknown = False
    if not session:
        windows_unknown = True
        missing = extra = dead = []
        lines.append("WINDOWS: UNKNOWN — session unset")
    else:
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-s", "-t", session,
                 "-F", "#{window_name} #{pane_index} #{pane_dead}"],
                capture_output=True, text=True, timeout=2)
            pane_states = {}
            if result.returncode == 0:
                for row in result.stdout.splitlines():
                    fields = row.split()
                    if len(fields) == 3:
                        pane_states[(fields[0], fields[1])] = fields[2]
            actual = {name for name, _ in pane_states}
        except (OSError, subprocess.TimeoutExpired):
            actual = set()
            pane_states = {}
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        dead = sorted(f"{name}.0" for name in expected & actual
                      if pane_states.get((name, "0")) != "0")
        lines.append("WINDOWS: " + ("PASS " if expected and not missing and not extra and not dead else "RED ") +
                     ",".join(sorted(actual)) + (" missing=" + ",".join(missing) if missing else "") +
                     (" dead=" + ",".join(dead) if dead else "") +
                     (" extra=" + ",".join(extra) if extra else ""))
    services_path = home / "health" / "services.json"
    local_services_unknown = False
    try:
        services = json.loads(services_path.read_text(encoding="utf-8"))
        if (not isinstance(services, list) or len(services) > 32
                or any(not isinstance(unit, str) or not unit.isprintable()
                       or not unit.endswith(".service") or unit.startswith("-")
                       or "/" in unit or any(c.isspace() for c in unit)
                       for unit in services)):
            raise ValueError("invalid local services list")
    except (OSError, ValueError, TypeError):
        services = []
        local_services_unknown = True
        lines.append("SERVICES: UNKNOWN — local manifest unavailable or malformed")
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
    restarts_path = home / "health" / "service-restarts.json"
    try:
        previous_restarts = json.loads(restarts_path.read_text(encoding="utf-8"))
        if not isinstance(previous_restarts, dict):
            previous_restarts = {}
    except (OSError, ValueError):
        previous_restarts = {}
    readings = _service_readings(services, env)
    failed_services = []
    current_restarts: dict[str, int] = {}
    for unit in services:
        active, sub, restarts = readings.get(unit, ("unknown", "unknown", None))
        baseline = previous_restarts.get(unit)
        restarted = (isinstance(restarts, int) and isinstance(baseline, int)
                     and not isinstance(baseline, bool) and restarts > baseline)
        if isinstance(restarts, int):
            current_restarts[unit] = restarts
        lines.append(f"SERVICE {unit}: {active}/{sub}" +
                     (f" restarts={restarts}" if isinstance(restarts, int) else ""))
        if active != "active" or sub == "auto-restart" or restarted:
            failed_services.append(unit)
    if current_restarts:
        try:
            restarts_path.parent.mkdir(parents=True, exist_ok=True)
            restarts_path.write_text(json.dumps(current_restarts, sort_keys=True), encoding="utf-8")
        except OSError:
            pass
    linked_unknown = []
    linked_failed = []
    linked_path = home / "health" / "linked-sites.json"
    try:
        registry = json.loads(linked_path.read_text(encoding="utf-8"))
        sites = registry["sites"] if isinstance(registry, dict) and registry.get("version") == 1 else None
        if not isinstance(sites, list) or len(sites) > 16:
            raise ValueError("unsupported or oversized linked-site registry")
    except FileNotFoundError:
        # External plants have no coordinator or linked-site registry.
        sites = []
    except (OSError, ValueError, KeyError, TypeError):
        sites = None
        linked_unknown.append("registry")
        lines.append("LINKED SITES: UNKNOWN — registry unavailable or malformed")
    deadline = monotonic() + 5
    if sites is not None:
        for index, site in enumerate(sites):
            label = site.get("session") if isinstance(site, dict) else None
            if not isinstance(label, str) or not label.isprintable():
                label = str(index)
            try:
                if (not isinstance(site, dict) or not isinstance(site.get("home"), str)
                        or not Path(site["home"]).is_absolute()
                        or not isinstance(site.get("session"), str) or not site["session"].isprintable()):
                    raise ValueError("invalid site record")
                site_services = json.loads(
                    (Path(site["home"]) / "health" / "services.json").read_text(encoding="utf-8"))
                if (not isinstance(site_services, list) or len(site_services) > 32
                        or any(not isinstance(unit, str) or not unit.isprintable()
                               or not unit.endswith(".service")
                               or unit.startswith("-") or "/" in unit or any(c.isspace() for c in unit)
                               for unit in site_services)):
                    raise ValueError("invalid services list")
            except (OSError, ValueError, TypeError, KeyError):
                lines.append(f"LINKED SITE {label}: UNKNOWN — services unavailable or malformed")
                linked_unknown.append(label)
                continue
            site_failed = []
            site_unknown = []
            for unit in site_services:
                try:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        status = "unknown"
                    else:
                        result = subprocess.run(
                            ["systemctl", "--user", "is-active", unit], capture_output=True,
                            text=True, timeout=min(2, remaining), env=env)
                        status = result.stdout.strip()
                        if (status not in {"active", "inactive", "failed", "activating",
                                           "deactivating", "reloading", "maintenance"}
                                or (status == "active" and result.returncode != 0)):
                            status = "unknown"
                except (OSError, subprocess.TimeoutExpired):
                    status = "unknown"
                lines.append(f"LINKED SERVICE {label} {unit}: {status}")
                if status == "unknown":
                    site_unknown.append(unit)
                elif status != "active":
                    site_failed.append(unit)
            if site_failed:
                linked_failed.append(label)
                lines.append(f"LINKED SITE {label}: RED — inactive services={','.join(site_failed)}")
            elif site_unknown:
                linked_unknown.append(label)
                lines.append(f"LINKED SITE {label}: UNKNOWN — service status unavailable")
            else:
                lines.append(f"LINKED SITE {label}: PASS")
    lines.append(ci_line(home))
    if doctor.returncode or missing or extra or dead or failed_services or linked_failed:
        verdict = "FAIL health internal check"
        lines.append("STATE: RED — internal check needs repair")
    elif windows_unknown:
        verdict = "UNKNOWN health session unset"
        lines.append("STATE: UNKNOWN — session unset")
    elif linked_unknown:
        verdict = "UNKNOWN health linked-site data unavailable"
        lines.append("STATE: UNKNOWN — linked-site service data unavailable")
    elif local_services_unknown:
        verdict = "UNKNOWN health local service data unavailable"
        lines.append("STATE: UNKNOWN — local service data unavailable")
    elif not expected:
        verdict = "UNKNOWN health expected windows unset"
        lines.append("STATE: UNKNOWN — expected windows unset")
    else:
        verdict = "PASS health internal checks"
        lines.append("STATE: GREEN — internal checks pass")
    _report(home, "health", verdict)
    return "\n".join(lines) + "\n"


def permissions(home: Path) -> str:
    lines = ["GOAL: make every operator decision visible and scoped to the work it unblocks",
             "SELECT: bottom pane — Up/Down select, Enter grants one, q returns to shell",
             "COMMAND: permit (selector) | permit revoke (selector) | permit list | permit grant REQUEST_ID",
             "REQUEST: permit request ID --owner ROLE --task TASK --capability NAME --unblocks PATH --reason TEXT"]
    requests = list_requests(home)
    retired = retired_requests(home)
    pending = [(item, state) for item, state in requests if state == "pending" and item.identity not in retired]
    lines.append(f"PENDING: {len(pending)} · TOTAL: {len(requests)}")
    for item, state in requests[-20:]:
        display_state = f"RETIRED ({state.upper()})" if item.identity in retired else state.upper()
        lines.append(f"{item.identity} {display_state} owner={item.owner} task={item.task} "
                     f"capability={item.capability} unblocks={','.join(item.unblocks)}")
        if state == "pending" and item.identity not in retired:
            lines.append(f"  WHY: {item.reason}")
    active = recoveries(home)
    lines.append(f"RECOVERIES: {len(active)} unresolved obligations; keep other useful work moving")
    for row in active:
        status = "GRANTED; VERIFY RECOVERY" if row["status"] == "retry" else row["status"].upper()
        lines.extend([f"{row['id']} {status} owner={row['owner']} task={row['task']} resolver={row['resolver']}",
                      f"  MISSING: {row['missing']}", f"  NEXT: {row['action']}",
                      f"  ALTERNATIVES: {'; '.join(row['alternatives'])}",
                      f"  CUTOFF: {row['cutoff']}",
                      f"  EVIDENCE: {row['evidence']['path']}"])
    verdict = "UNKNOWN permission recovery route incomplete" if any(row["status"] == "unknown" for row in active) else "PASS permission requests visible"
    _report(home, "permissions", verdict)
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--view", choices=("discover", "senses", "health", "permissions"), required=True)
    args = parser.parse_args()
    print(globals()[args.view](args.home), end="")


if __name__ == "__main__":
    main()
