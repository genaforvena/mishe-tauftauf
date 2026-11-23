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
| `radios` | only when `ok` is true: one object per known radio, `{"band": str, "channel": int or null, "freq_ghz": float or null, "noise_dbm": int or null, "stats": object or null}`, from a read-only `iwinfo <radio> info` pass; `[]` when the router did not report channel/noise. `stats` is `{"tx_ok", "tx_fail", "rx_ok", "rx_crc", "temp_c"}` from a read-only `iwpriv <radio> stat` pass, cumulative since the router booted, `null` when the driver printed no counters; `tx_per` (float percent) is the driver's own frame-error rate printed after `Tx fail count`, absent on rows written before 2026-11-18. No SSID, MAC or signal is stored here |
| `clients` | only when `ok` is true: list of Wi-Fi clients |
| `uptime_s` | only when `ok` is true: router uptime in seconds |
| `wan` | only when `ok` is true: `{"up": bool, "uptime_s": int}` |
| `uplink` | only when `ok` is true and the WAN link device is named: `{"device": str, "rx_errors": int, "rx_dropped": int, "tx_errors": int, "tx_dropped": int}`, cumulative since the router booted; `null` when the device is not named |
| `wan_link` | only when `ok` is true and the default route's device was named: `{"device": str, "speed_mbps": int or null, "duplex": str or null, "carrier_up": int or null, "carrier_down": int or null}` from the router's own `/sys/class/net/<device>/{speed,duplex,carrier_up_count,carrier_down_count}`; the carrier counts are cumulative since boot, a field the kernel did not report is `null` (never zero), `null` when no device was named |
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
at least 1.8 h, or when the physical link went down (`carrier_down`) three or more
times inside the window — a cable or port fault, not ISP maintenance. It is UNKNOWN
when no row is newer than 15 minutes, when fewer than two rows fall in the window,
when the newest row carries no `wan_link` or no negotiated speed, when a counter
restarted inside the window and the drops counted since stayed below those bounds,
or when the log is unreadable — an alarm that cannot read the log is UNKNOWN, never a quiet pass. A
row whose `uplink` object is missing or `null` is skipped, so a reading that does not
carry the counters can neither raise nor clear the alarm. Both counters are cumulative
since the router booted, so the duty sums the steps between readings instead of
subtracting the endpoints: a step backwards is a reboot or driver reset, the drops
seen on either side still count (a lower bound, since what happened between the last
reading before the restart and the restart itself was never observed), three or more
observed link drops are still RED, and a restarted window below that is UNKNOWN — a
reboot can neither look like a flapping cable nor hide one.

The `network-wifi` duty (same checker, subcommand `wifi`, every 300 s, `max_age` 900 s,
resolver `network`) watches that sub-reading separately: the router read can succeed while
`iwinfo` silently yields no channel/noise lines. It is UNKNOWN when no fresh reading carries
them or a field is missing, RED only on impossible values (channel outside 1–196 or noise
outside -120…0 dBm), GREEN when both known radios report plausible numbers. It is also UNKNOWN
when a band's transmission counters are there but the driver's own `PER` is not: the frame-loss
reading is part of what the window promises, and a driver that stops printing it must not leave
a GREEN on the strength of the attempts share alone.

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

The `ROUTER` line prints its three load averages as `1/5/15 min` from a separate live read (cached for
a minute), so its 5-minute figure can differ from the `ROUTER LOAD` line's: that one comes from the
recording made every five minutes. Both carry their own read age, so two different numbers for
"5-minute load" in the same pane are two moments, not a contradiction.

The same checker also guards the network area's own continuity. The
`network-progress` duty (`checks/network-care.py`, subcommand `progress`, every
900 s, `max_age` 1800 s, resolver `network`) is RED when the network mind's wall
(`SITE_HOME/walls/network.md`) is missing or more than six hours old, or when none
of `tools/network_view.py`, `tools/network_boundary.py`, `rust/mishe-network-probe`
or `checks/network-care.py` was committed in the last 48 hours — the area has gone
quiet. It is UNKNOWN when `git log` cannot be read, so a shallow or damaged checkout
is not mistaken for a quiet mind.

