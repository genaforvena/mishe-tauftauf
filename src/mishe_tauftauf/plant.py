#!/usr/bin/env python3
"""Core lifecycle: plant the resident channels on one Linux account."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from . import ci_watch, discovery, seed, seed_permission_panel
from .feed import Feed
from .tmux import _tmux, owns_session

# Resident mind roles the plant launches and supervises. `docs` is a chartered,
# wall-addressable role (activity.ROLES, seed_docs_charter.md); leaving it out
# meant its pane existed only when something else respawned it directly, so a
# reboot dropped it with no service to raise it again.
ROLES = ("genome", "witness", "discover", "senses", "health", "docs", "research-methods")
EXPLORATION = ("discover", "senses")
CONTRACT_START = "<!-- mishe-tauftauf plant contract -->"
CONTRACT_END = "<!-- end mishe-tauftauf plant contract -->"
OUT_OF_BAND = ("silence",)
"""Supervisors the plant reconciles but does not launch.

``coordination`` installs itself (coordination/launcher.py) because only the core
checkout runs it; ``silence`` has no site unit file at all on this plant, so it is
reconciled from ``silence_unit_text``. Both stay out of ``ROLES``: they are
supervisors, not seeded minds, so neither gets a pane or a mind directory. They
*are* in the persist loop, because a pin advance that repoints only the seeded
minds leaves them importing the old root and the dashboard reads DRIFT.
"""

def site_declared_roles(home: Path) -> set[str]:
    """Site-declared residents beyond the built-in roles.

    A site can add a resident by dropping a charter in ``charters/<slug>.md``
    and an executable launcher in ``minds/<slug>``. The plant preserves those
    windows across a replant instead of overwriting them with the hard-coded
    set. An unreadable site returns an empty set.
    """
    declared: set[str] = set()
    try:
        for path in (home / "charters").glob("*.md"):
            if (home / "minds" / path.stem).is_file():
                declared.add(path.stem)
    except OSError:
        return set()
    return declared


def _manifest_windows(home: Path) -> list[str]:
    """Windows already recorded in ``health/windows.json``, or an empty list."""
    manifest = home / "health" / "windows.json"
    if not manifest.exists():
        return []
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _expected_windows(required: set[str], previous: list[str], live: set[str]) -> list[str]:
    """Expected windows to record for the health check after a plant.

    The required set plus every previously recorded window still present, so an
    extra window a site relies on survives a replant. A name that is neither
    required nor live is dropped: a renamed or closed window must not become a
    permanent expectation that fails the health check forever.
    """
    return sorted(required | (set(previous) & live))


def refresh_contract(current: str, contract: str) -> str:
    block = contract.rstrip() + "\n" + CONTRACT_END
    if CONTRACT_START not in current:
        return current.rstrip() + ("\n\n" if current.strip() else "") + block + "\n"
    before, after = current.split(CONTRACT_START, 1)
    suffix = after.split(CONTRACT_END, 1)[1] if CONTRACT_END in after else ""
    return before.rstrip() + ("\n\n" if before.strip() else "") + block + suffix.rstrip() + "\n"


def unit_name(session: str, slug: str) -> str:
    return f"{session}-{slug}.service"


def unit_fragment_matches(fragment: str, expected: Path) -> bool:
    return Path(fragment).resolve() == expected.resolve()


def unit_fragment_mismatch(fragment: str, expected: Path) -> str | None:
    """Why a linked fragment is not ``expected``, or None when it agrees.

    A linked fragment that resolves to the site unit is the shape plant itself
    installs, so a replant rewrites it in place. Anything else is a defect the
    caller must not paper over: a *regular file* at the systemd path has drifted
    out of band and silently blocks every later refresh, and the bare "already
    belongs to" that ``plant`` used to raise reads like a write conflict rather
    than the shape defect it is, so name the shape.
    """
    if not fragment:
        return None
    linked = Path(fragment)
    if linked.resolve() == expected.resolve():
        return None
    if linked.is_symlink():
        return f"symlink points at {linked.resolve()} instead of the site unit"
    return f"regular file instead of a symlink to the site unit"

def service_manifest(home: Path, session: str, persist: bool) -> list[str]:
    """Units a plant is responsible for, merged with units already installed.

    Out-of-band supervisors (`coordination`, `silence`) install their own units
    and append themselves to the manifest; overwriting here would drop them from
    dashboard coverage, so a dead one would read GREEN and silent. Keep any
    installed unit of this session even when the plant no longer generates it.
    `silence` is the exception: the plant generates it now (see
    ``silence_unit_text``), so it is named here rather than only inherited from
    an existing manifest. `coordination` still installs itself, because only the
    core checkout runs it.
    """
    units = [unit_name(session, slug) for slug in (*ROLES, "permissions", "ci", *OUT_OF_BAND)]
    if not persist:
        return []
    installed = set()
    path = home / "health" / "services.json"
    if path.is_file():
        try:
            installed = {name for name in json.loads(path.read_text(encoding="utf-8"))
                         if isinstance(name, str)}
        except (OSError, ValueError, json.JSONDecodeError):
            installed = set()
    prefix = f"{session}-"
    return sorted({*units, *(name for name in installed if name.startswith(prefix))})


def write_service_manifest(home: Path, session: str, persist: bool) -> list[str]:
    """Record the units this plant is responsible for without erasing coverage.

    A plant that installs no services computes an empty manifest; writing it over
    a live site's manifest would drop every unit from dashboard coverage, so an
    existing manifest is preserved. A fresh site still gets the empty manifest so
    the dashboard reports the missing services rather than an unreadable file.
    """
    manifest = service_manifest(home, session, persist)
    path = home / "health" / "services.json"
    if manifest or not path.exists():
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    return manifest


def ensure_engine_for_new_minds(home: Path, engine_command: str) -> None:
    argv = shlex.split(engine_command)
    if not argv:
        raise ValueError("engine command is empty")
    if any(not (home / "minds" / slug).is_file() for slug in ROLES) and shutil.which(argv[0]) is None:
        raise ValueError(f"agent command unavailable: {argv[0]}")


def preferred_operator_window(home: Path, requested: str | None) -> str:
    """Name the human operator's convenience shell. It is not a role or a gate."""
    names = _manifest_windows(home)
    if names:
        candidates = set(names) - {*ROLES, "permissions", *site_declared_roles(home)}
        if len(candidates) == 1:
            existing = candidates.pop()
            if requested and requested != existing:
                raise ValueError(f"site already uses operator window {existing!r}; requested {requested!r}")
            return existing
    return requested or "operator"


