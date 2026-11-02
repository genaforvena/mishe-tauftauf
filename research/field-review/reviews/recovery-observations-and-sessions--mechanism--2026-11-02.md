---
field: recovery-observations-and-sessions
angle: mechanism
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/recovery_io.py
---

## What we read

- Mike Burrows, **The Chubby lock service for loosely-coupled distributed systems**, Proc. 7th USENIX OSDI 2006, pp. 335–350.
  - <https://static.googleusercontent.com/media/research.google.com/en//archive/chubby-osdi06.pdf> — PDF read locally to `/tmp/chubby-osdi06.pdf` 117687 bytes sha256 `0747c84a49d32744a2744e2f2951b05fb7a0829ce44afb0b700833eb2bb3ecdd`, 16 pp. + extracted `/tmp/chubby.txt` 1546 lines via `pdftotext`, fetched 2026-11-02.
  - Canonical ACM entry <https://research.google/pubs/pub27897/> (same text, HTML 141836 bytes sha256 `e88e46bf287b`).

  Operational mechanism is **§2.4 Locks and sequencers** (p. 4–5). Chubby file locks are advisory and coarse-grained; each acquisition carries a **sequencer**, "an opaque byte-string that describes the state of the lock immediately after acquisition. It contains the name of the lock, the mode in which it was acquired (exclusive or shared), and the lock generation number." The client "passes the sequencer to servers (such as file servers) if it expects the operation to be protected by the lock. The recipient server is expected to test whether the sequencer is still valid and has the appropriate mode; if not, it should reject the request." Validity can be checked against the server's Chubby cache or against the most recent sequencer observed. As imperfect fallback for servers that do not check sequencers, a **lock-delay** (configurable, currently bounded at one minute) prevents immediate re-acquisition after a failure-driven release and reduces race from delayed messages.

  Session and lease context is **§2.7–§2.9** (p. 6–9): each Chubby session has a lease interval; the master guarantees not to terminate it before the lease timeout and advances it on KeepAlive (default extension 12 s) while blocking the RPC until near expiry. A secondary **grace period** (default 45 s) after local lease expiry lets clients quiesce on jeopardy and resume on `safe` without restart.

- Martin Kleppmann, **How to do distributed locking**, blog 8 Feb 2016 (research for *Designing Data-Intensive Applications*), § "Making the lock safe with fencing".
  - <https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html> — HTML read locally 42424 bytes sha256 `8cf7f237e65c`, fetched 2026-11-02.

  Same mechanism restated as **fencing token**: "a fencing token is simply a number that increases (e.g. incremented by the lock service) every time a client acquires the lock." Example sequence quoted verbatim: "Client 1 acquires the lease and gets a token of 33, but then it goes into a long pause and the lease expires. Client 2 acquires the lease, gets a token of 34 (the number always increases) … the storage server remembers that it has already processed a write with a higher token number (34), and so it rejects [33]." Kleppmann's diagnostic: "it is not obvious to me how one would change the Redlock algorithm to start generating fencing tokens. The algorithm does not produce any number that is guaranteed to increase every time a client acquires the lock" — and "you need to include a fencing token with every write request to the storage service," using a store that checks it (ZooKeeper `zxid`, HDFS generation, or a `compare-and-set` on a version). Redlock's lack of this integer is already sufficient to reject it for correctness-critical use.

Stated limits kept from both sources so the transfer is not overread: Chubby's sequencer is advisory and relies on modified servers that actually check it; lock-delay is explicitly called "imperfect". Kleppmann notes fencing only helps when the downstream storage *enforces* the check — a lock without storage-side validation is not safe. Neither source claims a bounded fencing check makes lease expiry detectable from the holder's side; the holder only learns it is stale when its next write is rejected. The papers address external stores (file/Bigtable), not a single-host flock.

## What this plant already does

`recovery-observations-and-sessions` is the litteral recovery boundary. The plant already has a stop/lease split that contrasts with the Chubby/Kleppmann account:

