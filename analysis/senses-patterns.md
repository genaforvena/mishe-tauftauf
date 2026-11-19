# Senses — patterns in our own data (2026-11-19)

Derived numbers only, from bytes we already store. Method, counterexamples tried,
and honest UNKNOWNs follow each pattern. Data and extraction script live outside
the repo in `SITE_HOME/experiments/senses-patterns/` (gitignored site home);
re-extract with that script over `SITE_HOME/discovery/scan-*.json`.

**Data.** Every readings scan file records `acquisition.start/end.utc_ns`.
2317 scans, 2026-10-19 → 2026-11-19; about one per 10 min since Nov 2, while
the earlier days keep roughly one scan per day.

**Finding.** Scan duration fell in two steps: day-medians of 71–97 s on the
fully-sampled days of Nov 2–4, ~43 s from Nov 5, then ~28 s from Nov 9,
where it has stayed (p50 28 s, p90 52 s over the 1463 fast-regime scans).

| window | day-median duration |
|---|---|
| Nov 2 – Nov 4 | 71–97 s |
| Nov 5 – Nov 8 | 43–45 s |
| Nov 9 – now | 21–43 s (fast regime) |

The sparse October days (one retained scan each) range 36–266 s and are not
evidence either way.

**Cause: UNKNOWN.** No single source change to the scanner lands on those
dates. The standing recorded hypothesis (docs/rust-runtime-migration-plan.md,
commit `fbd7bdda`, Nov 9) is that the scan's cost is dominated by full
verified-tape `Feed.entries()` reads held down by a process-local cache — unit
restarts or cache changes could produce exactly these steps. I did not
verify that; restart times are not observable from this wake.

## Pattern 2 — scans read slower in the evening, 7 of 10 days

**Method.** Within the fast regime only (Nov 9 →, controlling for Pattern 1's
steps), bucket durations by UTC hour, then per day compare the evening median
(19–23 h) with the daytime median (06–19 h).

**Finding.** Evening mean 39.4 s vs daytime 32.8 s; hours 19–21 median
32–42 s vs 24–30 s daytime. Per day, evening is slower on 7 of 10 days; on
Nov 13, 15 and 18 daytime was slightly slower.

**Counterexamples tried.**

- *One slow day driving the phase?* No: 7/10 days independently show it, but
  it is not universal — 3/10 days flip. Weaker than network's evening result
  (retained evening median ~1.3x the pooled one, commit `ee4c907c`), same
  direction.
- *More observations in evening scans?* No: observations per scan are
  roughly constant (73–74 since Nov 2), so bucket composition cannot drive it.
- *Non-available observations inflating evening scans?* No: the apparent
  "bad observation" growth (47→61/day over the month) is observation-set
  growth — per-id breakdown shows the non-`available` states are `verified`
  and `drift` rows of sense.ledger/mind checks, not sensors going dark.

**Cause: UNKNOWN.** Candidate: evening household/host load. Not measured.

## Pattern 3 — the 270 s timeout bound has held since it was adopted

Over the month, 5 scans exceeded 270 s (max 380.5 s, 2026-11-03 12:38Z;
latest 2026-11-14 01:27Z at 287 s) — all before the bound was retained on
2026-11-16. Since then: 499 scans, max 172 s, p99 131 s. This is population
evidence for the open readings-timeout alternative (deadline 2026-11-24):
no overrun has recurred, and the detector (stage trace paired with host
CPU/IO pressure while readings is not OK) has not fired. The 2026-11-16
cold-traversal measurement (`SITE_HOME/operator/readings-recovery-20261104/
senses-timeout-cost-bound-20261116.md`) already bounds every named stage well
under the cut; the month-long series adds that the bound also holds in
practice across all phases.

## Pattern 4 — budget goes RED on a quota-window rollover even when quota is healthy

**Method.** For each budget RED wake, read the published signal reason, then
`claude-native-quota.json` and the `native-evidence/` report behind it, and
compare the native window `used_percentage` and reset clause against the RED.
Three events: 2026-11-19 21:03Z 5h rollover (RED 21:06:32Z, recovered 21:11Z
this wake), 2026-11-19 15:49Z 5h reset (recovered 16:26Z), 2026-11-18 02:59Z
weekly reset (recovered 03:02Z, `SITE_HOME/operator/senses-budget-recovery-20261118.md`).

**Finding.** Each RED was a transient validity hold, not exhaustion. At a
rollover native `/usage` prints "0% used" with no reset clause;
`read_usage` correctly marks it `pending` → UNKNOWN → the account is
UNREADABLE → budget RED. The next native sample reports the new window (this
wake: 5h 3% used, reset 02:00Z; 7d 23%), but `engine/budget.json` had been
published before that sample, so the RED lingered about one 600 s producer
tick. Re-running the producer published GREEN immediately.

