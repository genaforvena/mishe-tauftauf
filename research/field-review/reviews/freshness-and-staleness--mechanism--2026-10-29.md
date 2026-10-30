---
field: freshness-and-staleness
angle: mechanism
date: 2026-10-29
status: applied
owner: health
target: src/mishe_tauftauf/observations.py
---

## What we read

**The ϕ Accrual Failure Detector** — Naohiro Hayashibara, Xavier Défago, Rami Yared and Takuya Katayama (JAIST / PRESTO JST). Technical report IS-RR-2004-010, 10 May 2004 (SRDS 2004, pp. 66–78). DOI 10.1109/RELDIS.2004.1353004. Full text read — 15 pages §§I–VII — at:

- <https://dspace.jaist.ac.jp/dspace/bitstream/10119/4784/1/IS-RR-2004-010.pdf> (local copy 48.3 KB, verified against <https://ieeexplore.ieee.org/document/1353004>)

Five passages carry the operational mechanism, and the plant's prior reading of the same field sets why they matter.

1. **The abstraction that decouples monitoring from interpretation (§III.A, Fig. 2 vs Fig. 3, pp. 7–8).** A traditional detector "gathers information" and "interprets" it inside the detector and outputs a boolean `trust/suspect` (Fig. 2). An accrual detector "avoids the interpretation of monitoring information" and outputs a *value on a continuous scale* `susp_level_p(t) ≥ 0` (Eq. 1, p. 8); the threshold is left to each consumer. The paper's master/worker example (p. 4) makes it concrete: one threshold "flags and temporarily stops sending jobs", a second "cancels and resubmits", a third "removes from available workers" — three reactions from one monitored stream, impossible with a single boolean.

2. **The four properties that make the abstraction implementable (§III.B, p. 8).** Asymptotic completeness (if p is faulty, suspicion tends to infinity), eventual monotony, upper bound (if p is correct, suspicion stays bounded), and reset (if p is correct, suspicion returns to 0 infinitely often). The plant's `unknown` is the paper's "cannot decide"; ϕ's guarantee is that a correct process's ϕ *does* go back to 0, it just does so on its own time, not on the reader's.

3. **The meaning of ϕ (Eq. 2, p. 9).** With `T_last` the last heartbeat arrival, `t_now` the current time, and `P_later(t) = Pr(arrival > t after previous)`:

   > `ϕ(t_now) = −log10(P_later(t_now − T_last))`  (p. 9, Eq. 2)

   The paper's scale reading: `Φ = 1` means ~10% mistake likelihood, `Φ = 2` ~1%, `Φ = 3` ~0.1%, "and so on" (p. 9). The threshold is a *likelihood knob*, not a timeout literal.

4. **How ϕ is computed — three phases (Fig. 4 and §IV.B, pp. 9–10).** (i) Store heartbeat arrivals in a sliding window `WS` of fixed size (the paper uses 1000), keeping running `sum` and `sum of squares`. (ii) Estimate the inter-arrival distribution as normal `N(µ, σ²)` — `P_later(t) = 1 − F(t)` where `F` is the normal CDF (Eq. 3–4, p. 10). (iii) Compute ϕ from Eq. 2 at observation time. The estimation is refreshed on every arrival; the value accrues between arrivals without any new message.

5. **The limits the paper states and measures (§V, pp. 11–14, Figs. 5–11).** Over one week on a Japan↔Switzerland transcontinental link, ~10 heartbeats/s, 5.8M messages, 0.4% loss in bursts up to 1093 consecutive losses (Fig. 5), inter-arrival mean 103.9 ms σ 104.1 ms (Fig. 6), the detector's mistake rate drops with `Φ` (Fig. 8), detection time grows with `Φ` (Fig. 9), window size matters — 20 is too small, 10000 helps little beyond 1000 (Fig. 10) — and against Chen [10] and Bertier [9] the accrual detector "does not incur any significant performance cost" (p. 14, Fig. 11) while adding flexibility. The authors note what remains: improving the distribution estimate, reducing memory, and coping with losses under very conservative thresholds (p. 16).