- **The stop is external and advisory.** `src/mishe_tauftauf/seed.py:258-259` `fcntl.flock(LOCK_EX)` on `.seed.lock` plus `stamp_seed_holder` records pid and `PYTHONPATH` release; `src/mishe_tauftauf/recovery_io.py:461-466` `controller_lock` is the same flock as `LOCK_EX|LOCK_NB` on `recovery/controller.lock` yielded to the caller. No integer is generated or returned on acquisition. The lock name is the pathname, the mode is always exclusive, and there is no `generation`/`sequencer`/`fencing token` field in `status()`, `guard()`, or `evaluate()`.

- **The write path carries no token.** Every mutation that holds the lock — wall append, `delivery.py` forced lease, `task_state` claim, settlement via `seed_yield` — writes bounded local files under the flock and releases. The file content does not carry `{lock_name, mode, generation}` and the reader does not check `token > highest_seen` before accepting it. The only ordering is the flock itself and the tape/entry `sequence` number, which is assigned on append rather than at lock acquisition and is not validated as a predecessor generation.

- **Stale-holder diagnosis exists but does not fence.** `_judged_holder_age` (`recovery_io.py:365`) and `_holder_fact` (`:394`) can name which pid holds `.seed.lock` and how long, and `seed_lock_probe` (`:423`) waits up to `SEED_LOCK_WINDOW_SECONDS=10.0` with stuck bound `SEED_HOLDER_STUCK_SECONDS=120.0`. Host-pressure's `LIMITED` shedding (`host_pressure`, `controller_shed`) can delay a holder for seconds — the exact "long pause" Kleppmann diagrams — but when that holder resumes, its already-locked write is still accepted because flock was never lost, and a holder that races after losing a non-blocking `controller_lock` try simply gets `BlockingIOError` rather than a rejected token on a store.

- **Prior mechanism on this field argued `stop=crash` is sufficient.** The 2026-10-31 mechanism review (Candea & Fox 2003 crash-only, target `tmux.py`) proposed making the survivor count after `kill-session` observable, noting the reaper covers only test sessions `mishe-tauftauf-test-*` and production `stop()` at `tmux.py:300` is external. That review deliberately left the session tree alive by design (`tmux.py:318-322` docstring). Fencing is the dual gap: `stop`-as-crash asks whether anything survived the session kill; *sequencer-as-predecessor* asks whether a write that was *protected* by an earlier lease is still valid after the lease changed, even if nothing was killed.

- **Recent/misread/critique/metric revisits on this field do not name it.** Veresov 2024 transparency (`recovery_io.py:1` def 5.10 Observability, owner health), Chandra-Toueg 1996 adaptive timeout (`host_containment.py:37-61` fixed 5 s probe, owner genome), Yuan 2014 Aspirator (`recovery_io.py` handler inventory, owner health), the OTP MaxR/MaxT intensity (`session_restarts.py` `max_r/window_minutes`, owner senses) and the absent-time metric (`session_restarts.py` `absent_minutes`, owner senses) are sensor, detector, handler, and QoS readings — none mint an acquisition-scoped generation number or gate a store on it. The φ accrual review (2026-11-02, target `recovery_io.py`) makes suspicion continuous but does not fence writes. A sweep of `grep -rn "fenc\|sequencer\|generation.*lock" src` returns only prose fences in `post_check.py` (`:103`), confirming no literal generation exists, while `docs/protocol-boundaries.md` defines only `parsing-policy` boundaries, not recovery fencing.

## What is new here

The transfer is the generation check, not the delay:

1. **Generation per acquisition.** Every time the advisory lock is acquired on `.seed.lock` or `recovery/controller.lock`, a strictly increasing `generation` (monotonic `int` stored beside the stamp) is incremented and exposed to the holder as an opaque sequencer `{name, mode, generation}`. The integer is persisted under the lock's directory so crash and fail-over do not reset it; wrap-around is forbidden (raise on overflow rather than wrap).

2. **Store-side validation.** Every write that claims to have been done while protected — wall event, `delivery` receipt, `recovery/state.json` row — carries `fencing_token = generation` and the reader (the next locker, or any consumer) keeps `highest_seen: int` per lock name. A write with `token <= highest_seen` is rejected (or routed to `UNKNOWN` with reason `stale-fencing-token`) even if its bytes are well-formed. This is exactly Kleppmann's "storage remembers 34, rejects 33" and Chubby's "check against most recent sequencer observed" — moved from a distributed store to local bounded files.

