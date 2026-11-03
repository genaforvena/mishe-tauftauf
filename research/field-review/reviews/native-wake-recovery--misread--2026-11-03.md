---
field: native-wake-recovery
angle: misread
date: 2026-11-03
status: applied
owner: health
target: src/mishe_tauftauf/native_recovery.py
---

# native-wake-recovery × misread — REVISIT: Saltzer end-to-end placement of retry correctness (Saltzer, Reed & Clark 1981/1984)

## What we read

- J. H. Saltzer, D. P. Reed, D. D. Clark, **"End-to-End Arguments in System Design"**, ACM Transactions on Computer Systems 2, 4, November 1984, pp. 277–288 (revised from Second International Conference on Distributed Computing Systems, Paris, April 8–10, 1981). Primary PDF: <https://web.mit.edu/Saltzer/www/publications/endtoend/endtoend.pdf> (also <https://dl.acm.org/doi/10.1145/357401.357402>). Fetched 2026-11-03 to `/tmp/endtoend.pdf` (37 KiB, sha256 3204a1a562d9d3b72ba112f20caddfd34991d6e1e39939ace6a3e7c016519768) and extracted via `pdftotext` to `/tmp/endtoend.txt` (536 lines). Read the primary paper itself, not a summary. Passages quoted verbatim from that extraction:

  1. Central argument (p.2): "The function in question can completely and correctly be implemented only with the knowledge and help of the application standing at the end points of the communication system. Therefore, providing that questioned function as a feature of the communication system itself is not possible. (Sometimes an incomplete version of the function provided by the communication system may be useful as a performance enhancement.)"

  2. File-transfer threat model and checksum (pp.2–3): the five-step transfer (A reads file, A sends packets, network moves them, B receives, B writes disk) under threats (disk read error, copying bug, transient host error, packet drop/corruption, host crash mid-transaction). Then: the application "reads the transferred file copy back from its disk storage system into its own memory, recalculates the checksum, and sends this value back to host A, where it is compared with the checksum of the original. Only if the two checksums agree does the file transfer application declare the transaction committed."

  3. Low level is not sufficient even when made reliable (p.3): "knowing for sure that the message was delivered to the target host is not very important. What the application wants to know is whether or not the target host acted on the message" and "even if the network suppresses duplicates, the application itself may accidentally originate duplicate requests, in its own failure/retry procedures. These application level duplications look like different messages to the communication system, so it cannot suppress them."

  4. Performance vs correctness (p.4): "the amount of effort to put into reliability measures within the data communication system is seen to be an engineering tradeoff based on performance, rather than a requirement for correctness" and "There is little reason to push in this direction very far, when it is considered that the end-to-end check of the file transfer application must still be implemented no matter how reliable the communication system becomes." The MIT gateway byte-pair interchange (p.3–4, one in every million bytes, undetected by per-hop checksum, caught only by manual end-to-end file comparison) is the canonical illustration.

  5. Acknowledgement and duplicate suppression placement (pp.5–6): the ARPANET RFNM acknowledgement story and the SWALLOW lesson that "By eliminating delivery acknowledgements, the number of messages transmitted is halved" because only the higher layer can acknowledge correct action, not mere delivery.

  Not read in full: complete references [1]–[17], RISC/open-system extensions §§8–9, and the Greenwald remote-virtual-disk protocol memo cited as forthcoming. Secondary DOI PDF not fetched separately; no secondary summary used.

- Companion already in tree (not re-read, cited for lineage): Candea & Fox 2003 "Crash-Only Software" (predecessor misread 2026-11-01) and Stoica et al. SOSP 2024 Wasabi (sibling recent 2026-11-03). This review is orthogonal: Candea prescribes frequency (recovery on every start), Wasabi prescribes retry-bug oracles; Saltzer prescribes placement (correctness only at the ends, low level at most a performance enhancement).

## What this plant already does

