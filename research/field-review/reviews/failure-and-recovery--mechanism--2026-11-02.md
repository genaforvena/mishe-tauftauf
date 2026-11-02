---
field: failure-and-recovery
angle: mechanism
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/recovery_io.py
---

## What we read

**Naohiro Hayashibara, Xavier Défago, Rami Yared, Takuya Katayama, *The φ Accrual Failure Detector*, JAIST Research Report IS-RR-2004-010, 10 May 2004, 15 pp.** — read full PDF at:

- <https://dspace.jaist.ac.jp/dspace/bitstream/10119/4784/1/IS-RR-2004-010.pdf> (317 KB, 17 pp. A4 via `pdfinfo` + `pdftotext`, fetched 2026-11-02; `pdftotext` 876 lines, verified `Title: The φ accrual failure detector` header)
- Canonical conference version: *Proc. 23rd IEEE SRDS 2004*, pp. 66–78, DOI <https://doi.org/10.1109/RELDIS.2004.1353004> (same text, IEEE Computer Society; we read the JAIST report which is the full technical report including §V wide-area evaluation Japan↔Switzerland over 6M UDP heartbeats at ~10 Hz for one week).

Four passages carry the operational mechanism this plant does not implement.

1. **Accrual vs boolean decoupling (Abstract + §III, Figs. 2–3, Properties 3–6, p. 2–7).** Conventional detectors combine monitoring and interpretation into one `trust/suspect` boolean per peer (Fig. 2). Accrual detectors split them (Fig. 3): the monitor outputs a continuous suspicion level `susp_level_p(t) ≥ 0` (Eq. 1) satisfying *asymptotic completeness* (if p is faulty, level → ∞), *eventual monotony*, *upper bound iff p correct*, and *reset* (infinitely often 0 if correct). Applications then choose their own threshold; a low threshold risks many wrong suspicions but detects fast, a high threshold is conservative. The master/worker example (p. 4) shows one monitor serving three reactions at three thresholds (flag → cancel → release). The transformation to ♦P (§III-C, Fetzer-style hysteresis with `T_low`/`T_high` increments) proves the abstraction still implements the needed completeness/accuracy when a boolean is required — it does not contradict FLP impossibility; accrual is subject to the same asynchronous limits and is a probabilistic construction.

2. **φ meaning and scale (Eq. 2, §IV-A, p. 7–8).** `φ(t_now) = −log10(P_later(t_now − T_last))` where `T_last` is the last heartbeat arrival, `t_now` current time, and `P_later(t)` = probability a heartbeat arrives more than `t` after the previous one. Scale is calibrated in decades: threshold `Φ=1` ≈ 10% mistake probability if we suspect now, `Φ=2` ≈ 1%, `Φ=3` ≈ 0.1%, and so on — the same live `φ` can be read by different consumers at different `Φ` without re-tuning the monitor.

3. **Computing φ from a sliding window under a normal model (Eq. 3–4, §IV-B, Fig. 4, p. 8).** Arrivals are stored in a window of size `W_S`; `μ` and `σ²` are maintained incrementally via sum and sum-of-squares. `P_later(t)` is estimated as the normal tail `1 − F(t)` with `F` the CDF `N(μ,σ²)` (Eq. 3: integral from `t` to ∞, Eq. 4). The pipeline is entirely local: heartbeat → sampling window → distribution estimate → `φ` → per-app threshold comparison. The window discards the oldest on each arrival. Evaluation (§V) runs on a Japan–Switzerland UDP link, `Δ_i ≈ 100 ms` (10 Hz), comparing φ against Chen-FD [10] and Bertier-FD [9] on the same 6M-sample trace — φ performs equally with the added flexibility.

4. **What the mechanism refuses (throughout, esp. §II-D note on `Δ_i` and §IV limits).** The `Δ_i` (heartbeat interval) is not an accuracy knob in practice: if `Δ_i ≪ Δ_tr` it inflates load and `Δ_tr`; if `Δ_i ≫ Δ_tr` it dominates detection time; any reasonable `Δ_i` tracks `Δ_tr` and is given by the system, not QoS. The normal model is an explicitly simplified fit (footnote 3: "formula is simplified assuming crashes are rare"); the paper does not claim it captures heavy tails, correlated loss, or OS-scheduling jitter beyond the measured link. φ is not a replacement for the impossibility result — deterministic ♦P in asynchrony remains impossible.

**Limits the source states.** Normal assumption can mis-estimate bursty or multimodal delay; small `W_S` makes `σ` noisy, large `W_S` lags adaptation — the report evaluates with `W_S` in the hundreds for 10 Hz. `φ` accrual is monotonic only after a crash; during healthy operation it resets to 0 infinitely often but can spike on transient loss. The paper's QoS claim is comparative on one intercontinental trace, not a proof for lock files or controller jitter.

## What this plant already does

