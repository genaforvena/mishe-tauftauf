"""Deterministic replacement readings for legacy runtime-only senses.

Two of the legacy readings the migrated scanner no longer emits describe the
plant's own tmux and renderer: ``sense.tmux.windows`` and
``sense.runtime.renderer-coverage``. Each reader here is a pure function over
injected observations, plus one thin probe, so the decision rules are testable
without a live tmux server.

Validity rules: a probe that cannot run, a missing socket, or an unreadable
pinned root reads UNKNOWN, never calm. A reachable server with no windows is a
fact (verified, count 0) only when the server itself answered.

Nothing here is wired into the running scanner; the wiring is a reviewed patch.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

TMUX_SENSE_ID = "sense.tmux.windows"
RENDERER_SENSE_ID = "sense.runtime.renderer-coverage"

# Modules the plant view entry point needs at the pinned runtime. The view host
# execs ``mishe_tauftauf.plant_view``; the boundary module is imported by it.
RENDERER_MODULES = ("plant_view", "plant_view_boundary")

from . import proc_table, unit_properties
from .plant_tmux import WINDOW_FORMAT as _WINDOW_FORMAT, parse_list_windows, parse_session_births, parse_session_pids


def _run_tmux(socket: str, args: list[str], timeout: float = 5) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["tmux", "-S", socket, *args], text=True,
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def read_tmux_windows(socket: str | None, *, runner=_run_tmux) -> dict[str, object]:
    """Sense of the inner tmux server's windows, or UNKNOWN with the cause.

    `runner(socket, args)` returns a CompletedProcess or None (probe could not
    run). Exit 0 with parseable output is verified. Any other outcome is UNKNOWN,
    because "no server" and "probe broken" look alike from the outside.
    """
    base = {"id": TMUX_SENSE_ID, "kind": "read", "source": socket or "unset"}
    if not socket:
        return {**base, "state": "unknown", "sample": "no tmux socket configured"}
    result = runner(socket, ["list-windows", "-a", "-F", _WINDOW_FORMAT])
    if result is None:
        return {**base, "state": "unknown", "sample": "tmux probe could not run"}
    if result.returncode != 0:
        return {**base, "state": "unknown",
                "sample": f"tmux list-windows exit={result.returncode} (no server or refused)"}
    windows = parse_list_windows(result.stdout or "")
    if windows is None:
        return {**base, "state": "unknown", "sample": "tmux list-windows output malformed"}
    if not windows:
        return {**base, "state": "verified", "sample": "windows=0 (server answered)",
                "windows": []}
    names = sorted({f"{w['session']}:{w['window']}" for w in windows})
    panes = sum(int(w["panes"]) for w in windows)
    return {**base, "state": "verified",
            "sample": f"windows={len(windows)} panes={panes} names={','.join(names)}",
            "windows": windows}


def _module_problem(path: Path) -> str | None:
    """Return None when `path` is a parseable module, else a failure reason."""
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
    except (OSError, UnicodeDecodeError) as exc:
        return f"unreadable: {exc.__class__.__name__}"
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return f"unparseable at line {exc.lineno}"
    return None


def read_renderer_coverage(pin_root: Path | None, *,
                           modules: tuple[str, ...] = RENDERER_MODULES) -> dict[str, object]:
    """Whether the pinned runtime carries every module the renderer needs.

    A missing or unparseable module is a verified drift fact (state "drift"), since
    the root was read. An unset or unreadable root is UNKNOWN: the absence of a
    root is not the same as a complete renderer.
    """
    base = {"id": RENDERER_SENSE_ID, "kind": "read", "source": str(pin_root or "unset")}
    if pin_root is None or not pin_root.is_dir():
        return {**base, "state": "unknown", "sample": "pinned runtime root not readable"}
    package = pin_root / "src" / "mishe_tauftauf"
    problems = []
    for name in modules:
        reason = _module_problem(package / f"{name}.py")
        if reason is not None:
            problems.append(f"{name}.py={reason}")
    if problems:
        return {**base, "state": "drift",
                "sample": "renderer modules incomplete: " + "; ".join(problems),
                "missing": problems}
    return {**base, "state": "verified",
            "sample": f"renderer modules present: {','.join(f'{m}.py' for m in modules)}"}


ROLE_RENDER_SENSE_ID = "sense.runtime.role-render-coverage"


def read_role_render_coverage(roles: list[str] | None, render) -> dict[str, object]:
    """Whether each declared role renders through the real renderer path.

    ``render(role)`` is the injected renderer; the scanner passes the plant view.
    A role whose render raises or returns nothing is drift (the renderer ran
    and failed). An unreadable role list is UNKNOWN, never calm.
    """
    base = {"id": ROLE_RENDER_SENSE_ID, "kind": "read", "source": "plant_view.render"}
    if roles is None:
        return {**base, "state": "unknown", "sample": "declared roles could not be read"}
    if not roles:
        return {**base, "state": "unknown", "sample": "no declared roles to render"}
    failed = []
    for role in roles:
        try:
            text = render(role)
        except Exception as exc:  # the renderer ran and raised: a fact, not calm
            failed.append(f"{role}={type(exc).__name__}")
            continue
        if proc_table.is_blank_text(text):
            failed.append(f"{role}=empty")
    if failed:
        return {**base, "state": "drift", "missing": failed,
                "sample": f"{len(failed)} of {len(roles)} declared roles did not render: " + "; ".join(failed)}
    return {**base, "state": "verified",
            "sample": f"rendered {len(roles)} declared roles: {','.join(roles)}"}


SESSION_SENSE_ID = "sense.host.session-stability"


def resolve_declared_session(declared: str | None, live: list[str] | None) -> str | None:
    """The session name this reading expects the plant's server to serve.

    The env override wins: a caller that was told which session it serves knows
    better than an enumeration (the historical ``_plant_session`` rule). With no
    override, the plant's own inner socket is site-namespaced, so a listing that
    names exactly one session names this home's session, and the reading can
    judge it. Zero sessions, two or more, or an unreadable listing names no
    single expectation, so the judge keeps its ``declared``-missing UNKNOWN
    rather than guessing one name.
    """
    if declared:
        return declared
    if live is not None and len(live) == 1:
        return live[0]
    return None


def judge_session_stability(declared: str | None, live: list[str] | None,
                            placements: dict[str, str | None] | None) -> dict[str, object]:
    """Judge the plant's tmux server from injected observations.

    ``live`` is the session names tmux answered with (``None`` when the list
    probe could not give an answer). ``placements`` maps each of this socket's
    own server pids to its ``proc_table.server_containment`` text, or None when
    that pid's cgroup was unreadable (``None`` for the whole mapping when the
    pid list itself was unreadable). The host guard no longer applies the -900
    OOM shield; containment by a managed slice with a finite memory bound
    replaces it, so the shield is not the invariant read here.

    A declared session absent from a real answer is drift. A server placed
    outside a managed slice, or without a finite bound, is drift (the
    ``unshielded`` key names these for consumers). Every unreadable input stays
    UNKNOWN, never calm.
    """
    base = {"id": SESSION_SENSE_ID, "kind": "read", "session": declared or "unset"}
    if live is None:
        return {**base, "state": "unknown", "sample": "tmux session list could not be read"}
    if placements is None:
        return {**base, "state": "unknown", "sample": "tmux server pids could not be read",
                "live": live}
    unreadable = sorted(pid for pid, place in placements.items() if place is None)
    if unreadable:
        return {**base, "state": "unknown", "live": live,
                "sample": f"containment unreadable for pids={','.join(unreadable)}"}
    # The two probes must agree that a server exists. Empty PID output beside
    # live sessions, or PIDs beside an empty session list, is contradictory
    # acquisition (e.g. the server vanished between probes): never calm.
    if bool(live) != bool(placements):
        return {**base, "state": "unknown", "live": live,
                "sample": f"live=[{','.join(live)}] server-pids={len(placements)} disagree; probes not coherent"}
    unshielded = sorted(pid for pid, place in placements.items() if proc_table.placement_unshielded(place))
    listed = f"live=[{','.join(live)}]"
    # An unshielded server is a drift fact whatever the session is called.
    if unshielded and not declared:
        return {**base, "state": "drift", "live": live, "unshielded": unshielded,
                "sample": f"{listed} unshielded-servers={','.join(unshielded)} declared=unset"}
    if not declared:
        return {**base, "state": "unknown", "live": live, "unshielded": [],
                "sample": f"{listed} declared session unreadable"}
    if declared not in live:
        return {**base, "state": "drift", "live": live, "unshielded": unshielded,
                "sample": f"{listed} session-missing={declared} unshielded=[{','.join(unshielded)}]"}
    if unshielded:
        return {**base, "state": "drift", "live": live, "unshielded": unshielded,
                "sample": f"{listed} unshielded-servers={','.join(unshielded)}"}
    return {**base, "state": "verified", "live": live, "unshielded": [],
            "sample": f"{listed} servers={len(placements)} contained"}


def _run_socket_tmux(socket: str, args: list[str], timeout: float = 2) -> subprocess.CompletedProcess | None:
    """Run tmux on the plant's own socket; None when the probe cannot run."""
    try:
        return subprocess.run(["tmux", "-S", socket, *args], text=True, capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def probe_session_stability(declared: str | None, socket: str | None, *,
                            runner=_run_socket_tmux, shield_reader=None) -> dict[str, object]:
    """Thin probe over the plant's own tmux socket, then the server placements.

    ``runner(socket, args)`` returns a CompletedProcess or None. An unset socket
    is UNKNOWN: the default socket may hold another server, not this plant's. A "no server running"
    answer is a fact (no live sessions); any other failure is UNKNOWN. The
    shield read is only taken for pids that tmux itself lists as this socket's
    session servers, so a foreign test server is never judged here.
    """
    if not socket:
        return judge_session_stability(declared, None, None)
    if shield_reader is None:
        shield_reader = proc_table.server_containment
    listed = runner(socket, ["list-sessions", "-F", "#{session_name}"])
    if listed is None:
        live: list[str] | None = None
    elif listed.returncode == 0:
        live = proc_table.window_names(listed.stdout or "")
    elif "no server running" in (listed.stderr or ""):
        live = []
    else:
        live = None
    owners = runner(socket, ["list-sessions", "-F", "#{pid}"])
    placements: dict[str, str | None] | None = None
    if owners is not None and owners.returncode == 0:
        # Empty or non-numeric output stays None (unreadable), never "no servers".
        pids = proc_table.strict_pane_pids(owners.stdout or "")
        if pids is not None:
            placements = {str(pid): shield_reader(str(pid)) for pid in pids}
    elif owners is not None and "no server running" in (owners.stderr or ""):
        placements = {}
    # The socket is site-namespaced, so its unique session is this plant's own;
    # the declared expectation stays unset only when the listing cannot name one.
    declared = resolve_declared_session(declared, live)
    return judge_session_stability(declared, live, placements)


SESSION_RESTARTS_ID = "sense.host.session-restarts"
RESTART_WINDOW_MINUTES = 60
RESTART_MAX_BIRTHS = 2


def judge_session_restarts(created: list[tuple[str, float]] | None, now: float, *,
                           declared: str | None = None,
                           window_minutes: int = RESTART_WINDOW_MINUTES,
                           max_births: int = RESTART_MAX_BIRTHS) -> dict[str, object]:
    """Count session births in the window from injected creation times.

    tmux keeps only the creation time of sessions still alive, so this reading
    sees births whose session is still running; a restart whose replacement has
    already ended is invisible and is stated in ``coverage``. ``created`` is None
    when the probe could not answer (including no server, whose births cannot be
    observed). More births than ``max_births`` in the window is drift.
    """
    base = {"id": SESSION_RESTARTS_ID, "kind": "read", "session": declared or "unset",
            "coverage": "live sessions only; births of already-ended sessions are not observable"}
    if created is None:
        return {**base, "state": "unknown", "sample": "tmux session birth times could not be read"}
    if not created:
        return {**base, "state": "unknown", "sample": "tmux answered without sessions"}
    if any(born > now + 60 for _, born in created):
        return {**base, "state": "unknown", "sample": "session birth time is in the future; clock disagrees"}
    since = now - window_minutes * 60
    births = sorted(name for name, born in created if born >= since)
    listed = f"births={len(births)} window={window_minutes}m"
    if len(births) > max_births:
        return {**base, "state": "drift", "births": len(births), "verdict": "churning",
                "sample": f"{listed} sessions={','.join(births)} exceeds max={max_births}"}
    return {**base, "state": "verified", "births": len(births), "verdict": "steady",
            "sample": f"{listed} max={max_births} steady"}


def probe_session_restarts(socket: str | None, now: float, *, declared: str | None = None,
                           runner=_run_socket_tmux) -> dict[str, object]:
    """Thin probe: session creation times on the plant's own socket."""
    base = {"id": SESSION_RESTARTS_ID, "kind": "read", "session": declared or "unset"}
    if not socket:
        return {**base, "state": "unknown", "sample": "no tmux socket configured; default server not read"}
    listed = runner(socket, ["list-sessions", "-F", "#{session_name}\t#{session_created}"])
    if listed is None:
        return judge_session_restarts(None, now, declared=declared)
    if listed.returncode != 0:
        if "no server running" in (listed.stderr or ""):
            return {**judge_session_restarts(None, now, declared=declared),
                    "sample": "no tmux server; births not observable"}
        return judge_session_restarts(None, now, declared=declared)
    # Same declaration rule as session-stability: the plant's own socket names
    # its own session, so a unique listing answers "expected" without a caller's
    # env. The judge's own declared-missing path stays for every other shape.
    births = parse_session_births(listed.stdout or "")
    declared = resolve_declared_session(declared,
                                        [name for name, _ in births] if births is not None else None)
    return judge_session_restarts(births, now, declared=declared)


SERVER_SLICE_ID = "sense.host.server-slice"


def judge_server_slice(expected: dict[str, str] | None,
                       placements: dict[str, str | None] | None) -> dict[str, object]:
    """Judge that each named mishe server sits in its managed slice.

    ``expected`` maps a server label (e.g. ``mishe-life-support@1827``) to the
    slice it must be contained in (e.g. ``mishe-lifeline.slice``). ``placements``
    maps the same labels to ``proc_table.server_containment`` text, or None when
    that cgroup was unreadable. Missing input is UNKNOWN, never calm: a None
    mapping, an unreadable placement, or nothing expected at all. A server that
    is missing, unbounded, or contained by a different slice is drift.
    """
    base = {"id": SERVER_SLICE_ID, "kind": "read"}
    if expected is None or placements is None:
        return {**base, "state": "unknown", "sample": "server containment could not be read"}
    if not expected:
        return {**base, "state": "unknown", "sample": "no expected servers declared"}
    unreadable = sorted(label for label in expected if label in placements and placements[label] is None)
    if unreadable:
        return {**base, "state": "unknown",
                "sample": f"containment unreadable for servers={','.join(unreadable)}"}
    missing = sorted(label for label in expected if label not in placements)
    if missing:
        return {**base, "state": "drift", "sample": f"servers-missing={','.join(missing)}"}
    wrong = []
    for label, slice_name in sorted(expected.items()):
        place = placements[label]
        if proc_table.placement_unshielded(place) or place != f"contained:{slice_name}":
            wrong.append(f"{label}={place}")
    if wrong:
        return {**base, "state": "drift",
                "sample": f"not-in-expected-slice: {'; '.join(wrong)}"}
    placed = ", ".join(f"{label}={slice_name}" for label, slice_name in sorted(expected.items()))
    return {**base, "state": "verified", "sample": f"servers placed correctly: {placed}"}


def probe_server_slice(expected: dict[str, tuple[str, str]] | None, *,
                       containment=proc_table.server_containment) -> dict[str, object]:
    """Thin probe: ``expected`` maps label -> (pid, required slice)."""
    if not expected:
        return judge_server_slice(None if expected is None else {}, None if expected is None else {})
    slices = {label: slice_name for label, (_pid, slice_name) in expected.items()}
    placements = {label: containment(pid) for label, (pid, _slice) in expected.items()}
    return judge_server_slice(slices, placements)


def read_server_slice(socket: str | None, *, runner=_run_socket_tmux,
                      containment=proc_table.server_containment) -> dict[str, object]:
    """Server-slice reading over the plant's own tmux socket (sessions it lists).

    Coverage is the inner socket only: servers on other sockets are not judged
    here. An unset socket, a failed listing or an unreadable line is UNKNOWN; a
    socket with no running server has nothing to judge, also UNKNOWN.
    """
    if not socket:
        return probe_server_slice(None)
    listed = runner(socket, ["list-sessions", "-F", "#{session_name}\t#{pid}"])
    if listed is None:
        return probe_server_slice(None)
    if listed.returncode == 0:
        return probe_server_slice(proc_table.expected_from_listing(listed.stdout or ""), containment=containment)
    if "no server running" in (listed.stderr or ""):
        return judge_server_slice({}, {})
    return probe_server_slice(None)


SERVER_KEEPER_ID = "sense.host.server-keeper"


def judge_server_keeper(sessions: dict[str, tuple[str, str]] | None) -> dict[str, object]:
    """Judge that each live session's tmux server is held by its own keeper unit.

    ``sessions`` maps a session name to ``(server pid, cgroup unit)``; the unit is
    ``proc_table.read_cgroup_unit`` of that pid, "" when unreadable. A session's
    server is verified only when its cgroup unit is one of that session's own
    keeper units (``proc_table.belongs_to_session``). A server held by a foreign
    unit (for example a test keeper) is drift, because nothing restarts it for the
    session. None, an empty mapping or an unreadable unit is UNKNOWN, never calm.
    """
    base = {"id": SERVER_KEEPER_ID, "kind": "read"}
    if sessions is None:
        return {**base, "state": "unknown", "sample": "session listing could not be read"}
    if not sessions:
        return {**base, "state": "unknown", "sample": "no live session to judge"}
    unreadable = sorted(name for name, (_pid, unit) in sessions.items() if not unit)
    if unreadable:
        return {**base, "state": "unknown",
                "sample": f"keeper unit unreadable for sessions={','.join(unreadable)}"}
    foreign = []
    for name, (pid, unit) in sorted(sessions.items()):
        if not proc_table.belongs_to_session(unit, name):
            foreign.append(f"{name} server-pid={pid} held-by={unit}")
    if foreign:
        return {**base, "state": "drift", "sample": "not held by own keeper: " + "; ".join(foreign)}
    held = "; ".join(f"session={name} server-pid={pid} keeper={unit}"
                     for name, (pid, unit) in sorted(sessions.items()))
    return {**base, "state": "verified", "sample": held}


def read_server_keeper(socket: str | None, *, runner=_run_socket_tmux,
                       cgroup_unit=proc_table.read_cgroup_unit) -> dict[str, object]:
    """Server-keeper reading over the plant's own tmux socket.

    An unset socket, a failed listing or a malformed line is UNKNOWN. A socket
    with no running server has no keeper to judge, so it is also UNKNOWN.
    """
    if not socket:
        return judge_server_keeper(None)
    listed = runner(socket, ["list-sessions", "-F", "#{session_name}\t#{pid}"])
    if listed is None:
        return judge_server_keeper(None)
    if listed.returncode != 0:
        return judge_server_keeper(None)
    pids = parse_session_pids(listed.stdout or "")
    if pids is None:
        return judge_server_keeper(None)
    sessions = {name: (pid, cgroup_unit(pid)) for name, pid in pids.items()}
    return judge_server_keeper(sessions)


DRIFT_SITES_ID = "sense.runtime.drift-across-sites"
DRIFT_SITES_TIMEOUT = 5


def judge_drift_across_sites(units: dict[str, dict] | None, site: str) -> dict[str, object]:
    """Judge that no unit under a mishe slice mixes site homes or foreign ones.

    ``units`` maps a unit name to ``{"declared": set, "live": set, "readable": bool}``:
    the site homes its ExecStart/WorkingDirectory declare, the site homes its live
    processes run from (cwd and argv), and whether both were read. A unit whose
    declared and live homes together name more than one site is drift (a unit
    running from another site than it declares, or two sites at once). A unit
    whose homes never include this site (``site``) is drift: a foreign-site
    service placed in this plant's slice. A unit naming no site home is not
    attributable and is left out. None, an empty mapping or no attributable unit
    is UNKNOWN, and so is an unreadable unit unless a drift fact already exists.
    """
    base = {"id": DRIFT_SITES_ID, "kind": "read", "site": site or "unset"}
    if units is None:
        return {**base, "state": "unknown", "sample": "mishe slice membership could not be read"}
    if not units:
        return {**base, "state": "unknown", "sample": "no unit under a mishe slice to judge"}
    drift: list[str] = []
    unreadable: list[str] = []
    attributed = 0
    for name, row in sorted(units.items()):
        if not row.get("readable"):
            unreadable.append(name)
            continue
        every = set(row["declared"]) | set(row["live"])
        if not every:
            continue
        attributed += 1
        homes = ",".join(sorted(every))
        if len(every) > 1:
            drift.append(f"{name} spans sites={homes}")
        elif site not in every:
            drift.append(f"{name} foreign-site={homes} placed in mishe slice")
    if drift:
        return {**base, "state": "drift", "drift": len(drift),
                "sample": "; ".join(drift) + (f"; unreadable={','.join(unreadable)}" if unreadable else "")}
    if unreadable:
        return {**base, "state": "unknown",
                "sample": f"unit data unreadable for {','.join(unreadable)}"}
    if not attributed:
        return {**base, "state": "unknown", "sample": "no unit names a site home"}
    return {**base, "state": "verified", "attributed": attributed,
            "sample": f"units={attributed} single-site={site}"}


def _run_user_show(unit: str) -> str | None:
    """``systemctl --user show`` declaration text for one unit, None when it cannot run."""
    try:
        done = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ExecStart", "-p", "WorkingDirectory"],
                              capture_output=True, text=True, timeout=DRIFT_SITES_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def read_drift_across_sites(site: str | None, *, proc_root: Path = Path("/proc"),
                            declared=_run_user_show,
                            cgroup_of=proc_table.read_cgroup_path) -> dict[str, object]:
    """Thin probe: group same-uid processes by mishe slice unit and judge their homes.

    Processes in another uid's cgroup are not listed by the user instance and are
    outside this reading, as they were for the legacy one. A process whose cgroup
    cannot be read while it still exists is an unreadable member, not calm.
    """
    if not site:
        return judge_drift_across_sites(None, "")
    try:
        pids = [name for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return judge_drift_across_sites(None, site)
    units: dict[str, dict] = {}
    for pid in pids:
        cgroup = cgroup_of(pid)
        unit = unit_properties.mishe_slice_unit(cgroup)
        if unit is None:
            if cgroup is None and (proc_root / pid).is_dir():
                units.setdefault("<unreadable-cgroup>", {"declared": set(), "live": set(), "readable": False})
            continue
        row = units.setdefault(unit, {"declared": set(), "live": set(), "readable": True})
        try:
            cmdline = (proc_root / pid / "cmdline").read_bytes()
            cwd = os.readlink(proc_root / pid / "cwd")
        except FileNotFoundError:
            continue  # exited between listing and reading
        except OSError:
            row["readable"] = False
            continue
        row["live"] |= unit_properties.cmdline_site_homes(cmdline)
        home = unit_properties.site_home(cwd)
        if home:
            row["live"].add(home)
    for unit, row in units.items():
        if not row["readable"]:
            continue
        text = declared(unit)
        if text is None:
            row["readable"] = False
        else:
            row["declared"] = unit_properties.declared_site_homes(text)
    return judge_drift_across_sites(units, site)
