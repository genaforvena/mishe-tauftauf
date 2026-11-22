# Network window: what is promised versus built

One line per promised slot in the network charter. Checked against `tools/network_view.py`
(`draw()` and its helpers) and `tools/network_router.py` and `rust/mishe-network-probe`, on 2026-11-13.
Speed, day-trend, baseline and Wi-Fi channel/noise/counter rows rechecked 2026-11-17; the upload
duty's verdict, the live Wi-Fi line's device naming and the driver's error-rate wording rechecked
2026-11-18; the day-trend label and its empty-bucket gap rechecked 2026-11-19; the "Worse than
usual?" busy-hour baseline rechecked 2026-11-20; the per-device reach row (the DEVICE PING
freshness, future-timestamp and reply rules, and the `network-device-reach` duty) rechecked 2026-11-21.

Status words: **BUILT** (the window shows it with an age or UNKNOWN), **PARTLY** (some of it is
shown), **MISSING** (not measured or not shown).

Terms used below:

- **window**: the network pane that the household reads (`tools/network_view.py`, described in [network view](network-view.md)).
- **producer**: the job that measures one reading and reports it as a signal. Its age is the time of the last observation, and a silent or missing reading is UNKNOWN, not GREEN. An unchanged value is not a stopped producer; see [budget-json-contract](budget-json-contract.md) (signals and their freshness).
- **GREEN / RED / UNKNOWN**: a duty's verdict. GREEN is OK, RED is a failure that wakes its resolver, UNKNOWN means no fresh evidence. See [how a mind chooses](how-a-mind-chooses.md).
- **duty** and **detector**: a duty is one entry in `duties.json` that checks a producer's reading against a maximum age and names a resolver; its detector is the check that turns the reading into GREEN, RED or UNKNOWN.
- **STALE**: the window's word for a reading older than its limit (usually 15 minutes); the value is still shown, marked with its age.
- **sparkline**: a row of block characters, one per probe or time bucket, showing the shape of recent values.
- **iwinfo / iwpriv**: the router's read-only Wi-Fi tools. `iwinfo` reports a radio's channel and noise; `iwpriv ... stat` prints the driver's own counters.
- **PER**: packet error rate, the share of frames the driver counts as lost. **tx_per** is that figure as stored in the router read.
- **self-limit**: the load and free-RAM level above which the measuring job backs off on the router.

## Reachability and quality

