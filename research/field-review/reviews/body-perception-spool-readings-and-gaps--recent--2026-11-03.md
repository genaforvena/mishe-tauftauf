---
field: body-perception-spool-readings-and-gaps
angle: recent
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-spool-readings-and-gaps — recent (2026-11-03)

## What we read

- Li, Wu, Lee, Sun, "From Freshness to Effectiveness: Goal-Oriented Sampling for Remote Decision Making," arXiv:2504.19507v3 [cs.IT] (submitted 28 Apr 2025 v1, revised 6 Feb 2026 v3; accepted IEEE Transactions on Wireless Communications / referenced as IEEE Trans. Information Theory vol.72 no.4 pp.2237-2276 Apr 2026 in v3 header). Source: https://arxiv.org/abs/2504.19507 — fetched 2026-11-03 via https://arxiv.org/html/2504.19507v3 (native HTML reader, 801 lines; abstract + §I Introduction + §II Related Work and Novelty including Table I, Fig.1, Fig.2 and eqs.(1)-(2) read in full; §III onward not relied on). PDF also reachable at https://arxiv.org/pdf/2504.19507v3 .
- Verbatims carrying the transfer (line refs to the HTML render):
  - Abstract: "However, freshness alone does not always equate to utility in decision-making. In decision-critical settings, some stale data may be more valuable than fresh updates."
  - §I p.2: AoI variants listed — nonlinear penalties, Age of Synchronization (AoS), Age of Incorrect Information (AoII), Age of Changed Information (AoCI), Age of Collected Information — plus for remote estimation MSEE/UoI `H(X_t|I_t)`.
  - §II-B Table I + p.5: taxonomy Standard MDP `O(t)=X_t` vs DDMDP `O(t)=X_{t-d}` (fixed d) vs SDMDP `O(t)=X_{t-D}` (`D` i.i.d. exogenous) vs AR-MDP `O(t)=X_{t-Δ(t)}` (this work); "the effective delay in AR-MDP is *sampling-dependent* (through S_i) and coupled with random transmission delay, so the time lag is policy-dependent rather than an exogenous fixed/i.i.d. variable."
  - §II-B eq.(1) `Δ(t)=t-S_i, D_i ≤ t < D_{i+1}` and eq.(2) `Δ(t+1)=Δ(t)+1` if no delivery else `Y_i` (random delay); Fig.2 caption: "The i-th sample is generated at S_i and delivered at D_i with random delay Y_i. The control is updated only upon delivery and is held constant until the next delivery."
  - §II-C novelty: "we treat information as a means to action, where its contribution is defined not by how precise it is, but by how well it enables timely and effective decisions." And via sensitivity analysis: "we derive a threshold for the sampling frequency, beyond which additional sampling does not yield further gains in decision-making."
  - §II-A counter-intuitive finding recapped: "optimal sampling may require the source to deliberately wait before submitting a new sample" (Sun et al. [68] threshold structure under max-rate constraint and random delay).
- Limits kept here: the paper's mechanism (AR-MDP constrained partially-observable SMDP, QuickBLP/OnePDSI) is not re-read in detail this turn; the transfer uses only the staleness taxonomy and the endogenous-AoI / threshold insight, not the solver.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py:767-845` defines freshness as a binary gate: `FRESH_SECONDS = 30.0`, `DENSE_FRESH_SECONDS = 30.0` (same), and `_row_status` returns `fresh-clock-conditional` vs stale/unknown based on `now - epoch`. No AoI `Δ(t)` process is accumulated; staleness is not integrated.
- `read_redmi10_delivery_gaps` (956-1134) and `_gap_records` (999-1035) count consumer class-C halt records over `GAP_WINDOW_SECONDS = 86400.0` (one day) — a windowed count and worst-delay ` _worst_batch_delay`, not a per-slot AoI evolution between `S_i`/`D_i`/`Y_i`.
- `read_redmi10_dense_baseline` (1871-2038) reads `DENSE_TAIL_BYTES = 262144` (~2500 rows, ~3.5 h at 5 s) with `DENSE_WINDOW_ROWS = 2000` (~2.8 h); `_dense_sequence_accounting` (1835-1868) does expected-vs-received sequence accounting (loss/reorder/duplicate counters, steps_back), not AoI.
- `read_node_spool_summary` (2302-2356) and `read_node_consumer_state` (2226-2300) decode the linked site's local spool summary per body; spool overflow is counted by the producer as `spool overflow dropped=N` lines (personal-multibody.py), not modelled as queue backlog driving `Δ(t)`. No sampling-frequency / delay coupling is represented.
- Prior REVISIT for this cell (2026-11-01, Jungo arXiv:2401.05437, discarded) correctly noted gap stays gap and no imputation; this REVISIT addresses a different recent finding — freshness vs effectiveness — that does not require imputation.

## What is new here

The plant's freshness gaps are reported as "how old is the latest row" and "how many halts in 24 h," while the recent AoI literature (Li et al. §I–§II) moves the question from "how fresh" to "how useful is stale data for the downstream decision, given delay is shaped by when we sampled."

