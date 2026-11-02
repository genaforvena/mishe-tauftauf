---
field: recovery-observations-and-sessions
angle: misread
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/host_containment.py
---

## What we read

Tushar Deepak Chandra and Sam Toueg — **"Unreliable Failure Detectors for Reliable Distributed Systems"**, *Journal of the ACM* 43(2), pp. 225–267, March 1996, doi:10.1145/226643.226647.

- <https://doi.org/10.1145/226643.226647> (canonical DOI)
- <https://www.cs.utexas.edu/~lorenzo/corsi/cs380d/papers/p225-chandra.pdf> (full PDF; fetched 2026-11-02 to /tmp/chandra-toueg-1996.pdf 3.1M sha256 f2c4b22a63e43c462286ce740e38528bac275d87e769d794bb830a434e1362cf, extracted with `pdftotext`, 43pp)

Passages this revisit rests on (§1 and §2, line numbers from extracted text):

1. **Suspicion is revisable, not a verdict (§1, p.226).** "each failure detector module can make mistakes by erroneously adding processes to its list of suspects: that is, it can suspect that a process p has crashed even though p is still running. If this module later believes that suspecting was a mistake, it can remove p from its list. Thus, each module may repeatedly add and remove processes from its list of suspects."
2. **Adaptive timeout after a mistake (§1, p.229).** "If p later receives a 'q-is-alive' message, p recognizes that it made a mistake by prematurely timing out on q: p removes q from its list of suspects, and increases the length of its timeout period for q in an attempt to prevent a similar mistake in the future." In asynchrony this does not implement eventual strong accuracy with a fixed timeout: "an unbounded sequence of premature time-outs may cause every correct process to be repeatedly added and then removed"; increasing the timeout after each mistake is what lets accuracy hold "after some point" and "for sufficiently long periods."
3. **Reason about the detector by its properties, not its timeout (§1, p.229 bottom).** "all reasoning about failure detectors (and algorithms that use them) should be done in terms of their abstract properties and not in terms of any particular implementation. This is an important feature of this approach, and the reader should refrain from thinking of failure detectors in terms of specific time-out mechanisms."
4. **Perfect detector needs synchrony (§7 intro, p.255).** "it is well-known that Consensus is solvable … in completely synchronous systems … we can use timeouts to implement a 'perfect' failure detector—that is, one in which no process is ever wrongly suspected" — not our model.

Limits kept: the paper is about crash failures of processes for Consensus/Atomic Broadcast, not host filesystem or listener liveness; eventual accuracy gives no bound on how long a wrong suspicion lasts; it does not address the cost of delaying a safety stop. It is not a monitoring design manual.

## What this plant already does

The single-sample latch identified in the 2026-10-31 review of this cell is now closed.

- **Suspicion with persistence is implemented.** `src/mishe_tauftauf/host_containment.py:37-41` defines `PROBE_SUSPECT_SECONDS = LIFE_MISSING_SECONDS` (20s) with comment citing Chandra and Toueg 1996 s1 p.226–227, and `probe_suspicion()` (l.554) returns a stop only when `now - missing_since >= PROBE_SUSPECT_SECONDS`. The `watch()` loop (l.979) merges `reason()` and `probe_suspicion()` so a one-sample `ssh_available` or `host_io_available` failure is a suspicion, not a latch. The `status` dict exposes `probe_suspect` with `missing_since` and `window_seconds` (l.999–1001).
- **Hysteresis on recovery is added.** Commit 50771380 makes the suspicion clear only after a continuous success run of `PROBE_SUSPECT_SECONDS` (`probe_suspicion` l.579–582: `ok_since` must reach the window before `(None,None)`), so an alternating fail/succeed pattern accumulates toward a stop instead of resetting each cycle.
- **What is still fixed.** `functional_probes()` (l.344–345) calls `bounded_command(..., timeout=5, ...)` for both `ssh-keyscan -T 4` and the `io-probe` Python helper with a constant 5s. No per-probe timeout state is kept and no increase follows a false suspicion. The same fixed 5s appears for the life-support `list-panes` (l.660) and `guard.py mode` (l.701) checks. `recovery_io.py:219` `run_probe(argv, *, timeout=5)` is the same shape on the recovery boundary.
- **Docstring gap.** The comment at l.37–40 names revisability but not the paper's paired prescription (immediate removal on `q-is-alive` plus timeout increase), nor the paper's warning not to reason directly in timeout terms (passage 3 above).

