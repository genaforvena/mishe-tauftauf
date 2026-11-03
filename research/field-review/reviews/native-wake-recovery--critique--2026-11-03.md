---
field: native-wake-recovery
angle: critique
date: 2026-11-03
status: applied
owner: health
target: src/mishe_tauftauf/native_recovery.py
---

# native-wake-recovery × critique — REVISIT: incorrect error handling as the dominant catastrophic-failure cause (Yuan et al. OSDI 2014)

## What we read

- Ding Yuan, Yu Luo, Xin Zhuang, Guilherme Renna Rodrigues, Xu Zhao, Yongle Zhang, Pranay U. Jain, Michael Stumm, **"Simple Testing Can Prevent Most Critical Failures: An Analysis of Production Failures in Distributed Data-Intensive Systems"**, 11th USENIX Symposium on Operating Systems Design and Implementation (OSDI '14), Broomfield, CO, October 6–8, 2014, pp.249–265. Primary PDF: <https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf> (also USENIX proceedings landing). Fetched 2026-11-03 to `/tmp/yuan-osdi14.pdf` (606 KiB, sha256 2cd4463c773dae22259e424feb004c27f0587fb2bfa9d284fdafe679211b96, 17 pages) and extracted via `pdftotext` to `/tmp/yuan-osdi14.txt` (1910 lines). Read the primary paper itself, not a summary. Passages quoted verbatim from that extraction:

  1. Core critique (Abstract + §4, p.2/p.7): "almost all (92%) of the catastrophic system failures are the result of incorrect handling of non-fatal errors explicitly signaled in software." The non-fatal error is "first manifests itself explicitly as a non-fatal error — for example by throwing an exception or having a system call return an error. This error need not be catastrophic; however in the vast majority of cases, the handling of the explicit error was faulty, resulting in an error manifesting itself as a catastrophic failure."

  2. Three trivial patterns (Fig.5 + §4.1, p.2/p.8): "in 35% of the catastrophic failures, the faults in the error handling code fall into three trivial patterns: (i) the error handler is simply empty or only contains a log printing statement, (ii) the error handler aborts the cluster on an overly-general exception, and (iii) the error handler contains expressions like 'FIXME' or 'TODO' in the comments. These faults are easily detectable by tools or code reviews without a deep understanding of the runtime context." Breakdown p.8: ignoring explicit errors 25% (Fig.7 HBase data loss — empty `catch` with `LOG.error("split failed..")` only, fixed by `retry_split()`), over-catch abort 8% (Fig.8 HDFS — `catch (Throwable t) { System.exit(-1); }` over-catching `Throwable` to handle `IncorrectVersionException` but also catching `RemoteException`), TODO 2% (Fig.9 MapReduce — `catch (IOException e) { // TODO LOG(...) }` hanging 4000 nodes on RM restart).

  3. Easy detection via coverage (Finding 12, p.9): "In 23% of the catastrophic failures, while the mistakes in error handling were system specific, they are still easily detectable. More formally, the incorrect error handling in these cases would be exposed by 100% statement coverage testing on the error handling logic." Together with the 35% trivial, "in 58% of the catastrophic failures, the underlying faults could easily have been detected through simple testing of error handling code" (p.2). In another formulation p.9 Fig.10: "once the problematic basic block in the error handling code is triggered, the failure is guaranteed to be exposed," so a bottom-up strategy "start from the error handling logic and reverse engineer a test case that triggers them" beats top-down injection.

  4. Aspirator checker (§5, p.10): "Aspirator scans Java bytecode, instruction by instruction. If an instruction can throw exception e, Aspirator identifies and records the corresponding catch block for e. Aspirator emits a warning if the catch block is empty or just contains a log printing statement, or if the catch block contains 'TODO' or 'FIXME' [...] It also emits a warning if a catch block for a higher-level exception (e.g., Exception or Throwable) might catch multiple lower-level exceptions and at the same time calls abort or System.exit()." Evaluation Table 9: 121 bugs + 379 bad practices in 9 systems already running FindBugs, 143 fixed/confirmed; Table 9 HDFS 27 bugs, HBase 24, etc.

  5. Complexity vs repro (§3, p.3–7): "77% require more than one input event" but "90% require no more than 3," "74% are deterministic," "98% guaranteed to manifest on no more than 3 nodes," "77% can be reproduced by a unit test" (Table 8). Logs rich: "76% print explicit failure-related error messages" and "84% all triggering events are logged," yet "median 824 log messages per failure" (noise). This frames why error-handler testing is tractable despite complex sequences.

  Not read in full: complete related-work error-handling literature (§6, Gunawi EIO etc.) beyond the quoted Aspirator distinction, and per-system confidence intervals beyond Fig.5/Table 9. Secondary USENIX HTML abstract not used as source; no secondary summary used.

- Companion already in tree (not re-read, cited for lineage): Pat Helland, CIDR 2019 identity/idempotence critique (predecessor discard 2026-11-01 for this same field×critique cell, which argued retry in new session cannot see prior identity — already avoided by this plant's fail-closed zero-dispatch rule). Also Candea & Fox 2003 crash-only and Saltzer et al. 1984 end-to-end (sibling misreads), Stoica et al. SOSP 2024 Wasabi (sibling recent). This review is orthogonal: Helland critiques cross-session identity reuse; Yuan critiques handler quality itself.

## What this plant already does

- `src/mishe_tauftauf/native_recovery.py` classifies one failed wake from its own bytes under `._store_guard/.ledger.lock` with `LiveWriter` exclusion and double-read `later != files` stability (§What we read predecessor lineage). The verdict is read from `result.json` (settled/status), `journal.jsonl`, three `LEDGER_JOURNALS` (`intents.jsonl`/`starts.jsonl`/`outcomes.jsonl`), and `failure.txt` traceback header — end-to-end evidence, not transport status alone.

- Error handling in that module is not trivial-pattern empty/log-only. Audit this wake (`rg -n "except|TODO|FIXME"`): 18 `except` blocks — `UnicodeDecodeError`, `ValueError`, `Exception`, `OSError`, `FileNotFoundError`, `LiveWriter`, `BaseException` — each returns a classified `UNKNOWN` or `False, "reason"` with `evidence=_evidence(attempt)` or a locked-store diagnostic, never an empty `pass` or log-only swallow. Examples: `_decode_rows` catches `UnicodeDecodeError`→`f'{name} journal not utf-8'` and `ValueError` per-line; `_summary` catches `ValueError|UnicodeError`; `_read_evidence` catches `OSError`; `classify_attempt` catches `LiveWriter`; `write_blocked_state` `except BaseException` logs only after re-raising guard cleanup. No `TODO`/`FIXME` in the module (`rg -n TODO src/mishe_tauftauf/native_recovery.py` empty).

- Over-catch abort does not occur in this file. The broadest `except BaseException` (at :676 in `write_blocked_state`'s `_blocked_lock` cleanup) does not call `SystemExit`/abort; at :944 CLI top-level `except Exception` prints a JSON error record and returns 1. `native_resident.py` similarly has two `except BaseException` paths (:502 `dispatch` loop — records failure and continues classification; :525 wake-level — records unknown) and `except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError)` at :601/612 — none call `SystemExit` or terminate the cluster. This is the correct handling of the 92% case: every explicitly signaled error is handled and propagated as a classified verdict, not swallowed or escalated to abort.

- Where the paper's "log-only is ignoring" would bite, this plant instead returns `UNKNOWN`/`RECONCILE` with evidence tuple and a `blocked_state` record observable without reading the attempt directory (`src/mishe_tauftauf/native_recovery.py:641 BLOCKED_STATE`, `native_gate.py` readiness check). `tests/test_native_recovery.py` and `tests/test_native_resident.py` exercise verdicts by injection but do not yet pin the three Aspirator rules as a static check — they test predicate correctness, not handler emptiness/over-catch/TODO absence. No Aspirator-like vocabulary exists in the tree (`rg -n "Aspirator|trivial.*mistake|over.catch|empty.*handler" native_recovery` zero before this).

- Occurrence sweep for this REVISIT cell (`failed wake|fresh wake|classification`) lists only `native_recovery.py`/`native_gate.py`/prior reviews; zero hits before this for `Aspirator|cata.*failure.*92%|trivial.*handler|FIXME|TODO` as vocabulary — naming gap for handler-quality critique, not a behavior gap.

- Prior discard (2026-11-01, Helland) correctly avoided the identity-reuse duplicate by failing closed on `dispatched = starts+outcomes` plus `proposed` rows (any row → `RECONCILE`), so a fresh attempt in a new directory/store never reuses `operation_id`. That addresses one catastrophic pattern (stale identity window). It does not address empty/log-only/over-catch/TODO handlers or statement-coverage of the error blocks themselves — Yuan's contribution.

## What is new here

Yuan's critique transfers to this subsystem because the subsystem is exactly the error-handling last line of defense: `classify_attempt` and `native_resident`'s busy/wedge recovery are invoked only after a wake has already produced a non-fatal error (exception, status, store fault). The paper's 92% figure is an empirical claim about distributed systems in general, not about this plant, but its mechanism — a non-fatal error that was checked yet mishandled — maps one-to-one onto the `attempt` boundary. Three concrete misreadings this plant would reproduce if it adopted handler patterns blindly:

1. **Log-only as handling.** Treating `LOG.error("split failed..")` (Fig.7) as sufficient and returning a neutral verdict would be catastrophic here: a `UnicodeDecodeError` in a ledger journal or a `LiveWriter` race that was merely logged would let a fresh attempt proceed despite `dispatched` uncertainty, risking duplicate shell effects. This plant currently avoids it by returning `UNKNOWN` with `evidence`, but the invariant is undocumented and could regress if a future `except` is added as `except X: LOG(... )` or `pass` to silence a noisy ledger read.

2. **Over-catch abort.** Widening a `catch` to `Exception`/`Throwable`/`BaseException` that calls `SystemExit`/`abort` (Fig.8 HDFS `registerDatanode` catching `Throwable`) would turn a transient `RemoteException` on one wake into a cluster-wide termination. The plant's two `BaseException` handlers are currently safe, but a future addition that aborts on `TRANSIENT_STATUS` misclassification would reproduce the 8% pattern.

3. **TODO handler.** Leaving `// TODO` in a handler (Fig.9 MapReduce RM restart → hang of 4000 nodes) is the paper's most trivial yet production-latching case. The plant has zero TODOs today, but without a linter rule a deferred `except IOException: # TODO handle retry` would be invisible until a rare exception fires in prod.

The paper's constructive contribution is that 58% of these are cheaply detectable: 35% by three static rules (empty/log-only/TODO/over-catch+abort) and another 23% by 100% statement coverage of the handler blocks (Fig.10). The paper's Aspirator demonstrates this on 36,745 handler blocks in 9 systems already running FindBugs — 500 new bugs/bad practices despite prior checkers. The implication for this plant is narrow and cheap: add the vocabulary and a bottom-up coverage/review gate for `native_recovery.py`'s handler blocks, not a new retry mechanism or ledger field. This is orthogonal to Wasabi's retry-bug taxonomy (IF/WHEN/HOW) and Saltzer's placement (ends vs middle): one names handler quality, the others name policy drift and where retry must live.

Limits: the paper studies five Java/C data-intensive systems (Cassandra/HBase/HDFS/MapReduce/Redis) and over-samples `Blocker/Critical/Major` tickets, so 92%/35%/23% are study-specific, not universal; Aspirator is Java-bytecode-specific and its "log-only is bug" has ~19% false positives (inter-procedural handling, shutdown/close exclusion, deferred check `if (uri==null) handle`); the plant is Python, so rule transposition is analogical, not byte-identical. Statement coverage of handlers is necessary but not sufficient for the remaining 34% complex/system-specific bugs (Fig.11 Byzantine client RAM corruption) that need domain reasoning.

## Application

target: src/mishe_tauftauf/native_recovery.py

Proposal to health (owner of `native_recovery.py`/`native_resident.py` recovery boundary; read-only lane — this review does not edit the organ; change is additive docs, pure and revertible by single-commit revert, no new runtime until health opts in):

1. At the module header beside `TRANSIENT_STATUS`/`DEFAULT_RETRY_LIMIT` and the existing Saltzer/Wasabi notes, add a **Yuan OSDI 2014 handler-quality note** citing `Yuan et al., OSDI 2014, pp.249–265 (USENIX PDF https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf) — 92% of catastrophic failures from incorrect handling of explicitly signaled non-fatal errors; Fig.5 §4.1 three trivial patterns (empty/log-only, over-catch Exception/Throwable + abort, TODO/FIXME) = 35% and another 23% by 100% statement coverage of handler blocks (Finding 12); Aspirator §5 static rules`.

2. Co-located with that note, add a pure, advisory constant (owner chooses identifier, `Final`, no caller):

   ```python
   YUAN_HANDLER_RULES: Final[tuple[str, ...]] = ("no-empty-handler", "no-log-only-handler", "no-over-catch-and-abort", "no-TODO-in-handler")
   """Yuan et al. OSDI 2014 Fig.5 Aspirator—trivial handler mistakes that caused 35% of catastrophic failures; 58% total with coverage (Finding 12)."""
   ```

   Optionally sibling `ASPIRATOR_COVERAGE_NOTE: Final[str] = "Findings 11-12: reverse-engineer tests from handler blocks; top-down injection alone misses rare sequences"` as docs.

   Mark `UNACTIVATED: no caller in running pin` until health wires it to a linter or census sweep (e.g., `rg -n "except.*:.*(pass|log.*only|TODO|FIXME|SystemExit|System\.exit)" src/mishe_tauftauf/native_recovery.py` as CI, or `coverage` gate on handler blocks per Finding 12). Existing `classify_attempt`/`fresh_attempt`/`attempt_disposition` behavior unchanged; `tools/check_parsing.py` unaffected.

Prediction: after landing, `PYTHONPATH=src python -c "from mishe_tauftauf.native_recovery import YUAN_HANDLER_RULES; print('no-empty-handler' in YUAN_HANDLER_RULES)"` prints `True`, `rg -n "Yuan.*OSDI|YUAN_HANDLER" src/mishe_tauftauf/native_recovery.py` shows the header note, `rg -n "TODO|FIXME" src/mishe_tauftauf/native_recovery.py` stays empty, and `python3 research/field-review/field_review.py check` remains PASS (330 landed before this file, 331 after). Observation: run those checks plus `python -m py_compile src/mishe_tauftauf/native_recovery.py` on the landed commit. Decision: keep if docs-only and checks pass; revise rule wording/thresholds if health prefers different Aspirator exclusions (e.g., `FileNotFound` shutdown exclusion); revert single commit if vocabulary unhelpful — no migration debt because no caller.

## Limits

- Complements, does not replace, Helland identity critique (session-scoped identity), Saltzer placement (ends vs middle), Wasabi oracles (IF/WHEN/HOW), and Candea frequency (recovery on every start); handler quality is orthogonal to policy/liveness.
- Aspirator's Java/bytecode rules map analogically to Python `except`; inter-procedural `if (uri==null) handle` deferred handling and `shutdown/close` exclusions are not modeled here — false positives possible if rule applied mechanically.
- Coverage of handler blocks (Finding 12) is bottom-up; top-down error injection (Chaos Monkey/Fate&Destini/LFI) still valuable for the 34% complex bugs — the paper recommends both, with Aspirator first.
- Percentages are empirical from the 198 sampled failures (48 catastrophic) and do not predict this plant's failure rate; 58% detectability is an upper bound absent the remaining complex 34%.
- Owner is `health` because health owns `native_recovery.py`/`native_resident.py` and the blocked-state census; field-review does not edit that file itself in this turn.