## What the view says about it

- `last probe` header: the age of the newest probe, the measured cadence, and `STALE` once that age
  passes 5 minutes. The per-site rows, the gateway line and the outage list carry no freshness of
  their own, so this header is the only thing that says they are old. A dead probe leaves their last
  numbers on screen, and `failed in last 40: 0` there is not a calm reading while `STALE` shows.
- `HOME INTERNET`: the reachability verdict from the newest probe — DOWN when the router or the
  secure sites stop answering, DEGRADED on loss or absolute slowness, UNKNOWN when the probe is stale
  or older than the web checks. An otherwise OK headline also names a sustained relative slowdown
  measured by the same ten-probe median and baseline as `USUAL`: `OK, but much slower than usual
  over the last 31 min`, with `for the evening` added during the evening hours, where the baseline
  is the evenings' own. The window is named so a fast newest probe below does not look like a
  contradiction; a stale or unmeasurable baseline leaves the headline at plain OK. `verdict` itself
  is unchanged, so the outage list still counts only DOWN probes.
- `SPEED` and `UPLOAD`: the last good download and upload with the method and age of each. One
  5 MB download and one 2 MB upload per night, only in 02:00–04:59 local, at most every 12 h;
  both are single bounded requests against Cloudflare's speed endpoint, so the times include
  connection setup and are a trend, not a calibrated line test. A failed last try shows its error
  instead of the older good number; no reading at all, or one older than 36 h, is UNKNOWN. The
  upload has its own log and its own 30-minute retry inside the night, so a failed upload does not
  erase the download and a transient failure does not cost the whole night. The duty that produces
  both follows the upload as well: a failed upload attempt, a good upload older than 36 h, or no
  attempt at all once the night window is open makes the `network-speed` signal UNKNOWN, so a night
  of failing uploads wakes the network mind instead of passing as GREEN. Both lines also answer
  "was that usual?": tonight's figure is compared with the median of the earlier measured nights
  (one row a night, bounded to tonight plus the last 13 earlier nights), so `about usual vs 35 Mbps
  over 6 earlier nights (28.1 to 41.1)` or `faster than usual` tells a reader whether the number is
  a bad night. One or two earlier nights are named instead of being called typical, and with none
  the line says `first night measured, no usual yet` rather than inventing a baseline.
- `EXIT NODE`: each client's exit-node state from the `egress` duty's file with the file's age. A
  client that is not GREEN gets an indented line with that duty's own first reason, cut to one
  line at a word boundary, for at most three clients, so an UNKNOWN or RED client says why it is
  not GREEN (for example "the client could not be reached: …"). When that client is still in the
  newest recorded router read, the line instead names the fact a household can see for itself —
  `still on Wi-Fi at -29 dBm; not answering ssh (it may be asleep)` — because an associated device
  that has gone to sleep and a real connectivity fault produce the same `No route to host`. An
  older read (over 15 minutes) is not used for that, so a stale listing cannot explain a live
  fault. UNKNOWN when the file is missing, unreadable or over 15 minutes old.
- `RECORDED 5-MIN`: the recorded router read (one per 5 minutes): client count by band, weakest
  device by name and dBm, the age of that read and the client count over the last hour. It is the
  read that supplies the device names the live `WI-FI` line shows. When at least one client moved
  across the last six good reads but a named client's rx, tx and signal stayed identical for all six,
  the line adds `unchanged over the last 6 reads (rates and signal): <names>`. That is an observation
  only: an idle or dead device looks the same, so it is not a fault verdict.
- `OUTAGES`: each run of DOWN probes with its start and end, plus a trailing span line saying how many
  probe rows were read and how much time they cover (`none over the last 1440 probes in 3.4 days
  (11-14 16:57Z to 11-18 02:06Z)`). Timestamps on different days carry the month and day, so a
  multi-day span or an outage across midnight cannot read as a few hours; an outage still in progress
  says "still down at the last probe" instead of inventing an end. The view reads the last 1440 probe
  rows (`rows()`), which at the current cadence is a few days, and the span line states that length.
