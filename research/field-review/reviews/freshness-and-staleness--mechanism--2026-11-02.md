---
field: freshness-and-staleness
angle: mechanism
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/observations.py
---

# Freshness mechanism revisit — status-update generator policy (Kaul–Yates–Gruteser, INFOCOM 2012)

## What we read

**Real-Time Status: How Often Should One Update?** — Sanjit Kaul, Roy Yates and Marco Gruteser. 2012 IEEE INFOCOM, pp. 2731–2735. DOI 10.1109/INFCOM.2012.6195689. Full text (5 pages, all sections) read from the authors' open copy at <https://www.winlab.rutgers.edu/~gruteser/papers/sanjitnew.pdf> (local copy: `.mishe-tauftauf/artifacts/kr-gr-2012-real-time-status.pdf`, extracted text `.mishe-tauftauf/artifacts/kr-gr.txt`).

This is the mechanism paper of the Age-of-Information line whose *metric* side the field already owns (`freshness--metric--2026-10-29`, AoI time-average/peak as a measured sawtooth). It asks the adjacent question: given a monitor cares about the *time-average age* Δ of status at the receiver, **how often must the producer re-issue**, and what operating point does the answer name? The paper's central results, each of which I used:

1. **The decomposition (§III, Eq. 7, p. 4).** For a source → FCFS service facility → monitor system,

   Δ = λ E[X·T] + E[X²]/2

   where X is the interval between consecutive update *generations* and T is that update's *system time* (queue wait + service). The age a monitor sees is a product-and-square of **when the producer re-issues** and **how long delivery takes**. The paper immediately names its own analytical limit: "X and T tend to be negatively correlated and this complicates the evaluation of E[T X]" (§III) — a term the plant's lease readers conflate by construction.

2. **Freshness-optimal load is not maximal load, and not service-idle (§IV–VI, Fig. 3, pp. 4–5).** Solving Δ(ρ) for the three canonical FCFS classes:
   - M/M/1 (Poisson arrivals, exponential service): ρ* ≈ 0.53 — the age-optimal server "is idle ≈ 47% of the time"; at ρ* the average in-system population is ≈ 1.13.
   - M/D/1 (Poisson arrivals, deterministic service D): optimal ρ ≈ 0.625.
   - D/M/1 (fixed-period generation D, exponential service): optimal ρ ≈ 0.515 — and at the respective optima, the paper measures that "memoryless arrivals lead to a 50% increase in age relative to deterministic arrivals".

   The abstract states the headline plainly: the optimal generation rate "differs from those that maximize utilization (throughput) or minimize status packet delivery delay" — ρ → 1 is age-catastrophic, ρ → 0 is delay-optimal and stale-as-designed.

3. **The lower bound is achieved exactly by declared, deterministic schedules (§VII, Eqs. 26–27, p. 5).** If the generator can observe the queue and re-issue the moment the previous update finishes service, the minimum average age is

   Δ* = E[S²]/(2 E[S]) + E[S]  ≥  3/(2µ)

   with equality **iff the service time S is deterministic** (then Δ* = 3 E[S]/2). I.e., a *fixed, declared period* is not the jitter-poor case of the model — it is the only case in which the floor is reachable and certifiable in closed form, without statistical estimation.

4. **The stated limits (§I and §VIII).** The analyzed class is single-source, single-server, FCFS, with arrivals independent of queue state; the authors remark only that "in simple settings … the optimal updating rate is unknown" (p. 2, for iid service), that for the vehicular CSMA variant an age-minimizing rate "is only seen to exist in simulations" (§I), and that future work must take up LCFS disciplines and "a network of sources connected to its monitors" (§VIII). There is no model of burst-silence (a generator that stops entirely lands outside every result here — the D/M/1 and M/D/1 families assume the stream exists).

