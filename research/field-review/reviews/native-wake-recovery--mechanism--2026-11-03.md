---
field: native-wake-recovery
angle: mechanism
date: 2026-11-03
status: applied
owner: health
target: src/mishe_tauftauf/native_gate.py
---

# native-wake-recovery × mechanism — REVISIT: φ accrual failure detector (Hayashibara et al. 2004) → graded suspicion for busy/wedge classification

## What we read

- Naohiro Hayashibara, Xavier Défago, Rami Yared, Takuya Katayama, **"The φ Accrual Failure Detector"**, JAIST Research Report IS-RR-2004-010 (May 10, 2004) / SRDS 2004 pp.66–78 (IEEE doi:10.1109/RELDIS.2004.1353004). Primary PDF fetched 2026-11-03 to `/tmp/phi-jaist.pdf` (317 KiB, 316 kB on wire, sha256 `5aba342899a82d0ebf1c19b538caf4878d09f8d77fabee20355f9ff5e0bce5d1`) via https://dspace.jaist.ac.jp/dspace/bitstream/10119/4784/1/IS-RR-2004-010.pdf (hdl:10119/4784, also IEEE Xplore 1353004) and extracted to `/tmp/phi-jaist.txt` (876 lines) via `pdftotext`. Read the primary paper itself (§§I–IV definition and §V measurement), not a summary. The 2026-11-01 attempt for this same cell correctly discarded after a 301 to `coord.c.titech.ac.jp` and dead mirrors; the JAIST dspace copy reached this wake and is retained above.

  Passages relied on (verbatim from `/tmp/phi-jaist.txt`):

  1. Abstraction (Abstract + p.1): "We present a novel abstraction, called accrual failure detectors, that emphasizes flexibility and expressiveness and can serve as a basic building block to implementing failure detectors in distributed systems. Instead of providing information of a boolean nature (trust vs. suspect), accrual failure detectors output a suspicion level on a continuous scale. The principal merit of this approach is that it favors a nearly complete decoupling between application requirements and the monitoring of the environment."

  2. Motivation — tradeoff and interference (p.1–2): "It is well-known that there exists an inherent tradeoff between (1) conservative failure detection (i.e., reducing the risk of wrongly suspecting a running process), and (2) aggressive failure detection (i.e., quickly detecting the occurrence of a real crash). There exists a continuum of valid choices between these two extremes" and "One of the major obstacles to building a failure detection service is that simultaneously running distributed applications with different quality-of-service requirements must be able to tune the service to meet their own needs without interfering with each other."

  3. Illustrative graded use (p.2): master/worker with three thresholds — "When the confidence level reaches some low threshold, the master simply flags the worker process pw and temporarily stops sending new jobs to pw. Then, when reaching a moderate threshold, the master cancels all unfinished computations that were running on pw and resubmit them to some other worker processes. Finally, when reaching a high threshold, the confidence that pw has crashed is high, so the master removes pw from its list of available workers."

  4. Definition of accrual (§III.B): `susp level p (t) ≥ 0` (1) with Properties 3–6: "Property 3 (Asymptotic completeness): If process p is faulty, the suspicion level susp level p (t) tends to infinity" + "Property 4 (Eventual monotony): ... monotonic increasing" + "Property 5 (Upper bound): Process p is correct iff susp level p (t) has an upper bound" + "Property 6 (Reset): If process p is correct, then for any time t0, susp level p (t)=0 for some time t≥t0."

  5. Decoupling (Fig.2 vs Fig.3 + p.3): traditional — "monitoring and interpretation parts are combined within the failure detector (see Fig.2). The output ... is of boolean nature; trust or suspect. An elapsing timeout is equated to suspecting" vs accrual — "accrual failure detectors provide a lower-level abstraction that avoids the interpretation of monitoring information (see Fig.3). Some value is associated with each process that represents a suspicion level. This value is then left for the applications to interpret."

  6. φ meaning and formula (§IV.A, Eq.2): "Let Tlast, tnow, and Plater(t) denote respectively: the time when the most recent heartbeat was received (Tlast), the current time (tnow), and the probability that a heartbeat will arrive more than t time units after the previous one" then "ϕ(tnow) = −log10(Plater(tnow − Tlast))" (2). Threshold semantics: "Given some threshold Φ, and assuming that we decide to suspect p when ϕ ≥ Φ = 1, then the likeliness that we will make a mistake ... is about 10 %. The likeliness is about 1 % with Φ = 2, 0.1 % with Φ = 3, and so on."

  7. Computation (§IV.B, Eq.3–4): sampling window "of fixed size WS. Whenever a new heartbeat arrives, its arrival time is stored into the window, and the data regarding the oldest heartbeat is deleted" with running sum/sum-of-squares to maintain mean µ and variance σ²; Plater(t) under normal assumption: "`Plater(t) = 1/σ√2π ∫_t^∞ e^{-(x−µ)²/2σ²} dx = 1 − F(t)` where F(t) is the cumulative distribution function of a normal distribution with mean µ and variance σ²." Application uses "The time of last arrival Tlast, the current time tnow and the estimated distribution are used to compute the current value of ϕ. Applications trigger suspicions based on some threshold (Φ1 for App.1 and Φ2 for App.2), or execute actions as a function of ϕ (App.3)" (Fig.4 caption).

  Not read in full: §V intercontinental 1-week / 5.8 M samples parameter sweeps and Chen/Bertier comparison beyond the quoted awareness that "enhanced flexibility ... does not induce any significant overhead," and §VI related-work beyond the Chen/Bertier/Jacobson sketches; comment thread not present. This review treats only the abstraction and φ implementation (§I–IV), not the empirical optimum of WS=1000 vs Φ.

