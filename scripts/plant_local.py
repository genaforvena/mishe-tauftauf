#!/usr/bin/env python3
"""Plant the resident development and exploration channels on one Linux account."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mishe_tauftauf import ci_watch, discovery, seed, seed_permission_panel  # noqa: E402
from mishe_tauftauf.tmux import _tmux, owns_session  # noqa: E402

ROLES = ("genome", "witness", "discover", "senses", "health")
EXPLORATION = ("discover", "senses", "health")
CONTRACT_START = "<!-- mishe-tauftauf plant contract -->"
CONTRACT_END = "<!-- end mishe-tauftauf plant contract -->"


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


def preferred_operator_window(home: Path, requested: str | None) -> str:
    manifest = home / "health" / "windows.json"
    if manifest.exists():
        names = json.loads(manifest.read_text(encoding="utf-8"))
        candidates = set(names) - {*ROLES, "permissions"}
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
    raised = home / ".seed-raised"
    if session is None and raised.is_file():
        session = raised.read_text(encoding="utf-8").split()[0]
    if session is None:
        session = "mishe-seed" if default_site_name == ".mishe-seed" else "mishe-" + workspace.name.replace("_", "-")
    return home, session


def unit_text(home: Path, session: str, slug: str, python: str) -> str:
    command = (f"{python} -m mishe_tauftauf.ci_watch --home {home} --follow" if slug == "ci" else
               f"{python} -m mishe_tauftauf.seed_permission_panel --home {home} --session {session} --follow"
               if slug == "permissions" else
               f"{python} -m mishe_tauftauf --home {home} seed run --session {session} "
               f"--slug {slug} --interval 5 --self-pick-seconds {600 if slug == 'discover' else 300} "
               "--clear-grace-seconds 30")
    return (
        f"[Unit]\nDescription=Mishe {slug} resident channel\nAfter=default.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={home.parent}\n"
        f"Environment=PYTHONPATH={ROOT / 'src'}\n"
        f"Environment=PATH={home / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}\n"
        f"ExecStart={command}\n"
        "Restart=always\nRestartSec=15\n\n[Install]\nWantedBy=default.target\n"
    )


def plant(home: Path, session: str, engine_command: str, operator_window: str, persist: bool) -> None:
    home = home.resolve()
    workspace = home.parent
    repository = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True)
    if repository.returncode or Path(repository.stdout.strip()).resolve() != workspace:
        raise ValueError(f"site must be directly inside a Git worktree: {workspace}")
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
    if workspace != ROOT:
        contract = (ROOT / "src" / "mishe_tauftauf" / "seed_agent_contract.md").read_text(encoding="utf-8")
        agents = workspace / "AGENTS.md"
        current = agents.read_text(encoding="utf-8") if agents.exists() else ""
        updated = refresh_contract(current, contract)
        if updated != current:
            agents.write_text(updated, encoding="utf-8")
    if any(char.isspace() for char in str(home)):
        raise ValueError("site path with whitespace is not supported by generated systemd units")
    argv = shlex.split(engine_command)
    if not argv:
        raise ValueError("engine command is empty")
    if shutil.which(argv[0]) is None:
        raise ValueError(f"agent command unavailable: {argv[0]}")
    if operator_window in {*ROLES, "permissions"}:
        raise ValueError("operator window needs its own name")
    (home / "charters").mkdir(parents=True, exist_ok=True)
    (home / "top-pains").mkdir(parents=True, exist_ok=True)
    for slug in EXPLORATION:
        charter = home / "charters" / f"{slug}.md"
        if not charter.exists():
            charter.write_text((ROOT / "src" / "mishe_tauftauf" / f"seed_{slug}_charter.md").read_text(),
                               encoding="utf-8")
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
    print(seed_permission_panel.ensure(home, session), flush=True)
    for name in (operator_window, *ROLES, "permissions"):
        _tmux("set-window-option", "-t", f"{session}:{name}", "automatic-rename", "off")
    (home / "health").mkdir(exist_ok=True)
    (home / "health" / "windows.json").write_text(
        json.dumps(sorted({operator_window, *ROLES, "permissions"})) + "\n", encoding="utf-8")
    units = [unit_name(session, slug) for slug in (*ROLES, "permissions", "ci")]
    (home / "health" / "services.json").write_text(json.dumps(units if persist else []) + "\n",
                                                   encoding="utf-8")
    previous_session = os.environ.get("MISHE_SEED_SESSION")
    os.environ["MISHE_SEED_SESSION"] = session
    try:
        print(f"discovery scan: {discovery.scan(home)}", flush=True)
        print(f"ci reading: {ci_watch.tick(home)}", flush=True)
    finally:
        if previous_session is None:
            os.environ.pop("MISHE_SEED_SESSION", None)
        else:
            os.environ["MISHE_SEED_SESSION"] = previous_session
    if persist:
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        active_before = {}
        for slug in (*ROLES, "permissions", "ci"):
            unit = home / unit_name(session, slug)
            unit.write_text(unit_text(home, session, slug, sys.executable), encoding="utf-8")
            linked = subprocess.run(["systemctl", "--user", "show", unit.name, "-p", "FragmentPath", "--value"],
                                    capture_output=True, text=True, check=True, env=env).stdout.strip()
            if linked and not unit_fragment_matches(linked, unit):
                raise RuntimeError(f"service {unit.name} already belongs to {linked}")
            if not linked:
                subprocess.run(["systemctl", "--user", "link", str(unit)], check=True, env=env)
            active_before[slug] = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", unit.name], env=env
            ).returncode == 0
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, env=env)
        for slug in (*ROLES, "permissions", "ci"):
            unit = home / unit_name(session, slug)
            subprocess.run(["systemctl", "--user", "enable", "--now", unit.name], check=True, env=env)
            if active_before[slug]:
                subprocess.run(["systemctl", "--user", "restart", unit.name], check=True, env=env)
    actual = set(_tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines())
    required = {operator_window, *ROLES, "permissions"}
    if not required <= actual:
        raise RuntimeError(f"missing windows: {sorted(required - actual)}")
    print(f"plant ready: session={session} windows={','.join(sorted(actual))} services={'enabled' if persist else 'skipped'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--engine-command", default="codex")
    parser.add_argument("--operator-window")
    parser.add_argument("--no-services", action="store_true", help="start panes without installing user services")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    home, session = site_and_session(workspace, ".mishe-seed" if workspace == ROOT else ".mishe-tauftauf",
                                     args.home, args.session)
    plant(home, session, args.engine_command, preferred_operator_window(home, args.operator_window), not args.no_services)


if __name__ == "__main__":
    main()
