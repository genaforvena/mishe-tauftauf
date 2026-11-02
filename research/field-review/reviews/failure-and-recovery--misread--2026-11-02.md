---
field: failure-and-recovery
angle: misread
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/native_recovery.py
---

## What we read

**Why Do Computers Stop and What Can Be Done About It?** — Jim Gray (Tandem Computers). Technical Report 85.7, June 1985, Revised November 1985, PN87614, 46 pp. — read full text at:

- <https://web.archive.org/web/20240101000000/https://www.hpl.hp.com/techreports/tandem/TR-85.7.pdf> (archived PDF, 903 KB, 46 pages, retrieved 2026-11-02; `pdftotext` 4654 lines, local copy `/tmp/gray85_archive.pdf`)
- Original: <https://www.hpl.hp.com/techreports/tandem/TR-85.7.pdf> (HPL; 2026-11-02 returned 403/SSL timeout, hence the archive)
- Also reprinted as: Jim Gray, *Why Do Computers Stop and What Can Be Done About It?*, Tandem TR 85.7

Four passages carry the misread this plant makes. All line references are to the extracted text `/tmp/gray85_archive.txt`.

1. **Soft faults dominate, and retry is the remedy — Abstract; § "Software Faults Are Soft, the Bohrbug-Heisenbug Hypothesis" p. 17 (lines 2583–2681):** "I conjecture that there is a similar phenomenon in software — most production software faults are soft. If the program state is reinitialized and the failed operation retried, the operation will usually not fail the second time." And: "If you consider an industrial software system which has gone through structured design, design reviews, quality assurance, alpha test, beta test, and months or years of production, then most of the 'hard' software bugs, ones that always fail on retry, are gone." This is stated as a conjecture, not a census, and explicitly scoped to production-hardened code.

2. **Bohrbug vs Heisenbug — definitions p. 17–18 (lines 2746–2786):** "Heisenbugs that go away when you look at them … Bohrbugs, like the Bohr atom, are solid, easily detected by standard techniques, and hence boring. But Heisenbugs may elude a bugcatcher for years … This is analogous to the Heisenberg Uncertainty Principle in Physics." The names are not taxonomy for taxonomy's sake — they name *observability under re-execution*: a Heisenbug disappears when the program is reset and retried; a Bohrbug fails deterministically on the same input and state.

3. **One measurement — spooler fail-fast retry, p. 18 (lines 2795–2830):** "The spooler is constructed as a collection of fail-fast processes. When one of the processes detects a fault, it stops and lets its brother continue the operation. If the brother also fails, then the bug is a Bohrbug rather than a Heisenbug. In the measured period, one out of 132 software faults was a Bohrbug, the remainder were Heisenbugs." And the MVS/XA citation [Mourad] that follows: functional recovery routines that retry soft faults show the same pattern. The 131/132 figure is a single subsystem over one interval — the paper warns "A poll yields nothing … This is difficult … The one experiment I did" — not a universal constant.

4. **Fail-fast + process-pair + transaction as the handling — p. 16–17 (lines 2352–2511):** "The keys to this software fault-tolerance are: * Fault containment through fail-fast software modules. * Process-pairs to tolerate hardware and transient software faults. * Transactions …" And: "Before developing the next step in fault-tolerance, process-pairs, we need to have a software failure model." The retry claim is inseparable from the containment claim: fail-fast bounds the fault, the pair provides a fresh state to retry in, and the transaction makes the retry safe. Without those, "retry" is just repeated execution in the same corrupted state.

The paper's stated limits this review keeps: the fault-tolerance it describes assumes fail-fast modules, process-pairs, and transactions — without them the Heisenbug claim does not transfer. The 1/132 figure is one spooler, not a law. Administration and operations dominate outages (Table on p. 12, 42% administration, 25% software), so even perfect software retry leaves the largest cause untouched. And Gray's "reinitialize and retry" is a process-pair takeover in a duplexed fault-tolerant OS, not a loop that re-reads the same journal directory three times.

## What this plant already does