- Limit: Paper is for heartbeat UDP liveness on a link, assumes roughly periodic arrivals and normal inter-arrival approximation; it does not address wake-store ledger evidence (settled/dispatched/traceback) and its normal assumption is explicitly an approximation that can underfit heavy-tailed losses (Fig.5 burst tail, Fig.6 log-scale inter-arrivals). Threshold->mistake mapping (10^-Φ) is asymptotic under that approximation, not a calibration for this plant.

## What this plant already does

- `src/mishe_tauftauf/native_gate.py:22-29` + `:55 LIVE_WINDOW_SECONDS=1800.0` + `:167 _marker_age` + `:241 busy_age` implement a **binary, fixed-window** liveness call: a `*.busy` marker is "mid-turn only while it is fresh" (docstring :22); older than 1800 s is wedged; no graded suspicion, no distribution, no threshold dial; monitoring (age) and interpretation (fresh vs wedged) are combined in the same `detect()` verdict.
- `src/mishe_tauftauf/native_recovery.py:374 classify_attempt` classifies one *failed* wake from its own bytes under `._store_guard/.ledger.lock` with `LiveWriter` exclusion and `later != files` double-read (verdicts `SETTLED`/`RETRYABLE='retryable-no-effect'`/`RECONCILE`/`UNKNOWN` at :92–95). This is evidence reading (journal + three `LEDGER_JOURNALS` + `failure.txt` traceback header), not a suspicion level over time; its decision is also boolean per attempt, gated by `TRANSIENT_STATUS`/`DEFAULT_RETRY_LIMIT=3`.
- Together the two modules realize a **two-stage binary** gate: gate decides live vs wedged (time), recovery decides retryable vs reconcile (bytes). Neither outputs a continuous value that two consumers could interpret at different thresholds without retuning the monitor — the exact interference problem the paper names: "must be able to tune ... without interfering with each other" (quoted above). Prior work in this lane already proposed complementary oracles/vocabs: `recovery_policy.py` MTTR metric (2002), OTP MaxR/MaxT window discard (bounded but unobserved `-retry-` storm), Wasabi IF/WHEN taxonomy + cap/delay oracles (`native_recovery.py` `RETRY_BUG_TAXONOMY`), Brooker jitter for WHEN-delay spread, Saltzer end-to-end placement, Yuan handler-quality — none introduced a graded, decoupled suspicion scale.
- Occurrence sweep for this REVISIT cell (`failed wake|fresh wake|classification`) lists only `native_recovery.py`/`native_gate.py`/prior reviews; zero hits before this for `accrual|φ accrual|phi accrual|suspicion level|Plater|Hayashibara|Defago|Φ threshold|susp level` as vocabulary — the mechanism, not just the name, is absent.

