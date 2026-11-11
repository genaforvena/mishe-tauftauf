#!/usr/bin/env python3
"""Idempotent tmux window restorer driven by operator/window-registry.json.

Only restores windows whose registry entry has restore == true and that are
missing; respawns dead panes (remain-on-exit) of those windows. Never types
into a pane, never touches windows that are not restorable, never touches
wake/hold/guard. Default is a dry run; pass --apply to act.
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
    env = {k: subst(v, checkout, site) for k, v in (w.get("env") or {}).items()}
    if env:
        cmd = ["env", *[f"{k}={v}" for k, v in env.items()], *cmd]
    return cmd


def plan(reg, checkout, site, state):
    """state: {session_key: None | {window_name: [dead_flags]}}; returns actions."""
    acts = []
    for w in reg["windows"]:
        if w.get("restore") is not True or not w.get("command"):
            continue
        sk = w["session"]
        live = state.get(sk)
        argv = argv_for(w, checkout, site)
        if live is None:
            acts.append(("new-session" if not any(a[1] == sk and a[0] == "new-session" for a in acts) else "new-window", sk, w["name"], argv))
        elif w["name"] not in live:
            acts.append(("new-window", sk, w["name"], argv))
        else:
            for i, dead in enumerate(live[w["name"]]):
                if dead:
                    acts.append(("respawn-pane", sk, w["name"], argv, i))
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
    socks = {k: (v["socket"], v["session"]) for k, v in reg["sessions"].items()}
    state = {k: observe(*socks[k]) for k in socks}
    acts = plan(reg, checkout, site, state)
    for act in acts:  # noqa
        kind, sk, name, argv = act[:4]
        sock, sess = socks[sk]
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
            print("  FAILED", r.stderr, file=sys.stderr)
        elif kind != "respawn-pane":
            tmux(sock, "set-option", "-w", "-t", f"{sess}:{name}", "remain-on-exit", "on")
    if not acts:
        print("nothing to restore")


if __name__ == "__main__":
    main()