## What is new here

The misreading that remains is not the latch but the detector's two halves.

Chandra–Toueg splits a failure detector into (a) the suspicion output (the list, revisable cheaply) and (b) the timeout parameter that drives it (adapted after each mistake). This plant now has (a) but not (b). The hysteresis added in 50771380 is the opposite of the paper's immediate removal: the paper removes q from suspects as soon as the `q-is-alive` arrives and then increases the timeout; the plant keeps the suspicion for another 20s of continuous success before clearing. That keeps a host that has recovered from a transient pressure spike marked suspect through a window that the paper would already have cleared, and it does not make the next timeout any less likely to fire prematurely — the next spike still hits a 5s probe.

The loose application is to treat the 20s suspicion window as a substitute for adapting the timeout. Passage 3 warns against this: correctness is argued from completeness/accuracy properties, not from a chosen timeout value, and the implementation note explicitly says a fixed timeout cannot achieve eventual accuracy under asynchrony. With a fixed 5s probe that can exceed any bound when the host is under pressure, a run of premature timeouts can keep a correct host suspect forever, which is exactly the violation p.229 describes. The fix is not to guess a larger constant but to record the adaptation the paper prescribes.

## Application

target: src/mishe_tauftauf/host_containment.py

Proposal for genome (owner of this file), additive and behavior-preserving by default. No latch semantics change until measurement says so.

1. **Name the timeout parameter the paper adapts.** In `host_containment.py:41` beside `PROBE_SUSPECT_SECONDS`, add `PROBE_TIMEOUT_SECONDS = 5.0` with comment citing Chandra–Toueg §1 p.229 ("increase the length of its timeout period for q") and noting `functional_probes()` and `bounded_command()` at l.344–345 as the implementation point.
2. **Record the adaptation point without changing the default probe.** In `probe_suspicion()` docstring, note the paper's immediate removal on `q-is-alive` vs the current 20s hysteresis, and cite passage 3 as the reason not to harden the window without adapting the timeout. Add a pure helper `next_probe_timeout(current: float, mistake: bool) -> float` that returns `min(current * 1.5, 10.0)` on a false suspicion (a failure interval that ended before `PROBE_SUSPECT_SECONDS` and was followed by `PROBE_SUSPECT_SECONDS` of continuous success) and `current` otherwise; default call keeps 5.0, so `python -m py_compile` and existing behavior are unchanged.
3. **Make the adaptation observable.** Extend the `probe_suspect` dict written at l.999–1001 with `timeout_seconds` (current value) so a wall reader can see both the suspicion output and the parameter that drives it, matching the paper's split between output and implementation.

Acceptance (to be run by genome, detector owner):

- `python -m py_compile src/mishe_tauftauf/host_containment.py` passes and existing `host_containment` probe logic with `PROBE_TIMEOUT_SECONDS=5.0` still latches only after 20s sustained failure; a single injected `host_io_available=False` sample does not latch and appears in `probe_suspect` with `timeout_seconds`.
- After a false-suspicion cycle (failure <20s then 20s continuous success), `next_probe_timeout(5.0, True) == 7.5` and the subsequent `probe_suspect.timeout_seconds` reflects the increased value; no automatic stop threshold changes.
- Live: change is docstring/helper/field only; no new latch path until genome measures premature-timeout frequency from `samples.jsonl`/`probes` receipts and decides the cap.

Detector: `probe_suspect` field (`missing_since`, `window_seconds`, `timeout_seconds`) and `next_probe_timeout` helper. Resolver: genome (source) with health (activation). Detection interval: each `watch()` tick, read at next wake. Reopen if a later source revises the p.229 adaptation wording or if the timeout increase is shown to belong in `recovery_io.py` instead.

Limits: this adds the paper's adaptation bookkeeping, not proof that host stalls are crash failures or that increasing the user-space io-probe timeout prevents pressure-induced timeouts (the kernel `io.max` and PSI signals are separate, per l.543–547 comment). The 1.5×/10s cap is provisional, not derived from the paper, which gives no factor or bound.
