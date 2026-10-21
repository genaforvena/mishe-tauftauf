#!/usr/bin/env python3
"""Detector: a client's egress still leaves through the Tailscale exit node.

The gap this closes.  The home network moved from "the router runs a VPN" to
"each machine runs Tailscale and selects phaedra as its exit node".  A machine
can be *configured* for that and still not use it: the exit node can be
deselected, the exit node can be offline, the tunnel can be up while the
egress still leaves from the home ISP, and — the failure that actually
happened on 2026-10-21 — an exit node with LAN access disallowed installs
routes that send ``192.168.8.0/24`` into the tunnel, so the machine reaches
neither the internet *nor* its own LAN.  Nothing in the plant watched any of
that, so the state was known only when a person noticed.

What this detector decides, per client:

1. **exit node selected** — the Tailscale CLI's own view names the expected
   exit node and reports that peer online.
2. **egress identity** — a request leaves from the exit node's public address
   and not from the home ISP's.
3. **LAN still reachable** — the local network is still reachable, which is
   what an exit node silently breaks when LAN access is disallowed.

A client that cannot answer is UNKNOWN, never GREEN.  A client that answers
and is wrong is RED.  The exit node's public address is the only prose this
script interprets: an IP-echo service returns one bare address token, and
anything that is not that token (including an empty body) is a failed sample,
not a pass.

Scope: this script is an operator-side probe for the home network, not a
plant-consistency check.  It lives in ``checks/`` beside the other detectors
but is deliberately outside the ``src``/``coordination``/``tools`` trees that
``tools/check_parsing.py`` scans for parsing sites, so it needs no new entry
in ``parsing-policy.json``.

Exit codes: 0 all clients GREEN, 2 at least one RED, 3 no RED but at least
one UNKNOWN.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field

EXIT_NODE_IP = "100.94.116.17"      # phaedra's tailnet address
EXIT_NODE_PUBLIC_IP = "38.49.216.141"  # phaedra's public address
HOME_ISP_IP = "5.227.25.226"        # the home ISP's egress address
LAN_PROBE = "http://192.168.8.1/"   # the router's own web UI
IP_ECHO = "https://api.ipify.org"
SOCKS = "127.0.0.1:1080"            # the iMac's tailscaled SOCKS5 door


@dataclass
class Target:
    """One client and the commands that speak to it."""

    name: str
    how: str                        # "local" or "ssh"
    ts: str                         # the tailscale CLI invocation
    egress: str                     # the curl invocation that leaves the client
    ssh: tuple = ()                 # (host, user) when how == "ssh"
    attempts: int = 4
    notes: str = ""


TARGETS: tuple[Target, ...] = (
    Target(
        name="mesh-home",
        how="local",
        ts="tailscale",
        egress=f"curl -s --max-time 12 {IP_ECHO}",
        notes="Linux, kernel TUN; egress leaves directly.",
    ),
    Target(
        name="imac-rozalia",
        how="ssh",
        ssh=("ilya@192.168.8.214",),
        ts="~/bin/tailscale --socket=$HOME/.tailscale/tailscaled.sock",
        egress=f"curl -s --max-time 12 --socks5-hostname {SOCKS} {IP_ECHO}",
        notes="macOS 10.13, tailscaled in userspace mode; egress leaves "
              "through the local SOCKS door, the OS itself cannot reach the "
              "tailnet.",
    ),
)


@dataclass
class Result:
    name: str
    state: str                      # GREEN | RED | UNKNOWN
    reasons: list = field(default_factory=list)
    samples: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"client": self.name, "state": self.state,
                "reasons": self.reasons, "samples": self.samples}


def run(cmd: str, target: Target, timeout: int = 40) -> subprocess.CompletedProcess:
    """Run one shell command, locally or on the target's host."""
    if target.how == "local":
        argv = ["sh", "-c", cmd]
    else:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                *target.ssh, cmd]
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout)


