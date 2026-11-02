---
field: recovery-observations-and-sessions
angle: recent
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/recovery_io.py
---

## What we read

Veresov, Spenger, Carbone, Haller — **Failure Transparency in Stateful Dataflow Systems (Technical Report)**, extended version of ECOOP 2024 (doi:10.4230/LIPIcs.ECOOP.2024.42), arXiv:2407.06738v2, 18 Oct 2024, 44pp.

- <https://arxiv.org/abs/2407.06738> (abstract) and <https://arxiv.org/pdf/2407.06738> (PDF read locally, not a summary)
- Fetched to `/tmp/freview/2407.06738.pdf` 1067205 bytes sha256 `5fd07c5a2d4e0d4fdc7611d6e688f12a1f4ea0c68a150bdd64c6076d37fd61ff` (pdfTeX/LIPIcs, CreationDate 2024-10-21T00:40:02Z, 44pp) + extracted `/tmp/freview/2407.06738.txt` 3365 lines via `pdftotext`.

The passages this review rests on (page numbers from the technical report):

1. **Definition 5.3 (Observability Function)** p.20: "An observability function O of an execution … maps an execution to a sequence of observations." The paper uses `out` (Def 4.3) — output messages on sink streams — as its O for the dataflow instance.
2. **Definition 5.4–5.5 (Observational Explanation / Explainability)** p.20–21: a concrete execution is observationally explainable by an abstract one when the two produce the same observation sequence under O after a monotone index translation.
3. **Definition 5.10 (Failure Transparency)** p.24: "A set of rules R is failure-transparent with respect to failure rules F ⊆ R for a monotonic observability function O and a set of initial configurations K, iff: R —O→_{K,F} (R \ F)" — i.e. the full implementation (with failures) is observationally explainable by the failure-free fragment (without F), with the same O and identity translation on K.
4. **Instance and theorem** §6 p.24–27: the implementation model is the ABS-based stateful-dataflow small-step semantics (§4) and the theorem is that it is failure-transparent for `out` (Def 4.3). Liveness is proved separately under a fair-execution assumption (§6.3).

Stated limits kept from the paper: the result is for acyclic dataflow graphs under crash-recovery failures and FIFO channels, with the ABS protocol (Chandy-Lamport variant without in-flight events). The Coq mechanisation covers the definitions and theorems (`https://github.com/aversey/abscoq`). It is not a claim about arbitrary session supervisors, timed leases, or host-capacity shedding.

## What this plant already does

`src/mishe_tauftauf/recovery_io.py` is the literal recovery boundary: bounded local files under `recovery/`, Linux probes (`run_probe` at :219, `pressure` at :293, `_flock_holder_pids` at :344), time-bounded locks (`controller_lock` at :460, `SEED_LOCK_WINDOW_SECONDS` 10.0 at :309, `SEED_HOLDER_STUCK_SECONDS` 120.0 at :314), and the observable outputs `status()` at :493, `guard()` at :730, `evaluate()` at :747 and `event`/`deliver` at :504/:630. The observer aggregates those into the wall via `observations.py` and `session_watch.py`. Doctrine already states that missing/stale evidence stays UNKNOWN/RED and that tmux and its lease establish presentation liveness only.

What is absent: no explicit partition of the rule set into F vs R\F, no named observability function O, and no statement of what "transparent" would mean for the operator wall. A retry that masks a transient probe failure and a shed that surfaces as CRITICAL both go through the same `status` path without marking whether the operator could distinguish them from a failure-free run. The 2026-10-31 discard for this cell is for that reason: no readable 2023–2026 source with a transferable mechanism was then available.

## What is new here

The paper's contribution relevant to this lane is not ABS itself but the *testable definition* of failure transparency as observational explainability with an explicit O and an explicit F. Transferred to this plant:

- **O** is what the operator wall renders from `recovery_io.status`/`guard`/`evaluate` plus the `observations` snapshot — not internal probe retries, controller-lock contention samples, or `samples.jsonl` latencies.
- **F** is the failure-related rule subset that the paper separates: timed probe expiry (`run_probe` timeout), `pressure`/`host_cgroup` shedding, seed-holder stuck detection (`_judged_holder_age` at :365), `controller_lock` unavailability, and the guard/mitigation branch in `check()` at :538. **R\F** is the same system with those rules removed (every probe succeeds within its timeout, every lock is available).
- **Transparency claim** would be: every execution of R produces an O-sequence that some execution of R\F could also produce — i.e. the wall cannot tell that a failure happened except via the declared `recovery` observation (`sense.recovery`). The paper proves this for `out` under ABS; this plant does not claim it and does not currently have a way to check it.

That makes transparency a checkable predicate rather than informal "hide failures". A violation would be an execution where the wall shows a transient `health:probe` blip or a `controller-shed` state that has no failure-free counterpart with the same O.

## Application

target: src/mishe_tauftauf/recovery_io.py

Proposed change for health (owner), additive and docstring-only, no behaviour change:

1. In the module docstring at `recovery_io.py:1`, add a paragraph after the first line citing Veresov et al. 2024 (arXiv:2407.06738v2 §5, Def 5.10, 18 Oct 2024, sha256 `5fd07c5a2d4e0d4fdc7611d6e688f12a1f4ea0c68a150bdd64c6076d37fd61ff`) and stating: *Observability function O is the sequence of wall-visible outputs from `status`/`guard`/`evaluate` (state, reason, delivered events). Failure rules F are the timeout/shedding/lock/mitigation branches enumerated above; the failure-free fragment R\F is defined as those branches taking the success path. Failure transparency (Def 5.10) would be R —O→_{K,F} (R\F). This module does not claim that property; the definition is recorded so a violation can be detected as a wall sequence with no failure-free counterpart.*

2. Beside `validate_state` at :469, add a pure helper `is_failure_transparent_trace(observations_with_failures, observations_without) -> bool` stub that compares two O-sequences for equality (identity translation), returning `True` iff they match — mirroring the paper's identity translation on K. Leave the body as `return obs_with == obs_without` with a comment that the real check is trace-level (per §6) and that liveness is separate (fair-execution assumption, not assumed here).

Prediction: `python -m py_compile src/mishe_tauftauf/recovery_io.py` passes, `python tools/check_parsing.py` still passes (frozen budget unchanged), and `field_review.py check` remains PASS.

Observation: the check and a single call `is_failure_transparent_trace([{"state":"UNKNOWN"}], [{"state":"UNKNOWN"}]) == True` and `!=` case.

Keep if the helper is pure, additive, and no probe/latch behaviour changes. Revert if it touches `check`/`status`/`guard` outcomes or introduces a verdict field.

Limits: this records the definition and a syntactic equality check; it does not prove transparency for this plant (the paper's proof depends on ABS + FIFO + acyclicity, none of which this recovery boundary assumes). It also does not define liveness — a transparent system can still stall; the paper proves liveness separately under fairness and this plant does not import that assumption.

Routing caveat: `recovery_io.py` is the literal recovery boundary already owned by health/genome. This review does not edit it; health applies the docstring/helper on its wake.
