---
field: native-wake-recovery
angle: transfer
date: 2026-11-03
status: applied
owner: health
target: src/mishe_tauftauf/native_recovery.py
---

# native-wake-recovery × transfer — REVISIT: AWS Exponential Backoff and Jitter (Brooker 2015) → bounded fresh-attempt delay

## What we read

- Marc Brooker, **"Exponential Backoff and Jitter"**, *AWS Architecture Blog*, 2015-03-04 (updated 2026-10-22), https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/ — fetched 2026-11-03 to `/tmp/aws-jitter.html` (1,437,372 bytes, sha256 `c5e7beec5a112ade2eee4f5b1d712cc9632b9f0afad679153514174f9a219e5f`). Secondary simulator repo referenced by the post (aws/aws-arch-backoff-simulator) not fetched separately; formulas are images in the post and quoted via surrounding prose here.

  Verbatims relied on (from stripped `/tmp/aws-jitter.html` text, ll. 1678-1702):

  1. "Capped exponential backoff means that clients multiply their backoff by a constant after each attempt, up to some maximum value. In our case, after each unsuccessful attempt, clients sleep for:"
  2. "Running the simulation again shows that backoff helps a small amount, but doesn't solve the problem. Client work has only been reduced slightly."
  3. "It's obvious that the exponential backoff is working, in that the calls are happening less and less frequently. The problem also stands out: there are still clusters of calls. Instead of reducing the number of clients competing in every round, we've just introduced times when no client is competing."
  4. "The solution isn't to remove backoff. It's to add jitter. Initially, jitter may appear to be a counter-intuitive idea: trying to improve the performance of a system by adding randomness. The time series above makes a great case for jitter – we want to spread out the spikes to an approximately constant rate. Adding jitter is a small change to the sleep function:"
  5. "There are a few ways to implement these timed backoff loops. Let's call the algorithm above 'Full Jitter', and consider two alternatives. The first alternative is 'Equal Jitter', where we always keep some of the backoff and jitter by a smaller amount:" [next paragraph] "A second alternative is 'Decorrelated Jitter', which is similar to 'Full Jitter', but we also increase the maximum jitter based on the last random value."
  6. "In the case with 100 contending clients, we've reduced our call count by more than half. We've also significantly improved the time to completion, when compared to un-jittered exponential backoff."
  7. "It's worth noting that none of these approaches fundamentally change the N2 nature of the work to be done, but do substantially reduce work at reasonable levels of contention."

  Not read in full: simulator code/equations rendered as images (`sleep = min(cap, base * 2**attempt)` + `random_between(0, ...)` variants); per-graph numeric tables; comment thread. This review treats only the prose mechanism and the three named jitter variants, not the exact image constants.

- Limit: Blog thresholds and the 10 ms/4 ms network simulator are illustrative, not a specification; site note (May 2023 update) says most AWS SDKs now implement standard/adaptive retry with backoff+jitter natively, so hand-rolled sleep is discouraged for AWS SDK calls — the mechanism here is the jitter *shape*, not a mandate to reimplement SDK retry.

## What this plant already does

