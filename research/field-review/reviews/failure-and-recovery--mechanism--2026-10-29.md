---
field: failure-and-recovery
angle: mechanism
date: 2026-10-29
status: applied
owner: health
target: src/mishe_tauftauf/native_recovery.py
---

## What we read

**Exponential Backoff And Jitter** — Marc Brooker (Amazon). *AWS Architecture Blog*, 2015-03-04 (updated 2026-10-22, still canonical — note "After 8 years, this solution continues to serve as a pillar … Most AWS SDKs now support exponential backoff and jitter", May 2023 update) — read full text at:

- <https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/> (HTML, sim Figs. 1–13, OCC model with mean network delay 10 ms, variance 4 ms; simulator at <https://github.com/awslabs/aws-arch-backoff-simulator>)
- <https://d1.awsstatic.com/builderslibrary/pdfs/timeouts-retries-and-backoff-with-jitter.pdf> (PDF, 6 pp., *Amazon Builders' Library: Timeouts, retries, and backoff with jitter*, © 2019 Amazon Web Services, Marc Brooker — companion that embeds the mechanism in the timeout/retry discipline)

Four passages carry the operational mechanism this plant does not implement.

1. **OCC under contention is linear in time, quadratic in work — without jitter it stays clustered (Blog, "Introducing OCC" + Figs. 1–2; PDF p. 2).** N clients starting together each succeed once per round → N rounds, N² total calls. Exponential backoff without jitter (`sleep = min(cap, base * 2^attempt)` capped, Fig. 3) "helps a small amount, but doesn't solve the problem" (Fig. 4): the per-second call trace (Fig. 5) still shows clusters separated by gaps where no client competes — variance in network delay spreads slightly, but contention per round is unchanged.

2. **Full jitter spreads the clusters to a constant rate and halves the work at N=100 (Blog, "Adding Jitter", Figs. 6–9).** One-line change to the sleep (Fig. 6: `sleep = rand(0, min(cap, base * 2^attempt))`): the trace (Fig. 7) becomes approximately constant beyond the initial spike, gaps disappear, and total calls at 100 contenders drop by >50% (Fig. 8); wall time vs un-jittered backoff improves enough that the latter is "leave[n] off the graph" (Fig. 9). The mechanism is not "retry less" but "retry at decorrelated times".

3. **Three jittered variants, one ordering — none fixes N² (Blog, Figs. 10–13).** Full Jitter (above), Equal Jitter (`sleep = cap/2 + rand(0, cap/2)` where `cap = min(cap, base*2^attempt)` — "always keep some of the backoff"), Decorrelated Jitter (`sleep = min(cap, rand(base, prev_sleep * 3))` — cap grows from the last random value, Fig. 11). Measured: Full and Equal make ~same call count, Decorrelated slightly more; Equal does "slightly more work … and takes much longer" (Fig. 13); Full uses least work, Decorrelated least time — "both … substantial decrease". None changes the N² asymptotic; the win is at reasonable contention. AWS SDKs now ship standard/adaptive retry modes with these built in.

4. **Jitter is not only for retries — and retries must be at one layer, with timeouts and idempotency (PDF pp. 2–5).** Periodic work must also be jittered: clients that tick "once per minute" line up on ":00" and overwhelm the first seconds of each minute; per-second load shows spikes hidden by aggregated metrics. The fix Brooker uses on EBS/Lambda is *consistent* jitter — "same number every time on the same host" via a deterministic hash, so a race or overload is reproducible rather than random. On retries: choose a timeout from the downstream p99.9 at an acceptable false-timeout rate (e.g. 0.1%) plus network padding; cap the backoff; limit the count and "handle the resulting failure earlier in the stack"; with a 5-deep call stack, 3 retries per layer → 243× amplification on the database; best practice is to retry at a single layer, use a token-bucket throttle after the cap (AWS SDK 2016), and only retry idempotent (read-only or token-gated, e.g. EC2 RunInstances) calls. Circuit breakers are noted as modal and slow to recover — bounded retries with jitter and a token bucket are preferred.

**Limits the source states.** The blog's simulation is OCC over DynamoDB conditional writes with synthetic delay; not a proof for journal replay or store locks. The PDF warns that capped backoff without a retry limit "now all clients are retrying constantly at the capped rate"; the remedy is a count limit. Timeout selection via p99.9 fails for internet clients (add worst-case network) and for tight p50–p99.9 services (add padding). Decorrelated jitter's larger max can increase tail latency. Jitter does not make retries safe on non-idempotent APIs.

## What this plant already does

*Bounded retry with classification, but no delay and no decorrelation — the exact clustered retry the paper measures.*

`src/mishe_tauftauf/native_recovery.py` caps work by count, not by time: `DEFAULT_RETRY_LIMIT = 3` (:100), `TRANSIENT_STATUS = {408,429} ∪ [500,599]` (:99), `classify_attempt()` (:374) decides `SETTLED`/`RETRYABLE`/`RECONCILE`/`UNKNOWN` from the attempt's own `summary.json`/`journal.jsonl`/three ledger journals/`failure.txt` (`_failure_record` :200, `TRACEBACK_HEADER` :112, `_journal_effect_rows` :354). `fresh_attempt(directory, wake, *, limit=DEFAULT_RETRY_LIMIT)` (:489) returns the next `attempt-N/` directory or `None`; `attempt_disposition()` (:568) chooses `fresh-attempt` vs `reconcile` vs `settled` vs `live`. There is no `sleep`, no `backoff`, no `jitter` in any of those paths: once a wake is `RETRYABLE`, the next `fresh_attempt` is eligible on the next tick, at the same phase as every other `RETRYABLE` wake that failed against the same transient 5xx. The scheduler (`wall.tick` 30 s, `site_sync._leases` 20 s, `RENDERER_FRAME = 20+2+5 = 27 s` in `coordination/site_sync.py:35`) is periodic and un-jittered except for one host-level collector (`engine/watch-cache.json` every 60 s ±10 s jitter, `docs/model-changes.md:107`); the wake retry itself carries no `TTL`, no `idempotent` flag, and no token bucket.

*Consequence the mechanism predicts.* Under a transient 500 that makes several wakes `RETRYABLE` together, the plant produces the blog's Fig. 5 trace by construction: N fresh attempts contend for `STORE_LOCK = '.ledger.lock'` (:109) via `_store_guard()` (:303) at the same tick, exactly one `LiveWriter` (:115) classification succeeds in reading, the rest block or fail and become candidates for the next tick — linear rounds, quadratic lock attempts — and the same holds if the downstream recovers at minute-boundary: all periodic retries line up at that second.

*The plant's best nearby practice.* `recovery_policy.py`'s `transition` (:8) already carries `deadline`/`stable_seconds`/`max_gap=90` and `capacity` (:58) carries `recover_seconds`/`max_gap`, so a timed budget exists at the policy layer; but it is a deadline, not a per-retry decorrelated delay. The operator doctrine "bounded recovery" (AGENTS.md) is enforced for observability (`BLOCKED_STATE` :641, `read_blocked_state` :682, `(value, reason)` tuples) not for load shaping.

## What is new here

**The next `fresh_attempt` should not be eligible immediately — it should be eligible after a jittered backoff, and the delay should be decorrelated per wake so clustered retries spread to a constant rate, exactly as Brooker's mechanism does for OCC.**

Three ideas transfer directly, each with a line number where it lands.

1. **Capped exponential backoff does not help here without jitter — the paper's measurement says so.** Replacing "retry next tick" with `sleep = min(cap, base * 2^attempt)` alone reproduces Fig. 5: gaps with no attempts, then clusters. The plant would still produce N² lock acquisitions under a shared transient, just spaced by powers of two. The blog's simulation shows backoff alone "only … reduced slightly" (Fig. 4); the gap-free trace appears only after `rand`.

2. **Full jitter is the one-line change that halves the thundering herd; decorrelated jitter is the right default for retries, consistent jitter for the tick.** Full Jitter `sleep = random(0, min(cap, base * 2^attempt))` (Blog Fig. 6) with `base ≈ 2 s, cap ≈ 60 s` fits the plant's 27 s frame and 30 s `/ 45 s / 60 s` deadlines in `recovery.md`. Decorrelated `sleep = min(cap, rand(base, prev_sleep * 3))` (Fig. 11) needs no `attempt` count and adapts when a retry budget is exhausted and re-created — suitable for `attempt_disposition` which reclassifies each time. For periodic probes (`wall.tick`, `site_sync._leases`) the PDF's "same number every time on the same host" applies: `sleep = hash(role, host_id) % interval` so a misaligned lease is reproducible and visible per second, not washed out by randomness.

3. **Retry-at-one-layer + token bucket + idempotency + timeout are the other three legs of the same mechanism, and the plant has the hooks for each.** Retry at one layer: today both `native_recovery.fresh_attempt` and `site_sync` (5-deep stack analogue: cli → wall → site_sync → discovery) can retry; the paper says pick one — `native_recovery` is that layer, others propagate `RETRYABLE` without re-queuing. Token bucket: the AWS SDK's 2016 throttling ("retry as long as tokens, then fixed rate") is the plant's `capacity(limited=True)` (`recovery_policy.py:58`) plus `host_capacity probes` — the retry gate checks the bucket before creating `attempt-N/`. Idempotency: `EffectIntent`/`DispatchClaim` (`inference_effects.py`) should carry `idempotent: bool` so `attempt_disposition` refuses non-idempotent retries (PDF p. 4). Timeout: `observations._RENDERER_TIMEOUTS` and the `45 s/60 s` service deadlines should be chosen from downstream p99.9 as the PDF prescribes, not as fixed constants.

**Limits kept.** Jitter does not change N² asymptotically and does not fix non-transient (`UNKNOWN`/`RECONCILE`) failures. Decorrelated jitter can lengthen tail latency vs full — cap it. Consistent jitter trades burst smoothing for per-host determinism intentionally. The mechanism is not a circuit breaker (transfer review's lane) and does not replace the `DEFAULT_RETRY_LIMIT=3` bound — it spaces the three retries, it does not add a fourth.

## Application

target: src/mishe_tauftauf/native_recovery.py

**Add a jittered-backoff primitive beside `fresh_attempt`/`attempt_disposition` and gate the next retry on its expiry — additive, behind a flag/env, same evidence vocabulary, no new branch.**

1. **Add `retry_backoff()` and `retry_after()` pure functions in `src/mishe_tauftauf/native_recovery.py` beside `fresh_attempt()` (:489) and `attempt_disposition()` (:568).** `retry_backoff(attempt: int, *, base: float = 2.0, cap: float = 60.0, previous_sleep: float | None = None, jitter: str = "decorrelated", seed: int | None = None) -> float` implements the three variants the paper measures: `full` → `rand(0, min(cap, base*2^attempt))`, `equal` → `min(cap, base*2^attempt)/2 + rand(0, min(cap, base*2^attempt)/2)`, `decorrelated` (default) → `min(cap, rand(base, (previous_sleep or base)*3))`. `seed` selects deterministic consistent jitter (`hash = sha256(f"{wake}:{attempt}") % 1000 / 1000 * cap`) for periodic scheduling; when `seed is None` uses `random.uniform`. No new imports beyond `hashlib, random, math`; no new parsing-boundary debt (`native_recovery.py` is not in `parsing-policy.json`).

2. **Gate the next fresh attempt on expiry and expose `next_eligible_at`.** `attempt_disposition(attempts, wake, *, limit=DEFAULT_RETRY_LIMIT, now=None, jitter="decorrelated")` computes `attempt = len(reports)` (number of prior attempts) and `delay = retry_backoff(attempt, ...)` for the `fresh-attempt` branch; it returns `Disposition(retry_after=now+delay, ...)` alongside existing `verdict`/`evidence`, and `fresh_attempt` (or its caller in `runner.py`) refuses to create `attempt-N/` while `now < retry_after`. The `BLOCKED_STATE` written by `write_blocked_state()` (:659) carries `retry_after` and `backoff: {attempt, base, cap, jitter, delay}` in its JSON so the probe can report `UNKNOWN` with `reason: retry_backoff until={retry_after} attempt=2/3 delay=7.3s jitter=decorrelated` — same `evidence=(blocked_row_json, last_attempt_evidence)` vocabulary the breaker proposal uses. While gated, no `attempt-N/` directory is created and no `STORE_LOCK` is acquired.

3. **Wire token-bucket and idempotency checks as the paper prescribes, additively.** Before computing `delay`, `attempt_disposition` consults `recovery_policy.capacity(...).limited` (or a simple `tokens > 0` counter) and, on `limited`, returns `delay = cap` at fixed rate (PDF p. 4); non-idempotent `EffectIntent`s (future `idempotent` field) skipped for retry — the disposition stays `RECONCILE`. All default `jitter`/`base`/`cap` values live beside `DEFAULT_RETRY_LIMIT`.

4. **Consistent jitter for the tick itself (follow-up, same file).** `wall.tick` / `site_sync._leases` callers pass `seed=hash(home, role)` to `retry_backoff` so the 30 s/20 s probes spread per second — the PDF's EBS/Lambda fix for ":00" bursts — without changing semantics.

**Acceptance (deterministic, runnable from this repository):**

- `python tools/check_parsing.py` still exits 0 and `python -m py_compile src/mishe_tauftauf/native_recovery.py` passes — additive only, no new boundary path; `grep -rn backoff src/` shows only this file.
- With `MISHE_RETRY_JITTER=0` (default) the next `attempt_disposition`/`fresh_attempt` sequence for a fixture `wake` with 2 prior `RETRYABLE` attempts produces the same `Disposition` bytes as before when `now >= retry_after`, and refuses `fresh_attempt` (returns `None`) when `now < retry_after` — replay the last 3 `failure.txt` + journal fixtures through `classify_attempt`/`attempt_disposition`.
- With `seed=42` and `jitter="full", base=2, cap=30`, `retry_backoff(0..2)` each lies in `[0, min(cap, base*2^attempt)]` and is deterministic across runs; with `jitter="decorrelated"` successive delays satisfy `delay_{n+1} <= min(cap, rand(base, delay_n*3))` property checked over 1000 calls; with `jitter="equal"` delay ≥ `min(cap, base*2^attempt)/2`.
- High-contention simulation: 20 synthetic `RETRYABLE` wakes at `now=0` with `jitter="decorrelated", base=1, cap=30` show per-second creation rate approaching constant after the first spike and total `STORE_LOCK` acquisitions halved vs un-jittered `base*2^attempt` over 100 runs (mirrors Fig. 8's halving at N=100); production `BLOCKED_STATE` carries `retry_after` with second precision.
- Periodic jitter: `retry_backoff(0, seed=hash("health"))` returns the same value on every call for the same seed, different seeds spread uniformly over `[0, cap)`.

**Ownership.** `src/mishe_tauftauf/native_recovery.py` is the bounded fresh-wake recovery discipline; `health` is resolver for `native recovery, health, supervisors, watchdogs` (field `failure-and-recovery`'s `plant_part`) and owns the `DEFAULT_RETRY_LIMIT`/`TRANSIENT_STATUS` tuning the backoff spaces. The check lives beside `checks/native-recovery-optin.py`, which already gates the recovery wake.

**One thing this review does not ask for.** It does not change queue discipline, commit policy, or retry limit to minimize MTTR, and does not add a circuit breaker (that is `failure-and-recovery--transfer`'s `recovery_policy.py` breaker, routed to health as DM 108802). It only spaces the bounded retries the plant already has.

