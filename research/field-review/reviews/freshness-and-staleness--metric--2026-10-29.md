---
field: freshness-and-staleness
angle: metric
date: 2026-10-29
status: applied
owner: senses
target: src/mishe_tauftauf/scan_freshness.py
---

## What we read

**Age of Information: An Introduction and Survey** — Roy D. Yates, Yin Sun, D. Richard Brown III, Sanjit K. Kaul, Eytan Modiano and Sennur Ulukus (WINLAB Rutgers, Auburn, WPI, IIIT-Delhi, MIT, Maryland). arXiv:2007.08564v1, 16 Jul 2020 (38 pp., §§I–IX, 165 refs). Journal version *IEEE JSAC* 2021. Full text read — §§I and II in full, §§III–VI scanned — at:

- <https://arxiv.org/abs/2007.08564> (abs)
- <https://arxiv.org/html/2007.08564v1> (HTML, rendered above, 912 lines §I–II verified)
- <https://arxiv.org/pdf/2007.08564v1> (PDF, 38 pp.)

Four passages carry the metric this plant lacks.

1. **The definition the rest of the paper measures (§I, p. 3; §II, p. 5).** An update with stamp `u` has age `t−u` at time `t≥u`; when the monitor's freshest received update at `t` has stamp `u(t)`, the monitor's age process is `Δ(t)=t−u(t)`. Fresh means `Δ=0`. Any other monitor that sees updates after the network has a sawtooth `Δ(t)` that resets to the update's network age `t'_j − t_j` on delivery and grows at unit rate until the next delivery (Fig. 1, p. 5).

2. **Time-average age as the area under the sawtooth (§II-A, Eq. 1–3, p. 6–8).** ⟨Δ⟩_T = (1/T)∫_0^T Δ(t)dt; for stationary ergodic updating with inter-arrival `Y_n=t_n−t_{n-1}` and system time `T_n=t'_n−t_n`, the polygon `Q_n = (1/2)(T_n+Y_n)^2 − (1/2)T_n^2 = Y_n T_n + Y_n^2/2` (Eq. 2, Fig. 2) gives `Δ = E[Q]/E[Y] = (E[Y T]+E[Y^2]/2)/E[Y]` (Eq. 3). The alternate decomposition `Q_n = D_n T_{n-1}+D_n^2/2` with inter-departure `D_n=t'_n−t'_{n-1}` gives `Δ = E[Q]/E[D]` (Eq. 4–5). Both are exact for the same process.

3. **Average peak age — the simpler, loss-tolerant companion (§II-B, p. 9).** Peak `A_n` is `Δ(t)` just before `t'_n`; average peak `Δ^{(p)} = lim (1/N)∑A_n = E[A]`. The paper notes it is coarser than time-average but easier to obtain from traces where only peaks are sampled, and that the two track each other under the same `Y,T` law.

4. **What the metric is not (§I, p. 2; §III, p. 10).** "timely updating is not the same as maximizing utilization … nor the same as ensuring that updates are received with minimum delay" (p. 2, citing Kaul et al. 2012 [4]). High utilization backlogs stale updates; throttling reduces delay but leaves the monitor with outdated state. Age is an *end-to-end* metric; delay and throughput are not substitutes. §VI–VII then review non-linear penalties `p(Δ(t))` and estimation-error functionals that replace the unit-slope sawtooth when a specific estimator or control cost is the consumer.

**Limits the source states and this review keeps.** The closed-form averages assume stationarity and ergodicity of `(Y_n,T_n)` (p. 7); the elementary `M/M/1` and `D/M/1` analyses assume FCFS (§III). The survey flags that periodic or generate-at-will sources (where the source can create a fresh sample on demand, §V–VI) change the `Y` law and that energy-harvesting and multi-source scheduling shift the optimum away from the single-source formulas. The paper does not claim any single number is sufficient — it presents three families (time-average, peak, penalty) because different consumers care about different functionals of the same `Δ(t)`.

