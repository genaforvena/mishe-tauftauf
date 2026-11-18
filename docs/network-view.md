# Network view and router log

The network view (`tools/network_view.py`, outer window `plants:network`) shows the home
connection in plain words. Every number carries its age, and anything unmeasured says UNKNOWN.

## Router log: `network/router.jsonl`

Written by `tools/network_router.py` every 300 s (duty `network-router`, timeout 30 s).
The probe is read-only: it reads the router's status, Wi-Fi clients, DHCP names and each
radio's channel and noise floor over LAN.

One JSON object per line, oldest first.

| field | meaning |
| --- | --- |
| `ts` | epoch seconds (float), when the read ran |
| `ok` | `true` if the read worked, `false` if it failed or the pass was skipped |
| `radios` | only when `ok` is true: one object per known radio, `{"band": str, "channel": int or null, "freq_ghz": float or null, "noise_dbm": int or null, "stats": object or null}`, from a read-only `iwinfo <radio> info` pass; `[]` when the router did not report channel/noise. `stats` is `{"tx_ok", "tx_fail", "rx_ok", "rx_crc", "temp_c"}` from a read-only `iwpriv <radio> stat` pass, cumulative since the router booted, `null` when the driver printed no counters. No SSID, MAC or signal is stored here |
| `clients` | only when `ok` is true: list of Wi-Fi clients |
| `uptime_s` | only when `ok` is true: router uptime in seconds |
| `wan` | only when `ok` is true: `{"up": bool, "uptime_s": int}` |
| `uplink` | only when `ok` is true and the WAN link device is named: `{"device": str, "rx_errors": int, "rx_dropped": int, "tx_errors": int, "tx_dropped": int}`, cumulative since the router booted; `null` when the device is not named |
| `router_load` | only when `ok` is true: `{"load_1m": float, "load_5m": float, "mem_available_mb": float}` from the router's `/proc/loadavg` and `MemAvailable`; `null` when the read carries neither a load nor free RAM |
| `error` | only when `ok` is false: the failure text, or a `skipped: …` reason when the pass was held off over the router's self-limit (see `docs/network-router-monitoring.md`) |

Each client:

- `id`: hashed MAC, 8 hex characters. The MAC itself is not stored.
- `name`: from the DHCP leases. If no lease name is known, it is `unnamed device`.
- `band`: `"2.4"` or `"5"`.
- `dbm`: signal strength. Higher is stronger, so -9 is stronger than -36.
- `rx`, `tx`: rates in MBit/s. These can be `null` if the router did not report them.

Retention: rows older than 2 days are dropped (`KEEP_S`). Failed and skipped rows are kept,
so a dead probe shows as UNKNOWN in the view, not as a calm network.

Exit codes: 0 when the read worked, 3 when it failed or was skipped over the self-limit
(UNKNOWN in the duty contract, not RED).

The rows are also the input to the `network-uplink` duty, which runs every 300 s
(`checks/network-care.py`, subcommand `uplink`) and judges the rows of the newest
two hours. It is RED when the newest row reports a receive error (`rx_errors`
above zero), or when `rx_dropped` grew by more than 400 per hour across a span of
at least 1.8 h. It is UNKNOWN when no row is newer than 15 minutes, when fewer
than two rows fall in the window, when the `rx_dropped` counter went backwards (a
router reboot or driver reset), or when the log is unreadable — an alarm that
cannot read the log is UNKNOWN, never a quiet pass. A row whose `uplink` object is
missing or `null` is skipped, so a reading that does not carry the counters can
neither raise nor clear the alarm.

The `network-wifi` duty (same checker, subcommand `wifi`, every 300 s, `max_age` 900 s,
resolver `network`) watches that sub-reading separately: the router read can succeed while
`iwinfo` silently yields no channel/noise lines. It is UNKNOWN when no fresh reading carries
them or a field is missing, RED only on impossible values (channel outside 1–196 or noise
outside -120…0 dBm), GREEN when both known radios report plausible numbers.

The `network-router-load` duty (same checker, subcommand `load`, every 300 s, `max_age` 900 s,
resolver `network`) watches the router's own self-limit — the same 5-minute load above 1.0 or
free RAM below 120 MB that makes the producer back off and that `ROUTER LOAD` prints. A single
spiky sample is GREEN but the message names the breach; an unbroken run of over-limit readings
spanning at least 30 minutes is RED, so a persistently busy router wakes a person instead of
only changing one line in the window. A throttled read carries no load numbers, so it neither
raises nor hides the alarm; a gap of more than 20 minutes between load readings ends the run
rather than joining two breaches; no load reading in six hours, or a newest one over 15 minutes
old, is UNKNOWN. The window prints the same run — `ROUTER LOAD` says "over the self-limit for 12
min, alarm at 30 min" instead of only the instant — because one walk
(`tools/network_boundary.py`, `load_run`) feeds both the line and the duty, so they cannot
disagree about how long a breach has lasted.