*The plant classifies retryability by HTTP status and by whether bytes were admitted, not by whether the fault is a Heisenbug.*

`src/mishe_tauftauf/native_recovery.py:92-100` defines four terminal words — `SETTLED`, `RETRYABLE`, `RECONCILE`, `UNKNOWN` — plus `TRANSIENT_STATUS = frozenset({408, 429})` extended to `500 <= status <= 599` (`transient_status` :143-146, `_transient` :267-275) and `DEFAULT_RETRY_LIMIT = 3` (:100). `classify_attempt()` (:374-448) walks a decision tree that turns failure into a kind of stop without asking whether the fault would disappear on re-execution:

- `if settled: return Attempt(SETTLED, ...)` (:422-425) — clean stop, no recovery;
- `if dispatched or proposed: return Attempt(RECONCILE, ...)` (:426-430) — effect admitted (`starts.jsonl`/`outcomes.jsonl` or `_journal_effect_rows` counting `proposal`/`tool_result` :354-358) must be reconciled, never retried;
- `if not transient: return Attempt(UNKNOWN, ...)` (:435-440) — terminal failure whose status is not transient and whose `failure.txt` completeness (`_failure_record` :200-220, `TRACEBACK_HEADER` :112) decides `UNKNOWN`;
- otherwise `return Attempt(RETRYABLE, ...)` (:446-448) — only when nothing was dispatched, the failure was transient, and `failure.txt` is a complete traceback.

In Gray's terms this is a misreading: "transient" here means "HTTP 408/429/5xx," not "soft/Heisenbug — goes away when state is reinitialized and the operation is retried." A deterministic bug that returns `500` on the same input is a Bohrbug and will fail again, but the classifier marks it `RETRYABLE` and spends one of three attempts on it. Conversely a soft race that loses a semaphore and returns `400` or truncates `failure.txt` is a Heisenbug that would succeed on retry, but the classifier marks it `UNKNOWN`/`RECONCILE` and never retries it. The HTTP status is the wrong signal.

*The plant counts attempts; Gray's plant pairs processes and bounds faults.*

`fresh_attempt()` (:489-502) returns `Path(directory) / f'{wake}-retry-{number}'` for `number in 1..limit` or `None`. `attempt_disposition()` (:568-622) chooses `live`/`settled`/`reconcile`/`fresh-attempt` from that count. There is no process-pair — the retry reuses the same attempt directory shape, the same `STORE_LOCK = '.ledger.lock'` (:109), the same `_store_guard()` (:303-332) that reads the failed writer's own `failure.txt` through its own lock. Gray's Heisenbug disappears because "the program state is reinitialized and the failed operation retried" *in a fresh process* with fail-fast containment; here the operation is retried by re-reading the same bytes that the failed writer left behind. The transaction analogue exists (`LEDGER_JOURNALS` :104-108, `DispatchClaim`/`EffectOutcome`) but is used to decide "was an effect admitted?" not "is this retry safe?" The 1/132 figure is never tested: the plant has three blind retries, not a Bohrbug detector.

*The 2003 crash-only review on this same cell kept the same status-code split.*

The landed `failure-and-recovery--misread--2026-10-29` (Candea & Fox 2003) correctly diagnosed the internal switch and the counted-budget-vs-lease gap, and proposed collapsing stop to crash and externalizing the switch. It left intact the `TRANSIENT_STATUS` taxonomy it inherited. This revisit does not contradict it — it names the *second* misread on the same file: even with an external switch, a retry policy that keys on HTTP codes replays Bohrbugs and discards Heisenbugs.

## What is new here

**The misread is treating HTTP transient as Heisenbug, and treating "three attempts" as "process-pair retry."**

Gray's conjecture is precise: in production-hardened code, most residual software faults are Heisenbugs — races, limit conditions, transient device faults, lost interrupts — that succeed when retried from a quiescent state, while Bohrbugs — deterministic logic errors that always fail on the same input — are rare because testing has already removed them. The spooler measurement (1 Bohrbug in 132) and the MVS/XA recovery-routine citation are the only numbers, and the paper hedges them. The operational consequence is not "retry everything three times" but "retry *once* in a fresh state; if it fails again the same way, it is a Bohrbug — stop retrying and reconcile."

