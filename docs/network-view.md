# Network view and router log

The network view (`tools/network_view.py`, outer window `plants:network`) shows the home
connection in plain words. Every number carries its age, and anything unmeasured says UNKNOWN.

## Router log: `network/router.jsonl`

Written by `tools/network_router.py` every 300 s (duty `network-router`, timeout 30 s).
The probe is read-only: it reads the router's status, Wi-Fi clients and DHCP names over LAN.

One JSON object per line, oldest first.

| field | meaning |
| --- | --- |
| `ts` | epoch seconds (float), when the read ran |
| `ok` | `true` if the read worked, `false` if not |
| `clients` | only when `ok` is true: list of Wi-Fi clients |
| `uptime_s` | only when `ok` is true: router uptime in seconds |
| `wan` | only when `ok` is true: `{"up": bool, "uptime_s": int}` |
| `error` | only when `ok` is false: text of the failure |

Each client:

- `id`: hashed MAC, 8 hex characters. The MAC itself is not stored.
- `name`: from the DHCP leases. If no lease name is known, it is `unnamed device`.
- `band`: `"2.4"` or `"5"`.
- `dbm`: signal strength. Higher is stronger, so -9 is stronger than -36.
- `rx`, `tx`: rates in MBit/s. These can be `null` if the router did not report them.

Retention: rows older than 2 days are dropped (`KEEP_S`). Failed rows are kept, so a dead
probe shows as UNKNOWN in the view, not as a calm network.

Exit codes: 0 when the read worked, 3 when it failed (UNKNOWN in the duty contract, not RED).

## What the view says about it

- `RECORDED`: client count by band, weakest device by name and dBm, the age of the last read.
- `UNKNOWN`: the log has no reading, or the last read failed.
- `STALE`: the last good reading is older than 15 minutes.
- Trends and weekday baselines are UNKNOWN until two days of comparable rows exist.

## Limits

- Signal values are one sample per 5 minutes. A single strong reading is not a finding; it
  becomes one only when it repeats over a day.
- Not measured yet: upload speed, VPN (sing-box) state, router error and drop counters.
