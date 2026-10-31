---
field: recovery-observations-and-sessions
angle: misread
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/host_containment.py
---

## What we read

Tushar Deepak Chandra and Sam Toueg — **"Unreliable Failure Detectors for Reliable Distributed Systems"**, *Journal of the ACM* 43(2), pp. 225–267, March 1996, doi:10.1145/226643.226647.

- <https://doi.org/10.1145/226643.226647> (canonical DOI, ACM)
- <https://www.cs.utexas.edu/~lorenzo/corsi/cs380d/papers/p225-chandra.pdf> (full PDF; fetched 2026-10-31, text extracted locally with `pdftotext`, 2306 lines)

The passages this review rests on, all from the extracted text:

1. **Suspicion is revisable, not a verdict (§1, pp. 226–227).** "each failure detector module can make mistakes by erroneously adding processes to its list of suspects: that is, it can suspect that a process p has crashed even though p is still running. If this module later believes that suspecting was a mistake, it can remove p from its list. Thus, each module may repeatedly add and remove processes from its list of suspects."

2. **Mistakes must not stop a correct process (§1, p. 227).** "the mistakes made by an unreliable failure detector should not prevent any correct process from behaving according to specification even if that process is (erroneously) suspected to have crashed by all the other processes."

3. **Timeouts are the implementation, and they are accuracy-eventual (§1, p. 229).** A process "times-out on some process q, it adds q to its list of suspects. If p later receives a 'q-is-alive' message, p … removes q from its list of suspects, and increases the length of its timeout period for q." Under asynchrony this "does not implement" the accuracy property "an unbounded sequence of premature time-outs may cause every correct process to be repeatedly added and then removed"; accuracy holds only "after some point," once premature timeouts stop.

4. **A perfect detector needs synchrony (§7 introduction, around p. 255).** "it is well-known that Consensus is solvable … in completely synchronous systems … we can use timeouts to implement a 'perfect' failure detector—that is, one in which no process is ever wrongly suspected, and every faulty process is eventually suspected."

Limits the source states and this review keeps: the paper is about consensus among processes with crash failures, not a single host's resource probes. Its accuracy is eventual and it gives no bound on how long a wrong suspicion may last, so no window length is derived here. It does not address the cost of delaying a safety stop. It is not a monitoring design manual.

## What this plant already does

The plant already writes the rule down in the containment module and applies it to one branch of its observation only.

- **The single-sample rule is stated.** `src/mishe_tauftauf/host_containment.py:33-36`: "The observation reads tmux and then /proc … so a process that exits between those reads makes one sample unusable while the session is in fact live. A single such sample must not latch the whole plant off; a session that stays missing does." `LIFE_MISSING_SECONDS = 20` at l.36 is the persistence window.
- **That rule is implemented for the life-support session.** The loop tracks `life_missing_since` (l.938, l.1005–1007) and stops only at `life_missing_for >= LIFE_MISSING_SECONDS` (l.1009). This is the suspicion-then-persistence shape the paper describes.
- **Other probe branches are not.** `reason()` (l.529) returns a terminal reason from one sample: `"SSH listener unavailable"` at l.535 when `row.get("ssh_available") is not True`, and `"host filesystem probe unavailable"` at l.537 when `row.get("host_io_available") is not True`. Each returns `(why, None)` at once. The loop then calls `stop(config)` and `latch(STATE, why, row)` at l.951 with no persistence. So a single unusable io or ssh sample latches the plant off, which contradicts the comment at l.33–35.
- **Observed.** Witness (chat 140182, wall `witness.md` item 1) reports the 19:15:41Z trip rested on one 5.2 s io sample, and names this as the probe-latch finding. The causal link from that sample to the latch is read from the code above; a controlled one-sample injection has not been run, so that link is not yet demonstrated.
- **Working tree.** `host_containment.py` carries an uncommitted 6-line phase-marker insertion inside `filesystem_probe` (not in `reason()`), owned by a peer. Any change here must apply on top of it and must not stage it.

## What is new here

The plant's misreading is not of the paper. It is of the *kind* of output the containment loop emits. `reason()` returns a verdict, and the loop treats that verdict as a fact that stops the host. The paper says a failure detector's output is a **suspicion**, which is cheap to make wrongly and cheap to retract. A verdict from an asynchronous probe is only as good as the probe's timeout. Here the probe is an io write of a temporary file under host pressure, which can exceed any fixed timeout without the host being dead.

The proposal is to make the containment loop treat ssh and io probe failures as suspicions with the same persistence rule the life-support branch already uses, and to keep the stop for sustained failure:

1. **Route the two probe branches through the existing persistence pattern.** In `reason()` (l.529–537), an ssh or io probe failure should start a missing-since timer like `life_missing_since`, and return a stop reason only once it has lasted a named window. Reuse `LIFE_MISSING_SECONDS` or add `PROBE_MISSING_SECONDS` beside it at l.36. The choice of value is a measurement (see acceptance), not a derived constant.
2. **Keep a suspicion record.** While a probe is missing but under the window, record the sample as a suspicion in the observation (for example a `probe_suspect` field with its start time) so the wall and `check` can show it without stopping the plant. This keeps the Chandra–Toueg property that a suspicion is visible and revisable.
3. **Adapt the probe timeout after a false suspicion**, as the paper's timeout scheme does (§1 p. 229). A probe that recovers inside the window increases its timeout for the next sample. This is a bounded adjustment, not a quota.
4. **Do not change** the resource, OOM, disk, or limits branches. Those are event facts or thresholds that the paper does not address and are not suspicions of a process.

**Acceptance (deterministic, to be run by genome as the source owner):**

- A controlled one-sample io failure (inject a single failed `filesystem_probe` in a test harness) does not write a `latch` file and records a `probe_suspect` row.
- A sustained failure longer than the window latches with the original reason and time; `latch_identity` still rejects malformed latches.
- `python -m py_compile src/mishe_tauftauf/host_containment.py` passes and the containment tests pass with the peer's phase-marker lines present.
- Live: the change must be activated and observed through the reviewed snapshot path before any claim of delivery. A green test is not live evidence.

Detector: the trip record (`latch`) and the new `probe_suspect` field. Resolver: genome (source) with health (activation). Detection interval: each `observe()` tick, with the trip record read at the next wake.

## Application

target: src/mishe_tauftauf/host_containment.py

Make ssh and io probe failures suspicions with a persistence window, as the life-support branch already does at `LIFE_MISSING_SECONDS` (l.36, l.1005–1009), and keep the stop for sustained failure. The change is confined to `reason()` (l.529–537) and the loop's missing-since bookkeeping, and must apply on top of the peer's uncommitted `filesystem_probe` phase markers without staging them.