Three ideas transfer directly, each with a line number in this repository where it would land.

1. **Heisenbug is not a status code; it is observable non-repeatability.** A failure that returns `500` twice with the same traceback signature is a Bohrbug; a failure that returns `500` once and then succeeds, or that returns different signatures, is a Heisenbug. The plant's `classify_attempt` already has the bytes to observe this — `failure.txt` last frame, `_terminal_failure` summary, journal rows — but never compares attempt N to attempt N-1.

2. **One retry is the experiment; the second identical failure is the diagnosis.** Gray's process-pair logic is: fail-fast, pair takes over, retry; if the pair also fails, Bohrbug. The plant's `DEFAULT_RETRY_LIMIT=3` spends three attempts without asking whether the second failure was identical to the first. The measurement says the answer matters — 131/132 times the retry would have succeeded, 1/132 times it failed identically and should have been reconciled immediately rather than burned through three attempts and load.

3. **Fail-fast containment is a prerequisite, not optional.** The spooler works because the faulty process stops and its brother continues from a known state. The plant's `_store_guard`/`_writer_active`/`LiveWriter` (:303-352) already names the containment idea, but `classify_attempt` still trusts the failed writer's `failure.txt` completeness as evidence. A fail-fast discipline would make a truncated `failure.txt` or a held `STORE_LOCK` itself the verdict (`UNKNOWN`/`RECONCILE`), not a parse error that flips `RETRYABLE` vs `UNKNOWN`.

**Limits the proposal keeps from the source.** The empirical base is one spooler subsystem and one MVS/XA citation — the 1/132 figure is not a universal Bohrbug rate and says nothing about this plant's wake/journal workload. Gray's fault-tolerance assumes duplexed hardware, fail-fast OS, and transactions; this plant has none of those and cannot claim their MTBF boosts (hardware MTBF ×5–100 with ECC/retransmission, p. 12). Administration/operations dominate outages — retrying software Heisenbugs faster does not address the 42% administration share. The paper does not give a Bohrbug detector algorithm; signature equality is a heuristic with false positives (same last frame, different root cause) and false negatives (Bohrbug with varying stacks). And "reinitialize and retry" in 1985 meant a process-pair takeover, not a directory `attempt-N/` — the mapping is an analogy, not an implementation proof.

## Application

target: src/mishe_tauftauf/native_recovery.py

**Add a Bohrbug detector beside `classify_attempt`/`attempt_disposition` — classify the *second* failure by its repeatability, not by its HTTP code, and stop blind retries on a repeated signature. Additive, behind a flag, no change to the four verdict words.**