- `WAN LINK`: the WAN device's negotiated speed and duplex from the router's own
  `/sys/class/net` files, plus how many times the cable's carrier dropped since the router booted
  and how many of those were in the last hour. The last-hour count sums the counter's
  steps between the readings inside that hour, so an old boot does not hide a fresh drop.
  A downgraded negotiation (for example 100 Mb/s) or repeated drops is the physical cause
  the page-load lines cannot name. UNKNOWN when the newest read failed, carries no
  `wan_link`, or the kernel reports no speed; a step backwards in the last hour (a reboot
  or driver reset) still counts the drops since it and reads "at least N in the last hour
  (counters reset by a reboot)", never "none". STALE past 15 minutes.
- `DEVICE PING` shows each named household device's average ping and loss, along with the age
  of the newest reading in `network/devices.jsonl`. The line shows UNKNOWN when a device did
  not answer or its result is unreadable, and names the affected device. When the reading's
  neighbour state is `FAILED` or `INCOMPLETE`, ARP itself got no answer, so the line says
  `not answering the LAN (no ARP answer)`: the device is asleep or firewalled, not a fault on the
  router side. A missing or unreadable reading, a timestamp more than five seconds in the future,
  or a reading older than 15 minutes also shows UNKNOWN. Readings older than 15 minutes are marked STALE. The five-minute
  `network-device-reach` duty logs the same problems and lets its last GREEN expire 15 minutes
  after the last usable observation.
- `router (LAN)` (in the reachability table): the newest probe's latency, loss and jitter beside a
  sparkline, then the worst packet loss across all loaded probe rows. That worst spans the whole
  loaded window, not the 40 probes in the sparkline next to it, so the line names its span
  (`worst loss 0% (over 3.4 days)`); a loss blip outside the sparkline stays visible and is dated
  by that span instead of reading as a fresh problem. UNKNOWN when no loaded row measured loss.
- `DNS lookup`: the home resolver's time for a fresh lookup in the newest probe, with a sparkline.
  The note on the line matters: the probe's curl `%{time_connect}` (the `connect_ms` that "WHERE
  TIME GOES" prints below) counts name lookup as well as the TCP connect, while this is one separate
  lookup of another name. A cached answer can make that part of a connect far quicker, so
  "reaching the site 41 ms" under "DNS lookup 102 ms" is not a contradiction. UNKNOWN when the
  newest probe carries no lookup time.
- `WHERE TIME GOES`: of the page-load time just above, three disjoint phases that add up to it for
  the newest probe only: reaching the site (`time_connect`), the secure handshake
  (`time_appconnect` minus `time_connect`), and the rest of the page (`time_total` minus
  `time_appconnect`). curl reports all three as cumulative checkpoints from the start of the
  request, so printing them raw would double count the connect and leave the figures short of the
  page total.
  It also gives the handshake's usual median over the loaded rows and, when both have at least
  `PHASE_BASELINE_MIN` rows, the medians for overnight (00:00–05:59) and the busy hours
  (16:00–20:59). Those hours are named because the web checks and the handshake are elevated there
  in the loaded window (16/17/18h medians 609/671/562 ms for web, 475/520/411 ms for the handshake,
  against about 350/210 ms overnight); 21:00 sits near the daytime baseline. The line says where
  the time went, not why:
  household traffic and the ISP's evening peak look the same from here. Probe cadence does not
  vary by time of day: the thin evening counts in the eight-day log come from a partial first day
  and the host reboot of 2026-11-10 (boot ended 18:15Z, next probe 19:54Z), not evening load, and
  at the current 180 s timer period every clock hour carries 17–19 probes. UNKNOWN when the newest
  probe is stale or did not carry all three checkpoints.
  When one host's handshake is at least `PHASE_SPLIT_RATIO` (2x) the other's and at least
  `PHASE_SPLIT_MS` (200 ms) apart, a second indented line names both hosts and the slower one.
  The printed figure is still the mean of the two, so a 710 ms handshake built from gstatic 229 ms
  and cloudflare 1192 ms is explained instead of looking like a general secure-setup problem.
  One host failing to answer is not a divergence: it leaves one sample, with no second line.
  When the headline flags a sustained slowdown, a second indented line names the phase that accounts
  for it, using the same ten-probe window and day baseline as `USUAL` (for example `slowdown is in
  the secure handshake: last 10 probes (31 min) 375 ms vs typical 222 ms (21 h of samples)`). The span
  after the typical names the phase-measured baseline rows, which leave out the newest 60 good samples,
  not all baseline rows, the recent window or the whole loaded day: phase support can be sparse, so the
  web baseline's span would overstate what the typical actually used. This matters because the
  three figures above are the newest probe only: it can be back to normal while the median stays
  slow, and the two lines would otherwise disagree. A phase is named only when it adds at least half
  the extra time and at least 20 ms; when the increase is spread across phases, no line is printed
  rather than a guess. On 2026-11-18 the handshake added 154 of the 178 ms extra (recent median
  375 ms against a 221 ms baseline), and every sliding slowdown window in the last 1440 probes was
  handshake-led; the reply phase stayed near its usual 71 ms (10th–90th percentile 67–83 ms), so
  it needs no standing explanation.
  The comparison uses only probes where one host reported all three checkpoints, so the extra time
  and the three phases come from the same rows and stay additive. When fewer than half the recent
  window, or fewer than `PHASE_BASELINE_MIN` baseline rows, have those checkpoints, the line says
  `slowdown phase UNKNOWN (phases measured on N of M recent probes, K baseline rows)` instead: a
  median from one probe must not be printed as a ten-probe median. With partial but adequate support
  the line names the measured count and the span of those rows, for example `5 of last 10 probes
  (14 min) measured`: the minutes come from the five phase-measured probes, not all ten web-good
  probes that supplied the web median.