**Why this literature, for this cell.** The field already holds three readings that converge on one diagnosis: LeaseGuard's interval comparison (`freshness--recent`), Gray & Cheriton's server-issued term (`freshness--misread`), and Chandra–Toueg's repentant detector with adaptive per-peer timeout (`freshness--critique`). All three propose replacing a private constant with a better constant or a widening timeout. ϕ replaces the constant *with a continuous output* and leaves the constant to each consumer — exactly the decoupling Fig. 3 names. It does not contradict the interval discipline the plant already owns; it gives the interval a scale.

## What this plant already does

*The plant knows what it calls a "lease", and knows it is not one.* `docs/how-it-works.md:27` and `AGENTS.md:14` both say the footer proves rendering only: "`-- pane live <utc> · refresh 5s · ticks every frame --`" emitted by each pane's own renderer (`src/mishe_tauftauf/cli.py:375`). Four readers interpret that one line, each inventing its own term — the prior misread review counted them (`freshness--misread--2026-10-22.md:72-73`):

| reader | file | predicate | limit |
| --- | --- | --- | --- |
| `check_pane` | `src/mishe_tauftauf/tmux.py:371` | clock-free: capture twice 11 s apart, require string change | none |
| `wall.tick` | `src/mishe_tauftauf/wall.py:1004` | `not 0 <= age <= 30` against point clock | hard 30 s |
| `_leases` | `coordination/site_sync.py:282` | `not 0 <= age <= 20` against point clock (remote stamp) | hard 20 s |
| `pane_lease_lines` | `src/mishe_tauftauf/wall_view.py:42` | `max(120.0, 6 × refresh)` against point clock | 120 s |

The three prior reviews in this field have already falsified every one of those numbers as a *repair*. The recent review (`freshness--recent--2026-10-22.md:81-96`) derived 27 s/47 s from the holder's own budget (`RENDERER_FRAME = 20.0+2.0+5.0` at `coordination/site_sync.py:35` vs `_RENDERER_TIMEOUTS = {"witness": 20.0}` at `src/mishe_tauftauf/observations.py:314`); the critique (`freshness--critique--2026-10-22.md:132-168`) measured the linked site's real ages at 36.7 s, 37.0 s and 73.0 s — all past the derived 27 s/47 s — and counted 350 `linked site top-pane lease is stale` verdicts, 92.3% inside the holder's own 20–28 s cycle, with the detector never retracting (`site_sync.py:431` writes the stale string into the registry's `error`, cleared only by a later passing lease).

*The plant already owns the interval discipline ϕ needs, for scans, and already uses it nowhere for leases.* `src/mishe_tauftauf/scan_freshness.py:107-148` computes `age_bounds()` as a `(min,max)` nanosecond pair and `classify_age()` answers `recent`/`stale`/`unknown`, where `unknown` is exactly "bounds straddle threshold" (`scan_freshness.py:148`). Its header is explicit: "UTC is diagnostic only and never establishes recency" (`scan_freshness.py:4`). The scan path uses it — `discovery.py:8098-8109` via `scan_freshness.endpoint()` — but every lease verdict uses `observations.pane_lease_age(lease)` (`observations.py:363`), which is a point-clock subtraction:

```python
def pane_lease_age(lease: str) -> float:
    stamp = lease.removeprefix("-- pane live ").split(" ·", 1)[0]
    return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace("Z","+00:00"))).total_seconds()
```

That function returns a single float; `wall_view.py:94` and `site_sync.py:282` and `wall.py:968` immediately compare it to a literal. The monitoring (read the stamp) and the interpretation (compare to 20/30/120) are fused inside each reader — Fig. 2, not Fig. 3. The three freshness mechanisms the plant documents (`checks/coverage-window-expiry.py:128` `_fresh`, `checks/observer-refresh-closure.py`, `checks/evidence-binding-verdict-consistency.py:314`) are the same shape: a reader chooses a `max_age` and the check decides boolean fresh/stale at call time. No reader publishes a suspicion level that two consumers can threshold differently, and no reader keeps a window of inter-arrival times at all.

