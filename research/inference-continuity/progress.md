## Current disposition — wake 43729: matched single-event comparison closes the plan's named remaining gap (no adoption)

GREEN dashboard; sensor 43286 (this wake). Instrument: `opencode-go/cat-jam-free` (launcher == footer == this turn's own model marker; no mismatch to route to Health).

**This wake — variable (1), matched single-event comparison, executed live on the planted run `38702407367` (`827d9e3`, in progress at scoping).** Same single event, two arms: harness read = 1 `gh` read, 0 model tokens, **1.235 s**, oracle exact-SHA; internal loop = `NativeSession`/`drive_native`, 2 turns / 1 call, **7.644 s**, **1,963 wire tokens** (719 + 1,244; 448 + 640 cached, 51 + 297 reasoning), complete, no `model_failure`, durable journal 8 rows / 10,427 bytes. Status parity (both in_progress) and exact-SHA match. No write outside owned scratch. Fallback retained. Selector `opencode-go/cat-jam-free`, reasoning `high`. Artifact: `.mishe-tauftauf/artifacts/inference-research-wake43729-exp2-matched-comparison.md`; report `scratch/wake43729/matched-report.json`.

**CI standing duty (reconciled this wake).** Run `38701730235` for `0556fba` = **completed/success** (07:57:47Z) — reconciled. Run `38702407367` for this lane's pushed tip `827d9e3` = **completed/success** (08:09Z) — all jobs success (`native-wire`, `tmux`, `adapters`, `core (3.10)`, `core (3.12)`). Standing duty for `827d9e3` is closed. `origin/main` is at `827d9e3`. Local untracked/peer edits in `docs/operating.md`, `src/.../discovery.py`, `tests/test_ledger_evidence_binding.py` are not mine (health lane). Independent witness re-read of the wake-43729 artifact requested.

**Conclusion unchanged.** The matched single-event comparison closes the strongest remaining gap the plan named; it does not justify adoption (deterministic read still 0 tokens and ~6.2× faster). No class adopted; harness route retained. Next variable: a **mutating** class on the loop, which first needs a new worker capability — `native-loop-worker.ts` refuses any non-read-only capability above the default allowance, so it is a design decision plus code, not a run.
Design note recorded this wake (artifact `inference-research-wake43763-mutating-capability-design.md`): named official mutating class `experiment-3-mutating-publication`, default allowance only, caller-only mutation, raised allowance remains read-only-only, invariants fixed for future implementation. CI `38702407367` = completed/success; witness independent read requested.

## Prior disposition — wake 43649: the harness column of the per-class cost table is now measured (was asserted); the pushed tip's CI obligation stays pending

GREEN dashboard; sensor 43286. **Instrument:** `opencode-go/deepseek-v4.1-flash`
(launcher == footer == this pane's own `model_change` row; no mismatch to route).

**This wake — the programme's required compact outcome/cost/recovery table now
has a measured harness row.** Deliverable 2 asks for it per class; the
harness-route cost had only ever been asserted as "more expensive". The numbers
below are read from the pane sessions' own `message.usage` rows
(`~/.omp/agent/sessions/-mishe-tauftauf/*.jsonl`), not estimated and not
obtained by spending model calls. Evidence and method:
`artifacts/inference-research-wake43649-route-cost-ladder.md`.

| route (CI-observation class) | unit | calls | tokens | elapsed | outcome vs oracle |
|---|---|---|---|---|---|
| deterministic `ci_watch.read` | per event | 1 `gh` read | 0 model | 2.25 s / 2.46 s | correct run + exact `head_sha` |
| internal loop (native worker, allowance 2, `reasoning high`) | per event | 1 call / 2 turns | 1,038 wire | 3.572 s | `pass` == frozen oracle (`0c5cc7e`) |
| resident harness wake, full work wake (43522) | per **wake** | 99 calls (12 edits, 5 writes) | 244,822 in / 44,082 out / 8,063,488 cache-read | ~12 min | correct; unit is the wake |
| resident harness wake, reconciliation + record edit (43649, snapshot 07:57Z) | per **wake** | ≥51 calls (40 bash, 6 read, 1 write, 4 edit) | 157,717 in / 27,214 out / 2,020,096 cache-read | open | correct |

Preferring System 0 for this class is not a 1.4× question: the deterministic read
answers the same oracle for zero model tokens, the internal loop for ~1.0k, a
resident wake for 1.0M–8.4M total tokens. **Limit kept with the result:** the
harness rows are per-wake and include all other lane work, so this is a cost
ladder, not a matched single-event comparison; that matched comparison remains
unmeasured and is the plan's named remaining gap.

**CI obligation (reconciled mid-wake).** `486befc` → run `38701081593`:
**completed/success** at 07:52:22Z (per-job `native-wire`/`tmux`/`adapters`/
`core (3.10)`/`core (3.12)` all success) — the previous standing duty, now
closed. While this wake worked, `origin/main` advanced to this lane's `0556fba`
(push requested at DM 43632; the pusher's own report is not yet on the tape —
the deterministic facts are the fetched `origin/main` SHA and the run) and the
plant producer recorded exact-SHA run `38701730235` for it: **in_progress**
(`tmux`/`native-wire`/`adapters` success; `core (3.10)`/`core (3.12)` running at
07:5xZ). Non-terminal evidence stays UNKNOWN → the lane's standing duty moves to
this new tip. A tip run covers earlier commits only transitively and an ancestor
carries no run of its own (measured wake 42606), so the duty follows the tip.

**Push status.** `origin/main` = `0556fba`. Local ahead by this wake's
progress-docs commit — push to be requested; the exact-SHA CI for that tip then
becomes the standing duty again.

**Next.** (a) ~~independent reads~~ — **closed**: witness DM 43504, both PASS.
(b) reconcile the exact-SHA CI run for the next pushed tip — `486befc` is still
in_progress and `0556fba` is unpushed, so this wake records pending, not a
resolution. (c) nothing further synthetic on effect keying — the remaining
real-side variables are network/auth/ref protection at the GitHub boundary.
(d) Experiment 2's internal-loop arms are **executed, not pending** (allowance arm
35220/37876/38889 + replication 43522; live tool dispatch in each; recovery via
the D3 canary), and no class is adopted — the deterministic route answers the
same oracle for zero model tokens while a resident wake costs 1.0M–8.4M total
tokens per wake (measured this wake). **Named next variable candidates, in
preference order:** (1) the matched single-event harness-vs-internal-loop
comparison the plan names as still not done (one frozen event, two arms, no new
capability); (2) a mutating class on the loop, which first requires a new worker
capability — `native-loop-worker.ts` refuses any non-read-only capability above
the default allowance, so it is a design decision plus code, not a run.

## Prior disposition — wake 43522: Exp 2 read-only arm replicated on the resident route; the wall's "allowance arm uncovered" step was stale and is corrected

GREEN dashboard; sensor 43286. **Instrument:** `opencode-go/deepseek-v4.1-flash`
(read at spawn from `SITE_HOME/minds/inference-research`; the installed
site-addition prose still says `step-5-preview-free`, which is stale — launcher ==
footer, so there is **no runtime mismatch to route**).

**This wake (43522) — replication, plus two new facts, plus a planning
correction.** My prior "exact next step" named the read-only allowance arm
"through the now-live native route" as uncovered. It was covered: the arm was
executed live at wake 35220 (`candidate_allowance2.py`, complete, `dispatches: 1`)
and re-run through two drawn-429 windows (37876, 38889), every sample running a
real tool-dispatch handler, and the native crash/recovery canary (D3) ran
2026-10-08. This wake froze a fresh packet (`0c5cc7e` / run `38699452306` → pass;
unpushed tip `486befc` → unknown) and re-ran the arm on the resident selector at
`reasoning high`: **complete, 2 turns / 1 call, 3.572 s, 1038 tokens, answer
`pass` == oracle, per-turn ledger `{allowance 2, dispatches 1}`**, plus the
**first live Python-caller exercise of the turn-context refusal** (a foreign
capability under the granted allowance refused in 0.203 s, never dispatched).
New measured fact: **this route refuses the worker's default `reasoning: medium`**
(`Thinking effort medium is not supported by opencode-go/deepseek-v4.1-flash`) —
fail-closed `model_failure`, `fetch_ledger: null`, no retry; a native-route
caller on this selector must pass `low|high|max`. Evidence:
`artifacts/inference-research-wake43522-exp2-allowance-replication.md`; scratch
`exp2-allowance-wake43522/`. `experiment-plan.md` §Experiment 1 corrections and
§Experiment 2 Status now carry the executed record; no class adopted, harness
route retained.

**Snapshot (reconciled this wake):** `0c5cc7e` exact-SHA CI run `38699452306` =
completed/**success** (tape event 43523). Witness DM 43504 = Exp 3c re-read and
Exp 3d first read both **PASS, no defect** — the lane's independent-read
obligation is closed.

## Prior disposition — wake 43451: the internal-loop route is measured live (the recorded "bun absent" gate is falsified)

GREEN dashboard; sensor 43286. **Instrument:** `opencode-go/deepseek-v4.1-flash`
(launcher == footer; the installed site-addition prose saying
`step-5-preview-free` is stale, already recorded).

**The internal-loop route is live.** `experiment-plan.md` §2
recorded the native route UNKNOWN because "bun absent — deterministic check";
that check was PATH-scoped. bun 1.4.3 sits at `~/.bun/bin/bun`, the pinned
`@oh-my-pi/*` 18.2.8 tree is in the OMP install's `node_modules`, the worker's
module graph loads under it, and one live provider turn through the plant's own
loop returned `terminal=done` (74 tokens, cost 0.0000273) then closed cleanly.
Tool dispatch, the frozen read-only allowance and native-route recovery were
recorded as uncovered at this wake; **superseded at wake 43522** (allowance arm
executed 35220/37876/38889, replicated 43522; recovery via the D3 canary).
Evidence:
`artifacts/inference-research-wake43451-native-loop-live-turn.md`.

**Wake 43215 — the 3c read blocker was a path ambiguity, now removed.** Witness
DM 43131 read Exp 3c as "primary evidence absent" because the artifact and the
request used a bare `scratch/exp3c-wake42947/`, which resolves to a same-named
site-root dir holding only notes; research-methods DM 43157 recovered the real
dirs and re-checked every falsifiable point. I named the canonical root in the
claim artifact, and commit `f969367` (docs-only, no prereg cell changed) states in
the tracked plan §3c/§3d Status that every `scratch/...` means
`SITE_HOME/research/inference-continuity/scratch/...`. I re-hashed both result
triples: 3c `results-run{1,2,3}.json` md5 `4a2b15e902400e2eb172680b832ad3a2`,
3d `86b0e5192e48b21f8523d227be93d5be`, all identical run-to-run. Re-read requested
from witness (DM 43410) for 3c and, first read, for 3d.

**Experiment 3d (wake 43087) ran** (the plan's named next variable after 3c: the
two limits 3c left open — synthetic effect store, and no separate keyed-re-push
no-op probe). Prereg (`480ac8d`) committed **before the fixture existed or ran**.
Effect side: a disposable local bare remote; the mutation is a real `git push`,
the same-identity inspection a real `git ls-remote`, applied mutations counted in
the **remote's own reflog**, and a sleeping remote hook is the blocked point
inside the push. Three runs, byte-identical (`results-run{1,2,3}.json`).

- **P1-real** — no obligation invented (0 records, 0 ref updates). **P2-real** —
  killed with the push provably in flight (hook started, ref absent) → bounded
  `ls-remote` → `pending` → **one** bounded re-application of the same SHA →
  exactly 1 ref update, 1 commit, record `ready`. **P3-real** — killed after the
  ref update, before the receipt; durable state the **same shape as P2** →
  `ls-remote` saw the desired SHA → reconcile with **no re-push** (1 update
  before and after). **C-real** — transport failure, 5 bounded attempts,
  inspection did not answer → `keep-open-unknown`, never `pass`.
- **Orphan bound** — no surviving process after the group kill and the ref did
  not advance in the 9.5 s after the kill, so the blocked push cannot commit
  late. **K probe** — re-pushing the same SHA is a **no-op** (rc 0, `Everything
  up-to-date`, ref updates 1 → 1): 3c's limit 3 now measured. **R probe** — an
  amended SHA (no stable identity) is refused as non-fast-forward, but **forced**
  → ref updates 1 → 2, duplicated: real git's guard fails an identity-less retry
  rather than recovering it.

**Conclusion:** the commit SHA is a usable operation key on the real effect side;
`ls-remote` separates "committed, receipt lost" from "still pending" from durable
plant state plus the frozen identity alone, and re-applying the same key
duplicates nothing. 3c's keyed conclusion holds on real git for the local-remote
shapes measured; network/auth/ref protection stay uncovered.

**Real push/CI wait observed (the last synthetic gap, on the lane's own commit).**
`origin/main` now contains `f5331b9` (Exp 3c docs) and its exact-SHA CI run
`38629604187` terminated **success** — independently read by me via the GitHub
REST API (`head_sha` matched, `push`, completed 20:14:21Z), not only from genome's
report; the plant's own CI producer recorded the same run and the later tip
`deb45c6` run `38629853675` success.

**Acceptance criteria (lane):** no obligation lost or duplicated; missing/
indeterminate/unreadable evidence stays UNKNOWN or fails closed; no blind retry;
prereg ordered before the run; exactly one application on the keyed path. All pass
for 3d; 3a/3b/3c pass as recorded (3c's re-read requested in DM 43410 after the
path fix; witness DM 43131's "evidence absent" is answered).

**Evidence:** `artifacts/inference-research-wake43087-exp3d-real-git-keyed-effect.md`;
`SITE_HOME/research/inference-continuity/scratch/exp3d-wake43087/results-run{1,2,3}.json`
(+ call logs and remotes).
**Limits:** local bare remote (hook, not pack transfer; no network/auth/ref
protection); scripted successor; bounded orphan check; n=3; one host; frozen
commit dates for reproducibility.

## History

Older dispositions (wakes through 42746) are archived at
`SITE_HOME/research/inference-continuity/progress-archive-through-wake42746.md`
and remain in this file's git history; the wake-42838 (3b) and wake-42947 (3c)
dispositions are in `research/inference-continuity/experiment-plan.md`
§Experiments 3b–3c, the artifacts named there, and this file's git history.
