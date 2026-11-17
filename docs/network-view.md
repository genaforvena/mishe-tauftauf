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
| `radios` | only when `ok` is true: one object per known radio, `{"band": str, "channel": int or null, "freq_ghz": float or null, "noise_dbm": int or null}`, from a read-only `iwinfo <radio> info` pass; `[]` when the router did not report channel/noise. No SSID, MAC or signal is stored here |
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

The same checker also guards the network area's own continuity. The
`network-progress` duty (`checks/network-care.py`, subcommand `progress`, every
900 s, `max_age` 1800 s, resolver `network`) is RED when the network mind's wall
(`SITE_HOME/walls/network.md`) is missing or more than six hours old, or when none
of `tools/network_view.py`, `tools/network_boundary.py`, `rust/mishe-network-probe`
or `checks/network-care.py` was committed in the last 48 hours — the area has gone
quiet. It is UNKNOWN when `git log` cannot be read, so a shallow or damaged checkout
is not mistaken for a quiet mind.

## What the view says about it

- `RECORDED`: client count by band, weakest device by name and dBm, the age of the last read.
- `WI-FI RADIO`: each known radio's channel, frequency and noise floor from the read-only router
  pass, with the age of the reading; UNKNOWN when the sub-reading is absent, STALE past 15 minutes.
- `UNKNOWN`: the log has no reading, or the last read failed.
- `STALE`: the last good reading is older than 15 minutes.
- Trends and weekday baselines are UNKNOWN until two days of comparable rows exist.

## Limits

- Signal values are one sample per 5 minutes. A single strong reading is not a finding; it
  becomes one only when it repeats over a day.
- Not measured yet: upload speed and Wi-Fi retry counters. Channel and noise floor are measured once
  per 5 minutes; a single reading is not evidence of channel trouble, and the view does not yet
  compare the two APs' channels.
- VPN (sing-box) state is shown as "running" and "tun0 up" from the
  router's process list; it does not say which clients use the tunnel. The WAN error and drop counters
  are measured: the view reports them from the last recorded router read, and the drop
  count over a window needs at least 10 minutes of counters (`_wan_drops` in
  `tools/network_view.py`).
