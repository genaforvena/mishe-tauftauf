# Network window: what is promised versus built

One line per promised slot in the network charter. Checked against `tools/network_view.py`
(`draw()` and its helpers) and `tools/network_router.py`, on 2026-11-12.

Status words: **BUILT** (the window shows it with an age or UNKNOWN), **PARTLY** (some of it is
shown), **MISSING** (not measured or not shown).

## Reachability and quality

| slot | status | evidence |
| --- | --- | --- |
| Is the internet reachable from home | BUILT | `verdict()` in the HOME INTERNET line (OK / DEGRADED / DOWN / UNKNOWN) |
| Latency to the router (LAN) | BUILT | `gateway_line()`, average of 5 pings, with sparkline |
| Latency to 1.1.1.1 and 8.8.8.8 | MISSING | deliberately not used: the router answers public pings itself, so they look fast when the internet is down (note in `draw()`) |
| Latency to a couple of regional hosts | MISSING | only two secure sites are checked (gstatic, cloudflare) |
| Packet loss | PARTLY | loss to the router only (`loss_pct`, "worst loss"); no loss to the internet |
| Jitter | BUILT | router jitter from the gateway pings, shown in the window (UNKNOWN when absent) |
| DNS lookup time | BUILT | `dns_ms` line with sparkline |
| HTTPS time to stable sites | PARTLY | two sites (gstatic.com, cloudflare.com), full time and "failed in last 40" |

## Speed

| slot | status | evidence |
| --- | --- | --- |
| Download speed | PARTLY | `speed_line()`: last good 5 MB download, age shown, one night-time try (02-05 local) |
| Upload speed | MISSING | `speed_line()` says "no upload yet" |
| Method and age shown | BUILT | the speed line gives the method and hours since the reading |

## Home Wi-Fi

| slot | status | evidence |
| --- | --- | --- |
| Number of clients | BUILT | `wifi_facts()`: total and per band (2.4 / 5 GHz) |
| Who is weak, by a name the household knows | PARTLY | the RECORDED line names the weakest device and its dBm; the live WI-FI line does not name devices ("no devices named") |
| Noisy or channel trouble | MISSING | no channel, noise floor or retry counters are read |
| Weak-signal flag | BUILT | "WEAK SIGNAL" when the weakest client is below `WEAK_WIFI_DBM` |

## Router and uplink

| slot | status | evidence |
| --- | --- | --- |
| Router uptime | BUILT | `router_facts()`: "router up N days" from `/proc/uptime` |
| WAN link state | BUILT | `router_facts()`: "WAN up" or "UNKNOWN/down" from `ubus` |
| Errors and drops | BUILT | WAN link errors and drops from `ip -s link`, shown as an uplink line (UNKNOWN when absent) |
| Load | BUILT | `router_facts()`: 1/5/15-minute load from `/proc/loadavg` |
| VPN state (sing-box) | MISSING | nothing reads sing-box |
| Tailscale exit-node path | MISSING | nothing reads tailscale; the old `egress` duty is not shown in the window |

## Trend and outages

| slot | status | evidence |
| --- | --- | --- |
| Sparkline (last hour) | BUILT | sparklines on gateway, web and DNS lines (last 40 probes, about 40 minutes) |
| Last day sparkline | MISSING | the view only spans the last 40 probes; no day view |
| "Worse than usual?" | MISSING | no baseline; the router log will allow a comparison after two days of rows (see `docs/network-view.md`) |
| Outages with start and end | BUILT | `outage_lines()` from `outage_episodes()` |

## Count

- BUILT: 13
- PARTLY: 4
- MISSING: 8

## Next gaps, in order of value

1. Upload speed, one bounded night-time try alongside the download.
2. Worse-than-usual baseline, once two days of `network/router.jsonl` rows exist.