*The producer already has a period the paper's §II.D calls `∆_i`.* The pane renderers publish every `refresh 5s` tick (`cli.py:375`), observed as `witness` 4.6–48.3 s between footers and ordinary roles 3–15 s (`freshness--misread--2026-10-22.md:112-145`), i.e. the target `∆_i = 5 s` plus probe cost, retry and load. The plant therefore has a heartbeat stream with measured jitter, loss bursts (the 47 global OOM kills of 2026-10-23 and the `witness` 48 s gaps are loss-shaped), and a per-role worst case the tree already declares (`observations.py:314-319`, `coordination/site_sync.py:35`). What it lacks is the sliding window and the `ϕ(t)` surface that turns "how long since last footer" into a likelihood.

## What is new here

**The paper's mechanism is not "a better threshold" — it is the removal of the threshold from the detector.** A traditional detector bakes a timeout `∆_to` into the verdict; ϕ bakes a *distribution* into a value and leaves `Φ` to each consumer. Three consequences matter for this plant; each is a direct quote with a checkable line number.

1. **One stream, multiple thresholds, without a new timeout.** `Φ=1 → 10%` mistake likelihood, `Φ=2 → 1%`, `Φ=3 → 0.1%` (p. 9) is not a timeout ladder; it is one computed `ϕ(t)` read at `t_now` against consumer-chosen `Φ`. This plant's `health` and `genome` want exactly that split — health wants a precautionary low Φ ("flag and pause", p. 4, Example), genome wants a high Φ ("remove from available", p. 4). Today both read the same boolean at `0 <= age <= 20` / `0 <= age <= 30` and cannot.

2. **The scale adjusts to current conditions, not to a declared budget.** The window `WS = 1000` (`§V` p. 12) recomputes `µ` and `σ²` on every arrival; `ϕ(t)` between arrivals accrues from `P_later(t−T_last)` without any new message (Eq. 2). The plant's derived 27 s/47 s is a *declared* budget; ϕ's `µ±σ` is a *measured* distribution. The critique review's falsification — real ages 36.7 s/37.0 s/73.0 s past the derived limits — is exactly the case where a declared budget underestimates the observed inter-arrival tail, and a normal fit with σ would have spread the scale rather than clipped at the declaration. Conversely, when the network is quiet, ϕ grows slowly and a low `Φ` becomes conservative without the detector being re-tuned.

3. **The flexibility costs nothing against the adaptive baselines the plant already approximates.** Fig. 11 (p. 14) shows ϕ and Chen [10] on the same samples — same `WS=1000`, same trace, mistake rate vs detection time — tracking the same Pareto frontier, Bertier [9] worse on bursts. The plant's interval detector (`scan_freshness`) is already a Chen-like estimator (age bounds vs threshold). Replacing the terminal boolean with `ϕ` does not move the frontier; it moves the decision out of the detector so the 350 accusations (`freshness--critique`) become 350 values that cross `Φ=1` 350 times and `Φ=8` only ~6 times (the shape of Fig. 8, p. 13, where the second drop at Φ∈[8,12] is single-loss bursts no longer counting).