- `USUAL`: is the web slower than usual? The recent figure is the **median of the last ten web
  samples**, and the line names that window — `last 10 probes (31 min)` — because at the current
  cadence it covers about half an hour, not the instant: the `gstatic.com` and `cloudflare.com`
  lines above show the newest probe only, and two different numbers both labelled as "now" made
  the pane look self-contradictory (on 2026-11-18 09:36Z the newest probe read 371 ms while the
  half-hour median read 664 ms). The
  typical is phase matched: during the busy hours (16–20 UTC, where pages run structurally
  elevated — on the 2026-11-20 17:20Z loaded window the 16/17/18h web medians were 609/671/562 ms
  against about 350 ms overnight) it is the median of the retained busy-hour samples with the newest
  60 of them left out, labelled `of evenings`, so an ordinary peak hour no longer reads `slower than
  usual` against a baseline biased low by the quiet hours; the headline then says `for the evening`.
  Outside those hours,
  or without enough evening samples, it is the median of the last day's samples with the last
  `USUAL_EXCLUDE` (60) samples left out, labelled `of samples`, so a spike cannot inflate its own
  baseline; fewer than `USUAL_MIN_SAMPLES` (120) samples is UNKNOWN. `about usual` below 1.25×,
  `slower than usual` below 2×, `much slower than usual` at 2× or more; `STALE` past 15 minutes.
  The trend
  sparkline buckets the whole loaded day into `USUAL_BUCKETS` (40) means and is labelled `trend 24 h`
  at the line: the header's "last 40 probes" is only a default, and says wider windows are labelled.
  Without that label a day-long ramp read as a two-hour ramp.
  A bucket with no answered check draws as `·` rather than being skipped, so a dead
  stretch keeps its place on the trend instead of closing up and looking continuous.
  The span after the comparison names the baseline's own sample window (about `21 h of samples` at
  the current cadence), not the duration of the slowdown — the headline above already says how long
  that lasted; a bare "over 24 h" read here as a day-long slowdown that contradicted it. The
  `trend 24 h` label still names the whole loaded day, a wider window than the baseline.
- `SPIKES`: how many of the same last ten web probes took over 1 s (`SPIKE_WEB_MS`). A median
  hides intermittent spikes, so this row names the count beside `USUAL`, on its own line because
  `USUAL` is already at the pane width. It counts the web samples in the window even when the
  `USUAL` baseline is too thin, and reads `UNKNOWN` only when there are no web samples at all.
  It is a count, not a verdict: it does not turn the headline into a warning.