3. **What lock-delay is not.** Chubby's lock-delay (≤60 s) is not imported as a correctness mechanism; it is an intentionally lossy grace that avoids hot restart races for servers that cannot be modified. This plant's `SEED_LOCK_WINDOW_SECONDS=10.0` wait in `seed_lock_probe` is the same kind — a polling window, not a proof. Fencing does not replace it; fencing is the check the load-bearing writes perform, delay is the unmodified-path mitigation. Kleppmann's separation ("efficiency vs correctness" lock question, p. 2) maps here as: an un-fenced lock may be fine for best-effort probes (`seed_lock_probe`'s `"lock available; actual settlement untested"` at `recovery_io.py:457`), never for the `controller.lock` row that `validate_state` later trusts as committed recovery state.

4. **Complementarity with the other revisits.** Veresov's `O` (wall-visible `status/guard/evaluate`) defines *what* is observable after a failure; fencing defines whether a write with a late `O` is admissible at all. Chandra-Toueg's adaptive timeout defines when suspicion becomes a verdict; fencing defines what a verdict holder may write while suspect. Yuan's handler inventory defines which catch blocks would be hit by the `stale-fencing-token` rejection; φ accrual defines a continuous suspicion scale on which the fence's `highest_seen` could later be conditioned. OTP `MaxR/MaxT` and absent-time define restart QoS; fencing bounds the *effect* of a restart on stored state. None of those defines the integer and none is duplicated by adding it.

## Application

target: src/mishe_tauftauf/recovery_io.py

Proposed change for health (owner), additive and behavior-preserving by default. No lock mode change, no lease-period change, no new probe.