def site_and_session(workspace: Path, default_site_name: str, home: Path | None,
                     session: str | None) -> tuple[Path, str]:
    if home is None:
        candidates = [workspace / name for name in (".mishe-seed", ".mishe-tauftauf")
                      if (workspace / name / ".seed-raised").is_file()]
        if len(candidates) > 1:
            raise ValueError("multiple resident sites exist; pass --home and --session")
        home = candidates[0] if candidates else workspace / default_site_name
    if session is None:
        session = seed.recorded_session(home)
    if session is None:
        session = "mishe-seed" if default_site_name == ".mishe-seed" else "mishe-" + workspace.name.replace("_", "-")
    return home, session


def _service_search_path(home: Path) -> str:
    """The service ``PATH`` with this site's ``bin`` exactly once, at the front.

    The seed service already runs with this site's ``bin`` on ``PATH`` from the
    unit that launched it, so prepending it unconditionally added one duplicate
    on every replant: the generated unit was never idempotent and the entry list
    grew without bound. Keep the site entry at the front, drop its earlier
    occurrences, and leave every other entry — duplicates and empty entries
    included — in order.
    """
    site_bin = str(home / "bin")
    inherited = os.environ.get("PATH", "/usr/bin:/bin")
    rest = [entry for entry in inherited.split(os.pathsep) if entry != site_bin]
    return os.pathsep.join([site_bin, *rest])


def unit_text(home: Path, session: str, slug: str, python: str) -> str:
    from .runtime_source import source_for
    source = source_for(home, ROOT)
    self_pick_seconds = 3600 if slug == "research-methods" else 600 if slug == "discover" else 300
    command = (f"{python} -m mishe_tauftauf.ci_watch --home {home} --follow" if slug == "ci" else
               f"{python} -m mishe_tauftauf.seed_permission_panel --home {home} --session {session} --follow"
               if slug == "permissions" else
               f"{python} -m mishe_tauftauf --home {home} seed run --session {session} "
               f"--slug {slug} --interval 5 --self-pick-seconds {self_pick_seconds} "
               "--clear-grace-seconds 30")
    return (
        f"[Unit]\nDescription=Mishe {slug} resident channel\nAfter=default.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={home.parent}\n"
        f"Environment=PYTHONPATH={source / 'src'}\n"
        f"Environment=PATH={_service_search_path(home)}\n"
        f"ExecStart={command}\n"
        "Restart=always\nRestartSec=15\n\n[Install]\nWantedBy=default.target\n"
    )