*Boolean, coupled interpretation at every seam — exactly the Fig. 2 structure the paper separates.*

- `src/mishe_tauftauf/recovery_io.py:88-89` validates `timeout` as `0 < p["timeout"] ≤ 30` and `run_probe(argv, *, timeout=5)` (`:93`) returns a boolean `ok` (`True/False/None`) per probe into `checks[name]["ok"]` (`:427,442`). `check():` aggregates those booleans into `functions[name] = policy.transition(old, result=checks[name]["ok"], deadline, stable_seconds, max_gap=interval*3)` (`:454`), where `recovery_policy.py` maps the single current sample to one of `{HEALTHY, FAILED, MITIGATED, RECOVERING, RESTORED, UNKNOWN}` with fixed `deadline/stable_seconds/max_gap`. The guard freshness is a hard window: `guard_ok = -lead ≤ now − guard_row["checked_at"] ≤ max(45, interval*3)` (`:434`). There is no continuous suspicion: a probe is either inside its deadline or it is `FAILED`; a guard is either fresh or `None`.

- `src/mishe_tauftauf/substrate_watch.py` replicates the same pattern at the substrate layer: a single `now` sample drives `status ∈ {KNOWN, UNKNOWN}` (`:670`) with coalescing windows `CHANGE_NOTICE_SECONDS = 900` (`:30`) and `LIVENESS_DEBOUNCE_SECONDS = 300` (`:34,696-714`). The detector's *scale* never changes with recent conditions — it is `interval` and the two global constants.

- Consequence the mechanism predicts: two consumers that need different QoS (e.g., a dashboard that should flag quickly vs a watchdog that should act only when confident) cannot share the same `interval*3` interpretation without interfering. Today they must either run separate probes or accept the same `ok → FAILED` threshold. A transient controller shed (`_controller_shed`) that adds 200 ms to probe latency for a few cycles looks identical to a real stall at the boolean seam; the only tuning is the global `deadline`.

- Best nearby practice: `recovery_io.py:392-426` already samples `pressure()`, `host_cgroup`, and per-probe `seconds` into `samples.jsonl` (`:485-486`), so a window of arrival times exists in principle — but it is appended and never used to estimate `μ,σ²` or to emit a continuous level.

## What is new here

**Keep the monitor's output continuous and dynamically scaled; let interpretation choose thresholds — then the same `φ` serves fast-flag and safe-act consumers without a second probe.**

1. **The accrual split itself.** Replace the single boolean interpretation inside `policy.transition` with a two-layer pipeline: sampling window → `φ(t_now)` (monitor) → per-consumer `Φ` thresholds (interpretation). One monitor can feed the dashboard at `Φ=1` (flag, 10% mistake budget) and the recovery guard at `Φ=4` (act, 0.01%) simultaneously, exactly the paper's master example — no extra UDP heartbeats, no second `interval`.

2. **φ with dynamic scale from recent history.** `μ,σ²` track the last `W_S` inter-arrivals of the *same* signal the plant already watches (`guard.json:checked_at`, `render-action.json:at`, or `recovery/state.json:checked_at`) rather than a new heartbeat protocol. A slow controller (`LIMITED`, `shed` set) that shifts `μ` by +150 ms automatically raises the scale on which "late" is judged; a later quiet period lowers it — the detector adapts where today's `max(45, interval*3)` is fixed. `φ = −log10(1 − F(t_now − T_last))` keeps the decade meaning, so operators reason in constant mistake budgets, not seconds.

3. **What this would replace, and what it would not.** It does not replace the boolean actions: `health`/`genome`/`witness` still receive booleans via a `Φ` → `suspect` hysteresis (the §III-C `T_high/T_low` transform) — accrual is the level below, not a new verdict kind. It does not fix the normal-model limit: OS scheduling spikes and cgroup throttle create heavier tails than `N(μ,σ²)`; a practical plant detector will need a tail-aware fallback (the prior revisit's exponential backoff/jitter for retries and the 2026-10-29 metric review's empirical `P_later` histogram are the complementary guards). Window sizing matters: with `interval≈30 s` the sampling rate is ~0.03 Hz, so `W_S=100` spans ~50 minutes — adaptation will be slower than the 10 Hz UDP evaluation and must be tuned empirically.

## Application

target: src/mishe_tauftauf/recovery_io.py

**Add a pure φ-accrual primitive beside `run_probe`/`check` and route one of its thresholds through the existing `policy.transition` seam — additive, behind a flag/env, no new branch or probe.**