1. **Module docstring paragraph after the first line** (`recovery_io.py:1`), citing both sources with pin:

    *Fencing per Chubby §2.4 and Kleppmann 2016 § "Making the lock safe with fencing" (Burrows et al. OSDI '06 PDF sha256 `0747c84a49d32744a2744e2f2951b05fb7a0829ce44afb0b700833eb2bb3ecdd`, `/tmp/chubby-osdi06.pdf` 117687 bytes 16 pp., fetched 2026-11-02; Kleppmann HTML 42424 bytes `8cf7f237e65c` same date). A **sequencer** is `{lock_name, mode, generation_number}` minted on each acquisition and checked by the store: the server accepts a write only if `token > highest_seen[token.lock]` (Chubby: "contains the name, mode, generation"; "validity can be checked against the most recent sequencer observed"; Kleppmann: "a number that increases every time a client acquires the lock … the storage server remembers that it has already processed a write with a higher token number (34), and so it rejects [33]"). The imperfect fallback for unmodified servers is a bounded lock-delay (Chubby ≤60 s; this plant `SEED_LOCK_WINDOW_SECONDS=10.0`). This module does not yet claim the property; the definition is recorded so a stale-after-pause write can be detected as `stale-fencing-token`.*

2. **Beside `validate_state` (`:469`), add pure helpers with no I/O, mirroring `histogram_buckets`/`slo_check` naming:**

    ```python
    def fencing_sequencer(lock: str, generation: int, mode: str = "exclusive") -> dict:
        """Opaque sequencer per Chubby §2.4: {name, mode, generation}. No I/O."""
    def next_generation(prev: int) -> int:
        """Strictly increasing generation; ValueError on overflow or non-int."""
    def is_sequencer_valid(token: dict, highest_seen: dict) -> bool:
        """True iff token.generation > highest_seen.get(token.name, -1) and mode matches."""
    def check_fencing_token(token_generation: int, highest_seen_generation: int) -> bool:
        """Kleppmann check: accept iff token > highest_seen. Pure, no files."""
    ```

    All four are pure, no clock, no flock. `fencing_sequencer` returns `{"name": lock, "mode": mode, "generation": int(token)}`; `next_generation` is `prev + 1` guarded against non-`int` and against wrap (raise `ValueError` if `prev >= 2**63-1`); `is_sequencer_valid`/`check_fencing_token` implement `>` not `>=` so equal tokens conflict. Default `highest_seen` missing is `-1` so generation `0` is valid on first mint, matching the existing tape start.

3. **Make the integer observable without gating writes yet.** When recovery writes `recovery/state.json` or a `delivery` receipt while holding `controller_lock`, compute `generation = next_generation(last_generation_on_disk or -1)`, persist it atomically beside the stamp (e.g. `recovery/controller.generation`), and emit it in `check()` as `checks["controller-lock"]["fencing"] = {"generation": g, "highest_seen": h}` and in `samples.jsonl` as `{fencing_generation, fencing_highest_seen}`. Do not reject any write when the field is absent (absent ⇒ `valid = True`) so the probe is byte-identical when the file is missing; when present, `validate_state` does not reject on `token <= highest_seen` — instead `check()` flags it in `checks` with reason `stale-fencing-token` for health to route. Actual rejection under the lock is a separate health decision with its own activation review.

4. **Do not move pause detection or add a heartbeat.** The generation is a local file counter, not a Chubby KeepAlive lease or grace period; it does not change `SEED_HOLDER_STUCK_SECONDS=120.0` or the 12 s/45 s defaults cited from Chubby — those remain documentation values for how long a pause may last before fencing matters. Do not add exponential back-off or jitter for retries here — the φ accrual review already owns suspicion scaling.

Prediction: `python -m py_compile src/mishe_tauftauf/recovery_io.py` passes, `python tools/check_parsing.py` still exits 0 (no new parsing call), and `grep -rn "fencing_\|sequencer" src` shows only `recovery_io.py`.

Observation (deterministic, runnable from this repository, no lease edit):

- `from mishe_tauftauf.recovery_io import fencing_sequencer, next_generation, check_fencing_token, is_sequencer_valid; s33 = fencing_sequencer("controller.lock", 33); s34 = fencing_sequencer("controller.lock", 34); assert next_generation(33) == 34 and check_fencing_token(33, 34) is False and check_fencing_token(34, 33) is True and is_sequencer_valid(s33, {"controller.lock": 34}) is False and is_sequencer_valid(s34, {"controller.lock": 33}) is True`
- With missing `recovery/controller.generation`, a replay of `recovery/state.json` + `guard.json` fixtures through `recovery_io.check(home, config, observation=obs, now=t)` yields byte-identical `state`/`functions` as before, with additive `checks["controller-lock"]["fencing"]` containing `generation`/`highest_seen` only when the generation file exists on the first call that actually writes it.

Keep if the helpers are pure, additive, and no probe/latch/state outcome or verdict field changes when no generation file is present; revert if any existing `check`/`status`/`guard`/`deliver` outcome changes without the file, or if a reachable `generation` write without a flock is added.

Limits: generation counting proves a write is stale-after-pause, not why the pause happened (OS scheduling, cgroup `io.max`, controller shed, and normal lock contention all look like "delay" to this detector — Kleppmann's emphasis on storage-side enforcement remains). An in-lock overwrite after acquisition is still valid — fencing rejects only cross-acquisition staleness. The integer does not make flock mandatory; writers that bypass the lock can still corrupt state and will not carry a valid sequencer. Crash during the generation bump itself can leave a gap; the detector's `highest_seen` must be lenient to gaps and strict to replays (`>` not `>=`).

Routing caveat: `src/mishe_tauftauf/recovery_io.py` is the litteral recovery boundary already owned by health/genome. This review does not edit it; health applies the docstring/helpers on its wake. Complement to the 2026-11-02 Veresov transparency, Yuan handler-inventory, and φ accrual reviews on the same file, and to the Chandra-Toueg adaptive-timeout revisit on `host_containment.py` — it does not duplicate the crash-only survivor-count mechanism on `tmux.py` (Candea & Fox 2003) which observes liveness after kill rather than validity after lease change. Together the three 2026-11-02 reads on this field cover observability (Veresov), correctness of the latch (Chandra-Toueg + Yuan), scale of the detector (φ accrual), and stale-write fencing (Chubby/Kleppmann).