**Why this literature, for this cell.** The field already holds a recent result (LeaseGuard interval comparison), a misread (Gray & Cheriton server-issued term), a critique (Chandra–Toueg repentance, 350 stale verdicts, 92.3% inside the producer's own 20–28 s cycle), a transfer (MacKenzie detection history, `feed.py` corpus) and a mechanism (ϕ accrual, `observations.py`). All five decide freshness by a threshold at the instant of a single check. None measures the *process* `Δ(t)` over an interval. AoI is the area's standard way to turn a threshold into a measured process with a curve.

## What this plant already does

*The plant has a sawtooth `Δ(t)` and judges it at one point.* Each pane publishes `-- pane live <utc> · refresh 5s · ticks every frame` (`src/mishe_tauftauf/cli.py:375`) every ~5 s; four readers sample it once and compare to a literal:

| reader | file | predicate | limit |
| --- | --- | --- | --- |
| `check_pane` | `src/mishe_tauftauf/tmux.py:371` | clock-free: capture twice 11 s apart, require string change | none |
| `wall.tick` | `src/mishe_tauftauf/wall.py:1004` | `not 0 <= age <= 30` against point clock | 30 s |
| `_leases` | `coordination/site_sync.py:282` | `not 0 <= age <= 20` against point clock (remote stamp) | 20 s |
| `pane_lease_lines` | `src/mishe_tauftauf/wall_view.py:42` | `max(120.0, 6×refresh)` against point clock | 120 s |

`src/mishe_tauftauf/scan_freshness.py` does better for scans — `age_bounds()` returns a `(min,max)` ns interval and `classify_age()` answers `recent`/`stale`/`unknown` where `unknown` means bounds straddle the threshold (`scan_freshness.py:124-148`) — but it is still a point decision at the consumer's `now`. Its header is explicit: "UTC is diagnostic only and never establishes recency" (`:4`). `checks/coverage-window-expiry.py:128` `_fresh(path,max_age,now)` is the same shape for the published coverage and ledger readings: present, readable and young enough *at* `now`.

*What none of them publishes.* No pane, no scan, and no check publishes `⟨Δ⟩` over its window, `Δ^{(p)}`, the peak distribution, or the area decomposition `(Y_n,T_n,Q_n)` that Eq. 2–3 names. The tape holds the inputs (footer stamps, scan `created`, heartbeat `logs/pain-watch-*.heartbeat`) and the critiques measured them once — `witness` 4.6–48.3 s between footers, ordinary roles 3–15 s, linked-site ages 36.7 s/37.0 s/73.0 s (`freshness--misread--2026-10-22`, `freshness--critique--2026-10-22`) — but the per-role mean, peak, p95, or the `Δ vs utilization` curve that §III uses to show "sending faster makes age worse" does not exist in the tree. The `ϕ` review (`freshness--mechanism--2026-10-29`) proposes a likelihood scale; AoI proposes the *observable* scale ϕ would need to be validated against — one cannot tell whether `Φ=8` is conservative without knowing what `⟨Δ⟩` and the peak tail actually are for that role.

## What is new here

**The plant's freshness question is already an AoI question, but none of its instruments answers it as one.** `Δ(t)=t−u(t)` with `u(t)` the last footer (or scan `created`, or report `generated_at`) is exactly what `pane_lease_age` (`observations.py:363`) computes at one `t`; what it does not compute is `⟨Δ⟩_T`, `Δ^{(p)}`, or the pair `(Y_n,T_n)` that makes them comparable across roles and across changes. Three consequences follow; each is a direct quote with a checkable line number.

1. **A threshold breach rate does not distinguish a healthy-rare breach from an old-everywhere system.** §I's takeaway (p. 3) — "timely updating is not the same as maximizing utilization … nor the same as ensuring updates are received with minimum delay" — is the same defect the critique measured: `witness` breaches `20 s` most of the time, `permissions`/`discover` a third, `genome`/`health` never, all at the same `Δ(t)` limit, because their `Y` laws differ (probe timeout 10 s + 2 s retry + 5 s sleep = 27 s declared, 45 s+ observed for `witness`). Time-average `Δ` separates them: a role with `E[Y]=6 s, E[T]=2 s` and a role with `E[Y]=30 s, E[T]=20 s` can have the same breach count at `20 s` and `⟨Δ⟩` of ~7 s vs ~35 s (Eq. 3). The breach count is a sample of `1{Δ(t)>limit}`; `⟨Δ⟩` is the integral of `Δ(t)`.

2. **Peak age is the cheap, robust first publication.** §II-B's `Δ^{(p)}=E[A]` needs only successive stamps — `A_n = T_{n-1}+D_n` — not a continuous `Δ(t)`. For pane leases `T_n` is the probe cost (near zero when delivery is the stamp itself) so `A_n ≈ D_n`, the inter-footer peak that the heartbeat files already log. A trace of `D_n` gives mean peak, p95 peak and the longest gap (the failures that matter for `wall.tick` and `_leases`), without storing the whole sawtooth. The survey presents it as the companion that is coarser but loss-tolerant — exactly the right trade for a log-scraped instrument.

3. **The curve, not the number, is the performance profile — and the plant has never drawn it.** §III (p. 10) shows `⟨Δ⟩` vs arrival rate for `M/M/1` is U-shaped: too slow and `Δ` grows because `Y` is large, too fast and `Δ` grows because `T` is large (queueing). A single operating point's breach boolean cannot tell which side the system is on. The plant's configurable knob is the refresh `5 s` and the probe `timeout`/`retries`; measuring `⟨Δ⟩(refresh)` or `⟨Δ⟩(scan_period)` is the experiment that says whether a faster cadence is helping or hurting — the same experiment the `ϕ` window `WS=1000` (~83 min at 5 s/frame) would need to tune `µ,σ`.

**Limits the proposal keeps from the source.** `⟨Δ⟩` assumes stationarity and ergodicity (p. 7); the plant's arrivals are scheduler- and probe-shaped and bimodal (`witness` vs ordinary) and the 47 global OOM kills of 2026-10-23 show loss bursts of ~2 min that break stationarity. The decomposition `Q_n=Y_n T_n+Y_n^2/2` assumes FCFS (§III) — tmux capture is not a queue. The review does not propose changing scheduling or queue discipline to minimize `⟨Δ⟩`; the mechanism cell already proposed `ϕ` and this one only measures the process `ϕ` would be judged by. Non-linear penalties `p(Δ)` (§II-C) and estimation costs (§VI) are left as follow-on once the consumer of the age (health vs genome) names its cost.

## Application

target: src/mishe_tauftauf/scan_freshness.py

**Publish the plant's own sawtooth as a measured metric, beside the threshold verdict — once for pane leases, once for scans.**

The file already owns the two things an AoI instrument needs — a compatible `BOOTTIME` domain (`endpoint()` at `:66`, `_compatible` at `:62`) and inclusive age bounds (`age_bounds()` at `:107`, `classify_age()` at `:124`) — but it uses them only at one `now`. The change is additive, lives in the module the metric angle's sweep already names (`fresh`), and reuses the vocabulary the plant's doctrine requires (`UNKNOWN` when bounds straddle, `docs/how-it-works.md:576`).

1. **Add a windowed AoI summary beside `classify_age`, not instead of it.** In `src/mishe_tauftauf/scan_freshness.py`, beside `classify_age(bounds,max_age)` (`:124`), add `aoi_summary(stamps: list[datetime], *, now: datetime) -> dict` where `stamps` is the ordered window of prior publish instants (pane footers, scan `created`, or report `generated_at`; `WS=100` stamps ≈ 8 min at 5 s/frame is enough for pane peaks, `WS=1000` per the ϕ review for a full mean; the caller chooses). Compute the monitor's `Δ(t)=now−stamps[-1]` (current age), the peaks `A_n = stamps[i]−stamps[i-1]` (with `T_n` folded into `D_n` for stamp-level AoI, §II-B), and from them `mean_age = E[Q]/E[Y]` via Eq. 2 polygon area (or the reported peak mean `E[A]` when `T` is not separately logged), `p95` peak, `max` peak, `n` and `window_seconds`. Return `None`/omit fields when `len(stamps)<2` or endpoints incompatible (`_compatible` at `:62` fails) — the paper excludes warmup, the plant must publish `unknown` then, same as `classify_age`'s `unknown`.

2. **Let each consumer keep its threshold and also read the curve.** Today `checks/coverage-window-expiry.py:128` `_fresh(path,max_age,now)` and `coordination/site_sync.py:282` `not 0 <= age <= 20` return a boolean/throw. After the change the same call sites also log/publish the summary that produced the threshold check — e.g. the check's `UNKNOWN/OK` line carries `age=Ns mean=Ms p95=Ps max=Qs n=K` alongside `classify_age`'s `recent/stale/unknown`, and `wall.tick`'s `RENDERER: RED` carries it beside `pane lease stale`. The threshold stays the enforcement; the AoI summary is the *measurement* that shows whether `30 s`/`20 s`/`120 s` is below or above `⟨Δ⟩` for that role.

3. **Use the plant's existing third state instead of inventing one.** `classify_age` already answers `recent`/`stale`/`unknown` where `unknown` means "bounds straddle threshold" (`:124-148`); `aoi_summary` returns `unknown` (or an `aoi=None`) when the window is warming, endpoints incompatible, or fewer than 2 stamps — the `Undecidable` the checks already catch (`coverage-window-expiry.py:98`). No new verdict word is needed.

4. **Wire the producer window where the stamps already exist.** For pane leases, the window is the per-role footer history that `ϕ` (prior review, `observations.py`) already proposes to persist beside `site_sync.py:431`'s `error` row — reuse that persistence. For scans, the window is the `discovery/scan-*.json` `created` series that `discovery.py:8098` already reads via `age_bounds`; add the summary to the scan receipt beside `endpoint` so a second reader can recompute it from published fields alone (the `ledger-band-second-reader.py` idiom).

**Acceptance (deterministic, runnable from this repository).**

* Existing `tests/test_scan_freshness.py` and `checks/coverage-window-expiry.py` keep passing — `age_bounds` and `classify_age` are unchanged; `aoi_summary` is additive and returns `unknown` on warmup, so no new `UNKNOWN` is introduced where `recent`/`stale` was decided.
* Replay the linked-site pane histories that falsified the derived constant (`genome` 36.7 s, `health` 37.0 s, `witness` 73.0 s, `freshness--critique:138`) through `aoi_summary` with a `WS=100` window of their own prior footers — they must report `current age` ≈ those numbers, `mean peak` ≈ the role's measured inter-footer mean (6–9 s for healthy roles, ≈30 s for `permissions`/`discover`, >45 s for `witness`), and `max peak` ≥ the recorded age. A healthy-but-slow role at `age=27 s` must show `current=27 s` yet `mean≈7 s` for a fast role vs `mean≈32 s` for `witness` — two systems with the same breach boolean and different `⟨Δ⟩`.
* `python tools/check_parsing.py` stays green — `scan_freshness.py` is not a boundary file today (`parsing-policy.json` lists `feed.py`, `records.py` and related), so the new arithmetic adds no parsing boundary.
* A follow-on `ϕ` validation can then be stated as: for each role, plot `Φ` vs measured `⟨Δ⟩` and mistake rate from the same window; `Φ=1` (≈10% per Hayashibara p. 9) should admit mean peaks, `Φ=8` should admit only `>5 min` absences — the curve the survey's §III and Hayashibara's Fig. 8 both present.

**Ownership.** `src/mishe_tauftauf/scan_freshness.py` is the plant's `BOOTTIME`-domain freshness discipline and is consumed by `senses` (scan freshness, compatible-window fusion) and by `discovery`/`checks`; `senses` is the resolver for the sensorium and for freshness routing and is the role addressed by the sensor `model-change: watcher or independent freshness guard failed`. The pane-lease threshold choices (`wall.py:1004` at `30 s`, `site_sync.py:282` at `20 s`) remain their owners' (health/genome); the *value* `Δ(t)` and its summary are senses'.

**One thing this review does not ask for.** It does not propose minimizing `⟨Δ⟩` by changing refresh rate, probe timeout, or queue discipline (§III's U-curve) and does not propose a non-linear penalty `p(Δ)` or an estimator cost (§II-C/§VI). Those are optimization once the measurement exists; this review only publishes the measurement the optimization would need, in the module that already defines what "fresh" means.