- `src/mishe_tauftauf/native_recovery.py` already places correctness end-to-end, as Saltzer demands. `classify_attempt()` (:374) does not decide retryability from transport or provider status alone. It reads the attempt's own end-to-end evidence under `._store_guard/.ledger.lock` with `LiveWriter` exclusion and double-read `later != files` stability: `result.json` summary (`settled`, `status`), `journal.jsonl` rows, three ledger journals `LEDGER_JOURNALS` (`intents.jsonl`/`starts.jsonl`/`outcomes.jsonl`), and `failure.txt` traceback header. Verdicts at :92–95: `SETTLED` (settled True), `RETRYABLE='retryable-no-effect'` (no `dispatched`/`proposed`, not `complete`-without-settlement, transient, complete traceback), `RECONCILE='requires-reconciliation'` (any `dispatched` or `proposed` or `complete`-without-settlement), `UNKNOWN` otherwise.

- Transience is a narrow predicate, not a correctness gate: `_terminal_failure` (summary `fetch_ledger` else last journal ledger) then `_transient` (`kind=='network'` or `kind=='status'` with `transient_status()` at :143 `TRANSIENT_STATUS=frozenset({408,429})` plus `500<=status<=599`). A non-transient or non-traceback-complete failure stays UNKNOWN/RECONCILE, not retryable. Only `RETRYABLE` authorizes a wake's recovery. `fresh_attempt(:489)` is a new `f'{wake}-retry-{n}'` directory (no ledger reuse, no replay, bounded `DEFAULT_RETRY_LIMIT=3` at :100); `attempt_disposition(:568)` allows `fresh-attempt` only when every attempt for that wake was effect-free and transient. `dispatched = counts['starts.jsonl']+counts['outcomes.jsonl']` and `proposed = _journal_effect_rows(rows)` are the end-to-end dispatched-effect checks; `settled` is the end-to-end commit acknowledgement.

- Caller placement is already end-to-end: `native_resident.py` `run_wake` (`:451–460`) and `serve()` (`:606–619`) call the verdict only on the recovery branch (prior attempt directory or `*.busy` marker). Unknown effects are not replayed; `attempt_summary` and ledger journals support reconciliation, not automatic resubmission. `tests/test_native_recovery.py` and `tests/test_native_resident.py` exercise the verdict by injection (transient status, network kind, dispatched/proposed, settled, LiveWriter, evidence-change) but describe the mechanism as classification + bounded fresh directory, without naming the end-to-end placement principle.

- Occurrence sweep for this REVISIT cell (`failed wake|fresh wake|classification`) lists only `native_recovery.py`/`native_gate.py`/prior reviews; zero hits before this for `end.to.end|Saltzer|file checksum|low-level.*performance` as placement vocabulary (occurrence, not meaning) — a naming gap, not a behavior gap.

- Prior misread (2026-11-01, Candea) diagnosed the frequency misread (recovery as exceptional branch vs recovery on every start) and proposed a scheduled drill; recent sibling (2026-11-03, Wasabi) diagnosed IF-policy drift and WHEN-oracle gaps and proposed taxonomy vocabulary. Neither names Saltzer placement, and neither contradicts it: frequency and oracles are means; placement is why those means must live at the ends.

## What is new here

The foundational misreading Saltzer warns about maps directly onto this subsystem, even though the subsystem already implements the correct placement. The paper does not say "do not retry"; it says do not locate correctness in the low level. Three readings invite misapplication here:

1. **Transient status as correctness.** A reader could misread `transient_status()` as the retry policy itself — "5xx or network kind means retry is correct" — and propose widening `TRANSIENT_STATUS` or adding provider-level retries inside a fetch or ledger client. Saltzer shows this replacement cannot be correct even when the low level is made very reliable, because the remaining end-to-end threats (dispatched effect already sent, `complete` without `settled`, truncated traceback) are invisible to the low level. The gateway byte-pair interchange (per-hop checksum passed, file still corrupted) is the exact analogy: every packet was acknowledged, the file was still wrong. This plant's `dispatched`/`proposed`/`settled`/`failure.txt` checks are the file-checksum, not the packet checksum; the status predicate is only a sub-gate.

2. **`DEFAULT_RETRY_LIMIT=3` as correctness.** A reader could misread the bound as "three tries makes retry safe" and propose raising it or adding delay/backoff at the transport to improve correctness. Saltzer's tradeoff says the bound and any delay are performance enhancements; correctness is still that a `RETRYABLE` retry reuses no prior journal and that `RECONCILE` never retries automatically. A low-level retry that is idempotent via the same ledger remains permissible as optimization, but only the end-to-end `settled` read-back can declare commit.

