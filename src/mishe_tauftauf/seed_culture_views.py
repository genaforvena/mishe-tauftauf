"""Stable text panes for discovery, sensing, health, and operator decisions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import re
import sys
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path

from .access import list_requests, recoveries, retired_requests
from .ci_watch import line as ci_line
from .discovery import latest, producer_label, scan_attempt
from . import scan_freshness


def _report(home: Path, slug: str, verdict: str) -> None:
    path = home / "observations" / slug
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(verdict + "\n", encoding="utf-8")


def _scan_reading(snapshot: dict[str, object]) -> tuple[str, str]:
    current = scan_freshness.endpoint()
    bounds = scan_freshness.age_bounds(snapshot, current) if current is not None else None
    return (scan_freshness.classify_age(bounds, 900),
            scan_freshness.utc_consistency(snapshot))


def _attempt_reading(home: Path, snapshot: dict[str, object] | None) -> tuple[str, bool]:
    attempt = scan_attempt(home)
    if attempt is None:
        return "ATTEMPT: UNKNOWN — no durable scan attempt", False
    status = str(attempt.get("status", "unknown"))
    stage = str(attempt.get("stage", "unknown"))
    identity = attempt.get("scan_id")
    matches = snapshot is not None and identity == snapshot.get("scan_id") and isinstance(identity, str)
    line = (f"ATTEMPT: {status.upper()} stage={stage} scan_id={identity or 'unknown'} "
            f"created={attempt.get('created', 'unknown')}")
    if "artifact" in attempt:
        line += f" artifact={attempt['artifact']}"
    if "error" in attempt:
        line += f" error={attempt['error']}"
    if status == "succeeded" and not matches:
        line += " sample_match=unknown"
    return line, status == "succeeded" and matches


def _journal_bounds_valid(item: dict[str, object]) -> bool:
    coverage = item.get("coverage")
    count = item.get("count")
    if not isinstance(coverage, dict) or type(count) is not int or count < 0:
        return False
    boot = coverage.get("boot_id")
    if not isinstance(boot, str) or re.fullmatch(r"[0-9a-f]{32}", boot) is None:
        return False
    try:
        since = datetime.fromisoformat(coverage["since"].replace("Z", "+00:00"))
        until = datetime.fromisoformat(coverage["until"].replace("Z", "+00:00"))
        return since.utcoffset() is not None and until.utcoffset() is not None and since <= until
    except (KeyError, AttributeError, TypeError, ValueError):
        return False


def _observation_reading(item: dict[str, object], freshness: str) -> tuple[str, str]:
    source_state = str(item.get("state", "unknown"))
    state = source_state if freshness == "recent" or source_state == "unavailable" else "unknown"
    identity = str(item.get("id", "unknown"))
    sample = str(item.get("sample", "unknown"))
    if identity.startswith("sense.journal."):
        # Any journal sense carries the same bounds contract: a count over a
        # named boot and window. Trust the contract rather than a fixed id
        # list, so a new journal sense is not silently downgraded to UNKNOWN.
        valid = _journal_bounds_valid(item)
        if not valid and state != "unavailable":
            state = "unknown"
        coverage = item.get("coverage")
        fields = coverage if isinstance(coverage, dict) else {}
        detail = (f"historical sample={sample} fixed-window count={item.get('count', 'unknown')} "
                  f"since={fields.get('since', 'unknown')} until={fields.get('until', 'unknown')} "
                  f"boot={fields.get('boot_id', 'unknown')} source_state={source_state} "
                  f"source_bounds={'valid' if valid else 'unknown'} "
                  f"source_metadata={json.dumps({key: value for key, value in item.items() if key != 'id'}, sort_keys=True)}")
    else:
        detail = f"{sample[:120]} source_state={source_state}"
    if ((identity.startswith("sense.tmux.") and identity != "sense.tmux.windows")
            or item.get("cached") or item.get("imported")):
        if state != "unavailable":
            state = "unknown"
        detail += " source_freshness=unknown"
    return state, f"{state.upper()} {identity}: {detail}"


def discover(home: Path) -> str:
    lines = ["DESIRED STATE: useful reads have a real sample, honest status, and an owned next step"]
    snapshot = latest(home)
    attempt_line, attempt_succeeded = _attempt_reading(home, snapshot)
    lines.append(attempt_line)
    if snapshot is None:
        lines.append("STATE: UNKNOWN — no discovery scan yet")
        verdict = "UNKNOWN discover has no scan"
    else:
        freshness, consistency = _scan_reading(snapshot)
        lines.append(f"SCAN: {snapshot.get('created', 'unknown')} freshness={freshness} "
                     f"utc_consistency={consistency} producer={producer_label(home, snapshot.get('producer'))}")
        observations = snapshot.get("observations", [])
        unknown = 0
        for item in observations:
            state, line = _observation_reading(item, freshness)
            unknown += state not in {"verified", "unavailable", "available"}
            lines.append(line)
        if freshness != "recent":
            lines.append(f"STATE: UNKNOWN — scan freshness {freshness}; renew the read")
            verdict = f"UNKNOWN discover scan freshness {freshness}"
        elif not attempt_succeeded:
            lines.append("STATE: UNKNOWN — latest scan attempt not succeeded for this sample")
            verdict = "UNKNOWN discover latest attempt not succeeded"
        elif unknown:
            lines.append(f"STATE: UNKNOWN — {unknown} source reads unverified")
            verdict = f"UNKNOWN discover {unknown} source reads unverified"
        else:
            lines.append("STATE: GREEN — recent bounded read-only scan")
            verdict = "PASS discover recent scan"
    pending = [(item, state) for item, state in list_requests(home) if state == "pending"]
    lines.append(f"PERMISSION REQUESTS: {len(pending)} pending")
    for item, _ in pending[-5:]:
        lines.append(f"REQUEST {item.identity} unblocks={','.join(item.unblocks)}")
    lines.extend(["GOAL: discover useful new directions for Mishe and this plant's goals",
                  "PURSUIT: explore capabilities and crossed senses; use related literature when useful, assess fit, and check ideas",
                  "NEXT: renew an absent, stale, or unknown scan; otherwise choose one observation, reading, or experiment and record its outcome"])
    _report(home, "discover", verdict)
    return "\n".join(lines) + "\n"


def senses(home: Path) -> str:
    lines = ["GOAL: turn readings into honest senses with real samples and visible unknowns",
             "PURSUIT: wire useful reads, reproduce hollow or failed checks, and own reusable fixes through review, branch CI and ready integration",
             "NEXT: pursue a new useful read when an UNKNOWN has a documented retry condition; verify its live sample"]
    snapshot = latest(home)
    attempt_line, attempt_succeeded = _attempt_reading(home, snapshot)
    lines.append(attempt_line)
    if snapshot is None:
        verdict = "UNKNOWN senses no discovery sample"
        lines.append("STATE: UNKNOWN — no sample exists")
    else:
        freshness, consistency = _scan_reading(snapshot)
        senses_rows = [item for item in snapshot.get("observations", []) if str(item.get("id", "")).startswith("sense.")]
        unknown = 0
        unavailable = 0
        for item in senses_rows:
            state, line = _observation_reading(item, freshness)
            unknown += state not in {"verified", "unavailable"}
            unavailable += state == "unavailable"
            lines.append(line)
        lines.append(f"SCAN: {snapshot.get('created', 'unknown')} freshness={freshness} "
                     f"utc_consistency={consistency} producer={producer_label(home, snapshot.get('producer'))}")
        if freshness != "recent":
            verdict = f"UNKNOWN senses scan freshness {freshness}"
            lines.append(f"STATE: UNKNOWN — scan freshness {freshness}; renew the read")
        elif not attempt_succeeded:
            verdict = "UNKNOWN senses latest attempt not succeeded"
            lines.append("STATE: UNKNOWN — latest scan attempt not succeeded for this sample")
        elif unknown:
            verdict = f"UNKNOWN senses {unknown} unverified"
            lines.append(f"STATE: UNKNOWN — {unknown} senses need a checked read or honest unavailable claim")
        else:
            verdict = f"PASS senses {len(senses_rows)} verified samples"
            lines.append("STATE: GREEN — all wired sample reads verified")
        if unavailable:
            lines.append(f"UNAVAILABLE senses {unavailable} named an absent source, not a failed read")
    _report(home, "senses", verdict)
    return "\n".join(lines) + "\n"

def _service_readings(units: list[str], env: dict[str, str]) -> tuple[dict[str, tuple[str, str, int | None]], dict[str, list[str]]]:
    """One coherent systemctl sample for every listed unit, keyed by unit name.

    Per-unit ``is-active`` loops sample different instants, so a unit that crashes
    and is auto-restarted between two calls reads healthy each time: the restart
    window and the durable ``NRestarts`` counter never reach the pane. A failed
    read yields no readings, leaving every unit unknown rather than fabricating a
    healthy state. The same sample carries each unit's import roots so the caller
    can compare them with the pin without a second probe.
    """
    if not units:
        return {}, {}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", *units,
             "-p", "Id,ActiveState,SubState,NRestarts,Environment"],
            capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return {}, {}
    if result.returncode:
        return {}, {}
    from .wall_view import _import_roots
    readings: dict[str, tuple[str, str, int | None]] = {}
    roots: dict[str, list[str]] = {}
    for block in result.stdout.strip().split("\n\n"):
        values = dict(row.split("=", 1) for row in block.splitlines() if "=" in row)
        name = values.get("Id")
        if not name:
            continue
        count = values.get("NRestarts", "")
        readings[name] = (values.get("ActiveState", "unknown"), values.get("SubState", "unknown"),
                          int(count) if count.isdigit() else None)
        unit_roots = _import_roots(values.get("Environment", ""))
        if unit_roots:
            roots[name] = unit_roots
    return readings, roots



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
        dead = [f"{name}.0" for name in expected & actual
                if pane_states.get((name, "0")) != "0"]
        # A chartered mind's bottom pane can die at status 127 while its
        # renderer (.0) stays live: `remain-on-exit` keeps the corpse visible, so
        # the renderer lease cannot see it. `doctor --panes` checks this, but
        # nothing runs that periodically, and one busy peer masks a wedged mind
        # on the global activity line. Detect it from the pane snapshot already
        # taken, for windows that ship a mind launcher.
        dead += [f"{name}.1" for name in expected & actual
                 if (home / "minds" / name).is_file()
                 and pane_states.get((name, "1"), "0") != "0"]
        dead = sorted(dead)
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
    readings, service_roots = _service_readings(services, env)
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
    from .wall_view import pane_lease_lines
    lease_lines, lease_state = pane_lease_lines(home)
    lines.extend(lease_lines)
    causes: list[str] = []
    if doctor.returncode:
        causes.append("doctor")
    if missing:
        causes.append("windows-missing=" + ",".join(missing))
    if extra:
        causes.append("windows-extra=" + ",".join(extra))
    if dead:
        causes.append("windows-dead=" + ",".join(dead))
    if failed_services:
        causes.append("services=" + ",".join(failed_services))
    # Fold the runtime import-root check in, so a latched RED from the pane's
    # STATE line is also named in the durable report. The pane's own RUNTIME
    # line reads DRIFT when a covered service imports another root; an invalid
    # or absent pin reads UNKNOWN/UNPINNED and is not itself a fault.
    from .wall_view import _runtime_state
    runtime_state, _ = _runtime_state(home, Path(__file__).resolve().parents[2], service_roots)
    if runtime_state == "DRIFT":
        causes.append("runtime-drift")
    if linked_failed:
        causes.append("linked-sites=" + ",".join(linked_failed))
    if lease_state == "STALE":
        causes.append("pane-lease")
    # The report is the pane's SYSTEM ZERO line and the only durable record of a
    # check that runs at most every 600s. Name the failing predicate and the
    # compute time so a latched RED is diagnosable after the transient is gone.
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if causes:
        verdict = f"FAIL health internal check at {at} — " + "; ".join(causes)
        lines.append("STATE: RED — internal check needs repair")
    elif windows_unknown:
        verdict = f"UNKNOWN health session unset at {at}"
        lines.append("STATE: UNKNOWN — session unset")
    elif linked_unknown:
        verdict = f"UNKNOWN health linked-site data unavailable at {at}"
        lines.append("STATE: UNKNOWN — linked-site service data unavailable")
    elif local_services_unknown:
        verdict = f"UNKNOWN health local service data unavailable at {at}"
        lines.append("STATE: UNKNOWN — local service data unavailable")
    elif not services:
        # `plant.write_service_manifest` writes an empty manifest for a site that
        # installed no services, so the pane's `_service_block` reads it as RED
        # ("an empty list is not healthy"). Name it in the durable report too, so
        # a latched pane RED is diagnosable here instead of reading PASS; a
        # malformed or missing manifest is handled above as UNKNOWN.
        verdict = f"FAIL health internal check at {at} — services-manifest-empty"
        lines.append("STATE: RED — internal check needs repair")
    elif not expected:
        verdict = f"UNKNOWN health expected windows unset at {at}"
        lines.append("STATE: UNKNOWN — expected windows unset")
    else:
        verdict = f"PASS health internal checks at {at}"
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
