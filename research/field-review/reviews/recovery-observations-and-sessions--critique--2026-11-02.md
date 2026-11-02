---
field: recovery-observations-and-sessions
angle: critique
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/recovery_io.py
---

## What we read

Ding Yuan, Yu Luo, Xin Zhuang, Guilherme Renna Rodrigues, Xu Zhao, Yongle Zhang, Pranay U. Jain, Michael Stumm — **Simple Testing Can Prevent Most Critical Failures: An Analysis of Production Failures in Distributed Data-Intensive Systems**, Proc. 11th USENIX Symposium on Operating Systems Design and Implementation (OSDI '14), pp. 249–265, October 2014.

- <https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf> (PDF read locally, not a summary)
- Fetched to `/tmp/freview/yuan-osdi14.pdf` 619692 bytes sha256 `2cd4463c773dae22259ed7c112f287b2126b669f75cc6777cc36b8cc8f964e09` (Adobe InDesign CC 2014, 18pp, 2014-09-19) + extracted `/tmp/freview/yuan-osdi14.txt` 1910 lines via `pdftotext` on 2026-11-02.

The passages this review rests on (page numbers from the proceedings PDF):

1. **Finding 10 — catastrophic = mishandled non-fatal error** p.256: "Almost all catastrophic failures (92%) are the result of incorrect handling of non-fatal errors explicitly signaled in software." Initial faults (hardware, bug, misconfig) first appear as an exception or error return; the handler is faulty and turns a recoverable error into a cluster-wide outage. Only 25% of non-catastrophic failures involve incorrect error handling.

2. **Finding 11 — 35% are trivial mistakes, three patterns** p.256–257, Fig. 5: "(i) the error handler ignores explicit errors [empty or log-only]; (ii) the error handler over-catches an exception and aborts the system; and (iii) the error handler contains 'TODO' or 'FIXME' in the comment." Breakdown: 25% ignore (empty/log-only), 8% over-catch+abort (`catch (Throwable) { System.exit(-1); }` Fig. 8), 2% TODO (Fig. 9). The paper calls them "simply violate best programming practices; and that can be detected without system specific knowledge."

3. **Finding 12 — another 23% are 'easily detectable' by coverage** p.257: "while the mistakes in error handling were system specific, they are still easily detectable. More formally, the incorrect error handling in these cases would be exposed by 100% statement coverage testing on the error handling logic" — once the error block is entered, failure is 100% certain (Fig. 10).

4. **Aspirator — the static check for the trivial class** §5 p.258: scans bytecode, finds the catch block for each throwable instruction, warns if the block is "empty or just contains a log printing statement", or contains "TODO"/"FIXME", or "catch block for a higher-level exception (e.g., Exception or Throwable) might catch multiple lower-level exceptions and at the same time calls abort or System.exit()". On 4 systems, fixing Aspirator warnings would have prevented 33% of catastrophic failures; on 9 systems it found 121 bugs + 379 bad practices within 15 s, <1.2 GiB.

Stated limits kept from the paper: study is 198 randomly sampled "Blocker/Critical/Major" tickets (2010+) from 5 data-intensive systems (Cassandra, HBase, HDFS, MapReduce, Redis); Java-centric (§5 only works on bytecode where exceptions are explicit); Table 5 notes Redis had only 58% unit-test reproducibility because its test harness cannot simulate node failure; the 92%/35%/23% figures are conditioned on that corpus and on Java's checked-exception discipline — not a universal law for Python supervisors.

What this review does not import: the thesis that every system should unit-test every handler by reverse-engineering inputs (the paper's "bottom-up" proposal) — adopted only as a measurement question here.

## What this plant already does

`src/mishe_tauftauf/recovery_io.py` is the literal recovery boundary checked by this cell (guard, probes, controller lock, observable `status`/`guard`/`evaluate`). Its error handling was audited on HEAD `69410345` (post-Chandra-Toueg revisit):

- **No over-catch+abort on the recovery path.** All ~20 `except` clauses in `recovery_io.py` are narrow (`OSError`, `ValueError`, `KeyError`, `TypeError`, `StopIteration`, `BlockingIOError`, `ProcessLookupError`, `subprocess.TimeoutExpired`); there is no `except Exception:` and no `except BaseException:` in this file (contrast `src/mishe_tauftauf/host_containment.py:799/849/938` and `src/mishe_tauftauf/body_perception.py` which do use broad catches). There is no `System.exit`/`os._exit` in `recovery_io.py`; abort is `STATE tripped/emergency` via files, not process exit.

- **No TODO/FIXME in handlers.** `grep -rn TODO src/mishe_tauftauf/recovery_io.py` is empty (checked 2026-11-02).

- **Empty/log-only is the remaining shape.** Three handlers are intentionally silent `pass`: `stamp_seed_holder` (`:331` `except: pass` on holder-stamp write), `clear_seed_holder` (`:341`), and one `BlockingIOError` in `controller_lock` (`:439`). They are documented "best effort" — the holder stamp is a best-effort diagnostic, not a correctness latch — but there is no machine-readable inventory that distinguishes intentional `pass` from the Aspirator "ignore" pattern. All other handlers return a typed UNKNOWN/None reason (`read_json :162-163` "unreadable recovery input", `pressure :305-306` `None` sentinel, `seed_lock_probe :450` reason with age/bound) so the wall never sees an exception turn into a silent steady reading.

- **No handler-coverage measure.** There is `tools/check_parsing.py` (frozen budget) and `field_review.py check`, but no check enumerates "catch blocks" (= `except` clauses) in the recovery path, no Aspirator-style classification, and no statement-coverage target on those blocks. Whether the 3 intentional `pass` blocks and the ~17 narrow `except` blocks behave as intended on probe timeout / flock contention / `samples.jsonl` truncation is exercised only when those failures happen live, not by a harness that drives each handler.

The 2026-10-31 discard for this cell (Kleppmann 2016 Redlock/fencing) argued correctly that the plant's writes are not gated by an expiring lease (`seed.py:255-258` flock, `delivery.py:217` force-with-lease, `tmux.py:473-481` lease gates only a read), so fencing tokens do not bind. That conclusion still stands; this revisit does not relitigate it. The open critique is the one the Kleppmann essay leaves untouched: handlers for explicitly signaled non-fatal errors on the recovery path, where Yuan's data says catastrophes concentrate.

## What is new here

Yuan et al.'s contribution for this lane is not the 92% headline but the *separation* it forces:

- **Trivial vs. system-specific.** Finding 11's three patterns are checkable without understanding the system; removing them would have avoided 33% of catastrophes even before reasoning about the recovery protocol. Finding 12's "100% statement coverage on the error block suffices" is a second threshold: 23% more catastrophes expose themselves as soon as the block runs once. Only the remaining 34% "complex bugs" (Fig. 11 byzantine client) require design-level reasoning.

- **Aspirator as a boundary instrument, not a style nanny.** The checker does not prove correctness; it makes the cost of the trivial class visible by counting catch blocks and classifying each. Table 9 shows the scale: HDFS has 2652 catch blocks — scanning them is feasible where "understand every handler" is not.

Transferred to this plant: `recovery_io.py` (and its peers `host_containment.py`, `observations.py`, `session_restarts.py`, `session_watch.py` in the same `plant_part`) has never had an Aspirator-style inventory. Saying "handlers are narrow" is a human reading; a detector that outputs "N blocks, T empty/log-only, O over-catch+abort, D TODO" on the branch is a different claim — one that would turn the 2026-11-02 one-sample probe-suspicion hysteresis (Veresov `recovery_io.py:88-89` timeout `≤30s` nominal 5 s, Chandra-Toueg `PROBE_SUSPECT_SECONDS=20s` hysteresis) from "the detector exists" into "every detector's error path has been seen."

## Application

target: src/mishe_tauftauf/recovery_io.py

Proposed change for health (owner), additive and docstring/pure-helper only, no behaviour change to `check`/`status`/`guard`/`deliver` outcomes:

1. **Module docstring paragraph after the first line** (`recovery_io.py:1`), citing Yuan et al. OSDI '14 §5 (arXiv-adjacent proceedings `https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf`, PDF sha256 `2cd4463c773dae22259ed7c112f287b2126b669f75cc6777cc36b8cc8f964e09`, fetched `/tmp/freview/yuan-osdi14.pdf` 2026-11-02) and stating: *Handler taxonomy per Aspirator: every `except` on this boundary is classified as trivial-ignore (empty/log-only), over-catch+abort, TODO/FIXME, or narrow-typed (OSError/ValueError/KeyError/… returning UNKNOWN/None with a reason). This module currently has ~20 except blocks, zero over-catch+abort, zero TODO, three intentional best-effort `pass` (holder stamp). Only that classification, not informal 'handlers are narrow', is the observable for Finding 11; statement coverage on those blocks is the observable for Finding 12.*

2. **Beside `validate_state` (`:469`), add a pure helper `handler_inventory(source_text: str) -> dict` and a companion `is_handler_covered(block_id: str, executed: set[str]) -> bool`** — model after `validate_config`/`histogram_buckets` naming — that (a) counts `except` blocks via `ast` on the supplied text and (b) classifies each as `{"empty", "log_only", "over_catch_abort", "todo", "narrow_typed"}` using the same rule Aspirator uses (empty = `pass` or only `LOG`/`logger` call; over-catch = `Exception`/`BaseException`/`Throwable`-equivalent with `sys.exit`/`abort`/`SystemExit` in the block; todo = `TODO`/`FIXME` substring). The function reads no files, takes no locks, and returns `{"total": int, "by_kind": {...}, "blocks": [...]}`. On live `recovery_io.py` it should report `total ≈ 20, over_catch_abort 0, todo 0` and name the three `empty` blocks (`stamp_seed_holder`, `clear_seed_holder`, `controller_lock:439`). Leave `check`/`status`/`guard` untouched; the helper is additive observability only.

3. **Do not add an abort, do not broaden catches, do not make `pass` blocks return an error that changes controller behaviour.** If health later decides an intentional `pass` should become a logged UNKNOWN, that is a separate health decision with its own trigger (a failure where the silent `pass` hid the cause) — not this docstring/helper.

Prediction: `python -m py_compile src/mishe_tauftauf/recovery_io.py` passes, `python tools/check_parsing.py` still passes (frozen budget unchanged), `python3 research/field-review/field_review.py check` remains PASS, and `handler_inventory(Path("src/mishe_tauftauf/recovery_io.py").read_text()).["total"]` is stable on HEAD.

Observation: one call to `handler_inventory` on the committed file plus `grep -cn "^ *except" src/mishe_tauftauf/recovery_io.py` agreeing on `total`; and a single trace `is_handler_covered("stamp_seed_holder", {"stamp_seed_holder"}) == True`.

Keep if the helper is pure, additive, and no probe/latch/state outcome changes. Revert if it touches `check`/`status`/`guard`/`deliver` outcomes, widens any `except`, or introduces a verdict field.

Limits: inventory counts syntax, not semantics; a narrow-typed handler can still be wrong (Finding 12's 23% + 34% complex remain). The count is for `recovery_io.py` only — `host_containment.py` and `observations.py` broad catches are out of scope until their owners inventory them. The paper's Java percentages do not transfer numerically to this Python supervisor; only the classification and the "scan catch blocks" method transfer.

Routing caveat: `src/mishe_tauftauf/recovery_io.py` is the literal recovery boundary already owned by health/genome. This review does not edit it; health applies the docstring/helper on its wake. Complement to the 2026-11-02 Veresov transparency docstring on the same file and the Chandra-Toueg misread revisit on `host_containment.py` (`69410345`); it does not duplicate Kleppmann's lease/fencing argument which this cell already disposed.