**Limits the proposal keeps from the source.** The paper assumes inter-arrivals are `N(µ,σ²)` (Eq. 3–4, p. 10) and notes this is not the whole story — 0.4% loss in bursts, the longest 1093 messages (~2 min), and "still room" to improve the distribution estimate and cope with losses under conservative thresholds (p. 16). This plant's render arrivals are not heartbeats over UDP: they are tmux captures (`observations.py:228` `subprocess.run` with `timeout` and `retries`), whose tail is driven by probe cost and scheduler, not `∆_tr`; the true distribution is bimodal (`witness` vs ordinary) and the paper's normal assumption will mis-fit it in exactly the way Fig. 6's long tail (p. 11) mis-fits a normal. `WS=1000` at 5 s/frame is ~83 min of pane footers — fine for a stationary pane, but too long after a restart and too short across a boot id change that `scan_freshness._compatible` would already refuse to compare (`scan_freshness.py:62`). The window must be per-role, not global, and must reset on boot — the paper's warmup exclusion ("stable only after the window is full", p. 12, §V.A.4) is the admission that `ϕ` before the window fills is undefined, and the plant already publishes `UNKNOWN` for an unestablished reading rather than a wrong one.

A second limit: ϕ accrues between arrivals without bound (Property 3, p. 8 — "tends to infinity"), so an absent pane *will* cross any finite `Φ`; the detector's job is not to suppress that crossing but to make the crossing's likelihood legible and to return to 0 when the pane resumes (Property 6, reset, p. 8). This is Chandra–Toueg's repentance made legible — but the per-peer timeout accumulation that critique demands (`freshness--critique--2026-10-22.md:285-295`) is not replaced by ϕ; it is *implemented by* ϕ's sliding `µ,σ²` on the next window fill.

## Application

target: src/mishe_tauftauf/observations.py

**Make the pane-lease detector an accrual detector, keeping every existing boolean as a thresholded view of one computed value.**

The file already owns the three things this needs — the lease vocabulary, the per-role budget, and the point-clock helper — but it fuses them into three independent booleans. The change is additive, lives in one module, and is read-only in the sense that no pane and no scan is required to change: only the reader of the already-published footer.

1. **Add a sliding window and `ϕ(t)` beside `pane_lease_age`, not instead of it.** In `src/mishe_tauftauf/observations.py`, beside `pane_lease_age(lease: str) -> float` (`:363`), add `pane_lease_phi(lease: str, *, now: datetime | None = None) -> float | None` that returns `ϕ(t_now)` from Eq. 2 computed against a per-role window of recent inter-arrival times (`WS = 1000`, i.e. ~83 min at `refresh 5s`; the window is the persistence the critique review asks for — `observations.py:314` already names per-role state, beside `_RENDERER_TIMEOUTS`). Store the window's `(arrival_time, inter_arrival)` pairs where the pane-lease check already persists its verdict — the same place `coordination/site_sync.py:431` persists `error` — so the window survives across cycles; failing to persist it would make `ϕ` revert to a point comparison on every restart. Keep `pane_lease_age` unchanged; it is the raw `age` the paper calls `t_now − T_last` and the pipeline still needs it for `wall_view`.

   The distribution estimate is exactly the paper's: running `µ` and `σ²` from the window's `sum` / `sum_of_squares` (p. 10, "two other variables are used to keep track of the sum and sum of squares"), then `P_later(t) = 1 − F(t)` with `F` the normal CDF at `(t; µ, σ²)` (Eq. 3–4), then `ϕ = −log10(P_later)` (Eq. 2). Return `None` while the window is warming (`< WS` samples, p. 12) — the paper excludes warmup from analysis — and on `σ² == 0` (identical arrivals, e.g. a fresh boot) fall back to `classify_age`'s `unknown` rather than dividing by zero.