def silence_unit_text(home: Path, session: str, python: str) -> str:
    """The silence watcher's unit, generated from the same pin as the minds.

    This plant never had a site ``silence`` unit, so the installed fragment was an
    out-of-band regular file in ``~/.config/systemd/user``: nothing reconciled it
    on a pin advance, and a hand edit that copied content into it replaced the
    symlink shape plant uses, which then aborted every later replant. Generating
    it here gives the watcher the same single source of truth as a seeded mind.
    """
    from .runtime_source import source_for
    source = source_for(home, ROOT)
    return (
        "[Unit]\nDescription=Mishe independent mind silence watcher\nAfter=default.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={home.parent}\n"
        f"Environment=PYTHONPATH={source / 'src'}\n"
        f"Environment=XDG_RUNTIME_DIR=/run/user/{os.getuid()}\n"
        f"ExecStart={python} -m mishe_tauftauf.activity --home {home} "
        f"--session {session} --interval 5\n"
        "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
    )


def repoint_release_root(text: str, source: Path) -> str:
    """Repoint this site's release entry in ``Environment=PYTHONPATH``.

    Generated units are rewritten whole from ``unit_text``; units carrying
    custom content are edited in place, so only the release component each
    process imports may change. The plant's convention is one absolute
    ``<home>/releases/<rev>/src`` search-path entry, so a pin advance moves
    exactly the entries inside this site's ``releases`` directory and leaves
    every other line, plus any unrelated ``/src`` or extra search-path entry,
    untouched.
    """
    pinned = str(source)
    releases = source.parent.parent
    prefix = "Environment=PYTHONPATH="
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line.rstrip("\n")
        if not body.startswith(prefix):
            continue
        entries = body[len(prefix):].split(os.pathsep)
        lines[index] = prefix + os.pathsep.join(
            pinned if _is_release_source(entry, releases) else entry for entry in entries
        ) + ("\n" if line.endswith("\n") else "")
    return "".join(lines)


def _is_release_source(entry: str, releases: Path) -> bool:
    """True for a ``<...>/releases/<rev>/src`` entry under this site's releases."""
    path = Path(entry)
    return path.name == "src" and path.parent.parent == releases


def _reconcilable_fragment(home: Path, name: str, env: dict[str, str]) -> Path | None:
    """The file to edit for a covered unit the plant does not generate.

    A site unit file is the source of truth when present, because the systemd
    fragment symlinks to it — but only while it really does. A regular file or a
    foreign symlink at the systemd path means systemd loads something else, so
    repointing the site file would silently reconcile nothing and still report a
    successful restart on a consumer running the old release. Refuse that drifted
    shape exactly as the generated units do. A self-installed unit has no site
    file, so its fragment is edited in place; it is the only shape the unit has.
    Returns None when there is no editable file.
    """
    linked = subprocess.run(["systemctl", "--user", "show", name, "-p", "FragmentPath", "--value"],
                            capture_output=True, text=True, check=False, env=env).stdout.strip()
    unit = home / name
    if unit.is_file():
        problem = unit_fragment_mismatch(linked, unit) if linked else None
        if problem:
            raise RuntimeError(f"service {name} is a {problem}: {linked}")
        return unit
    fragment = Path(linked) if linked else None
    return fragment if fragment is not None and fragment.is_file() else None


def _service_active(name: str, env: dict[str, str]) -> bool:
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", name], env=env
    ).returncode == 0


