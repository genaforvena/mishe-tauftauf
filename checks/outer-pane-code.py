#!/usr/bin/env python3
"""An outer view running code from a superseded release must not look healthy.

Delivery verifies systemd consumers and the pin, but a long-lived outer tmux
pane keeps the code it started with: promoting the pin does not restart it.
Senses found `plants:senses-map` running tools/map_view.py from an old release
for days through several promotions (DM 303596, 2026-11-20). For every
registered outer window whose command runs a tools/*_view.py, this check reads
the live pane's cwd through the process boundary, finds the release it started
from, and compares that script's bytes with the script at the current pin.
Comparison is by content: a promotion that did not change the script is not a
false RED. A pane started from the working tree is not release-pinned and is
not judged here; nor is a missing or dead window, which view-window-coverage
owns. Unreadable tmux, registry, cwd, pin or code is UNKNOWN, not a quiet pass.
Exit codes: 0 GREEN, 2 RED, 3 UNKNOWN.
"""
from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mishe_tauftauf.proc_table import (  # noqa: E402
    decode_genome_pane_table,
    read_pane_cwd,
    release_names,
)
from mishe_tauftauf.state_files import read_json  # noqa: E402

SESSION = "plants"


def views(repo: Path) -> list[str]:
    return sorted(p.name for p in (repo / "tools").glob("*_view.py"))


def view_entry(view: str, windows: list[dict]):
    for w in windows:
        command = w.get("command")
        if isinstance(command, list) and any(view in str(arg) for arg in command):
            return w
    return None


def script_of(entry: dict, view_names: list[str]) -> str | None:
    """The tools/*_view.py this registered window runs, from decoded command args."""
    args = entry.get("command")
    if not isinstance(args, list):
        return None
    for view in view_names:
        if any(view in str(arg) for arg in args):
            return view
    return None


def digest(path: Path) -> bytes | None:
    try:
        return hashlib.sha256(path.read_bytes()).digest()
    except OSError:
        return None


def evaluate(home: Path, entries: list[dict], view_names: list[str],
             pids: dict[str, list[tuple[str, str]]], cwds: dict[str, str],
             pin: str) -> list[tuple[int, str]]:
    """Return (severity, sentence) for each finding; ``pids`` maps window name to
    (pane_index, pane_pid) rows already decoded by the boundary."""
    findings: list[tuple[int, str]] = []
    for entry in entries:
        script = script_of(entry, view_names)
        if script is None:
            continue  # not a tools/*_view.py window; nothing here to compare
        name = entry.get("name")
        rows = pids.get(name)
        if not rows:
            continue  # no live window: view-window-coverage reports that gap
        pin_dir = (home / "releases" / pin).resolve()
        expected = pin_dir / "tools" / script
        want = digest(expected)
        if want is None:
            findings.append((3, f"{name}: pinned script {expected} unreadable; cannot tell"))
            continue
        for _, pid in rows:
            cwd = cwds.get(pid, "")
            if not cwd:
                findings.append((3, f"{name}: pane {pid} cwd unreadable; cannot tell"))
                continue
            releases = release_names(cwd)
            if not releases:
                continue  # a working-tree pane is not release-pinned; other checks own it
            if len(releases) > 1:
                findings.append((3, f"{name}: pane {pid} names several releases; cannot tell"))
                continue
            running = home / "releases" / releases[0] / "tools" / script
            where = f"release {releases[0]}"
            have = digest(running)
            if have is None:
                findings.append((3, f"{name}: pane {pid} script {running} unreadable; cannot tell"))
                continue
            if have != want:
                command = shlex.join(entry["command"]) if isinstance(entry.get("command"), list) else ""
                findings.append((
                    2,
                    f"{name}: pane {pid} runs tools/{script} from {where}, not pin {pin}; "
                    f"respawn with: tmux respawn-pane -k -c {pin_dir} "
                    f"-t {SESSION}:{name} {command!r}",
                ))
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    a = ap.parse_args(argv)
    registry_path = a.home / "operator" / "window-registry.json"
    try:
        registry = read_json(registry_path)
    except (OSError, ValueError) as exc:
        print(f"outer-pane-code: registry unreadable ({exc}); cannot tell", file=sys.stderr)
        return 3
    if registry is None:
        print("outer-pane-code: window registry missing; cannot tell", file=sys.stderr)
        return 3
    windows = registry.get("windows") if isinstance(registry, dict) else registry
    entries = [w for w in windows if isinstance(w, dict) and w.get("session") == "outer"]
    view_names = views(a.repo)
    if not view_names:
        print("outer-pane-code: no tools/*_view.py found; cannot tell", file=sys.stderr)
        return 3
    try:
        pin = (a.home / "runtime" / "current").resolve().name
    except OSError as exc:
        print(f"outer-pane-code: pin unreadable ({exc}); cannot tell", file=sys.stderr)
        return 3
    if not pin:
        print("outer-pane-code: pin missing; cannot tell", file=sys.stderr)
        return 3

    pids: dict[str, list[tuple[str, str]]] = {}
    for entry in entries:
        if script_of(entry, view_names) is None:
            continue
        name = entry.get("name")
        try:
            r = subprocess.run(
                ["tmux", "list-panes", "-t", f"{SESSION}:{name}", "-F",
                 "#{pane_index}:#{pane_dead}:#{pane_pid}"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"outer-pane-code: tmux unreadable for {name} ({exc}); cannot tell", file=sys.stderr)
            return 3
        if r.returncode != 0:
            if "no server" in r.stderr or "error connecting" in r.stderr:
                print("outer-pane-code: tmux server unreachable; cannot tell", file=sys.stderr)
                return 3
            continue  # a missing window is view-window-coverage's finding
        try:
            rows = decode_genome_pane_table(r.stdout)
        except ValueError:
            print(f"outer-pane-code: malformed pane rows for {name}; cannot tell", file=sys.stderr)
            return 3
        pids[name] = [(idx, pid) for idx, dead, pid in rows if dead == "0"]

    live_pids = [pid for rows in pids.values() for _, pid in rows]
    cwds = {pid: read_pane_cwd(pid) for pid in live_pids}
    findings = evaluate(a.home, entries, view_names, pids, cwds, pin)
    for severity, text in findings:
        print(f"outer-pane-code: {text}", file=sys.stderr)
    if any(sev == 2 for sev, _ in findings):
        return 2
    if findings:
        return 3
    print(f"outer-pane-code: GREEN ({len(view_names)} views running their pinned script bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
