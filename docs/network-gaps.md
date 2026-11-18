# Network window: what is promised versus built

One line per promised slot in the network charter. Checked against `tools/network_view.py`
(`draw()` and its helpers) and `tools/network_router.py` and `rust/mishe-network-probe`, on 2026-11-13.
Speed, day-trend, baseline and Wi-Fi channel/noise/counter rows rechecked 2026-11-17; the upload
duty's verdict rechecked 2026-11-18.

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
| Jitter | BUILT | `jitter_ms` is the mdev of the 5 gateway pings from ping's summary line (`rust/mishe-network-probe`, `ping()`/`rtt()`); it is the spread of the round-trip times, not RFC 3550 jitter. Shown as "jitter N ms" in `gateway_line()`, or "jitter UNKNOWN" when absent |
| DNS lookup time | BUILT | `dns_ms` line with sparkline |
| HTTPS time to stable sites | PARTLY | two sites (gstatic.com, cloudflare.com), full time and "failed in last 40" |

## Speed

| slot | status | evidence |
| --- | --- | --- |
| Download speed | PARTLY | `speed_line()`: last good 5 MB download in `speed.jsonl`, age shown, one night-time try (02-05 local) |
| Upload speed | BUILT | `speed_up_line()` reads `speed-up.jsonl`: last good 2 MB upload with age, written by the same night-time duty with its own 30-minute failed-retry; UNKNOWN when never measured, last try failed, or older than 36 h. The producing duty's verdict follows a failed or stale upload and no attempt once the 02-05 window is open (`upload_state()`), so a night of failing uploads is UNKNOWN, not GREEN |
| Method and age shown | BUILT | both speed lines give the method and hours since the reading |

## Home Wi-Fi

| slot | status | evidence |
| --- | --- | --- |
| Number of clients | BUILT | `wifi_facts()`: total and per band (2.4 / 5 GHz) |
| Who is weak, by a name the household knows | PARTLY | the RECORDED line names the weakest device and its dBm; the live WI-FI line does not name devices ("no devices named") |
| Noisy or channel trouble | BUILT | `WI-FI RADIO` shows each known radio's channel, frequency and noise floor from a read-only `iwinfo <radio> info` pass in the router read, with age and UNKNOWN/STALE guards. `WI-FI QUALITY` shows the share of transmit attempts that failed since boot and over the last hour, plus radio temperature, from the driver's read-only `iwpriv <radio> stat` counters. `network-wifi` is UNKNOWN when either sub-reading vanishes, including when one band's `iwpriv` counters alone go missing (the window names that band instead of dropping it). There is one access point per band, so there are no two APs to compare channels with (`apcli0`/`apclix0` are client-mode interfaces on the same radios) |
| Weak-signal flag | BUILT | "WEAK SIGNAL" when the weakest client is below `WEAK_WIFI_DBM` |

## Router and uplink

| slot | status | evidence |
| --- | --- | --- |
| Router uptime | BUILT | `router_facts()`: "router up N days" from `/proc/uptime` |
| WAN link state | BUILT | `router_facts()`: "WAN up" or "UNKNOWN/down" from `ubus`. The physical link is its own line: `WAN LINK` shows the default-route device's negotiated speed and duplex and its carrier drops since boot (with the last hour and a reset clause) from `/sys/class/net`, so a down-negotiated port or a flapping cable is visible; UNKNOWN when the newest read has no `wan_link` or no speed; `network-uplink` is UNKNOWN on the same gap and RED on three carrier drops inside the two-hour window |
| Errors and drops | BUILT | WAN link errors and drops from `ip -s link` on the router, shown as the UPLINK line `uplink_line()`: totals since router boot, from the last recorded router read; "STALE" past 15 min, UNKNOWN when the read failed or has no WAN counters. `uplink_rate_line()` adds receive drops over the last hour, restarted by a reboot, and a trend clause comparing that rate with the six earlier hours (same, higher or lower; UNKNOWN-style "no comparison" with fewer than three earlier hours) |
| Load | BUILT | `router_facts()`: 1/5/15-minute load from `/proc/loadavg`. `ROUTER LOAD` compares the 5-minute average and free RAM with the self-limit the producer backs off on (load 1.0, free RAM 120 MB) and prints how long the current breach has lasted (`network_boundary.load_run`); the `network-router-load` duty turns the same run into an alarm after 30 unbroken minutes over it |
| VPN state (sing-box) | BUILT | `vpn_line()` reads the router's process list and `tun0` in the read-only router pass: "sing-box running" / "NOT running", "tun0 up/DOWN/absent". It says the tunnel exists, not which clients use it. UNKNOWN when the process list was not read or no reading exists; STALE after 15 min |
| Tailscale exit-node path | BUILT | `exit_line()` reads `health/egress.json` (the `egress` duty's per-client GREEN/RED, written every 5 min) and shows each client's state with the file's age; a non-GREEN client also gets its own indented line with the producer's first reason in plain words (`first_reason()`, one line, cut at a word boundary to `REASON_CHARS`), up to `REASON_CLIENTS` clients, so "imac-rozalia UNKNOWN" says why instead of leaving the cause in the file. UNKNOWN when the file is missing, unreadable or over 15 min old. It does not read tailscale itself |

## Trend and outages

| slot | status | evidence |
| --- | --- | --- |
| Sparkline (last hour) | BUILT | sparklines on gateway, web and DNS lines (last 40 probes, about 40 minutes) |
| Last day sparkline | BUILT | `usuality_line()` / `day_spark()` show a 40-bucket trend of web latency over the last day's probe rows, labelled with the real span (not a flat "24 h"), alongside the 40-probe (~40 min) sparklines on the per-site lines |
| "Worse than usual?" | BUILT | `usuality_line()` compares the median of the last 10 web samples with the median of the last-day samples excluding the most recent 60: "about usual" / "slower than usual" / "much slower than usual", with both numbers shown and UNKNOWN / STALE guards |
| Outages with start and end | BUILT | `outage_lines()` from `outage_episodes()` |

## Count

- BUILT: 19
- PARTLY: 4
- MISSING: 2

## Next gaps, in order of value

1. What the radio fail counter means, and when it deserves a threshold. The counters exist now
   (`iwpriv <radio> stat`, shown by `WI-FI QUALITY` with UNKNOWN/STALE guards), but the driver prints
   its own PER (3.3% on 2.4 GHz, 0.1% on 5 GHz) while the success/fail counters imply 14.6% and 6.3%,
   so "Tx fail" probably counts each retry attempt rather than a lost frame. Watch the last-hour rate
   for a few days, compare it with the driver PER line, then decide whether a household-visible
   threshold earns its place. The earlier "two APs on channel 13" question is closed: `ra0` is the
   2.4 GHz access point, `rax0` the 5 GHz one, and `apcli0`/`apclix0` are client-mode interfaces on
   the same radios.