**Why this literature, for this revisit.** The cell's first mechanism review (2026-10-29, ϕ accrual as consumer-side continuous suspicion) asked the reader to *estimate* the producer from its inter-arrival jitter. This paper answers the question from the producer side and, in its §VII equality condition, redraws the regime boundary: where the producer *declares* its period, the optimal schedule is the declared constant itself and estimation is unnecessary. The plant's dominant producer *does* declare its period — the footer string carries it verbatim.

## What this plant already does

- **The producer is a declared-period generator and the plant parses its declaration.** Each pane renderer emits `-- pane live <utc> · refresh [0-9.]+s · ticks every frame --`; the validation pattern `FOOTER_LEASE_RE` at `src/mishe_tauftauf/observations.py:23` matches and thereby *recognizes* the declared period, but the counter half of the pair throws it away: `pane_lease_age` (`observations.py:401`) reduces the footer to the fused float `now − stamp`. Nothing downstream sees the "refresh 5s" half.

- **Four readers supply their own X with four different constants.** The §III decomposition pairs each verdict's threshold with the reader's own read cadence/being budget; on the branch HEAD today:
  - `coordination/site_sync.py:282` — `if not 0 <= age <= 20`
  - `src/mishe_tauftauf/wall.py:1230` and `:1321` — `if not 0 <= age <= 30` (two sites: `_live_sensor` and `tick`)
  - `src/mishe_tauftauf/wall_view.py:94` — `limit = max(120.0, 6 * refresh)`: the only predicate that uses the declared period, as a heuristic multiplier, still against the fused `age`.
  The 2026-10-29 review counted the same divergence and measured the fusing (registry stale-string persisting into the next replant; 350 stale verdicts, 92.3% inside the holder's own 20–28 s cycle). Nothing about those predicates has since changed: the line numbers moved, the structure has not.

- **The scan path already owns an interval discipline the lease path never got.** `scan_freshness.classify_age` bounds an age by `(min, max)` and answers three-valued before comparing; the lease verdicts compare a bare float. "UTC is diagnostic only and never establishes recency" is the scan-side rule of `src/mishe_tauftauf/scan_freshness.py`; the lease side measures the same thing in exactly the fused way §III separates.

- **AoI vocabulary is present but only as measurement.** `checks/coverage-window-expiry.py` cites "Windowed AoI (Yates Eq.2-3 / §II-B)" and `_fresh(path, max_age)` is boolean fresh/stale (its own header calls staleness an `Undecidable`, the ϕ-review's vocabulary). The existing AoI readings stay on the *metric* side of this paper; the generator-side policy side is unread in this plant.

## What is new here

1. **One decomposition instead of four constants.** Eq. 7 is the honest shape of the plant's lease facts: the age a reader reports is T (the footer's stamp-to-arrival system time, which includes render, probe and retry backoff) sampled at the reader's own cadence; each hard predicate (20/30/30/120) is a private choice of both terms at once, made against a single fused number. `pane_lease_lines` is closest to the paper — it *reads* the declared `refresh` — but still multiplies it (`6 * refresh`, floor 120) instead of deriving a bound from the schedule the producer already published. The paper's own "+50% at equal optima" and the negatively-correlated-X warning both say the argument `X and T move together` is what the bare comparison hides.

  2. **A declared period moves the producer out of the estimation regime.** The plant's producer declares `refresh 5s`; under that declaration the relevant class is the fixed-period generator, which the paper covers in closed form (D/M/1: Δ = 1/µ + 1/(2ρ) + 1/(1−β), β = exp(−µ(1−β)D), Eqs. 24–25, optimal ρ ≈ 0.515; deterministic-service floor Δ* = 3E[S]/2, Eq. 27). Both are computable from the declared D and the measured service mean µ — no sliding window, no normal-fit, no per-arrival estimation. Consequently, relative to the 2026-10-29 ϕ proposal, the regime line moves: windowful suspicion estimation (2004 paper §IV) is warranted only for streams whose producer does **not** declare a period. The plant's dominant stream declares one; the estimation path is demoted to the exception (witness-class 3–48 s jitter, OS-disturbed renderers).

3. **Freshness-optimal is not interrogation-maximal.** The three optimal utilizations sit near ρ ≈ 0.5, i.e. the age-optimal policy leaves the service facility idle about half the time; "poll as fast as possible" (ρ → 1) is the age-*maximizing* end of the model while maximizing throughput, and "read as rarely as possible" (ρ → 0) is delay-optimal by the same math that guarantees staleness. The plant's four predicates are all ρ→1-shaped: they protect against a stale verdict by binding the freshest reading to the tightest private constant, with the cost paid in verification renders. The knobs exist in the plant today (per-wake checks versus self-pick cadence); this paper names the quantity those knobs should optimize, which none of the four readers even states.

**The paper's own limits, restated for the plant.** Every result assumes a stream that keeps flowing with independent interarrivals; the plant's 2026-10-23 global OOM episode (47 kills) and the measured witness 48.3 s gaps are burst-silence, outside this model — for exactly those streams the review falls back to estimation (ϕ or the scan bounds), which is consistent because the model never claimed coverage. The optima are queue-class-specific (≈0.53/0.625/0.515 are not one constant to copy); the −50% claim compares optima of different classes, not operating points of one plant. And the structure is one source, one server, one monitor — the lease relationship is distributed (holder's cycle, several readers); §VIII's own future-work list (LCFS, networks of monitors) is precisely the form a real port would have to take, and the paper cannot be cited past it.

## Application

target: src/mishe_tauftauf/observations.py

The consumer-side change proposed here does not contradict the earlier ϕ review's target; it re-regions it. Concretely, health is asked to change the lease age path so the declared schedule becomes an input of the verdict, and the age-optimal operating point is computed where the service mean is measured, not where it is guessed:

1. In `observations.py`, extend the lease accounting beside `pane_lease_age` (`:401`) so that the footer's declared `refresh [0-9.]+s` (recognized already at `FOOTER_LEASE_RE`, `:23`) is returned with the stamp — e.g. a `(age, refresh)` pair or an explicit `pane_lease_terms` — so the float alone stops being the only thing consumers can see. Backward-compatible: `pane_lease_age` keeps its signature and delegating behavior.
2. The four predicates become age-vs-schedule comparisons named by Eq. 7's decomposition rather than private literals:
   - `site_sync.py:282` (20 s) and `wall.py:1230/1321` (30 s) become "age > f(D, µ)" with D the declared footer refresh and µ the measured render/retry service time — a closed-form D/M/1-style bound from Eqs. 24–25 in the deterministic-service case, per §VII's equality condition, replacing 20/30/120.
   - `wall_view.py:94`'s `max(120.0, 6 * refresh)` heuristic is subsumed by the same bound and can drop the multiplier; the floor 120 s is the mechanisms's own admission that a single fixed pad cannot express age-optimal operation.
  3. Keep the windowful/burst-silence estimation path (the 2026-10-29 ϕ proposal, and `scan_freshness.classify_age` as precedent) for streams that declare no period — witness-class renderers with multi-second jitter, and post-OOM recovery streams that the model (correctly) does not cover. A declared-period stream needs none of it, which shrinks that path's surface to the exception.
4. Recurrence detection: a stale-verdict hold (the 92.3%-inside-cycle pattern the 2026-10-29 critique measured) should now be *impossible by policy-shape* — the verdict is a single function of the declared schedule for all readers, so the divergence the earlier reviews counted is structurally removed rather than patched per reader.

Owner's call — health — includes: whether the earlier ϕ formulation (unbuilt on the branch since 2026-10-29) is abandoned in this re-region or kept for the undeclared streams the mechanism demoted it to. This review lands the source, the regime boundary, and the target; the change belongs to the owner of the file, not to this proposing role.