- `WI-FI`: the live listing, one read a minute: client count per band and the weakest client with
  its dBm, its band and the name the household knows it by when the recorded read has that client;
  a device that only just joined shows as its dBm until the next recorded read names it. When any
  device is below -75 dBm (the threshold is printed beside the line), the line names every weak
  device on its own band instead of hiding the others behind one global minimum; at most three are
  named and any remainder counted. `WEAK SIGNAL` marks that state. When the names would run past the
  pane's width, the rest continues on an indented row below rather than wrapping over the frame's
  title; nothing is truncated. The listing is read over ssh and nothing is written; the MAC the
  router prints is mapped to the same short hash the router row stores and never shown. A band the
  listing did not show (its radio gave nothing) is named `UNKNOWN` there, so a silent 5 GHz radio
  is not read as "0 clients on 5 GHz".
- `WI-FI RADIO`: each known radio's channel, frequency and noise floor from the read-only router
  pass, with the age of the reading; UNKNOWN when the sub-reading is absent, STALE past 15 minutes.
  The router has one access point per band (`ra0` on 2.4 GHz, `rax0` on 5 GHz), so a band the newest
  read does not carry is named `UNKNOWN (missing from the newest read)` instead of quietly leaving a
  one-band line; the same gap turns `network-wifi` UNKNOWN.