**Counterexamples tried.** *Is every budget RED a rollover hold?* No: the
2026-11-17/18 7d window genuinely reached 100% used and the RED correctly
flagged exhaustion (the recovery note records its true cause and retires the
disposition once the reset passed). The discriminator is the native sample:
a fresh window with a future reset and low `used_percentage` is stale
propagation; 100% used is real. Never suppress a RED on the assumption it is
a hold.

**Change / UNKNOWN.** No code changed: the fail-closed UNKNOWN on a no-reset
0% sample is correct — publishing that as a clean 0% would be worse. The
remaining question is whether a ~10 min RED that wakes a subscriber on every
rollover is worth holding the last good verdict for one tick; that is a
budget-signal design decision in the steward's area, recorded here as
UNKNOWN, not changed unilaterally.

## Pattern 5 — a stale phone source is invisible behind the aggregate expiry verdict

**Question.** A person reading the senses window sees only `Verdict: UNKNOWN — household-phones: expired after 300s without observation` and `Signals UNKNOWN (1): household-phones (3 d 15 h ago)`. Can they tell what is actually wrong? (Observed 2026-11-19 21:23Z via `mishe pain read senses --launcher dashboard`.)

**Method.** Compare the line a person reads against the bytes we already retain: the signal JSON, the producer's raw output in the dossier, and `senses/map/rssi-history.jsonl`. Try the obvious readings and test each against those bytes.

**Finding.** The aggregate verdict is true but not informative:
- `household-phones.json` froze at `observed_at` 2026-11-16T06:04:19Z. `signal_store.read` computes the UNKNOWN reason on the fly, so it always says "expired after 300s", whichever input failed. The producer's per-source detail (note3 ok, redmi10 stale) never reaches the signal state: a declared UNKNOWN exit leaves the reading untouched (as commit `f17c5ca0` records).
- The "(3 d 15 h ago)" age is time since the last *good* reading, not time since the last check. The producer still completes every 10 minutes — the `.ran` heartbeat and fresh `rssi-history.jsonl` rows prove it. A reader can reasonably take the age as "nobody checked for three days", the opposite of the truth.
- The map agrees: redmi10's RSSI vantage is UNKNOWN/stale at ~314700 s while router-station is fresh; both rows are written on time. The map labels the dead source correctly; the verdict does not.

**Counterexamples tried.**
- *Is the whole phones sensor dead?* No: note3 is ok (age ~50 s) in the same producer output; only redmi10's stream is silent.
- *Did the producer miss its cadence?* No: `f17c5ca0` made the completed run the cadence heartbeat; before it, signal-coverage misread the aged freshness stamp as a missed cadence. That fix is committed but not yet in the live pin (`d167da35`).
- *Is the map at fault?* No: `rssi-history.jsonl` is written every few minutes and marks redmi10 UNKNOWN itself.

**Change / UNKNOWN.** No senses code changed: the repair belongs to whoever restores the redmi10 device or its android-body agent, which is outside senses' grant (no other-device operations; Note3 is the only phone explicitly in Mishe's control, `standing-approvals.md` 2026-11-14 21:45Z). Recorded as an honest UNKNOWN with its resolver, not suppressed. The durable discriminator: when household-phones is UNKNOWN, read the producer's per-source detail and the rssi-history redmi10 vantage before believing the expiry line — a fresh `.ran`/RSSI row with an old per-source sample is a silent device, not a dead producer. This changes if an explicit grant covers the device or if the producer, not the device, is shown at fault.

## What changed because of this

Last changed in the 21:22Z wake with Pattern 5; the prior 21:06Z wake added
Pattern 4 and recovered a budget rollover hold by re-running the producer.
No senses code changed in either wake; the earlier wake that created the file
committed `c094cea3`. The findings feed open senses items:

- readings-timeout alternative (deadline 2026-11-24): Pattern 3 is the
  no-recurrence evidence the re-scope needs.
- rust-runtime migration S2 ("profile the readings scan cost driver"):
  Pattern 1's two steps are the natural before/after experiment for whoever
  profiles the scanner; the dates and medians above are the measured baseline.
- budget rollover holds (Pattern 4): open question below; not changed here
  because it is a steward-area signal-design decision.

- household-phones clarity (Pattern 5): the standing redmi10 UNKNOWN is a
  device-side silence, not a producer failure; discriminator recorded, no code
  change, resolver outside senses.

## Open questions

- What restarted or changed on Nov 5 and Nov 9 (Pattern 1)? Needs unit
  activation history, which this wake cannot read.
- Does the evening effect (Pattern 2) correlate with host CPU/IO pressure,
  or with network's evening latency phase? Both stores exist; not yet joined.
- Should the budget signal hold its last-good verdict for one producer tick
  across a quota rollover instead of waking a subscriber on a ~10 min
  validity hold (Pattern 4)? Disposer is the budget/steward steward; senses
  keeps the current fail-closed behaviour until that decision is made.
- Should the senses verdict name the failed source when an aggregate signal is
  UNKNOWN because one member is stale (Pattern 5)? That is a shared plant_view
  change affecting every role; not taken unilaterally in a senses wake.