The same checker also guards the network area's own continuity. The
`network-progress` duty (`checks/network-care.py`, subcommand `progress`, every
900 s, `max_age` 1800 s, resolver `network`) is RED when the network mind's wall
(`SITE_HOME/walls/network.md`) is missing or more than six hours old, or when none
of `tools/network_view.py`, `tools/network_boundary.py`, `rust/mishe-network-probe`
or `checks/network-care.py` was committed in the last 48 hours — the area has gone
quiet. It is UNKNOWN when `git log` cannot be read, so a shallow or damaged checkout
is not mistaken for a quiet mind.

## What the view says about it

- `SPEED` and `UPLOAD`: the last good download and upload with the method and age of each. One
  5 MB download and one 2 MB upload per night, only in 02:00–04:59 local, at most every 12 h;
  both are single bounded requests against Cloudflare's speed endpoint, so the times include
  connection setup and are a trend, not a calibrated line test. A failed last try shows its error
  instead of the older good number; no reading at all, or one older than 36 h, is UNKNOWN. The
  upload has its own log and its own 30-minute retry inside the night, so a failed upload does not
  erase the download and a transient failure does not cost the whole night. The duty that produces
  both follows the upload as well: a failed upload attempt, a good upload older than 36 h, or no
  attempt at all once the night window is open makes the `network-speed` signal UNKNOWN, so a night
  of failing uploads wakes the network mind instead of passing as GREEN.
- `EXIT NODE`: each client's exit-node state from the `egress` duty's file with the file's age. A
  client that is not GREEN gets an indented line with that duty's own first reason, cut to one
  line at a word boundary, for at most three clients, so an UNKNOWN or RED client says why it is
  not GREEN (for example "the client could not be reached: …"). UNKNOWN when the file is missing,
  unreadable or over 15 minutes old.
- `RECORDED`: client count by band, weakest device by name and dBm, the age of the last read.
- `WHERE TIME GOES`: of the page-load time just above, how much went into reaching the site
  (`connect_ms`) and how much into the secure handshake (`tls_ms`), for the newest probe only.
  It also gives the handshake's usual median over the loaded rows and, when both have at least
  `PHASE_BASELINE_MIN` rows, the medians for overnight (00:00–05:59) and the busy evening
  (18:00–20:59). Those two hours were chosen because the handshake median is elevated there in
  both the eight-day log and the shorter window the view loads; 17:00 and 21:00 sit near the
  daytime baseline in both. The line says where the time went, not why: household traffic and the
  ISP's evening peak look the same from here. Probe cadence does not vary by time of day: the thin
  evening counts in the eight-day log come from a partial first day and the host reboot of
  2026-11-10 (boot ended 18:15Z, next probe 19:54Z), not evening load, and at the current 180 s
  timer period every clock hour carries 17–19 probes. UNKNOWN when the newest probe is stale or
  did not carry both phases.
- `WI-FI RADIO`: each known radio's channel, frequency and noise floor from the read-only router
  pass, with the age of the reading; UNKNOWN when the sub-reading is absent, STALE past 15 minutes.
- `WI-FI QUALITY`: per radio, the share of transmit attempts that failed since the router booted,
  and the share that failed over the last hour (the difference between two recorded counter reads,
  at least 10 minutes apart), plus the highest radio temperature. Read from the driver's own
  `iwpriv <radio> stat` counters. Attempts, not frames given up on: the driver counts each try, so
  this is "how often the radio had to send again or gave up", not "how many packets the household
  lost". UNKNOWN when the newest read carries no counters on any radio (a band whose counters are
  missing from the newest read is named "UNKNOWN (missing from the newest read)" rather than
  dropped, and `network-wifi` turns UNKNOWN too), or when a reboot has left fewer than two
  counter reads (10 minutes apart) in the hour. A reboot restarts the counters from zero, so only
  reads after it bracket a rate; with two post-reboot reads the line shows that shorter span
  instead of UNKNOWN. STALE past 15 minutes.
- `UNKNOWN`: the log has no reading, or the last read failed.
- `STALE`: the last good reading is older than 15 minutes.
- Trends and weekday baselines are UNKNOWN until two days of comparable rows exist.

## Limits

- Signal values are one sample per 5 minutes. A single strong reading is not a finding; it
  becomes one only when it repeats over a day.
- Channel, noise floor and the driver's transmit counters are measured once per 5 minutes; a single
  reading is not evidence of channel trouble. There is one access point per band (2.4 GHz and 5 GHz),
  so there is no second AP to compare channels with: `iwinfo` also lists `apcli0`/`apclix0`, which
  are client-mode interfaces on the same two radios, not extra access points.
- VPN (sing-box) state is shown as "running" and "tun0 up" from the
  router's process list; it does not say which clients use the tunnel. The WAN error and drop counters
  are measured: the view reports them from the last recorded router read, and the drop
  count over a window needs at least 10 minutes of counters (`_wan_drops` in
  `tools/network_view.py`).