- `WI-FI QUALITY`: one line per radio — the share of transmit attempts that failed since the router
  booted, the share that failed over the last hour (the difference between two recorded counter
  reads, at least 10 minutes apart), and the driver's own error rate (`driver's error rate N%`, the
  `PER` the driver prints after its fail counter) — then a short trailing line with the highest
  radio temperature, the caveat below and the age of the reading, so no band line overflows the
  pane. Read from the driver's own `iwpriv <radio> stat` counters. Attempts, not frames given up on:
  the driver counts each try, so the attempts share is "how often the radio had to send again or
  gave up" (the 2026-11-18 `iwpriv` experiment read 14.5% attempts against 3.3% PER on 2.4 GHz).
  The driver's PER is shown as the driver's own number because it cannot be reproduced from the
  counters printed beside it — 14.5% failed attempts against PER 3.4% on 2.4 GHz, 6.2% against 0.1%
  on 5 GHz, and on the receive side 15.7% CRC errors against PER 0.7% — so its averaging window is
  the driver's, not a period this line measured; the trailing line says `driver's error rate window
  not measured` instead of inventing one (the `since boot` wording of 2026-11-18 was withdrawn the
  same day for that reason). The error-rate clause is a number when the
  newest read carries `tx_per` (recorded since the 2026-11-18 05:17Z router read), and `driver's
  error rate UNKNOWN` when the newest read has no `tx_per` — a read written before that field
  existed, or a driver that stopped printing it, is never shown as a quiet radio. UNKNOWN when the
  newest read carries no counters on any radio (a
  band whose counters are missing from the newest read is named "UNKNOWN (missing from the newest
  read)" rather than dropped, and `network-wifi` turns UNKNOWN too; a band absent from the newest read
  altogether says `radio missing from the newest read`), or when a reboot has left fewer
  than two counter reads (10 minutes apart) in the hour, which shows as `(last hour UNKNOWN)`. A reboot
  restarts the counters from zero, so only reads after it bracket a rate; with two post-reboot reads
  the line shows that shorter span instead of UNKNOWN. After the last-hour share the line says how
  that hour compares with the completed hours held in the two-day router
  log (`about usual`; `worse than usual (usual N%)` at 1.5× and at least five points above it;
  `much worse than usual (usual N%)` at 2× and at least ten points above; `no usual yet` with fewer
  than three completed hours). The comparison is taken from the hours *before* a sustained bad run,
  not from a median the run would inflate: a radio busy for twelve hours still reads "worse than
  usual" instead of quietly becoming its own normal. If every completed hour is in the run, the
  baseline is the lifetime share since router boot and the line says `(usual N% since boot)`.
  When the current hour is flagged, consecutive completed hours that cleared the `worse` bar add
  `for at least N h`, so a five-hour streak does not read like a fresh blip;
  a calm hour or a hole in the log stops the count; a hole does not let a long bad run become its own
  baseline. An hour needs at least 50 minutes between readings and no gap over 15 minutes to count;
  insufficient history says `no usual yet`.
  It is a symptom, not a
  diagnosis: the attempts figure counts retries, and a radio can work harder at a busy hour without
  losing frames. STALE past 15 minutes.
- `UNKNOWN`: the log has no reading, or the last read failed.
- `STALE`: the last good reading is older than 15 minutes.
- Trends and weekday baselines are UNKNOWN until two days of comparable rows exist.

## Limits

- Signal values are one sample per 5 minutes. A single strong reading is not a finding; it
  becomes one only when it repeats over a day.
- The view redraws every 10 s and reloads its own edited files. A redraw that raises keeps the
  window: it prints `WINDOW ERROR <type>: <message>` and says that nothing in the pane is a reading
  until the error clears, so a partly edited or buggy view is visibly broken instead of making the
  outer window disappear. The five-minute `network-window` check notices a vanished pane and now also
  turns RED when the visible pane says `WINDOW ERROR`: process identity and code freshness alone used
  to call an error frame GREEN, though the frame itself says nothing in it is a reading. Pane text
  that cannot be captured, or an empty pane, is UNKNOWN, never a pass; only the visible pane is read,
  so an old error scrolled off cannot hold a recovered window RED.
  A new copy of the file is only run after it both compiles and starts (its top level is imported
  once in a short subprocess with the main block disabled); a copy that raises at import keeps the
  old pane and the window prints `EDIT REFUSED` with the error, because re-execing into that copy
  is what killed the window on 2026-11-18. A copy with no `if __name__ == "__main__":` block — an
  emptied file, or a save cut at a line before that block, which compiles and imports as a plain
  module — is refused for the same reason: it would exit instead of drawing. A copy that has a main
  block is then launched the way the window launches it (no `--once`) in a throwaway site home and
  kept only if it draws its first frame within five seconds and is still running a second after that;
  a copy cut inside the guard after a complete statement prints one frame and exits, and a copy cut
  inside the redraw loop stays up without drawing: both are refused ("the copy exits at once …",
  "the copy draws one frame and exits", "the copy spins without drawing …"). When the refused copy
  has exited, the refusal appends its own last error line (or its exit status), so a save that dies
  before its first frame still says why rather than only how. The window state records the
  fingerprint of the code the pane actually imported, not the bytes on disk, so a refused edit
  cannot make the five-minute `network-window` check compare the refused file with itself and call
  the old pane current: past the 30 s reload grace that duty is RED with the respawn command.
- Channel, noise floor and the driver's transmit counters are measured once per 5 minutes; a single
  reading is not evidence of channel trouble. There is one access point per band (2.4 GHz and 5 GHz),
  so there is no second AP to compare channels with: `iwinfo` also lists `apcli0`/`apclix0`, which
  are client-mode interfaces on the same two radios, not extra access points.
- The negotiated WAN speed is what the router's port and the ISP handoff agreed on, not the plan's
  speed; a 1000 Mb/s negotiation says the cable and port are fine, not that the line delivers it.
  The carrier counters come from the same read as everything else on the router line, so a missing
  router read is UNKNOWN, never "no drops".
- VPN (sing-box) state is shown as "running" and "tun0 up" from the
  router's process list, followed by the tunnel's own kernel byte counters (`tun0` `rx_bytes` +
  `tx_bytes` from `/sys/class/net`) differenced against the previous read that carried them, so a
  tunnel that is up but routes nothing reads as `nothing through tun0 in 5 min` instead of looking
  healthy. This says the tunnel carried traffic, not which clients use it. A read with no counters, or
  the first read after activation, says `tun0 traffic UNKNOWN` — never "no traffic" — and a counter
  that went backwards names a recreated `tun0`, not a negative rate. The WAN error and drop counters
  are measured: the view reports them from the last recorded router read, and the drop
  count over a window needs at least 10 minutes of counters (`_wan_drops` in
  `tools/network_view.py`).
