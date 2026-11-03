---
field: native-wake-recovery
angle: recent
date: 2026-11-03
status: applied
owner: health
target: src/mishe_tauftauf/native_recovery.py
---

# native-wake-recovery × recent — REVISIT: Wasabi retry-bug taxonomy (IF/WHEN/HOW) and outlier cap/delay oracles (Stoica et al. SOSP 2024)

## What we read

- Bogdan A. Stoica, Utsav Sethi, Yiming Su, Cyrus Zhou, Shan Lu, Jonathan Mace, Madanlal Musuvathi, Suman Nath, **"If At First You Don't Succeed, Try, Try, Again...? Insights and LLM-informed Tooling for Detecting Retry Bugs in Software Systems"**, *SOSP '24, November 4–6, 2024, Austin, TX* (ACM ISBN 979-8-4007-1251-7/24/11, DOI https://doi.org/10.1145/3694715.3695971). Primary PDF fetched this wake to `/tmp/SOSP2024-retry-bugs.pdf` (667 KiB, sha256 6581cd2faa2529fc11f60d3bdfe70aa76053f0eb8d41956ab978b22c859dac66, 12 pages) via https://www.microsoft.com/en-us/research/wp-content/uploads/2024/08/SOSP_2024__Detecting_Retry_Bugs_in_Software_Systems-1.pdf (canonical DOI landing https://dl.acm.org/doi/10.1145/3694715.3695971) and extracted to `/tmp/SOSP2024-retry-bugs.txt` (2015 lines, sha256 3528e1d3c72dcb14364bea0f114999ff8cd02dca365fc8f358c951a3c1d87357) via `pdftotext` 2026-11-03. Read from the extraction (not a summary of a summary): Abstract; §1 Introduction; §2 Understanding Retry Issues (Table 1 eight apps, Table 2 70 bugs, §2.2 IF — wrong retry policy / missing mechanism, §2.3 WHEN — delay/cap, §2.4 HOW — state reset / job tracking); Listings 1–4; §3 Wasabi (§3.1.1 retry-location identification, §3.1.2 fault injection with K=1/100, §3.1.3 three oracles, §3.1.4 planning, §3.2 static IF/WHEN checks, Fig.2 GPT-4 prompts); §4 evaluation (§4.1 191 reports, 109 true bugs).

  Passages relied on (verbatim from `/tmp/SOSP2024-retry-bugs.txt`):

  1. Abstract framing: "Retry—the re-execution of a task on failure—is a common mechanism to enable resilient software systems. Yet, despite its commonality and long history, retry remains difficult to implement and test."
  2. Three root causes, equal split: "the root causes of retry-related incidents are about equally common regarding (1) IF to retry a task upon an error (36%), (2) WHEN and how many times a task is retried (33%), and (3) HOW to properly retry without leaking resources or corrupting application states (31%)."
  3. IF — coarse bundling: "IOException encompasses a subclass AccessControlException, which indicates a permission failure and should not be retried." and HADOOP-16683 must "unwrap HadoopException to differentiate non-recoverable errors from recoverable ones." Paired with IF — missed transient on upgrade: "the Zookeeper library was upgraded and would return a new transient error, KeeperException.RequestTimeout, but this change was not noticed/fixed in HBase for over one year" (HBASE-25743) and "KAFKA-12339: an internal library was modified to return a new transient exception type UnknownTopicOrPartitionException, and yet the code calling this library was not changed to retry upon this new exception."
  4. WHEN — missing delay/cap: "Sometimes, the retry may be overly aggressive with no delay between each retry attempt (10 issues). This would lead to request flooding" and "Sometimes, retry attempts are conducted endlessly, without a cap on the total number/duration of retry attempts (13 issues). Infinite retry attempts can cause jobs to hang and even application crashes due to out-of-memory errors."
  5. Non-loop retry and identification: "In about 55% of the cases, the retry functionality is implemented as a simple loop, while in 45% of cases it is implemented as a non-loop structure, either as a finite state machine or using asynchronous task re-enqueing." and "There is no dedicated retry API in any of the cases we studied." with "we find comments, log messages, variable names, and error codes to offer the clearest evidence of a retry code structure."
  6. Unit-test gap: "About 0.1%–0.5% of unit tests in these applications contain a mechanism to deterministically inject transient errors, but they only test a tiny portion of retry logic (e.g., most of them check whether the injected exception can be caught or not) and are not capable of catching those most common retry bugs" and "Retry almost never occurs during unit testing as transient errors are extremely rare."
  7. Wasabi oracles and outlier detector: Missing-cap oracle "each test-run log is examined to see if any fault injection handler has thrown exceptions for 100 times or if any unit test has run for more than 15 minutes. If so, a missing-cap bug is reported." Missing-delay oracle "checks testing-run logs to see if there were any delays between consecutive retry attempts" via sleep APIs. Outlier IF detector: "We use the application-wide retry ratio, R_E / N_E, to infer recoverability of the exception E and identify outliers: when this ratio is very close to 1 (or 0) but is not equal to 1 (or 0), Wasabi would report the outliers."

  Not read in full: §4.2–§4.3 per-application breakdown beyond Fig.3 counts, complete reference list [23][32][63], and Wasabi's CodeQL/AspectJ/GPT-4 prompt tuning ablations; illustration values (K=100, 15 min, 20 retries/10 min) treated as prototype thresholds, not universal norms. Secondary mirror https://dl.acm.org/doi/pdf/10.1145/3694715.3695971 not fetched separately.