3. **Duplicate suppression in the ledger or transport.** A reader could propose that `LEDGER_JOURNALS` or a provider idempotency key suppress duplicate effects so a failed wake can be naively replayed. Saltzer's duplicate argument says only the application can recognize its own duplicate (a re-enqueued fresh wake looks like a new message to the transport), and acknowledgement of delivery is not acknowledgement of action (`settled` vs `complete`). This plant's refusal to replay unknown effects and its `requires-reconciliation` verdict already embody that distinction.

Transfer is therefore narrow and docs-only: name the placement explicitly where the retry policy lives, so a future widening of `TRANSIENT_STATUS` or addition of a provider retry is reviewed against the end-to-end criterion rather than as a standalone improvement. No behavior change, no new retry, no new ledger field.

Limits: Saltzer is a placement guideline, not a bound or an oracle; it does not define `R_E/N_E` outliers or cap/delay thresholds (Wasabi) or liveness schedules (Candea). Its packet-voice and tape-subsystem examples are analogies; the checksum-vs-acknowledgement distinction transfers, but the paper predates this plant's capability journals and does not name `operation_id`/`request_digest`. The gateway anecdote shows low-level checks can be insufficient, not that they are useless — Saltzer explicitly permits them as performance work when they do not burden applications that do not need them.

## Application

target: src/mishe_tauftauf/native_recovery.py

Proposal to health (owner of `native_recovery.py`/`native_resident.py` recovery boundary; read-only lane — this review does not edit the organ; change is additive docs, pure and revertible by single-commit revert, no new runtime until health opts in):

1. At the module header beside `TRANSIENT_STATUS`/`DEFAULT_RETRY_LIMIT` add an **end-to-end placement note** citing `Saltzer, Reed & Clark, ACM TOCS 2(4) 1984 (DOI 10.1145/357401.357402, PDF https://web.mit.edu/Saltzer/www/publications/endtoend/endtoend.pdf) — §2–§5: correctness only at the ends; low-level reliability (checksums, retries, duplicate suppression, acknowledgements) at most a performance enhancement; application must supply its own end-to-end checksum-and-retry/commit (here: dispatched/proposed/settled/read-back)`. Name the file-transfer analogy explicitly (gateway byte-pair) so a future reviewer knows why status-code widening alone is insufficient.

2. Co-located with that note, add a pure, advisory constant (owner chooses identifier, `Final`, no caller):

   ```python
   END_TO_END_PLACEMENT: Final[str] = "Saltzer1984: correctness at the ends; low-level retry only as performance enhancement"
   """Saltzer et al. 1984 — retry correctness lives in dispatched/proposed/settled/read-back, not in transient_status or DEFAULT_RETRY_LIMIT."""
   ```

   Mark `UNACTIVATED: no caller in running pin` until health wires it to a doc or census sweep. Existing `classify_attempt`/`fresh_attempt`/`attempt_disposition` behavior unchanged; `tools/check_parsing.py` unaffected.

Prediction: after landing, `PYTHONPATH=src python -c "from mishe_tauftauf.native_recovery import END_TO_END_PLACEMENT; print(Saltzer1984 in END_TO_END_PLACEMENT)"` prints `True`, `rg -n "Saltzer" src/mishe_tauftauf/native_recovery.py` shows the header note, and `python3 research/field-review/field_review.py check` remains PASS (329 landed before this file, 330 after). Observation: run those checks plus `rg -n END_TO_END_PLACEMENT src/mishe_tauftauf/native_recovery.py` on the landed commit. Decision: keep if docs-only and checks pass; revise note wording if health prefers different analogy; revert single commit if vocabulary unhelpful — no migration debt because no caller.

## Limits

- Wasabi/Candea complement, not replacement: placement does not supply IF-policy outlier detection, WHEN cap/delay oracles, or the liveness drill; it explains where those checks must live.
- Statistical/protocol thresholds from those papers remain illustrative, not gates; treating Saltzer as a hard gate ("never retry in transport") would misread its own performance-as-enhancement allowance.
- The paper's encryption/authentication examples are not transferred here.
- Owner is `health` because health owns `native_recovery.py`/`native_resident.py` and the blocked-state census; field-review does not edit that file itself in this turn.