## What is new here

1. **From boolean to accrual.** Classical failure detectors (including this plant's `LIVE_WINDOW`) output trust/suspect. Accrual outputs `ϕ(t) ≥ 0` that is guaranteed to accrue to infinity if the process is faulty (Properties 3–4) and is unbounded iff correct (5) and resets infinitely often when correct (6). Suspecting is then `ϕ ≥ Φ` for an application-chosen Φ, with the transformation `Thigh/Tlow` to implement ♦P — decoupling monitoring (heartbeat sampling → µ/σ → Plater → ϕ) from interpretation (threshold).

2. **Scale that fits the network, not the application.** `ϕ = −log10(Plater(tnow−Tlast))` normalizes time by recent conditions: same Φ ≈ 10^-Φ mistake probability across links because Plater is computed from the last WS=1000 inter-arrivals (mean+ variance). This is the paper's key improvement over Chen (`+ α`) and Bertier (Chen+Jacobson) — all adapt the timeout, but only φ exposes the suspicion as a calibrated scale so multiple applications can share one sampler at different Φ without re-estimating α per app (Fig.4: App1 Φ1, App2 Φ2, App3 function of ϕ). For this plant the analogue is: one busy-age sampler, many thresholds — e.g., flag at Φ_low, cancel at Φ_mid, evict at Φ_high — vs today one 1800 s cutoff for everyone.

3. **Cheap implementation.** Three phases: (i) sliding window of WS arrivals (sequence-numbered heartbeats, running sum/sumsq), (ii) µ/σ estimate → normal CDF → Plater(t)=1−F(t), (iii) ϕ. The paper's week-long JAIST↔EPFL link (≈10 Hz, 5.8 M samples, 0.4% loss, RTT 283 ms) shows mistake rate improves sharply Φ 0.5→2 and again 8→12 (loss-burst of 1), detection time rises slowly to Φ≈10 then steeply; WS 20→10000 steadily reduces mistakes at any Φ, with diminishing returns beyond ~1000. No new message type beyond heartbeats; per-heartbeat cost is O(1) update of µ/σ plus one CDF (erfc) evaluation.

4. **Why this is not a replacement for ledger evidence.** Saltzer's misread (2026-11-03, same target) correctly placed correctness end-to-end: no detector substitutes for reading `settled`/`dispatched`/`traceback`. φ is a *liveness* hint, not an effect proof; it belongs on the gate's busy-age path, not inside `classify_attempt`'s bytes path. Used there, it keeps `classify_attempt`'s fail-closed rule (zero `dispatched`+`proposed` → RETRYABLE, else RECONCILE/UNKNOWN) intact while giving the gate a calibrated Φ to avoid the paper's "short timeout quick detection but many mistakes vs long timeout accurate but slow" tradeoff per consumer. Brooker's jitter (transfer 2026-11-03) solves *when within a window retries spread*; φ solves *when to call it dead* at graded confidence.

5. **Limits of transfer.** Normal inter-arrival assumption is coarse for this plant's highly variable wake durations (1800 s window vs 100 ms heartbeats, bursty losses up to 1093 consecutive in paper Fig.5) — a heavy-tailed or mixture model would fit better; Wasabi/Yuan/Brooker gaps remain orthogonal; treating Φ's 10^-Φ as calibrated for wake ages without fitting µ/σ on this plant would miscalibrate mistakes; the paper's 1000-sample warmup is excluded — early wakes would have no estimate.

## Application

target: src/mishe_tauftauf/native_gate.py

Proposal to **health** (owner of `native_gate.py`/`native_recovery.py` liveness/recovery boundary; read-only lane — this review does not edit the organ; additive vocabulary, pure and revertible by single-commit revert, no new runtime until health opts in):

1. Beside `LIVE_WINDOW_SECONDS = 1800.0` (`native_gate.py:55`) add a **Hayashibara et al. φ-accrual note** citing `Hayashibara et al., "The φ Accrual Failure Detector", JAIST IS-RR-2004-010 / SRDS 2004, https://dspace.jaist.ac.jp/dspace/bitstream/10119/4784/1/IS-RR-2004-010.pdf (§§III–IV, Eq.2 ϕ=−log10 Plater(tnow−Tlast), Plater=1−F(t) normal µ/σ via WS window, threshold Φ≈−log10 mistake rate) — accrual decoupling (Fig.3): monitor (sampling→µ/σ→Plater→ϕ) vs interpretation (app threshold Φ); ♦P via Thigh/Tlow.`

2. Add pure, advisory constants co-located with that comment (names illustrative, `Final`, owner chooses identifiers):

   ```python
   PHI_ACCRUAL_WS: Final[int] = 1000
   """Hayashibara et al. §IV.B — sliding window of recent inter-arrivals for µ/σ estimate; paper's warmup-excluded default."""
   PHI_THRESHOLDS: Final[tuple[float, ...]] = (1.0, 2.0, 8.0)
   """Illustrative Φ values from Fig.8: mistake rate drops at 0.5→2 and 8→12 for the intercontinental link; 10^-Φ mistake semantics."""
   ```

3. Add two pure helpers in the same module (no I/O, no marker read), advisory only, `UNACTIVATED: no caller in running pin`:

   ```python
   import math

   def phi_from_plater(plater: float) -> float:
       """Hayashibara Eq.2 — ϕ = −log10(Plater); Plater in (0,1]; inf when 0."""
       if plater <= 0:
           return float("inf")
       if plater >= 1:
           return 0.0
       return -math.log10(plater)

   def phi_threshold_suspicion(*, phi: float, threshold: float) -> bool:
       """Accrual interpretation — suspect iff ϕ ≥ Φ; Φ≈−log10 mistake rate."""
       return phi >= threshold
   ```

   Optionally a third helper estimating Plater via normal CDF `Plater(t)=0.5*erfc((t−µ)/σ/√2)` when σ>0 — same purity contract. Existing `detect()`/`_marker_age`/`classify_attempt` behavior unchanged; `tools/check_parsing.py` unaffected. Mark `UNACTIVATED` until health wires `phi_from_plater` to a future gate census (busy-age distribution over recent wakes vs fixed 1800 s) or to a test repurposing `tests/test_native_gate.py` with injected ages. Revert is single-commit revert; no migration debt because no caller.

Prediction: after landing, `PYTHONPATH=src python -c "from mishe_tauftauf.native_gate import phi_from_plater; print(phi_from_plater(0.1)==1.0)"` prints `True` (Φ=1→10% as paper), `rg -n "PHI_ACCRUAL|phi_from_plater|Hayashibara.*phi" src/mishe_tauftauf/native_gate.py` shows the header note, `python3 research/field-review/field_review.py check` remains PASS (332 landed before this file, 333 after). Observation: run those checks plus `python -m py_compile src/mishe_tauftauf/native_gate.py` on the landed commit. Decision: keep if docs-only and checks pass; revise WS/Φ if health prefers different window or calibrated thresholds from this plant's own inter-arrival burst data; revert single commit if vocabulary unhelpful.

## Limits

- Paper's evaluation link (UDP 10 Hz, ~283 ms RTT, 0.4% loss) is not this plant's wake granularity (minutes–hours, tmux lease, chat trigger); WS=1000 heartbeats ≈100 s there is not 1000 wakes here — window must be re-expressed as recent busy intervals, not raw time, and warmup exclusion matters at startup.
- Normal `Plater=1−F(t)` is an approximation; paper's own Fig.5–6 show non-normal tails (burst loss up to 1093, log-scale inter-arrivals). Using it without validating against this plant's actual arrival distribution overstates calibration of 10^-Φ.
- Accrual complements, does not replace, Saltzer end-to-end (correctness via ledger) and Wasabi IF/WHEN or Brooker jitter — it only refines *when* a live-but-silent holder is called dead, not whether a failed attempt is retryable.
- Threshold choice remains application policy: low Φ fast but false suspicions, high Φ accurate but slow detection (Fig.8–9); the paper proves ♦P transformation correct but not optimal for any Φ, and this plant's cost of a false suspicion (fail-closed UNKNOWN/RECONCILE vs premature wedge eviction) differs from the paper's link-failure cost.