- `src/mishe_tauftauf/native_recovery.py:99-100` defines `TRANSIENT_STATUS = frozenset({408, 429})` plus `500<=status<=599` (`transient_status:143`) and `DEFAULT_RETRY_LIMIT = 3`. `fresh_attempt(:489)` allocates `f'{wake}-retry-{n}'` directories bounded by that limit, with no delay. `attempt_disposition(:568)` allows retry only when every attempt was `RETRYABLE='retryable-no-effect'` (effect-free + transient + complete traceback :92-95); any dispatched/proposed/complete-without-settlement is `RECONCILE`.
- `src/mishe_tauftauf/native_resident.py:430-460` `_fresh_attempt` calls that allocator synchronously when an attempt directory already exists and `MISHE_NATIVE_RECOVERY_WAKE` opts in, then `run_wake:459-461` `mkdir`s the new directory immediately. On transient effect-free failure, `run_wake:528-535` releases the `*.busy` marker at once — the next wake can arrive immediately and re-enter the same path with no sleep. `time.sleep` appears only at `:278` as a short `min(.05, ...)` remainder wait for a deadline, not as retry backoff.
- Occurrence sweep for this REVISIT cell (`failed wake|fresh wake|classification`) lists only `native_recovery.py`/`native_gate.py`/prior reviews; zero hits before this for `jitter|backoff|thundering herd|Full Jitter|Equal Jitter|Decorrelated` as vocabulary — the plant bounds *count*, not *timing* or *spread*. The Wasabi SOSP 2024 `recent` REVISIT (2026-11-03, health, `native_recovery.py`) already named the broader gap (WHEN missing delay, §2.3.1/§3.1.3 `Wasabi §2 Table 2 — 17/8/10/13/12/8/2`) and proposed advisory `RETRY_BUG_TAXONOMY`/`WASABI_MISSING_CAP_*`/`retry_ratio_outlier` (unactivated, no jitter formula) — this transfer is the complementary mechanism (Brooker's concrete jitter) for that WHEN delay.
- Live-tree signal: `find -retry-` at the predecessor transfer discard (2026-11-01) found 9 `-retry-` directories, all canary/experiment, none for a live role/wake — unbounded storm unobserved, but the *shape* (immediate, clustered) remains the one Brooker shows capped backoff alone does not fix.

## What is new here

1. **Count without timing still thunders.** Brooker's simulator with OCC under contention shows `N` clients take `N` rounds linearly, and total work grows `N^2` because `N, N-1, ...` compete each round. Capped exponential backoff (`sleep = min(cap, base * 2**attempt)`) "helps a small amount" but leaves *clusters*: backoff creates gaps when no client competes rather than reducing per-round contention. Random variance (4 ms) adds only "some spreading". The plant's `DEFAULT_RETRY_LIMIT=3` + immediate fresh-attempt is the same uncapped-timing shape: three rapid attempts for one wake, and a new wake (new trigger) resets the budget, so cross-wake retries can also cluster if the upstream stays transient.

2. **Jitter as the missing operation.** "Adding jitter is a small change to the sleep function" — randomizing within the backoff window spreads the spikes to an approximately constant rate, halves call count at `N=100`, and shortens completion vs un-jittered backoff, without changing `N^2` asymptotically. Three variants are named: **Full Jitter** (`random_between(0, min(cap, base*2**attempt))`), **Equal Jitter** (keep half the backoff, jitter the rest), **Decorrelated Jitter** (`sleep = min(cap, random_between(base, sleep*3))`). Full Jitter uses least work; Equal does more work and longer; Decorrelated is close to Full but higher work. All dominate no-jitter.

3. **Why OTP MaxR/MaxT alone is not this.** The predecessor discard (2026-11-01, OTP restart intensity) bounded *aggregate count* over a time window. Brooker bounds *competition per round* via per-client randomized delay. For this plant they compose: OTP intensity answers "how many wake retries in 60s", jitter answers "when within that window they re-enter" so they do not all hit the ledger/provider at the same instant. The plant needs the latter even when total count is bounded.

4. **Boundary to Saltzer/Wasabi lineage.** Saltzer 1984 (`misread` 2026-11-03) placed correctness end-to-end (ledger/settled read-back cannot be replaced by status codes). Wasabi classified WHEN as missing cap *or* delay. This transfer does not move correctness; it supplies the *delay shape* Wasabi left abstract and Saltzer left out of scope — a pure timing decoration, not a verdict change.

5. **Limits of the transfer.** (a) The plant's retry is not a tight loop — it is a *wake-granular* allocator gated by a chat trigger/sensor, so a `sleep` inside `fresh_attempt` would be the wrong place; the natural hook is advisory delay for the *next* trigger or for a caller that opts into a timed re-enqueue, not a blocking sleep in the classifier. (b) Backoff without jitter can be worse than none for latency (Brooker: leave it off the graph). Choosing a cap/base without measuring upstream actually harms time-to-completion, hence advisory-only constants here. (c) None of the variants fix `N^2` — they halve constants.

## Application

target: src/mishe_tauftauf/native_recovery.py

Proposal to **health** (owner of `native_recovery.py`/`native_resident.py` recovery boundary; read-only lane — this review does not edit the organ; additive vocabulary, pure and revertible by single-commit revert, no new runtime until health opts in):

1. Beside `TRANSIENT_STATUS`/`DEFAULT_RETRY_LIMIT` add a **Brooker taxonomy comment** citing `M. Brooker, "Exponential Backoff and Jitter", AWS Architecture Blog 2015-03-04 (updated 2026-10-22), https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/ — capped exponential backoff still clusters (N clients, N^2 work; gaps vs spread); jitter spreads spikes to constant rate at N=100 (halves calls). Variants: Full Jitter `random_between(0, min(cap, base*2**attempt))`, Equal Jitter (keep half, jitter rest), Decorrelated `min(cap, random_between(base, sleep*3))` — Wasabi WHEN-delay §2.3.1/§3.1.3 complement`.

2. Add pure, advisory constants co-located with that comment (names illustrative, `Final`, owner chooses identifiers):

   ```python
   JITTER_BACKOFF_BASE_S: Final[float] = 0.1
   """Brooker 2015 §Full Jitter base — illustrative; plant's natural hook is inter-wake delay, not intra-classifier sleep."""
   JITTER_BACKOFF_CAP_S: Final[float] = 5.0
   """Brooker 2015 §capped backoff cap — illustrative; choose per upstream, not as hard gate."""
   JITTER_VARIANTS: Final[tuple[str, ...]] = ("full", "equal", "decorrelated")
   """Brooker 2015 three shapes; Full dominates no-jitter (equal longer, decorrelated higher work)."""
   ```

3. Add three pure helpers in the same module (no I/O, no ledger read, deterministic via injected `rand` for testability), advisory only:

   ```python
   def jitter_backoff_full(attempt: int, *, base: float = JITTER_BACKOFF_BASE_S, cap: float = JITTER_BACKOFF_CAP_S, rand: Callable[[float, float], float] = random.uniform) -> float:
       """Brooker 2015 Full Jitter — uniform(0, min(cap, base*2**attempt)). No caller in pin."""
       ...

   def jitter_backoff_equal(attempt: int, *, base: float = JITTER_BACKOFF_BASE_S, cap: float = JITTER_BACKOFF_CAP_S, rand: Callable[[float, float], float] = random.uniform) -> float:
       """Brooker 2015 Equal Jitter — temp = min(cap, base*2**attempt); temp/2 + uniform(0, temp/2)."""
       ...

   def jitter_backoff_decorrelated(prev_sleep: float, *, base: float = JITTER_BACKOFF_BASE_S, cap: float = JITTER_BACKOFF_CAP_S, rand: Callable[[float, float], float] = random.uniform) -> float:
       """Brooker 2015 Decorrelated — min(cap, uniform(base, prev_sleep*3))."""
       ...
   ```

   Mark `UNACTIVATED: no caller in running pin` until health wires an inter-wake delay (e.g., `native_resident._fresh_attempt` advisory sleep, or scheduler-level jitter) or a test repurposing `tests/test_native_recovery.py` to assert spread. Existing `classify_attempt`/`fresh_attempt`/`attempt_disposition` behavior unchanged; `tools/check_parsing.py` unaffected.

Prediction: after landing, `PYTHONPATH=src python -c "from mishe_tauftauf.native_recovery import JITTER_VARIANTS, jitter_backoff_full; print(JITTER_VARIANTS); print(0 <= jitter_backoff_full(1) <= 5.0)"` prints `('full','equal','decorrelated')` and `True`, `python3 research/field-review/field_review.py check` remains PASS (332/332 landed after this file, was 331). Observation: run those checks plus `rg -n JITTER_BACKOFF src/mishe_tauftauf/native_recovery.py` on the landed commit. Decision: keep if advisory-only and checks pass; revise base/cap if health prefers different illustrative values; revert single commit if vocabulary unhelpful — no migration debt because no caller. Complements Wasabi `RETRY_BUG_TAXONOMY` advisory without overlap (taxonomy vs mechanism).

## Limits

- Formulas rendered as images in the post; prose names are precise but numeric constants (base 0.1, cap 5.0 here) are illustrative — real upstream requires measured base/cap.
- Blocking `sleep` inside `classify_attempt`/`fresh_attempt` would violate the plant's wake-granular model; correct hook is inter-wake or scheduler jitter, not classifier I/O.
- Simulated OCC contention (10 ms mean / 4 ms variance) is not this plant's wake trigger model; storm may never materialize (live `find -retry-` empty) — mechanism is preventive vocabulary.
- SDK note: AWS SDKs already implement jitter natively — do not re-implement jitter around SDK calls; advisory here is for plant-owned fresh-attempt spacing only.
- Owner is `health` because health owns `native_recovery.py`/`native_resident.py` and the blocked-state census; field-review does not edit that file itself in this turn.