- Limit: This review treats the paper as a recent (2024) empirical study + toolkit, not as a specification to adopt wholesale; GPT-4–aided retry-location identification is explicitly "neither sound nor complete" (paper §3.1.1) and prototype thresholds are illustrative.

## What this plant already does

- `src/mishe_tauftauf/native_recovery.py` classifies one failed wake from its own bytes: `classify_attempt()` (:374) reads `result.json` (summary), `journal.jsonl`, three ledger journals (`LEDGER_JOURNALS`), and `failure.txt` (Traceback header), under `._store_guard/.ledger.lock` with `LiveWriter` exclusion and double-read change detection (`later != files`). Verdicts at :92–95: `SETTLED` (settled True), `RETRYABLE='retryable-no-effect'` (no `dispatched`/`proposed`, not `complete` without settlement, transient, complete traceback), `RECONCILE='requires-reconciliation'` (any `dispatched` or `proposed` or `complete`-without-settlement), `UNKNOWN` otherwise.
- Transience is decided by terminal failure only (`_terminal_failure` summary fetch_ledger else last journal ledger, then `_transient`): `kind=='network'` or `kind=='status'` with `transient_status()` (`TRANSIENT_STATUS=frozenset({408,429})` plus `500<=status<=599` at :143). A deterministic rejection therefore stays UNKNOWN/RECONCILE, not retryable. This is deliberate: "Only a `retryable-no-effect` attempt authorizes a wake's recovery" (module header :50); fresh attempt `fresh_attempt(:489)` is a new `f'{wake}-retry-{n}'` directory (no ledger reuse, no replay, bounded `DEFAULT_RETRY_LIMIT=3` at :100). `attempt_disposition(:568)` allows retry only when every attempt was effect-free and transient.
- `src/mishe_tauftauf/native_resident.py` calls the verdict only on two branches: `run_wake` when attempt directory for this wake already exists, and `serve()` when `*.busy` marker present — predecessor discard noted this exceptional-branch shape vs crash-only "recovery on every start". The misread applied review (2026-11-01, health, `tests/test_native_resident.py`) already proposed a scheduled controlled-failure drill for transient-effect-free path; it does not cover IF-policy drift.
- `tests/test_native_recovery.py` exercises verdicts by injection (transient status, network kind, dispatched/proposed, settled, LiveWriter, evidence-change) but does not exercise fuzzy retry-location identification, cap/delay oracles, or outlier-ratio detection. Occurrence sweep via `field_review.py next` lists `failed wake|fresh wake|classification` hits only in `native_recovery.py`/`native_gate.py`/prior reviews; zero hits before this for `RETRY_BUG_TAXONOMY|transient bundling|R_E/N_E|missing cap.*100|missing delay.*sleep` as vocabulary (occurrence, not meaning).
- The gap this paper names is not bounded retry — the plant has that — but selective retry policy maintenance (IF) and WHEN-assurance under injected transience.