1. **Add a failure signature beside `_failure_record` (:200) and `failure.txt` handling.** New helper `_failure_signature(failure_data: bytes | None, terminal: dict | None) -> str` that hashes the last frame of `failure.txt` (or `terminal`'s `upstream_failure` dict when `failure.txt` is absent) into a short stable string, e.g. `sha1(last_tb_line + terminal_status)[:12]`. The signature is evidence, not a verdict — it is the observable that lets the next attempt answer "same bug or different bug?" This lives beside `_evidence` (:361) and is included in `Attempt`'s evidence tuple when present.

2. **Compare attempt N to attempt N-1 in `attempt_disposition` (:568).** After the existing `RETRYABLE` check, when `len(reports) >= 1` and the latest `Attempt` is `RETRYABLE` or `UNKNOWN`, compute `sig_current` (latest attempt's signature) and `sig_prev` (previous attempt's signature from `_report`/`wake_attempts` :772-815). If `sig_current == sig_prev` and both are non-empty, return `Disposition(RECONCILE, evidence=..., reason="bohrbug: identical signature on retry")` instead of `RETRYABLE` — the fault survived a reinitialized retry, so by Gray's definition it is a Bohrbug and the plant should reconcile, not burn a second or third `fresh_attempt`. If signatures differ or `sig_prev` is absent, keep the current `RETRYABLE`/`UNKNOWN` outcome — a Heisenbug that moved is still retryable. Gate the entire branch on `MISHE_NATIVE_BOHRBUG_DETECT=0/1` (default `0`).

3. **Make fail-fast explicit: truncated `failure.txt` or held `STORE_LOCK` is not `RETRYABLE`.** Today `_failure_record` completeness flips `RETRYABLE` vs `UNKNOWN` (:441-445). With the flag on, a truncated `failure.txt` combined with a missing `starts.jsonl` flush is scored as `UNKNOWN`/`RECONCILE` evidence ("writer did not fail fast"), and a `LiveWriter` state defers classification to `live` rather than peeking at partial bytes — the same discipline Gray names "fail-fast software modules have small …" (p. 16). This is a two-line change in `_transient`/`classify_attempt`'s `complete` branch.

4. **Preserve the existing API and the Candea branch.** `classify_attempt`/`attempt_disposition`/`fresh_attempt` signatures do not change; `SETTLED`/`RETRYABLE`/`RECONCILE`/`UNKNOWN` stay the four words (:92-95); `TRANSIENT_STATUS` and `DEFAULT_RETRY_LIMIT=3` remain the default gate when the flag is off. The crash-only external switch proposed in the 2026-10-29 review and the retry-budget/jitter proposed in the 2026-11-02 `failure-and-recovery--recent` review are orthogonal — the Bohrbug detector composes with both: budget says *whether* the system can afford a retry, Bohrbug says *whether* this fault would benefit from one.

**Acceptance (deterministic, runnable from this repository).**

- With `MISHE_NATIVE_BOHRBUG_DETECT=0` the existing `classify_attempt`/`attempt_disposition`/`fresh_attempt` sequence for a fixture wake produces the same `Attempt`/`Disposition` bytes as before (replay the last 3 `failure.txt` + journal fixtures through `classify_attempt`; `python tools/check_parsing.py` stays green — `native_recovery.py` is not a boundary file in `parsing-policy.json`, and the new symbols are additive).
- With `MISHE_NATIVE_BOHRBUG_DETECT=1` and a synthetic wake with two attempts whose `failure.txt` last frames are identical (`ValueError: x` at same file:line) and whose `terminal.status=500`, the first `attempt_disposition` is `RETRYABLE` (Heisenbug hypothesis — retry once), the second is `RECONCILE` with `reason` containing `bohrbug` and `evidence` containing the signature — no `attempt-3/` is created. Today the second is still `RETRYABLE`.
- With `MISHE_NATIVE_BOHRBUG_DETECT=1` and two attempts whose signatures differ (different last frames, or `500` vs `429`), the second `attempt_disposition` stays `RETRYABLE` (Heisenbug moved) and `fresh_attempt` creates `attempt-3/` — the detector does not suppress non-identical retries.
- With a synthetic attempt whose `failure.txt` is truncated (no `TRACEBACK_HEADER`) and whose `starts.jsonl` was not flushed, `classify_attempt` does not return `RETRYABLE` due to `transient=True` alone — it returns `UNKNOWN`/`RECONCILE` with fail-fast evidence, regardless of `transient_status`.

**Ownership.** `src/mishe_tauftauf/native_recovery.py` is the bounded fresh-wake recovery discipline consumed by the `health`/`genome` life-support slice; `health` is the resolver for `native recovery, health, supervisors, watchdogs` (field `failure-and-recovery`'s `plant_part`) and owns `recovery_io`'s probe sampling. The flag `MISHE_NATIVE_BOHRBUG_DETECT` lives beside `MISHE_NATIVE_RECOVERY_WAKE` checked in `checks/native-recovery-optin.py`.

**One thing this review does not ask for.** It does not propose duplexing the host, rewriting journals as Postgres transactions, or fixing administration-caused outages — those are the paper's §3–4 and Table p. 12 concerns, and they are plant-wide changes. It only asks that the wake's bounded retry observe Gray's one distinction the plant currently ignores: whether the fault that just failed is the same fault that failed before.
