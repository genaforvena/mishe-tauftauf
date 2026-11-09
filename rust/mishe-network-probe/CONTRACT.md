# mishe-network-probe contract (version 0.1, responsibility: one network probe row)

Status: BUILT and differentially checked against the retired Python probe
(`tests/test_mishe_network_probe.py`). Not yet installed, not wired into any
live consumer, not delivered. Owner: rust-plan.

This contract is the shape of the piece, not a speed claim. The point of the
slice is a boundary: a tiny POSIX executable that owns one responsibility
(one probe row), so it can be installed, checked and delivered on its own
instead of moving the whole plant's pin.

## Scope

`mishe-network-probe` runs one bounded connectivity probe and appends one
JSONL row to `HOME/network/probe.jsonl`. It measures:

- **gateway**: latency and loss to the default router on the LAN (ICMP is
  meaningful there).
- **internet**: a verified HTTPS page load against two stable sites.
- **dns**: resolution time through the home resolver.

It does not keep any content, does not read the router, and does not decide
whether the network is healthy for a human. That reading is
`tools/network_view.py` (via `tools/network_boundary.py`), which stays
Python.

### Why it is a secure page load and not a ping to a public address

The home router answers ICMP echo and accepts TCP for *any* address, even a
non-routable test address, in about a millisecond. A ping to `1.1.1.1` or
`8.8.8.8` therefore says nothing about the internet: the router answers it.
A secure fetch succeeds only when the real site answered with the expected
status. `ok` is set only when curl succeeded *and* the expected status came
back, so an intercepting or captive page is not a pass either.

The TLS stack stays with the system `curl`: it already does certificate
verification, and a hand-rolled TLS client in this crate would be a new
authority for "the site answered" rather than a migration. The Rust part is
the composition, the timing decode and the verdict; it adds no dependency.

## Invocation

```text
mishe-network-probe --home PATH
```

- `--home PATH` is required: the site home whose `network/probe.jsonl` gets
  the row. `HOME/network/` is created when missing.
- Any other argument is a usage error (exit 1).
- Nothing is read from stdin. Nothing is written to stdout except the
  one-line verdict.

## Output

### The row (stdout is not the row)

One line of JSON, appended to `HOME/network/probe.jsonl`, with no spaces
between separators (the retired Python probe's exact form):

```json
{"at":"2026-11-09T22:18:03Z","gateway_ip":"192.168.8.1","gateway":{"loss_pct":0.0,"min_ms":0.393,"avg_ms":0.462,"max_ms":0.496,"jitter_ms":0.036},"internet":{"gstatic":{"ok":true,"connect_ms":4.4,"tls_ms":220.4,"total_ms":300.4},"cloudflare":{"ok":true,"connect_ms":2.3,"tls_ms":202.7,"total_ms":268.7}},"dns_ms":0.7}
```

| Field | Meaning |
|---|---|
| `at` | UTC timestamp, `YYYY-MM-DDTHH:MM:SSZ` |
| `gateway_ip` | the default route's address, or `null` when there is no default route |
| `gateway` | `null`, or an object with `loss_pct` and, when ping reported them, `min_ms`, `avg_ms`, `max_ms`, `jitter_ms` |
| `internet` | one object per site, keyed by name (`gstatic`, `cloudflare`) |
| `dns_ms` | resolution time in milliseconds, or `null` when the lookup failed |

A site object is either `{"ok":true,"connect_ms":…,"tls_ms":…,"total_ms":…}`
(all three timings present, one decimal place) or
`{"ok":false,"curl_exit":N}` (curl's exit code; `-1` when curl could not be
run at all). A failed site never carries timings, and a successful site never
carries `curl_exit`.

`gateway` is `null` when the router did not answer ping at all. That is not a
quiet network: it is a router that is down, and the exit status says so.

### The verdict (stdout)

- A quiet network prints nothing.
- A down network prints one line: `network down: ` followed by
  `router/LAN not answering` and/or
  ` internet unreachable (no secure site answered)`.

## Exit status

| Status | Meaning | Caller reading |
|---|---|---|
| 0 | The probe ran; the router answered and at least one secure site answered | The row is a measurement |
| 2 | The probe ran; the router did not answer (or lost 50% or more of the pings) or no secure site answered | The row is a measurement of a down network |
| 3 | The probe could not run: `ip`, `ping` or `curl` could not be run, a helper did not finish in time, or the row could not be written | UNKNOWN. Never a quiet network |
| 1 | Usage error | Not a result; UNKNOWN |
| any other | Not part of the contract | UNKNOWN, never GREEN |

The statuses match the retired Python probe exactly: 0 healthy, 2 down, 3
UNKNOWN. A duty maps 2 to RED and 3 to UNKNOWN.

## Rules the row keeps

- `at` is UTC, formatted by this crate's own civil-calendar conversion (no
  timezone database, no local time).
- Millisecond timings carry one decimal place, like the Python probe's
  `round(value, 1)`.
- Loss is a percentage as ping reports it; `gateway` is `null` when ping
  reported no loss line at all.
- The row is appended, never rewritten: each run adds one line.

## Delivery

This piece is installed the `mishe-tape` way, so it goes live without moving
the plant's whole pin:

- `SITE_HOME/bin/mishe-network-probe` plus
  `SITE_HOME/bin/mishe-network-probe.install.json` (the sha256 of the
  installed bytes, the source commit and this contract's revision). A
  consumer that finds a mismatch reports UNKNOWN; there is no Python
  fallback.
- The per-component layout `SITE_HOME/components/mishe-network-probe/`
  (`releases/SHA`, one `current` symlink) is the other route, promoted by
  `mishe component promote`. See `docs/component-deploy.md`.

## Versioning

- The version is the crate version in `Cargo.toml`. A change to the row
  format, the exit meanings or the measurement rules bumps the minor version
  and updates this file in the same commit.
- `tests/test_mishe_network_probe.py` compares the binary against the
  retired Python probe's recorded answers; it runs when a binary resolves
  (`MISHE_NETWORK_PROBE_BIN`, then the site install) and skips with the
  reason otherwise.

## Build

- Crate `rust/mishe-network-probe/`, no dependencies, `Cargo.lock` tracked,
  toolchain pinned by `rust-toolchain.toml` (1.97.0, as `mishe-tape`).
- Built outside the checkout with the target dir under the gitignored site:
  `CARGO_TARGET_DIR=<site>/artifacts/rust-plan-20261107/target-network
  nice -n 19 cargo build --release`.
  Build only after the host check (at least 8 GB free, memory pressure near
  zero).
