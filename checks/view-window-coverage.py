#!/usr/bin/env python3
"""A view has a window: every tools/*_view.py has a registered, live outer tmux window.

System 0 check for the rule in docs/outer-windows.md. For each ``tools/*_view.py``:

  * RED when no entry of SITE/operator/window-registry.json runs it (the command names the file);
  * RED when its registered outer window is missing from the ``plants`` session or every pane of
    it is dead;
  * RED when the window has live panes but none of their processes names the view (a stale shell
    or a finished child is not the view). A pane's process is its pid's command line plus the
    command lines of its direct children, read from /proc: a loop wrapper such as
    ``sh -c "while :; do python3 tools/x_view.py; sleep 60; done"`` names the view.

Exit codes: 0 GREEN, 2 RED (each gap named with the window command that closes it), 3 UNKNOWN
(tmux unreachable or its rows malformed, registry missing or unreadable, a live pane's /proc
identity unreadable). A check that cannot look
is UNKNOWN, never a quiet pass.  The tmux rows and the registry are read through the existing
boundary decoders (plant_tmux, state_files); this file only compares decoded values.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mishe_tauftauf.plant_tmux import parse_pane_rows  # noqa: E402
from mishe_tauftauf.state_files import read_json  # noqa: E402

SESSION = "plants"


def views(repo: Path) -> list[str]:
    return sorted(p.name for p in (repo / "tools").glob("*_view.py"))


def registry_windows(registry) -> list[dict]:
    rows = registry.get("windows") if isinstance(registry, dict) else registry
    return [w for w in rows if isinstance(w, dict) and w.get("session") == "outer"]


def runs_view(text: str, view: str) -> bool:
    """True when ``text`` names ``view`` as a whole file name (``tools/map_view.py``, or inside a
    ``sh -c`` string), not as the tail of another name (``tools/old_map_view.py``)."""
    return re.search(r"(?:^|[\s/])" + re.escape(view) + r"(?![\w.-])", text) is not None


def window_for(view: str, windows: list[dict]):
    for w in windows:
        command = w.get("command")
        if isinstance(command, list) and any(runs_view(str(arg), view) for arg in command):
            return w
    return None


def pane_commands(pid: int) -> list[str]:
    """Command lines of a pane's process and its direct children (read from /proc).

    The pane's own command line must be readable (OSError otherwise: UNKNOWN). A child that
    exits between the scan and its read is skipped.
    """
    proc = Path("/proc")
    own = (proc / str(pid) / "cmdline").read_bytes()
    lines = [own.replace(b"\0", b" ").decode("utf-8", "replace").strip()]
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            if ppid != pid:
                continue
            raw = (entry / "cmdline").read_bytes()
        except (OSError, ValueError, IndexError):
            continue
        lines.append(raw.replace(b"\0", b" ").decode("utf-8", "replace").strip())
    return lines


def evaluate(view_names, windows, live, commands=pane_commands):
    """Return the list of gap sentences.

    ``live`` maps window name -> [(dead, pid)] from tmux; ``commands(pid)`` returns the process
    command lines behind a pane (the default reads /proc and may raise OSError).
    """
    gaps = []
    for view in view_names:
        entry = window_for(view, windows)
        if entry is None:
            gaps.append(f"{view}: no entry in operator/window-registry.json; add an outer entry whose "
                        f"command runs tools/{view} and open it: tmux new-window -t {SESSION} -d -n NAME ...")
            continue
        name = entry.get("name")
        panes = live.get(name)
        if not panes:
            gaps.append(f"{view}: registered window {SESSION}:{name} is missing; "
                        f"python3 tools/tmux_restore.py --apply recreates it")
            continue
        alive = [pid for dead, pid in panes if not dead]
        if not alive:
            gaps.append(f"{view}: window {SESSION}:{name} has only dead panes; respawn it with the restorer")
            continue
        if not any(runs_view(line, f"tools/{view}") for pid in alive for line in commands(pid)):
            gaps.append(f"{view}: window {SESSION}:{name} is live but no process in it runs tools/{view}; "
                        f"respawn it with the restorer")
    return gaps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    a = ap.parse_args(argv)
    registry_path = a.home / "operator" / "window-registry.json"
    try:
        registry = read_json(registry_path)
    except (OSError, ValueError) as exc:
        print(f"view-window-coverage: registry unreadable ({exc}); cannot tell", file=sys.stderr)
        return 3
    if registry is None:
        print("view-window-coverage: window registry missing; cannot tell", file=sys.stderr)
        return 3
    try:
        r = subprocess.run(["tmux", "list-panes", "-s", "-t", SESSION, "-F",
                            "#{window_name}\t#{pane_index}\t#{pane_dead}\t#{pane_pid}"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"view-window-coverage: tmux unreadable: {exc}; cannot tell", file=sys.stderr)
        return 3
    if r.returncode != 0:
        print(f"view-window-coverage: tmux session {SESSION} unreadable: {r.stderr.strip()}; cannot tell",
              file=sys.stderr)
        return 3
    try:
        live = parse_pane_rows(r.stdout)
    except ValueError:
        print("view-window-coverage: malformed tmux rows; cannot tell", file=sys.stderr)
        return 3
    names = views(a.repo)
    if not names:
        print("view-window-coverage: no tools/*_view.py found; cannot tell", file=sys.stderr)
        return 3
    try:
        gaps = evaluate(names, registry_windows(registry), live, pane_commands)
    except (OSError, ValueError) as exc:
        print(f"view-window-coverage: pane process identity unreadable ({exc}); cannot tell", file=sys.stderr)
        return 3
    if gaps:
        for gap in gaps:
            print("view-window-coverage: " + gap, file=sys.stderr)
        return 2
    print(f"view-window-coverage: GREEN ({len(names)} views, each with a registered live window running it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