def reconcile_services(home: Path, session: str, persist: bool, python: str) -> None:
    """Reconcile every covered service onto the pinned release.

    Generated units are rewritten whole. Units the dashboard covers but the
    plant does not generate — a site-declared mind or a self-installing
    supervisor — carry custom content and one may be a regular fragment rather
    than a symlink, so only their ``PYTHONPATH`` release component is repointed
    in place. Skipping them leaves them importing the previous release after a
    pin advance, and the dashboard then reads DRIFT.
    """
    if not persist:
        return
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # ``silence`` is a supervisor rather than a mind, but a pin advance that
    # skips it leaves the watcher importing the previous root and reading
    # DRIFT, so it is reconciled alongside the units a replant launches.
    reconciled = {slug: unit_text(home, session, slug, python)
                  for slug in (*ROLES, "permissions", "ci")}
    reconciled["silence"] = silence_unit_text(home, session, python)
    generated = {unit_name(session, slug) for slug in reconciled}
    active_before = {}
    for slug, text in reconciled.items():
        unit = home / unit_name(session, slug)
        unit.write_text(text, encoding="utf-8")
        linked = subprocess.run(["systemctl", "--user", "show", unit.name, "-p", "FragmentPath", "--value"],
                                capture_output=True, text=True, check=True, env=env).stdout.strip()
        problem = unit_fragment_mismatch(linked, unit) if linked else None
        if problem:
            raise RuntimeError(f"service {unit.name} is a {problem}: {linked}")
        if not linked:
            subprocess.run(["systemctl", "--user", "link", str(unit)], check=True, env=env)
        active_before[unit.name] = _service_active(unit.name, env)
    from .runtime_source import source_for
    pinned = source_for(home, ROOT) / "src"
    for name in service_manifest(home, session, persist):
        if name in generated:
            continue
        fragment = _reconcilable_fragment(home, name, env)
        if fragment is None:
            continue
        current = fragment.read_text(encoding="utf-8")
        updated = repoint_release_root(current, pinned)
        if updated == current:
            continue
        active_before[name] = _service_active(name, env)
        fragment.write_text(updated, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, env=env)
    for name, was_active in active_before.items():
        subprocess.run(["systemctl", "--user", "enable", "--now", name], check=True, env=env)
        if was_active:
            subprocess.run(["systemctl", "--user", "restart", name], check=True, env=env)


def _refresh_permissions_bottom_pane(home: Path, session: str, source: Path) -> None:
    """Refresh the permissions selector's bottom pane environment when idle.

    The bottom pane is only respawned by ``ensure`` when created or dead,
    so it keeps the tmux server's stale ``PYTHONPATH`` across pin advances.
    The pane is idle whenever one of its own shells is in the foreground: the
    generated ``permissions-shell`` runs the selector menu (``sh``) and
    ``exec``s ``bash`` after the operator quits. Respawn then, preserving
    active decisions in the permissions store; skip while any other command
    runs.
    """
    perm_target = f"{session}:permissions"
    perm_panes = _tmux("list-panes", "-t", perm_target, "-F", "#{pane_index}").stdout.decode().splitlines()
    if "1" in perm_panes:
        current_cmd = _tmux("display-message", "-p", "-t", f"{perm_target}.1",
                            "#{pane_current_command}").stdout.decode().strip()
        if current_cmd in {"bash", "sh"}:
            shell = home / "bin" / "permissions-shell"
            _tmux("respawn-pane", "-k", "-t", f"{perm_target}.1", "env",
                  f"PYTHONPATH={source / 'src'}", str(shell))