## What is new here

Four ideas from this paper are absent or weak in this plant, each bounded:

1. **IF is the largest class and fails by coarse bundling and by silent new transients.** The paper's IF examples map directly onto this plant's `transient_status`: `IOException`-as-retryable bundling `AccessControlException` (HADOOP-16580) and wrapping/unwrapping `HadoopException` (HADOOP-16683) warn that a single `except IOException: retry` is the paper's canonical IF bug — and `status in {500..599}` plus `kind=='network'` is the same kind of coarse predicate. The Zookeeper/Kafka upgrade cases (new `KeeperException.RequestTimeout` / `UnknownTopicOrPartitionException` missed for a year) warn that a static `TRANSIENT_STATUS` set drifts silently when a provider adds a transient kind. The paper's remedy for IF outliers is statistical, not manual curation: application-wide `R_E/N_E` (retry ratio per exception type) close to 0 or 1 but not exactly flags drift as the spec (§3.2.2). This plant has no per-exception retry-ratio view.

2. **WHEN bugs are not just "no bound" but missing delay and are 33% of incidents.** Missing delay (10) and missing cap (13) are distinct oracles in Wasabi (§3.1.3): cap = 100 injections or 15 min test time exceeding 20/10 min norms; delay = no `Thread.sleep`/`TimeUnit.sleep` between consecutive injections from the same coordinator. The plant's `DEFAULT_RETRY_LIMIT=3` caps fresh-attempt directories, but has no delay between fresh attempts and no exercised oracle for "retry storm" (Velida 2024) — bounded count alone does not prevent aggressive re-enqueue. Wasabi's `K=1` vs `K=100` plan (exercise post-retry HOW vs cap/delay WHEN) is a testing shape this plant does not run.

3. **Fuzzy identification beats structural search.** 45% of retry is non-loop (queue/state-machine); no dedicated retry API existed in any studied app; variable names/comments/error codes are better retry signals than control flow alone (§2.5, §3.1.1). This plant's scorer/discovery vocabulary already leans on keyword occurrence, but `native_recovery`'s own retry is identified structurally (busy marker / attempt directory), not by fuzzy publication signals — a gap when future retry moves to async enqueue.