| slot | status | evidence |
| --- | --- | --- |
| Is the internet reachable from home | BUILT | `verdict()` in the HOME INTERNET line (OK / DEGRADED / DOWN / UNKNOWN) |
| Latency to the router (LAN) | BUILT | `gateway_line()`, average of 5 pings, with sparkline |
| Latency to 1.1.1.1 and 8.8.8.8 | MISSING | deliberately not used: the router answers public pings itself, so they look fast when the internet is down (note in `draw()`) |
| Latency to a couple of regional hosts | MISSING | only two secure sites are checked (gstatic, cloudflare) |
| Packet loss | PARTLY | loss to the router only (`loss_pct`, "worst loss"); no loss to the internet |
| Jitter | BUILT | `jitter_ms` is the mdev of the 5 gateway pings from ping's summary line (`rust/mishe-network-probe`, `ping()`/`rtt()`); it is the spread of the round-trip times, not RFC 3550 jitter. Shown as "jitter N ms" in `gateway_line()`, or "jitter UNKNOWN" when absent |
| Per-device reach (each named household device) | BUILT | The [DEVICE PING line](network-view.md) shows each device's average ping, loss and the reading's age. Failed replies, unreadable results and invalid or expired readings show UNKNOWN. Its detector is `network-device-reach` in `duties.json` |
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
| Who is weak, by a name the household knows | BUILT | the live `WI-FI` line names the weakest client from the recorded read's id-to-name map (`client_names()`, ids are the same short hash the router row stores), and every client below `WEAK_WIFI_DBM` by name and band when there is more than one weak or a weak client on the other band; a client the recorded read has not seen yet shows its dBm only. The `RECORDED 5-MIN` line names the weakest device of the last 5-minute read |
| Noisy or channel trouble | BUILT | `WI-FI RADIO` shows each known radio's channel, frequency and noise floor from a read-only `iwinfo <radio> info` pass in the router read, with age and UNKNOWN/STALE guards. `WI-FI QUALITY` shows, one line per band, the share of transmit attempts that failed since boot and over the last hour, plus the driver's own frame-error rate (the `PER` it prints after the fail counter, `tx_per` in the router row, shown as "driver's error rate N%", with the trailing line saying the driver's averaging window is not measured) so the attempts figure can be read next to the frames actually lost, plus radio temperature, from the driver's read-only `iwpriv <radio> stat` counters. `network-wifi` is UNKNOWN when either sub-reading vanishes, including when one band's `iwpriv` counters alone go missing (the window names that band instead of dropping it), when a radio returns no channel or noise at all (the row keeps the band with empty fields, the window says `channel UNKNOWN`, and a row that never reached a band says `missing from the newest read`), and when the counters are there but the driver's `PER` is not. `network-wifi` also turns UNKNOWN when a band the router has (one access point per band, `ra0`/`rax0`, `EXPECTED_BANDS` in `tools/network_boundary.py`) is absent from the newest read, so a silent radio is never GREEN. There is one access point per band, so there are no two APs to compare channels with (`apcli0`/`apclix0` are client-mode interfaces on the same radios) |
| Weak-signal flag | BUILT | `WEAK SIGNAL` when any client is below `WEAK_WIFI_DBM`; the line then names every weak device with its band (capped at three, remainder counted), so several weak devices are not hidden behind one minimum. The threshold is printed beside the live line; the healthy case still shows only the single weakest client |

## Router and uplink

| slot | status | evidence |
| --- | --- | --- |
| Router uptime | BUILT | `router_facts()`: "router up N days" from `/proc/uptime` |
| WAN link state | BUILT | `router_facts()`: "WAN up" or "UNKNOWN/down" from `ubus`. The physical link is its own line: `WAN LINK` shows the default-route device's negotiated speed and duplex and its carrier drops since boot (with the last hour summed across a reboot or driver reset) from `/sys/class/net`, so a down-negotiated port or a flapping cable is visible; UNKNOWN when the newest read has no `wan_link` or no speed; `network-uplink` is UNKNOWN on the same gap, sums the counter steps so a mid-window reset neither hides nor invents flaps, and is RED on three link drops inside the two-hour window |
| Errors and drops | BUILT | WAN link errors and drops from `ip -s link` on the router, shown as the UPLINK line `uplink_line()`: totals since router boot, from the last recorded router read; "STALE" past 15 min, UNKNOWN when the read failed or has no WAN counters. `uplink_rate_line()` adds receive drops over the last hour, restarted by a reboot, and a trend clause comparing that rate with the six earlier hours (same, higher or lower; UNKNOWN-style "no comparison" with fewer than three earlier hours). It carries the newest read's age and "STALE" past 15 min too: the rate stays computable for up to an hour after the producer dies, so an undated "about the same" would read as a calm current trend |
| Load | BUILT | `router_facts()`: 1/5/15-minute load from `/proc/loadavg`. `ROUTER LOAD` compares the 5-minute average and free RAM with the self-limit the producer backs off on (load 1.0, free RAM 120 MB) and prints how long the current breach has lasted (`network_boundary.load_run`); the `network-router-load` duty turns the same run into an alarm after 30 unbroken minutes over it |
| VPN state (sing-box) | BUILT | `vpn_line()` reads the router's process list, `tun0` and the tunnel's `rx_bytes`/`tx_bytes` in the read-only router pass: "sing-box running" / "NOT running", "tun0 up/DOWN/absent", then the bytes the tunnel moved since the previous read that carried counters ("20.7 MB through tun0 in 53 s", or "nothing through tun0 in 5 min"). It says the tunnel exists and whether it carries traffic, not which clients use it. UNKNOWN when the process list was not read or no reading exists; `tun0 traffic UNKNOWN` when the read carries no counters (or there is no earlier counter read — not "no traffic"); a counter that went backwards names a recreated `tun0`; STALE after 15 min. `network-vpn` is UNKNOWN on a fresh read without the counters |
| Tailscale exit-node path | BUILT | `exit_line()` reads `health/egress.json` (the `egress` duty's per-client GREEN/RED, written every 5 min) and shows each client's state with the file's age; a non-GREEN client also gets its own indented line with the producer's first reason in plain words (`first_reason()`, one line, cut at a word boundary to `REASON_CHARS`), up to `REASON_CLIENTS` clients, so "imac-rozalia UNKNOWN" says why instead of leaving the cause in the file. UNKNOWN when the file is missing, unreadable or over 15 min old. It does not read tailscale itself |

## Trend and outages

| slot | status | evidence |
| --- | --- | --- |
| Sparkline (last hour) | BUILT | sparklines on gateway, web and DNS lines (last 40 probes, about 40 minutes) |
| Last day sparkline | BUILT | `usuality_line()` / `day_spark()` show a 40-bucket trend of web latency over the loaded day's rows, labelled `trend 24 h` at the line (the loaded span, not the 40-probe window); the header now says "sparklines = last 40 probes unless labelled", and the per-site lines keep their 40-probe (~40 min) sparklines. A bucket with no answered check draws as `·` (a gap), not skipped, so a dead stretch stays visible |
| "Worse than usual?" | BUILT | `usuality_line()` compares the median of the last 10 web samples with a typical and shows both numbers, with UNKNOWN / STALE guards. The typical is the busy hours' own median (retained busy-hour samples excluding the newest 60, still labelled "of evenings") during the 16-20 UTC busy hours, where pages run structurally elevated (16/17/18h web medians 609/671/562 ms on 2026-11-20 17:20Z, against about 350 ms overnight) and the pooled day median reads "slower than usual" on every ordinary peak hour; outside them (or without enough busy-hour samples) it is the last-day median excluding the most recent 60, labelled "of samples". The headline qualifies a flagged slowdown with "for the evening" so it cannot read as a whole-day claim |
| Outages with start and end | BUILT | `outage_lines()` from `outage_episodes()` |

## Next gaps, in order of value

1. What the radio fail counter means, and when it deserves a threshold. The counters exist now
   (`iwpriv <radio> stat`), and since 2026-11-18 the window shows the driver's own frame-error rate
   next to the attempts share as `driver's error rate N%`, from `tx_per` on the newest recorded
   read (the first such read is 2026-11-18 05:17Z; it is UNKNOWN before that field existed). The
   number is the driver's, and its averaging window is not measured: it cannot be reproduced from
   the counters printed beside it, so no single denominator or factor explains it (2026-11-18:
   2.4 GHz read 14.5% failed attempts against PER 3.4%, 5 GHz 6.2% against 0.1%, receive-side CRC
   15.7% against 0.7%). The trailing line therefore says `driver's error rate window not measured`;
   the "since boot" wording of 2026-11-18 was an inference and was withdrawn the same day. The
   attempts percentages were read directly from `iwpriv` in that experiment, not from a window
   reading. The window now also says how the last hour compares with the median of the last 24
   completed clock hours in the retained log (`much worse than usual (usual N%)` at 2× and ten
   points above). **Decision 2026-11-18:** that comparison is shown, but it is not yet an alarm
   duty. Reason: the attempts share counts retries, the retained log is two days, and the paired
   error-rate history is hours old (20 reads over 1.8 hours by 07:05Z that day, flat at 3.4% on
   2.4 GHz and 0.1% on 5 GHz), so it cannot yet tell a busy radio from a failing one.
   **Precondition for an alarm:** at least a day of reads with `tx_per` on both bands, and at least
   one "much worse" hour in that history. The driver's rate cannot discriminate in that test even
   then: if it is cumulative, as the counters beside it are, one hour's failures are about 0.3% of
   the 11.26 million failed transmits accumulated over 12.66 days of router uptime, so an hour with
   twice the usual failures moves the printed rate by about 0.01 points — below the 0.1% it prints
   — and an hour would have to fail about five times as often as usual before the printed number
   moved at all. A flat reading beside a worse hour is the expected result in every case, not
   evidence of a healthy radio; the hour comparison is the detector to watch. Limit of the
   comparison: it catches a change against those 24 hours, so a slow sustained shift becomes the new
   usual; the since-boot attempts share and the driver's rate stay the absolute readings. The
   earlier "two APs on channel 13" question is closed: `ra0` is the 2.4 GHz access point, `rax0` the
   5 GHz one, and `apcli0`/`apclix0` are client-mode interfaces on the same radios.
   **Recheck 2026-11-20:** the retained log held 514 good reads over 2.0 days; 34 complete hourly
   windows could be scored, and each band had exactly one `much worse` hour (2.4 GHz 27.8% failed
   attempts against a 12.7% usual at 11-19 04:00Z; 5 GHz 16.0% against 5.9% at 11-18 22:00Z).
   The longest run of consecutive `much worse` hours on either band was one, so the comparison
   stays display-only and no alarm duty is warranted. The trigger to revisit is two consecutive
   `much worse` hours on one band, or a sustained run that the window already labels `for at least
   N h`; both are visible in the pane before any new signal is added.