1. **Add `phi_accrual.py`-style pure functions in `src/mishe_tauftauf/recovery_io.py` beside `run_probe()` (`:93`) and `validate_config()` (`:70`), mirroring `recovery_policy.capacity()` naming.** `phi_window(size=W_S, samples=None)` maintains `sum/sum_sq/μ/σ²` incrementally; `phi_later(t, μ, σ) -> float` implements Eq. 3–4 via `math.erfc` (normal tail, no new dependency): `P_later = 0.5*erfc((t-μ)/(σ*sqrt(2)))` clamped to `[1e-12, 1.0]`; `phi_value(t_now, t_last, window) -> float` implements Eq. 2 as `-log10(P_later)`. All three are pure, no I/O, tested without clocks.

2. **Feed the window from signals the plant already records.** On each `check()` call, update a `phi_window` keyed by `"independent-guard"` using `guard_row["checked_at"]` deltas (or `render-action.json:at` for the render seam) when `guard_ok` sampling succeeds; on missing/stale input, advance `t_now` without inserting a sample so `φ` accrues as the paper prescribes. Default `W_S = 100` (configurable via `config["phi_window"]`, default 100) and `σ` floor `max(0.1, 0.05*μ)` to avoid log blowup when the window is still filling — window filling (<10 samples) returns `φ=0` (reset property).

3. **Interpret via per-consumer thresholds, hysteresis, and the existing transition.** Expose `phi_thresholds = {"dashboard": 1.0, "guard": 4.0}` (decades, env `MISHE_PHI_THRESHOLDS`). The `φ` for `"independent-guard"` maps to a derived boolean `phi_ok = (φ < Φ_guard)` via the §III-C hysteresis (`T_high=Φ_guard, T_low=Φ_guard−1`) to avoid flapping; `phi_ok` is then one of the `checks["independent-guard"]["phi_ok"]` inputs while the original `guard_ok` remains the monitor's `UNKNOWN`/`HEALTHY` source — when `MISHE_PHI_ACCRUAL=0` (default) `phi_ok` is not consulted, so the existing `guard_ok` path is byte-identical. When enabled, `policy.transition` for `"independent-guard"` consults `phi_ok` alongside `guard_ok` with `phi_ok` taking precedence on `UNKNOWN→FAILED` timing: the deadline interpretation becomes `Φ`-driven rather than `interval*3`-driven.

4. **Wire into `samples.jsonl` and `checks` for observability, not for a new verdict.** `check()` emits `checks["independent-guard"]["phi"] = phi_value(...)` and `checks["independent-guard"]["phi_window"] = {μ, σ, W_S, n}` plus `phi_thresholds` so the snapshot carries the continuous level beside the boolean `ok`; `samples.jsonl` appends `{phi, mu, sigma}` alongside `control_ok/limited`. No new state file, no new DM shape — the existing `functions["independent-guard"]` episode still drives delivery.

**Acceptance (deterministic, runnable from this repository):**

- `python tools/check_parsing.py` still exits 0 and `python -m py_compile src/mishe_tauftauf/recovery_io.py` passes — additive only; `grep -rn "phi_" src/` shows only this file.
- With `MISHE_PHI_ACCRUAL=0` (default) a replay of `recovery/state.json` + `guard.json` fixtures through `recovery_io.check(home, config, observation=obs, now=t)` yields byte-identical `checks`/`functions` as before (no new `phi` key consulted); with `MISHE_PHI_ACCRUAL=1` the same inputs expose `checks["independent-guard"]["phi"]` in `[0, 12]` and `phi_window.n ≤ W_S`, and `phi_value` satisfies Eq. 2 within 1e-9 against `0.5*erfc` on 1000 random `(t,μ,σ)` triples.
- On a synthetic trace where inter-arrivals are `N(30, 2)` for 200 samples then pause 90 s, `φ` stays <1.0 during steady state and accrues past `Φ=4` within the pause, resetting to 0 on the next sample — demonstrating Properties 3–6 on plant-typical intervals without new probes.
- Unit test replays the Japan→Switzerland UDP histogram shape at `Δ_i=30 s` downsampled to 100 points: `φ` at `t=μ+3σ` ≈ 3.0 ±0.3 (decade calibration holds under normal fit), while a heavy-tail burst (5× `σ` outlier) shows the documented normal-model underestimate — the test asserts the underestimate and documents the fallback to the existing boolean `guard_ok` when `σ` is floored.

**Ownership.** `src/mishe_tauftauf/recovery_io.py` is the litteral recovery boundary (guard, probes, controller lock) and already owns `interval/deadline/stable_seconds/max_gap` the φ detector replaces the interpretation of. `health` is resolver for `native recovery, health, supervisors, watchdogs` (`fields.json` plant_part) and owns the guard/seeds probes the φ window samples. The check beside it is `checks/native-recovery-optin.py`, which gates the recovery wake the detector would inform.

**One thing this review does not ask for.** It does not add a new heartbeat protocol, new probe, or new verdict kind; it does not change `interval`/`deadline` tuning or queue discipline; and it does not claim the normal tail fits the plant's lock-delay distribution — that claim would require the histogram instrument the prior metric cell asks for.