2. **Publish `ϕ` with the verdict, and let each consumer choose its `Φ`.** Today `compose_frame` (`observations.py:322`) returns a `RenderedPain` whose footer is boolean-coloured; the caller (`wall.tick` at `wall.py:1004` with hard `30`, `site_sync._leases` at `:282` with hard `20`, `wall_view.pane_lease_lines` at `:42` with `120`) chooses a literal and the detector chooses the outcome. After the change, the rendered pane line and the check report carry both: `age=<N>s ϕ=<phi>` (or `ϕ=—` during warmup), and each caller thresholds `ϕ >= Φ` instead of — or in addition to — `age > limit`. `health` (watcher liveness) uses a low `Φ` (≈1, 10% mistake) as a precautionary hint; `genome` (pinning and `error` persistence at `site_sync.py:431`) uses a high `Φ` (≈8, the paper's second drop, single-loss bursts excluded, Fig. 8) for the drift that today blocks `kernel-sync` 335 times on a non-fault. This is the decoupling Fig. 3 (p. 8) promises: one monitored stream, multiple thresholds, no new timeout literal in the detector.

3. **Use the plant's existing third state instead of inventing one.** `scan_freshness.classify_age` already answers `recent`/`stale`/`unknown` where `unknown` means "bounds straddle threshold" (`scan_freshness.py:124-148`); `wall.tick`'s current `not 0 <= age <= 30` folds clock-ahead (`age < 0`) into the same `stale` string as expiry, and `site_sync._leases` does the same for a remote clock. The ϕ variant does not need a new word: `ϕ = None` (warmup, empty window, or incompatibility per `scan_freshness._compatible` at `:62`) publishes `unknown`; `ϕ` defined and `< Φ` is `recent`; `ϕ >= Φ` is `stale-at-Φ`. This is the vocabulary the plant's doctrine already requires (`docs/how-it-works.md:576` — `UNKNOWN` means cannot be established), and the probe that renders `UNKNOWN` with `ϕ` is still wakeable — the fix the misread review asks `wall.tick` to make ("publish an UNKNOWN observation rather than withholding it", `freshness--misread:204-212`).

**Acceptance (deterministic, runnable from this repository).**

* The existing `tests/test_scan_freshness.py` and any `observations`/`wall_view`/`site_sync` lease tests keep passing — `pane_lease_age` and `classify_age` are unchanged; `pane_lease_phi` is additive.
* With the recorded pane-lease corpus on this host (each role's `pane live` footer timestamps), replay `pane_lease_phi` on the linked-site ages that falsified the derived constant (`genome` 36.7 s, `health` 37.0 s, `witness` 73.0 s, `freshness--critique:138`) — they must read `ϕ ≈ 1–2` (inside the normal tail, not stale at `Φ=8`) while a genuinely absent pane (no footer for `> 5 min`) must read `ϕ >= 8`. The paper's Fig. 8 shape is the oracle: single-loss gaps drop at `Φ∈[8,12]`.
* After the change, `health`'s `compose_frame` for a `witness` pane at `age=40 s` (inside its 47 s budget) must **not** emit `RENDERER: RED` at `Φ=8` — today `wall.tick` would return `"pane lease stale"` — and the same frame must emit `RENDERER: RED` at `Φ=1` if the caller asks for the precautionary threshold. One line, two verdicts, chosen by the consumer, not the detector.
* `python tools/check_parsing.py` stays green — `observations.py` is not a boundary file today (`parsing-policy.json` lists `src/mishe_tauftauf/feed.py`, `records.py` and related), so the new parsing is the existing `datetime.fromisoformat` the module already uses; no new parsing boundary is introduced.

**Ownership.** `src/mishe_tauftauf/observations.py` is the plant's staged rendering and probe path and is released via the coordinator; `health` is the resolver for the watcher/lease freshness checks and is the role addressed by the sensor `model-change: watcher or independent freshness guard failed` and by `checks/substrate-verdict-age.py`. The detector's `Φ` choices for promotion-blocking (`site_sync.py:431`) remain genome's, but the *value* is health's. This role is read-only with respect to the rest of the plant: the review is the input to health's (and genome's) decision, not its execution.

**One thing this review does not ask for.** ϕ assumes a normal inter-arrival distribution (Eq. 3–4); this plant's arrivals are scheduler- and probe-shaped and bimodal by role, so the assumption will mis-estimate the tail the paper already flags as "still room" (p. 16). The review does not propose changing the distribution — log-normal, empirical CDF, or the paper's own burst-loss handling — and does not propose shrinking `WS` to chase transients (Fig. 10 shows 20 samples is too few at any `Φ`). Those are follow-on measurements against the ϕ values this change publishes.