1. **Endogenous vs exogenous delay.** The paper's Table I distinction is that DDMDP/SDMDP treat delay `d`/`D` as exogenous (fixed or i.i.d. independent of sampling), whereas AR-MDP treats `Δ(t)` as endogenous — jointly determined by sampling decisions `S_i` and random delay `Y_i` (eqs.(1)-(2), Fig.2). The plant's current gaps assume the second shape: `_gap_records` treats halts as observed exogenous events in a delivered log, while `DENSE_TAIL_BYTES` is sampled at a fixed 5 s cadence with no modelled `S_i`→`Y_i`→`Δ(t)` coupling and no sampling-control knob.

2. **Freshness ≠ effectiveness; stale can beat fresh.** Abstract and §I survey establish that in decision-critical settings "some stale data may be more valuable than fresh updates" and that inference loss need not be monotonic in AoI when the source is non-Markovian (Shisher et al. selection-from-buffer [77][78] cited §II-A). The plant has no utility model for staleness: no MSEE/UoI/AoII/AoCI variant, no nonlinear penalty, and `read_redmi10` "never infer human movement" — a correct stance that leaves open the paper's question of how staleness should inform the *reader's* decision to sample/poll more often vs hold.

3. **Threshold beyond which more sampling does not help.** The paper derives under a sampling-frequency constraint that optimal sampling follows a threshold structure — wait until AoI or error exceeds a threshold (Sun et al. [68], §II-A) — and proves a sensitivity threshold "beyond which additional sampling does not yield further gains in decision-making" (§II-C). The plant has fixed windows/cadences (`DENSE_TAIL_BYTES` tail, `GAP_WINDOW_SECONDS` 86400) without such a threshold: neither the senses pane nor the consumer documents at what sampling frequency the dense baseline stops improving the carried-device verdict, nor whether zero-wait polling (sample as fast as possible) is optimal or wasteful given the tailnet's `Y_i`.

## Application

target: src/mishe_tauftauf/body_perception.py

Owner `senses` adds a docstring-only boundary note beside `FRESH_SECONDS` / `DENSE_FRESH_SECONDS` / `GAP_WINDOW_SECONDS` and `read_redmi10_dense_baseline` / `_dense_sequence_accounting` (around lines 111-115, 964-973, 1136-1155, 1835-1871), stating:

- Li et al. arXiv:2504.19507v3 §§I–II distinction per Abstract/Table I/Fig.2/eqs.(1)-(2): AoI `Δ(t)=t-S_i` with endogenous reset `Δ(t+1)=Δ(t)+1` or `Y_i` vs DDMDP fixed `d` / SDMDP i.i.d. `D` exogenous delay; freshness alone ≠ effectiveness (AoS/AoII/AoCI/UoI variants, non-monotonic inference loss, selection-from-buffer).
- This module currently reports freshness as a binary gate (`FRESH_SECONDS`/`DENSE_FRESH_SECONDS` 30 s, `_row_status` fresh-clock-conditional) and gaps as a 24 h halt count + worst batch delay + sequence loss counters (`GAP_WINDOW_SECONDS`, `_gap_records`, `_dense_sequence_accounting`); no `S_i`/`D_i`/`Y_i`/`Δ(t)` process is accumulated and no sampling-frequency coupling is modelled — delivery halts are observed exogenously, not as `Δ(t)` shaped by a sampling policy.
- The AR-MDP threshold result (optimal sampling waits until AoI/error exceeds a threshold; beyond a sampling-frequency threshold additional sampling yields no decision gain, §II-A/§II-C) has no analogue: `DENSE_TAIL_BYTES` (~2500 rows, ~3.5 h) / `DENSE_WINDOW_ROWS` (2000) are fixed windows, not threshold policies, and no document states the frequency at which denser polling stops improving the dense-baseline verdict vs queueing/congestion or the tailnet's random delay.
- Staleness handling remains pointwise and atemporal as of 2026-11-03; if a `Δ(t)` accumulator or a threshold/wait policy is ever wired, name the threshold and the caller here and re-check this note. Revertible by `git revert`; no verdict/behaviour change.

Test (proposed to senses; owner writes it, not this review): `python -m py_compile src/mishe_tauftauf/body_perception.py` exits 0; `python3 research/field-review/field_review.py check` passes; `rg -n "2504.19507|AR-MDP|Delt.*t.*S_i" src/mishe_tauftauf/body_perception.py` hits the new note and nowhere else in `src/`.

Activation: docstring change only; no runtime behaviour, no new `sense.*` verdict, no I/O, no sampling knob. Reversible by `git revert` of the owner's commit.

Disposition after landing: senses reads the note against Li et al. §§I–II and either keeps it, narrows it (e.g., adds the actual consumer `S_i`/`Y_i` distribution or a threshold if measured), or — if a `Δ(t)` accumulator is wired — replaces "no process" with its equation and caller here. The 2026-11-01 imputation discard and the existing sequence-accounting note are complemented, not edited, by this review.
