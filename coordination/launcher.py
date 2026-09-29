"""Plant the generic core and wire this checkout's release coordinator."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from mishe_tauftauf.plant import ROOT, main as plant_main, unit_fragment_matches

from .site_sync import register_site


def install_follower(home: Path, session: str) -> str:
    if home.parent.resolve() != ROOT:
        raise ValueError("release coordinator belongs only to the core checkout")
    unit = home / f"{session}-coordination.service"
    unit.write_text(
        "[Unit]\nDescription=Mishe linked-site release coordinator\nAfter=default.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={ROOT}\n"
        f"Environment=PYTHONPATH={ROOT}:{ROOT / 'src'}\n"
        f"ExecStart={sys.executable} -m coordination.site_sync --home {home} --follow\n"
        "Restart=always\nRestartSec=15\n\n[Install]\nWantedBy=default.target\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    linked = subprocess.run(["systemctl", "--user", "show", unit.name, "-p", "FragmentPath", "--value"],
                            capture_output=True, text=True, check=True, env=env).stdout.strip()
    if linked and not unit_fragment_matches(linked, unit):
        raise RuntimeError(f"service {unit.name} already belongs to {linked}")
    if not linked:
        subprocess.run(["systemctl", "--user", "link", str(unit)], check=True, env=env)
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit.name], env=env).returncode == 0
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, env=env)
    subprocess.run(["systemctl", "--user", "enable", "--now", unit.name], check=True, env=env)
    if active:
        subprocess.run(["systemctl", "--user", "restart", unit.name], check=True, env=env)
    services_path = home / "health" / "services.json"
    services = json.loads(services_path.read_text(encoding="utf-8"))
    if unit.name not in services:
        services.append(unit.name)
        services_path.write_text(json.dumps(services) + "\n", encoding="utf-8")
    return unit.name


def main() -> None:
    home, session, workspace, persistent = plant_main()
    if not persistent:
        return
    if workspace == ROOT:
        print(f"release coordinator ready: {install_follower(home, session)}", flush=True)
    elif register_site(home, session):
        print(f"linked site registered for checked core updates: {home}", flush=True)
    else:
        print("linked site unregistered: plant the core checkout first", flush=True)


if __name__ == "__main__":
    main()
