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

## What changed because of this

Nothing in code this wake. The findings feed two open senses items:

- readings-timeout alternative (deadline 2026-11-24): Pattern 3 is the
  no-recurrence evidence the re-scope needs.
- rust-runtime migration S2 ("profile the readings scan cost driver"):
  Pattern 1's two steps are the natural before/after experiment for whoever
  profiles the scanner; the dates and medians above are the measured baseline.

## Open questions

- What restarted or changed on Nov 5 and Nov 9 (Pattern 1)? Needs unit
  activation history, which this wake cannot read.
- Does the evening effect (Pattern 2) correlate with host CPU/IO pressure,
  or with network's evening latency phase? Both stores exist; not yet joined.