4. **Testing must inject transience; default tests never hit retry.** Only ~10% of unit tests exercise retry-adjacent code and "retry almost never occurs during unit testing as transient errors are extremely rare" — even after fix, only 348 regression tests label retry, most shallow. The plant's `test_native_recovery.py` does inject transience, but only in-process and only for the integer/status predicate; it does not repurpose the existing `tests/test_native_resident.py` drill to run `K=100` storm or delay check, nor does it restore artificially restricted caps (paper §3.1.4 notes 10% of tests override caps to 0–2, which Wasabi resets to defaults before testing).

  Why this is not the predecessor Temporal discard: that review (2026-11-01, docs.temporal.io) rejected "resume-by-heartbeat" replay because this plant deliberately reconciles rather than replays. This paper rejects nothing about replay; its contribution is a bug taxonomy with two checkable oracles and a drift detector for the transient set itself — orthogonal to heartbeat resumption and complementary to the Candea misread drill (which exercises the verdict's liveness, not its policy correctness).

## Application

target: src/mishe_tauftauf/native_recovery.py

Proposal to health (owner of `native_recovery.py`/`native_resident.py` recovery boundary; read-only lane — this review does not edit the organ; change is additive vocabulary, pure and revertible by single-commit revert, no new runtime until health opts in):

1. Beside `TRANSIENT_STATUS`/`DEFAULT_RETRY_LIMIT` add a **Wasabi taxonomy comment** citing `Stoica et al. SOSP 2024 §2–§3 (DOI 10.1145/3694715.3695971, PDF https://www.microsoft.com/en-us/research/wp-content/uploads/2024/08/SOSP_2024__Detecting_Retry_Bugs_in_Software_Systems-1.pdf) — IF (wrong/missing retry policy, §2.2, e.g., coarse IOException bundling AccessControlException HADOOP-16580/16683, new transient missed HBASE-25743/KAFKA-12339), WHEN (missing delay §2.3.1 HBASE-20492, missing cap §2.3.2 HDFS-15439), HOW (state reset / job tracking §2.4 HBASE-20616/SPARK-27630; Wasabi oracles §3.1.3 K=1/K=100, cap 100 or 15 min, delay via sleep APIs, outlier R_E/N_E §3.2.2)`.

2. Add pure, advisory constants co-located with that comment (names illustrative, `Final`, owner chooses identifiers):

   ```python
   RETRY_BUG_TAXONOMY: Final[tuple[str, ...]] = ("IF-wrong-policy", "IF-missing-mechanism", "WHEN-delay", "WHEN-cap", "HOW-state-reset", "HOW-job-tracking")
   """Wasabi §2 Table 2 — 17/8/10/13/12/8/2 of 70 incidents."""
   WASABI_MISSING_CAP_THRESHOLD: Final[int] = 100
   """Wasabi §3.1.3 missing-cap oracle — 100 injections exceeds 20/10 min norms (§3.1.3)."""
   WASABI_MISSING_CAP_TIMEOUT_S: Final[int] = 900
   """Wasabi §3.1.3 — 15 min (900s) test timeout as missing-cap signal."""
   ```

3. Add two pure helpers in the same module (no I/O, no ledger read), advisory only:

   ```python
   def retry_ratio_outlier(*, retried: int, total: int, low: float = 0.9, high: float = 0.95) -> bool | None:
       """Wasabi §3.2.2 R_E/N_E outlier — True when ratio close to 0 or 1 but not exactly; None when total==0."""
       ...

   def is_transient_status_coarse(status: object) -> bool:
       """Advisory — notes that `transient_status` coarse range 500–599 bundles non-transient members like 501 Not Implemented; prefer per-status allowlist (cf. HADOOP-16580)."""
       ...
   ```

   Mark `UNACTIVATED: no caller in running pin` until health wires `retry_ratio_outlier` to a future discovery/census sweep (per-exception `R_E/N_E` from journals) or a test repurposing `test_native_recovery.py` with `K=100` and sleep-oracle. Existing `classify_attempt`/`fresh_attempt` behavior unchanged; `tools/check_parsing.py` unaffected.

Prediction: after landing, `PYTHONPATH=src python -c "from mishe_tauftauf.native_recovery import RETRY_BUG_TAXONOMY, retry_ratio_outlier; print(retry_ratio_outlier(retried=99,total=100))"` prints `True` (outlier near 1), `retry_ratio_outlier(retried=0,total=100)` `False` only after threshold, `python3 research/field-review/field_review.py check` remains PASS (329 landed after this file, was 328). Observation: run those checks plus `rg -n RETRY_BUG_TAXONOMY src/mishe_tauftauf/native_recovery.py` on the landed commit. Decision: keep if advisory-only and checks pass; revise helper bounds if health prefers different low/high; revert single commit if vocabulary unhelpful — no migration debt because no caller.

## Limits

- Wasabi's GPT-4 location identification is explicitly unsound/incomplete (§3.1.1); its false-positive rate is ~1 false per 1.4–2.0 true bugs (§4.1) and thresholds (100, 15 min, 20 retries) are prototype norms, not guarantees — treating them as hard gates would misclassify legitimate 100-task loops (§3.1.3 caveat).
- The outlier `R_E/N_E` detector requires a codebase-wide retry-loop corpus (`N_E` hard to define without a dedicated retry API) and is statistical — it flags "likely" IF bugs for human triage, not a verdict change; applying it to this plant's three-value verdict (SETTLED/RETRYABLE/RECONCILE) needs adaptation (counts per status vs per exception).
- Delay oracle via `sleep` call-stacks (§3.1.3) assumes synchronous sleep; async scheduling delays (paper "Remember delay might be through scheduling after interval") need separate detection — this plant's async re-enqueue path is unmodeled here.
- Composes with, does not replace, the Candea crash-only drill (liveness of the verdict) and the Temporal no-replay rationale — IF-policy correctness and WHEN-assurance are orthogonal to replay policy.
- Owner is `health` because health owns `native_recovery.py`/`native_resident.py` and the blocked-state census; field-review does not edit that file itself in this turn.