def plant(home: Path, session: str, engine_command: str, operator_window: str, persist: bool,
          *, runtime_only: bool = False) -> None:
    home = home.resolve()
    workspace = home.parent
    seed._require_worktree(home)
    if runtime_only and not owns_session(home, session):
        raise ValueError("runtime-only refresh requires an existing owned session")
    ignored = subprocess.run(["git", "-C", str(workspace), "check-ignore", "-q", str(home)],
                             capture_output=True)
    if ignored.returncode:
        exclude = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--git-path", "info/exclude"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        exclude_path = Path(exclude)
        if not exclude_path.is_absolute():
            exclude_path = workspace / exclude_path
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with exclude_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n/{home.name}/\n")
    contract_changed = False
    if workspace != ROOT and not runtime_only:
        from .runtime_source import source_for
        contract = (source_for(home, ROOT) / "src" / "mishe_tauftauf" / "seed_agent_contract.md").read_text(encoding="utf-8")
        agents = workspace / "AGENTS.md"
        current = agents.read_text(encoding="utf-8") if agents.exists() else ""
        updated = refresh_contract(current, contract)
        if updated != current:
            agents.write_text(updated, encoding="utf-8")
            contract_changed = True
    if any(char.isspace() for char in str(home)):
        raise ValueError("site path with whitespace is not supported by generated systemd units")
    ensure_engine_for_new_minds(home, engine_command)
    if operator_window in {*ROLES, "permissions"}:
        raise ValueError("operator window needs its own name")
    (home / "charters").mkdir(parents=True, exist_ok=True)
    (home / "top-pains").mkdir(parents=True, exist_ok=True)
    for slug in EXPLORATION:
        charter = home / "charters" / f"{slug}.md"
        if not charter.exists():
            charter.write_text(seed._core_charter(slug, home), encoding="utf-8")
        top = home / "top-pains" / slug
        if not top.exists():
            top.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                           " -m mishe_tauftauf.seed_culture_views --home " + shlex.quote(str(home)) +
                           " --view " + slug + "\n", encoding="utf-8")
            top.chmod(0o755)
    for slug in ROLES:
        seed.init(home, slug, engine_command)
    for slug in ROLES:
        print(seed.start(home, session, slug, 5), flush=True)
    if not owns_session(home, session):
        raise RuntimeError(f"session {session} lost its site ownership marker")
    names = _tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines()
    if operator_window not in names:
        _tmux("new-window", "-d", "-t", session, "-n", operator_window, "-c", str(workspace), "sh")
    from .operator_view import ensure as ensure_operator
    ensure_operator(home, session, operator_window)
    status = seed_permission_panel.ensure(home, session)
    if status:
        print(status, flush=True)
    if (home / "health/runtime-release.json").exists():
        from .runtime_source import source_for
        source = source_for(home, ROOT)
        # Refresh only upper evidence panes. Running lower minds retain their work.
        for slug in (*ROLES, "permissions", *site_declared_roles(home)):
            _tmux("respawn-pane", "-k", "-t", f"{session}:{slug}.0", "env",
                  f"MISHE_SEED_SESSION={session}", f"PYTHONPATH={source / 'src'}",
                  sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                  "pain", "watch", slug, "--interval", "5")
        _refresh_permissions_bottom_pane(home, session, source)
    for name in (operator_window, *ROLES, "permissions", *site_declared_roles(home)):
        _tmux("set-window-option", "-t", f"{session}:{name}", "automatic-rename", "off")
    (home / "health").mkdir(exist_ok=True)
    write_service_manifest(home, session, persist)
    reconcile_services(home, session, persist, sys.executable)
    previous_session = os.environ.get("MISHE_SEED_SESSION")
    os.environ["MISHE_SEED_SESSION"] = session
    try:
        print(f"discovery scan: {discovery.scan(home)}", flush=True)
        # The resident CI service owns potentially slow task/model projections.
        # Refreshing installed code must not wait for that maintenance lane.
        print(f"ci reading: {ci_watch.tick(home, check_deliveries=False)}", flush=True)
    finally:
        if previous_session is None:
            os.environ.pop("MISHE_SEED_SESSION", None)
        else:
            os.environ["MISHE_SEED_SESSION"] = previous_session
    actual = set(_tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines())
    required = {operator_window, *ROLES, "permissions", *site_declared_roles(home)}
    if not required <= actual:
        raise RuntimeError(f"missing windows: {sorted(required - actual)}")
    (home / "health" / "windows.json").write_text(
        json.dumps(_expected_windows(required, _manifest_windows(home), actual)) + "\n",
        encoding="utf-8")
    print(f"plant ready: session={session} windows={','.join(sorted(actual))} services={'enabled' if persist else 'skipped'}")
    if contract_changed:
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        Feed(home).append("sync", f"[task] kernel-contract-{sha[:12]} owner=genome source=plant\n"
                          "The core planting refreshed the generated AGENTS.md contract. Review and land only "
                          "that scoped target-repository diff, preserve application instructions, and verify its own CI.", once=True)


def main(argv: list[str] | None = None) -> tuple[Path, str, Path, bool]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--engine-command", default="codex")
    parser.add_argument("--operator-window")
    parser.add_argument("--no-services", action="store_true", help="start panes without installing user services")
    parser.add_argument("--runtime-only", action="store_true", help="refresh an owned runtime without changing AGENTS.md")
    parser.add_argument("--runtime-source", type=Path, help="install a checked clean release source; preserve existing pin otherwise")
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    home, session = site_and_session(workspace, ".mishe-seed" if workspace == ROOT else ".mishe-tauftauf",
                                     args.home, args.session)
    if args.runtime_source:
        from .runtime_source import select_source
        if not owns_session(home, session):
            raise ValueError("selecting runtime source requires an existing owned session")
        select_source(home, args.runtime_source, session)
    plant(home, session, args.engine_command, preferred_operator_window(home, args.operator_window), not args.no_services,
          runtime_only=args.runtime_only)
    return home.resolve(), session, workspace, not args.no_services


if __name__ == "__main__":
    main()
