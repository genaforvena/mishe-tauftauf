# Operator notice — per-device Tailscale exit node (2026-10-21)

Status: DONE for the two clients that can be driven from here (mesh-home, the
iMac). The rest of the rollout needs the operator's hands and is listed below.
This record supersedes the parts of `OPERATOR-TOPOLOGY-2026-10-20.md` that
describe the router's VPN as the egress path.

## What the previous topology actually did

The 2026-10-20 record says sing-box on the router is "the only VPN path". It
is not, and on 2026-10-21 it was not even running:

- `sing-box` had no process on the router (`/etc/init.d/sing-box status` →
  `inactive`, no `tun0`), so the router's own egress was the home ISP address
  `5.227.25.226`.
- Its `tun` inbound has `auto_route` but no `auto_redirect`, so even when it
  ran it only ever tunnelled the *router's* traffic. The LAN DNS redirects
  (`dns_vpn`, `adguard_home`) are `enabled='0'`. Measured: mesh-home, a plain
  LAN client, egressed `5.227.25.226` — the LAN never had VPN egress at all.

So removing that path costs nothing. The router was left untouched here by the
operator's decision; note that its init script is enabled with `respawn` and
an `rc.local` guard, so the next router reboot restarts sing-box, and even
then it cannot affect LAN clients.

## The Mac was already on Tailscale

There is no Tailscale app for the iMac and there cannot be one: it is an
`iMac12,1` (27", 2011) running macOS 10.13.6, and the client's floor is
macOS 11. What exists instead is a hand-built `tailscaled 1.72.1-dev20240822`
in `~/bin`, run by the launchd agent `mesh.tailscaled` with
`--tun=userspace-networking`, serving SOCKS5 on `127.0.0.1:1080` and HTTP on
`127.0.0.1:1055`. **phaedra was already the selected exit node.**

What was broken was coverage and duplication:

- The macOS SOCKS proxy was enabled on the unused `Ethernet` service and
  **disabled on the active `Wi-Fi` service**, so GUI apps (Safari, Chrome,
  Mail) left straight from the home ISP while shells went through
  `~/.mesh-proxy.sh` (which sets `ALL_PROXY=socks5h://127.0.0.1:1080`).
- A second mechanism, `mesh.socks` → `ssh -D 1080` into mesh-home, was
  failing every ten seconds with `bind [127.0.0.1]:1080: Address already in
  use` — it wanted a port `tailscaled` already owned — and its premise
  ("mesh-home's own egress is phaedra's exit node") was false.

## What was changed

| Client | Change | Revert |
|---|---|---|
| mesh-home | `tailscale set --exit-node=100.94.116.17` | `tailscale set --exit-node=` |
| mesh-home | `tailscale set --exit-node-allow-lan-access=true` | `tailscale set --exit-node-allow-lan-access=false` |
| imac-rozalia | `tailscale set --exit-node-allow-lan-access=true` (exit node was already selected) | same, reversed |
| imac-rozalia | `launchctl bootout` + `launchctl disable` of `mesh.socks` (the broken duplicate) | `launchctl enable gui/$(id -u)/mesh.socks` and boot the plist back in |
| imac-rozalia | `networksetup -setsocksfirewallproxy "Wi-Fi" 127.0.0.1 1080` and `-setsocksfirewallproxystate "Wi-Fi" on` | `-setsocksfirewallproxystate "Wi-Fi" off` |

`--exit-node-allow-lan-access=true` is **required**, not a nicety. With the
default (`false`) Tailscale installs `192.168.8.0/24` into the exit node's
route table (table 52), so the client reaches neither the internet nor its
own LAN: on mesh-home the router became unreachable within seconds of
selecting the exit node, and the LAN probe answered `000`.

## The finding that bounds quality

The leg to the exit node is lossy and it is the ISP's, not the software's:

- ICMP to phaedra's public address: 40% loss from mesh-home, 57% from the
  iMac; the LAN to the router is 0% loss on both. ICMP is rate-limited
  upstream, so this overstates it.
- At TCP level: 10/10 TLS handshakes to phaedra from mesh-home (connect
  0.139 s, appconnect 0.29–3.87 s — the spread is retransmits); through the
  exit node 20/20 requests succeeded and 19/20 DNS probes.
- The iMac's userspace netstack is the weaker link: 17/20 requests through its
  SOCKS door succeeded, 2 of the 3 failures were 10 s timeouts and one was an
  immediate refusal. Successful requests took 0.6–9.5 s. Its IPv6 attempts
  fail fast with `network is unreachable` because the netstack has no v6 route
  to the exit node.

The old VLESS/REALITY path ran over the same leg with TCP on top of TCP, and
the 2026-10-20 record already measured that leg as lossy. This change does
not make the path worse; it is the first arrangement under which any LAN
device actually uses it.

## How a recurrence is detected and routed

`checks/exit-node-egress.py` (committed) probes each client for three things:
the exit node is selected and online, the egress address is phaedra's public
address, and the LAN still answers. A client that cannot answer is UNKNOWN, a
client that answers wrongly is RED.

`mishe-exit-node-egress.timer` runs it every five minutes and writes
`SITE/checks/exit-node-egress.json`; a RED or UNKNOWN posts one DM to health
per *change* of the problem set (an unchanged RED stays silent). Both
directions were exercised on 2026-10-21: deselecting the exit node produced
`RED` and the DM to health, restoring it produced `GREEN` and the recovery
DM; disallowing LAN access produced `RED` on the LAN probe alone, which is
the discriminating case the other two probes cannot see.

## What is still open

- **Operator's hands**: redmi-10 (Tailscale app → exit node = phaedra),
  default-string on Windows (client → exit node), note3 (no Tailscale
  installed at all; Android 5.0 / API 21 is exactly the app's floor, so the
  release's `minSdk` must be checked before sideloading), rip (offline, LAN
  unreachable). The household `tinangel266@` devices stay direct by decision.
- **Deferred by decision**: router cleanup, the household devices, the
  iMac's 2024 client build (no auto-update on 10.13), and split routing for
  `.ru` domains — Russian sites now see a Canadian address on the machines
  that use the exit node.
- **Unknown, not healthy**: the `observability` dashboard reads
  "dashboard stale or future" (pre-existing, not caused by this change).
- A stray empty `$MISHE_SEED_HOME/walls/` tree exists in the repository root
  — the exact typo `AGENTS.md` warns about. It is untracked and empty; it was
  left alone.
