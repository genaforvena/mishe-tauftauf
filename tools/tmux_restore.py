#!/usr/bin/env python3
"""Idempotent tmux window restorer driven by operator/window-registry.json.

Only restores windows whose registry entry has restore == true and that are
missing; respawns dead panes (remain-on-exit) of those windows. Never types
into a pane, never touches windows that are not restorable, never touches
wake/hold/guard. Default is a dry run; pass --apply to act.

Exit codes (for the ``tmux-restorer`` duty): 0 nothing failed, 2 a restore command failed,
3 the registry or its sessions could not be read (UNKNOWN, never a quiet pass).
"""
import os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mishe_tauftauf.plant_tmux import parse_pane_dead_flags, restore_arguments
from mishe_tauftauf.state_files import read_json


def tmux(sock, *args):
    return subprocess.run(["tmux", "-S", sock, *args], capture_output=True, text=True)


def subst(s, checkout, site):
    return s.replace("{CHECKOUT}", checkout).replace("{SITE}", site)


def argv_for(w, checkout, site):
    cmd = [subst(c, checkout, site) for c in w["command"]]
    return _with_env(cmd, w, checkout, site)


def _with_env(cmd, w, checkout, site, pane=None):
    env = {**(w.get("env") or {}), **((pane or {}).get("env") or {})}
    env = {k: subst(v, checkout, site) for k, v in env.items()}
    return ["env", *[f"{k}={v}" for k, v in env.items()], *cmd] if env else cmd


def pane_argv_for(w, checkout, site, index):
    """Command for one pane, falling back to the window command when no spec exists."""
    panes = w.get("panes")
    if isinstance(panes, list) and len(panes) > index and isinstance(panes[index], dict):
        cmd = panes[index].get("command")
        if isinstance(cmd, list) and cmd:
            return _with_env([subst(c, checkout, site) for c in cmd], w, checkout, site, panes[index])
    return argv_for(w, checkout, site)


def plan(reg, checkout, site, state):
    """state: {session_key: None | {window_name: [dead_flags]}}; returns actions."""
    acts = []
    for w in reg["windows"]:
        if w.get("restore") is not True:
            continue
        panes = w.get("panes")
        panes = panes if isinstance(panes, list) and panes else None
        if not panes and not w.get("command"):
            continue
        sk = w["session"]
        live = state.get(sk)
        argv = pane_argv_for(w, checkout, site, 0) if panes else argv_for(w, checkout, site)
        extra = [pane_argv_for(w, checkout, site, i) for i in range(1, len(panes))] if panes else []
        if live is None:
            acts.append(("new-session" if not any(a[1] == sk and a[0] == "new-session" for a in acts) else "new-window", sk, w["name"], argv, extra))
        elif w["name"] not in live:
            acts.append(("new-window", sk, w["name"], argv, extra))
        else:
            for i, dead in enumerate(live[w["name"]]):
                if dead:
                    acts.append(("respawn-pane", sk, w["name"], pane_argv_for(w, checkout, site, i), i))
    return acts


def observe(sock, session):
    if tmux(sock, "has-session", "-t", session).returncode != 0:
        return None
    r = tmux(sock, "list-panes", "-s", "-t", session, "-F", "#{window_name}\t#{pane_index}\t#{pane_dead}")
    return parse_pane_dead_flags(r.stdout)


def main():
    a = restore_arguments()
    site = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    checkout = str(Path(__file__).resolve().parent.parent)
    reg = read_json(Path(a.registry or f"{site}/operator/window-registry.json"))
    if not isinstance(reg, dict) or not isinstance(reg.get("sessions"), dict) or not isinstance(reg.get("windows"), list):
        print("tmux_restore: window registry missing or malformed; cannot tell", file=sys.stderr)
        return 3
    failed = 0
    socks = {k: (v["socket"], v["session"]) for k, v in reg["sessions"].items()}
    state = {k: observe(*socks[k]) for k in socks}
    acts = plan(reg, checkout, site, state)
    for act in acts:  # noqa
        kind, sk, name, argv = act[:4]
        sock, sess = socks[sk]
        extra = act[4] if kind != "respawn-pane" and len(act) > 4 else []
        print(("APPLY " if a.apply else "DRY ") + kind, sk, name, " ".join(argv)[:100])
        if not a.apply:
            continue
        w = next(x for x in reg["windows"] if x["session"] == sk and x["name"] == name)
        if kind == "new-session":
            r = tmux(sock, "new-session", "-d", "-s", sess, "-n", name, "-c", checkout, *argv)
        elif kind == "new-window":
            r = tmux(sock, "new-window", "-d", "-t", sess + ":", "-n", name, "-c", checkout, *argv)
        else:
            r = tmux(sock, "respawn-pane", "-c", checkout, "-t", f"{sess}:{name}.{act[4]}", *argv)
        if r.returncode:
            failed += 1
            print("  FAILED", r.stderr, file=sys.stderr)
        elif kind != "respawn-pane":
            tmux(sock, "set-option", "-w", "-t", f"{sess}:{name}", "remain-on-exit", "on")
            for extra_argv in extra:
                split = tmux(sock, "split-window", "-v", "-c", checkout, "-t", f"{sess}:{name}", *extra_argv)
                if split.returncode:
                    failed += 1
                    print("  FAILED split", split.stderr, file=sys.stderr)
    if not acts:
        print("nothing to restore")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