def selected_exit_node(target: Target) -> tuple[str, str]:
    """Return (state, detail) for "the expected exit node is selected and up".

    The Tailscale CLI's JSON names the peer whose ``ExitNode`` flag is set and
    carries that peer's addresses.  The CLI's own words are the authority; the
    expected exit node is identified by its tailnet address.
    """
    proc = run(f"{target.ts} status --json 2>/dev/null", target)
    if proc.returncode != 0 or not proc.stdout.strip():
        return "UNKNOWN", "tailscale status returned nothing"
    try:
        status = json.loads(proc.stdout)
    except ValueError:
        return "UNKNOWN", "tailscale status was not readable JSON"
    peers = status.get("Peer") or {}
    chosen = []
    for peer in peers.values():
        if peer.get("ExitNode"):
            chosen.append(peer)
    if not chosen:
        return "RED", "no exit node is selected"
    for peer in chosen:
        addresses = peer.get("TailscaleIPs") or []
        if EXIT_NODE_IP in addresses:
            if not peer.get("Online"):
                return "RED", f"exit node {peer.get('HostName')} is offline"
            return "GREEN", f"exit node {peer.get('HostName')} ({EXIT_NODE_IP}) selected and online"
    names = ", ".join(str(peer.get("HostName")) for peer in chosen)
    return "RED", f"a different exit node is selected: {names}"


def egress_identity(target: Target) -> tuple[str, str, list]:
    """Return (state, detail, samples) for "the egress address is the exit node's".

    The leg to the exit node is lossy (measured 2026-10-21: 10-57% packet loss
    on the MTS-mobile path to Montreal, and 10-20% of request attempts through
    the iMac's userspace SOCKS door fail), so one failed sample is not a fault
    of the exit node.  Several attempts are made and the sample set is
    reported: the exit node is judged by what it answered, not by one attempt.
    """
    samples: list[str] = []
    for _ in range(target.attempts):
        proc = run(target.egress, target, timeout=30)
        token = (proc.stdout or "").strip()
        samples.append(token or f"<empty rc={proc.returncode}>")
        if token == EXIT_NODE_PUBLIC_IP:
            return "GREEN", f"egress is {EXIT_NODE_PUBLIC_IP}", samples
        if token == HOME_ISP_IP:
            return "RED", f"egress is the home ISP address {HOME_ISP_IP}", samples
    return "RED", "no sample showed the exit node's address", samples


def lan_reachable(target: Target) -> tuple[str, str]:
    """Return (state, detail) for "the local network is still reachable"."""
    proc = run(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 8 {LAN_PROBE}", target)
    code = (proc.stdout or "").strip()
    if code == "200":
        return "GREEN", "the router answers on the LAN"
    if not code:
        return "UNKNOWN", "the LAN probe returned nothing"
    return "RED", f"the LAN probe answered {code!r}"


def check(target: Target) -> Result:
    result = Result(name=target.name, state="GREEN")
    states = []
    for probe in (selected_exit_node, egress_identity, lan_reachable):
        state, detail = probe(target)[:2]
        states.append(state)
        if state != "GREEN":
            result.reasons.append(f"{probe.__name__}: {detail} ({state})")
    if "RED" in states:
        result.state = "RED"
    elif "UNKNOWN" in states:
        result.state = "UNKNOWN"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", action="append", default=None,
                        help="check only this client (repeatable)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--state", default=None,
                        help="write the JSON result to this path as well")
    args = parser.parse_args()

    targets = [t for t in TARGETS
               if args.target is None or t.name in args.target]
    if not targets:
        print("no such target", file=sys.stderr)
        return 3

    results = []
    for target in targets:
        try:
            results.append(check(target))
        except subprocess.TimeoutExpired:
            results.append(Result(name=target.name, state="UNKNOWN",
                                  reasons=["the probe timed out"]))
        except OSError as exc:
            results.append(Result(name=target.name, state="UNKNOWN",
                                  reasons=[f"the probe could not run: {exc}"]))

    payload = {"results": [r.to_dict() for r in results]}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for result in results:
            print(f"{result.state:7} {result.name}")
            for reason in result.reasons:
                print(f"          - {reason}")
    if args.state:
        path = __import__("pathlib").Path(args.state)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    states = {r.state for r in results}
    if "RED" in states:
        return 2
    if "UNKNOWN" in states:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
