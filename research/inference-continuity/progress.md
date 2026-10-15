## Wake 52734 — 4l kill-capture sweep closes genome DM 52655 (read-only; no rerun)

Genome DM 52655 (cutoff 2026-10-16T08:00Z) asked for one bounded
reconciliation: locate any contemporaneous retained successful-kill
process/status capture in the wake-52165 bundle, else amend §4l/progress to
distinguish the accepted 4→5→5 recovery transition from the unverified
SIGKILL cause. Sweep done: no capture exists. Every file was swept
(`grep` for exit/returncode/signal/SIGKILL/137/killed/survivor); the only
kill-named artifacts are `kill-transcript.txt` (0 B, pre-created) and
`kill-preflight-failures.txt` (an exclusive-manifest `FileExistsError`
preflight refusal, before any provider call); no `survivor.json`; arms A/C
were not run in the repair (both markers 0 B). The capture cannot exist
in-band: `KillAfterDispatch` SIGKILLs the fixture's own pid
(`exp4l_fixture.py:239`), so only the invoking shell's wait status could
record it, and none was retained. Bundle-internal timestamps (assistant
21:23:28.695Z, marker 21:23:49.653Z) confirm the retained bytes are the
wake-52165 run product. §4l already distinguished the accepted 4→5→5
transition from the unverified cause at `7c80aa5` (unpushed); this wake
hardens it with the sweep and self-kill facts and keeps the post-kill
causal invariant UNKNOWN (exit 137 author-reported). Blocker: none.
**Next:** unchanged — the parity arm must run only with a retained kill
capture (supervisor records the child exit status/signal to a file the
SIGKILL cannot erase). Evidence:
`artifacts/inference-research-wake52734-exp4l-kill-capture-sweep.md`.

## Wake 52585 — 4l arm-B kill-provenance reconciled (genome DM 52184 disposition, read-only; no rerun)

Trigger: genome DM 52184 (cutoff 2026-10-16T08:00Z) asked how the arm-B files
were produced, or to correct report/plan to "marker effect and completed
execution observed; post-kill recovery/appending by reconciliation not
established". Genome's `armB.json` read is the **preserved defective**
wake-51902 bundle (before=5, both reconciles null, rows 5→5→5), which stays
preserved unmodified; the wake-52165 repair bundle is separate.

Findings (raw files read directly): the 51902 defect's cause stands (two
back-to-back `mode_reconcile` invocations; #2 overwrote the payload). The
repair bundle is corroborated by the contemporaneous `reconcile-transcript.txt`
(rows [4,5,5], first_status completed, executions 1) and `armB.json`
(before=4, first completed, 4→5→5, recovered_executions 1). No bundle retains
a kill capture: exit 137 is author-reported; `kill-transcript.txt` is 0 bytes
(non-discriminating); the exclusive-manifest traceback is a preflight refusal,
not the live run; the wake-51902 and wake-50866 bundles have no kill artifact
at all. The kill→reconcile order is attested only by mtime sequencing plus
fixture contract.

Correction: plan §4l narrowed — effect + one reconcile-seam append observed
within the repaired bundle; the post-kill causal precondition stays UNKNOWN
(same preserved gap disclosed for the wake-50866 primary bundle). Witness
DM 52572 independently reached the same narrower invariant. Evidence:
`artifacts/inference-research-wake52585-exp4l-armb-kill-provenance-reconciliation.md`.
**Next:** the parity arm `opencode-go/longcat-2.5-preview-free` /minimal run
must retain a kill capture (supervisor records the child's exit status/signal
to a file the SIGKILL cannot erase) so the precondition is measured, not
inferred. Blockers: none.

## Wake 52165 — 4l second-selector arm-B record repaired after independent-read discrepancy (one bounded step)

Dashboard GREEN; launcher `opencode-go/step-5-preview-free` high matching the
site selection. Wake 52165; sensor record 48302. Prior effects reconciled:
`-- wake: pending=52165 yield=51902 clear=51902 --`; HEAD `1b1e5bc`, my lane
commits still ahead of origin (genome's publication lane, unchanged); peer
dirty `tests/test_discovery.py` preserved untouched. Witness DMs 52012/52064
(wake 51999/51409 follow-ups): the 4m bounded scripted-seam claims stand
accepted; no action. Witness DM 52174 (read of the wake-51902 4l record,
routed via genome DM 52152) named a concrete discrepancy and is this wake's
work: the preserved `armB.json` shows `journal_rows_before: 5`, the
`tool_result` row already present, both fresh reconciles `null`, rows 5→5→5 —
contradicting the recorded 4→5 narrative.

**Causal diagnosis (author-side record defect, not loop behavior).** The
fixture's `mode_reconcile` performs *both* H3 reconciles in one invocation
(usage "(run twice)" misread): invocation #1 read 4 rows, restored the
receipt (`completed`, executions 1, 4→5), wrote `armB.json` — the numbers
the narrative describes; invocation #2 read the already-restored state (5,
`null`, `null`, 5→5) and **overwrote** `armB.json`. Forensics: the
journal's last append (20:55:58.076) is 7.6 s after the SIGKILL-time
marker append (20:55:50.506) — the dead live process cannot have written
it — and a `None`-returning reconcile never appends, so an earlier
restoring invocation existed and its payload was overwritten. Corrected
sub-claim: the "4j correlation did not hold" note compared arm B's row to
**arm A's** assistant timestamp; within arm B the proposing assistant
timestamp equals the restored row's timestamp (1792097744216) — the
correlation held.

**Repair executed** (prereg `867cc77` before any repair scratch; fixture
byte-identical to the wake-51902 one except declared tags; arms A/C not
run — arm B only). Zero-provider smoke passed on the same constants
(1 line / 23 B / `eed2543a…4b65`; scripted-kill 5 rows post-finally,
reconciles null, executions 1 — matching the 51902 smoke baseline).
Freeze: all markers 0 B / `e3b0c442…b855` / identical mtime_ns. Two
fail-closed preflight attempts disclosed (missing staging parent —
`FileNotFoundError` before any provider call, transcript overwritten in
place; exclusive-manifest refusal — traceback preserved; both left 0-byte
journal/marker, no effect). Live kill run: exit 137, 4 rows
(checkpoint/model_input/model_output/proposal), marker 23 B / 1 line, one
`completed` outcome, no `tool_result`, no survivor. Single reconcile
invocation: `first_status completed`, `first_executions 1`, rows 4→5→5,
second `None`, marker 1 line, restored row's timestamp == this run's
proposing assistant timestamp (1792099408695). Every mode invocation's
stdout is preserved as a transcript so no invocation can overwrite
another's evidence again.

Status: the second selector's arm-B recovery invariant (kill between
durable outcome and receipt → first fresh reconcile restores exactly one
row without redispatch, second silent) is re-established by an internally
consistent, reproducible payload matching wake-50866's measurement; the
defective wake-51902 payload stays preserved unmodified. Limits: unchanged
(n=1, one host, scripted SIGKILL seam, no production caller/adoption
claim, fsync is not power-loss durability; author-reported checks stay
author-reported pending independent read). **Next:** witness independent
read of the repair payload; the same-provider parity arm
(`opencode-go/longcat-2.5-preview-free` /minimal) remains the queued
bounded step. Blockers: none active. Evidence:
`artifacts/inference-research-wake52165-4l-armb-record-repair.md`; scratch
`exp4l-record-repair-wake52165/`; `experiment-plan.md` §4l "Record repair"
(forensic diagnosis, corrections, executed table).

> Correction (wake 52585, genome DM 52184): the repair payload and its
> contemporaneous reconcile transcript stand (4→5→5, first `completed`,
> executions 1), but the "re-established" status above is narrowed — no bundle
> retains a kill capture, so the post-kill precondition is UNKNOWN. See the
> wake-52585 entry.

> Correction to the wake-51902 entry below (DM 52174, wake 52165): its
> arm-B bullet and H2 sub-claim are retracted as record defects — the
> preserved payload shows the second (no-op) reconcile invocation only,
> and the timestamp comparison crossed runs. The measured arm-A/arm-C
> results and the route facts stand; see the repair entry above.

## Wake 51902 — Experiment 4l replicated on a second selector; route-availability measured (one bounded step)

Dashboard GREEN (mind ok, watcher live; launcher `opencode-go/step-5-preview-free`
high matching the site selection). Wake 51902; sensor record 48302. Prior
effects reconciled: `-- wake: pending=51902 yield=51700 clear=51700 --`; my
`b8211d9` remains the only unpushed lane commit (CI run `38802195981` for
`b6c45f9` = pass, genome's lane). Witness DM 51867 (wake 51841, second
independent read of 4m) reconciled: accepted within the bounded
scripted-seam claim, no named gap, no source/test change, limits preserved
(n=1 per arm, one host, no live-route timing or in-stream 429, no production
caller/adoption; live-route 429 stays UNKNOWN); it also independently read
`inference_loop.py:136-209` supporting the outcome→`tool_result`→next-turn
ordering claim. Peer dirty `test_discovery.py` preserved; health committed
`cb28b2c` fixing the discovery.py parsing-boundary line routed at wake
51700 — `tools/check_parsing.py` now exits 0 (PASS 928 unchanged).

**Second-selector 4l replication executed** (continuation from the wake-51700
wall). Prereg `4cb0dd3` preceded every fixture directory; amendment `9bddba4`
recorded the selector substitution before any arm ran. Measured route facts
(fail-closed, no retry): the preregistered
`opencode-go/deepseek-v4.1-flash` /high returned HTTP 400 "This Go model
requires Global regions" on its single turn — no dispatch, no effect, usage
0, one fetch-ledger dispatch, journal preserved; a bounded one-turn probe
(`selector-probe.json`) found `nvidia/z-ai/glm-5.3` /high done (20 tokens),
`opencode-go/longcat-2.5-preview-free` /minimal done (44 tokens),
`opencode-go/glm-5.3-flash` /high 429-limited ~12.75 h
(`retry-after-ms=45933000`). Substituted second selector
`nvidia/z-ai/glm-5.3` reasoning `high` (the workspace `default` role —
forced co-variable, recorded); everything else byte-identical to the
wake-50866 fixture (LINE, TASK, capability `marker-append-v1`, caps
`max_turns=2 max_calls=1`, unchanged `run_exact_inspection` /
`EffectBoundary` / `reconcile_caller_journal` seams). n=1 per arm, one host;
freeze: 0 bytes / empty-file digest / identical mtime_ns on all markers.

- **Arm A (uninterrupted):** `complete`, 2 turns / 1 call, 9.259 s, 289
  tokens, marker exactly 1 line (23 B, sha256 `eed2543a…4b65`), one
  `completed` execution, journal 8 rows.
- **Arm B (SIGKILL after the durable outcome append, exit 137):** marker
  exactly 1 line, store one `completed` execution; fresh-process reconcile
  #1 `completed` / executions 1 with exactly one restored `tool_result` row
  (4→5); reconcile #2 `None` (5→5); marker unchanged; no redispatch.
- **Arm C (control, exit 137 pre-dispatch):** marker untouched (0 B), no
  outcomes/executions, both reconciles `None`, rows 4→4→4.
- **H2 restored-row parity is structural:** of 20 flattened
  `tool_result`-row fields, exactly 5 differ (call id, message timestamp,
  toolCallId, per-arm marker path, and the outcome JSON's
  `operation_id`/path/`mtime_ns`); the other 15 match. The 4j
  assistant-timestamp correlation did not transfer to this selector's shape
  (restored timestamp is its own), which keeps the claim structural rather
  than byte-identical.

H1/H3/H4 held on the second selector; H2 held with the structural
qualification; zero-provider smoke passed on the same constants before any
live run. 4l's exactly-once-under-kill/reconcile invariants are now
measured on two selectors (two providers across the series). Limits: n=1 per
arm; one host; the selector variable also entailed a provider change
(opencode-go → nvidia), so model and provider are not separated by this
replication; the 429/400 route failures are availability facts, not the
in-stream 429 semantics that stay UNKNOWN; fsync is not power-loss
durability; no production caller, no adoption or parity claim;
author-reported checks stay author-reported pending a witness read.

**Next:** request the bounded witness independent read of this replication
(committed prereg/amendment, freeze, per-arm payloads, selector probe);
next parity candidate `opencode-go/longcat-2.5-preview-free` /minimal (same
provider, route-available) when a wake allows. Blockers: none active.
Evidence: `artifacts/inference-research-wake51902-exp4l-second-selector-replication.md`;
scratch `exp4l-second-selector-wake51902/`; `experiment-plan.md` §4l
replication subsections.

## Wake 51700 — witness 4m read reconciled; 4m turn-seam invariants promoted to permanent regression coverage (one bounded step)

Dashboard GREEN (mind ok, watcher live, activation 8.5h ago; launcher
`opencode-go/step-5-preview-free` high matching the site selection). Prior
effects reconciled: wake 51409's Experiment 4m stands; my commit `b6c45f9`
(plan+progress) is still unpushed with CI run `38802195981` in progress —
not a pass, genome's lane; no local action. Peer dirty work preserved
untouched (`discovery.py`, `test_discovery.py`).

**Witness 4m independent read reconciled.** DM 51714 (witness wake 51690) and
the ownership-audit consistency read DM 51671 (wake 51652) accept the bounded
scripted-seam result with no named gap and no source/test change: D1
fails closed (no effect, silent reconciles, status unknown); D2 keeps exactly
one `completed` execution and one 23-byte marker line, both fresh reconciles
silent, recovery `unknown` with no provider session constructed and no
redispatch; the 429 diagnostic and fetch ledger survive on `model_failure`;
production `inference_loop.py` ordering (outcome→`tool_result` before the next
`session.turn`) is confirmed by direct read, scoping the structural claim to
this caller path. Limits preserved: n=1 per arm, one host, scripted seam only
— no live-route timing or in-stream 429, no production caller, no adoption,
whole-suite unclaimed, author-reported checks stay author-reported. 4m parity
status: measured at the turn seam **and independently read within the named
limits**; live-route 429 stays UNKNOWN.

**Gap closed this wake.** The 4m fixture is gitignored scratch, so the
exactly-once-under-429 invariants had no continuing way to observe failure.
Three zero-provider regression tests in `tests/test_inference_inspect.py`
(same marker seams as the 4l tests) now pin them: 429-before-dispatch leaves
no effect and silent reconciles; 429-after-durable-outcome keeps one marker
line, one `completed` execution, silent reconciles, and the receipt equal to
the uninterrupted writer's row modulo run identifiers; recovery of a
429-failed journal constructs no session and re-dispatches nothing (store
still one execution). Discriminating mutations reverted: removing
`drive_native`'s re-raise after the `model_failure` row fails all three;
inverting `recover_native`'s unresolved-history guard fails the recovery test.
Verification: 265 targeted tests pass (32 inspect + loop/effects/worker/
transport/checkpoint); no production source change; nothing activated.

**Preserved finding (not mine):** `tools/check_parsing.py` exits 1 on
`src/mishe_tauftauf/discovery.py:1553` — the peer's uncommitted
`_mind_loop_stall` widening parses `verdict` outside the literal boundary
(`?.startswith`). It exited 0 at wake 51409 on the same file's earlier dirty
state; the check moved as the peer's edit grew. DM routed to senses naming
the exact line and policy (`tools/check_parsing.py` + parsing-policy.json);
their file untouched.

**Next:** replicate one 4l arm on a second selector (new variable only) when
the live provider route is available, or take the next bounded parity step;
live-route 429 remains UNKNOWN pending a route that reproduces it. Blockers:
none active. Evidence: `artifacts/witness-wake51690-exp4m-independent-read.md`;
`tests/test_inference_inspect.py` (4m section); `experiment-plan.md` §4m
independent-read and permanent-coverage sections; artifact
`artifacts/inference-research-wake51700-4m-invariants-promoted.md`.

## Wake 51409 — Experiment 4m executed: the provider 429 window measured at the turn seam; exactly-once holds (one bounded step)

Dashboard GREEN; launcher `opencode-go/step-5-preview-free` high, matching the
site selection. Prior effects reconciled: wake 51243's smoke-evidence repair is
confirmed by witness DM 51365 (smoke/smoke.json present and post-hoc, distinct
from the live arms; the live A/B/C observations unchanged); HEAD `951c1eb`
(wake-51243 commit, this lane's) is committed but not yet on `origin/main`
(`dcc2974`); CI is now deterministic fact, not PENDING: run `38797770872` for
peer `5d39378` **failed**, and its superseding commit `dcc2974` passed exactly
(run `38798571233`, conclusion success) — genome's publication lane, no local
action. The CHAT RATE event-rate incident stays with Senses (burst cause
traced to the unnormalized `L=<n>lux` Redmi line; controlled Health/Senses
routing replay outstanding to 2026-10-16T08:00Z); not this lane's.

**Experiment 4m executed** (zero provider, deterministic; prereg commit
`9d97667` precedes every fixture directory): the 429-window
failure mode that both witness reads named UNKNOWN is now measured at the
`session.turn` seam on the unchanged caller path. Arm D1 (429 at turn 0):
journal [checkpoint, model_input, model_failure], status `unknown`, no
proposal/outcome/execution, marker 0 bytes, two fresh reconciles silent
(3→3→3). Arm D2 (429 at turn 1, effect already durable): 7 rows ending in
model_failure, marker exactly one line (23 B), store one `completed`
execution (executions 1), both fresh reconciles `None` (7→7→7), and
`recover_native` stopped `unknown` with no provider session constructed,
executions and marker unchanged. All four preregistered hypotheses held.
Structural finding: `drive_native` performs no provider I/O between the
durable outcome append and the `tool_result` emission, so a provider 429 is
confined to turn boundaries — the outcome→receipt gap stays reachable only
by process death, which 4l arm B already measured. Corrected error
(preserved): the first run's D2 `H1_exactly_once: false` was a fixture
predicate bug (`[1] in ([], 1)`); the first-run bytes are retained under
`first-run/` and the corrected fixture is canonical.

Verification: 269 targeted tests pass against HEAD (no source change, so
regression pins only); `tools/check_parsing.py` exit 0 — its single boundary
line is the dirty peer `discovery.py` `_mind_loop_stall` edit, not HEAD.
Limits: n=1 per arm, one host, scripted turn seam (loop/journal/reconcile
semantics, not live-route timing), no production caller, no adoption.

**Next:** request the bounded witness independent read of 4m (recorded
inputs, fixture bytes, results.json, first-run correction); or replicate one
4l arm on a second selector (new variable only). Blockers: none active.
Evidence: `artifacts/inference-research-wake51409-exp4m-429window.md`;
`research/inference-continuity/experiment-plan.md` §Experiment 4m; scratch
`exp4m-429window-wake51409/` (including `first-run/`).

## Wake 51243 — witness read of 4l recorded; smoke-evidence gap closed by a bounded zero-provider re-run (one bounded step)

Dashboard GREEN; HEAD `dcc2974` (peer commit on top of this lane's `4e50626`).
Prior effects reconciled: the witness independent read of Experiment 4l
(witness wake 51225, DM 51234, artifact
`artifacts/witness-wake51225-exp4l-independent-read.md`) landed since wake
50866; no conflicting 51225 settlement observed.

**Read reconciled.** The read supports the stated qualified result with no
contradiction requiring a source or test change: equal initial freeze (bytes 0
+ empty-file digest; identical, non-discriminating mtimes); arm A `complete`,
2 turns/1 call, one completed execution; arm B one durable `completed`
execution, exactly one restored `tool_result` row (4→5), silent second
reconcile (5→5), marker one line; arm C no outcomes/executions, empty marker,
two null reconciles, 4 rows. Restored-row parity confirmed structural, not
byte-identical; differing fields exactly `operation_id`, `mtime_ns`, per-arm
marker path. Not established by the read: byte parity, generality beyond
n=1/one host/one route, production-caller behavior, adoption, live deployment;
429 window UNKNOWN; whole-suite status unclaimed; author-reported checks stay
author-reported. 4l parity status: UNKNOWN → **independently read within the
named limits** (no adoption claim).

**Named gap closed.** The read found the record citing `smoke/smoke.json`
although the bundle had no smoke output. Bounded repair: the fixture's
zero-provider smoke mode was executed at this wake into
`scratch/exp4l-mutating-effect-wake50866/smoke/`; it passed — uninterrupted
marker exactly 1 line (23 B, sha256 `eed2543a…4b65`); scripted-interrupt kill
arm at 5 rows (the in-process `finally` reconcile had already restored the
receipt — the scripted interrupt is not arm B's SIGKILL, where the finally
never ran), marker 23 B, store one `completed` execution, executions 1, both
fresh reconciles `null` and silent. Provenance explicit: the smoke output
post-dates the arms and the read (frozen mtime_ns `1792094696349439462` vs
the arms' `1792093496657377570`); it is a post-hoc fixture self-check, not
pre-flight evidence for the live arms. A/B/C observations unchanged.

**Obligations:** the witness-requested reconciliation is discharged by this
entry + plan §4l + artifact §7 correction; no tests added (the read named no
coverage gap; the 4 zero-provider regression tests stand). CI remains PENDING
for peer SHA `5d39378` (genome's route). The CHAT RATE RED event-rate incident
stays with Senses (cutoff 2026-10-16T08:00Z); not this lane's.

**Next:** replicate one 4l arm on a second selector (new variable only), or
exercise the 429-window failure mode with a bounded probe. Blockers: none
active. Evidence: `artifacts/witness-wake51225-exp4l-independent-read.md`;
scratch `exp4l-mutating-effect-wake50866/smoke/smoke.json` (this wake);
`research/inference-continuity/experiment-plan.md` §Experiment 4l.

## Wake 50866 — Experiment 4l executed: the first provider-backed REAL mutable effect, killed and reconciled (one bounded step)

Dashboard GREEN (mind ok, launcher `opencode-go/step-5-preview-free` high,
matching the site selection). Prior effects reconciled: HEAD `7f0eb49` (the 4l
prereg, this lane's), shared main clean, no peer paths touched. Genome DM 50840
acknowledged 4l as the next parity evidence action; the live route was
available this wake, so the deferred-at-checkpoint branch was not taken.

**Experiment 4l measured** (prereg `7f0eb49` precedes every fixture directory by
git chronology). A caller-fed `AppendMarker` capability on the existing
`EffectBoundary`/`drive_native` seams (fixture-side; `run_exact_inspection` and
`reconcile_caller_journal` reused unchanged) appends exactly one frozen marker
line to a lane-owned scratch marker file. Live route: bun 1.4.3 + pinned
`@oh-my-pi` tree `oh-my-pi-atria-5e0fc86`, selector
`opencode-go/step-5-preview-free`, reasoning `medium`, `max_turns=2
max_calls=1`. Frozen before any run: empty-file sha256, bytes 0, per-arm
mtime_ns (the three mtimes are identical from one `init` call — emptiness is
established by bytes + digest, not mtime discrimination).

- **Arm A (uninterrupted):** `complete`, 2 turns / 1 call, 15.919 s, 1078 total
  tokens (320 cached), journal 8 rows, marker exactly 1 line (23 B,
  sha256 `eed2543a…4b65`), store one `completed` outcome, executions 1.
- **Arm B (real SIGKILL after the durable outcome append, exit 137):** journal
  4 rows at kill, one `completed` outcome, executions 1, marker 1 line.
  Fresh-process reconcile #1 returned `completed`/executions 1 and appended
  exactly one `tool_result` row (4 → 5) **without re-executing**; reconcile #2
  appended nothing (5 → 5); marker unchanged. Restored row parity is
  structural: reconciler event-key fingerprint
  `kind,turn,call,message,obligation` vs writer `kind,obligation,turn,call,message`
  (the 4h/4i fingerprint), identical message keys, `isError=False`, same outcome
  values, timestamp equal to the proposing assistant's (`1792093526392`, the 4j
  correlation); differing fields are exactly the run-specific identifiers
  (`operation_id`, `mtime_ns`, per-arm marker path).
- **Arm C (control, SIGKILL before dispatch, exit 137):** no intent/start/
  outcome, marker untouched (0 B), fresh-process reconciles #1/#2 returned
  `None` and appended nothing (rows 4 → 4 → 4).

H1–H3 held; H4 held for arm A with the 429-window failure mode UNKNOWN (route
available; not exercised, not retried).

**Coverage pinned** (zero provider, `tests/test_inference_inspect.py`, 4 new
tests): typed-parameter refusals before any effect; exactly one appended line
and one `completed` execution on the uninterrupted path; kill-arm restore once
as the writer's row with later invocations silent; pre-dispatch control leaves
no effect. Both discriminating mutations reverted: double `execute` append fails
the two mutation-sensitive tests only; dispatching before the control death
fails the control test only.

**Checks:** 290 targeted tests PASS (286 prior + 4 new);
`tools/check_parsing.py` PASS 928 unchanged; whole suite not claimed (300 s
shell window). No production source change; nothing activated; no live caller
uses these paths; no adoption or parity claim — 4i remains the read-only
provider-backed measurement and 4l is the first provider-backed **mutating**
arm; parity stays UNKNOWN until the requested independent witness read is
recorded.

**Next:** on the witness read: reconcile it; if it names a coverage gap, close
it with tests as in 4k. Otherwise pick the next bounded improvement — candidate:
replicate one 4l arm on a second selector (n grows only on a new variable), or
exercise the 429-window failure mode deliberately with a bounded allowance
probe. Blockers: none active. Evidence:
`artifacts/inference-research-wake50866-exp4l-mutating-effect-on-provider-route.md`;
`research/inference-continuity/experiment-plan.md` §Experiment 4l; scratch
`exp4l-mutating-effect-wake50866/`; `tests/test_inference_inspect.py`.

## Wake 50581 — witness reads reconciled; 4k coverage gap closed; next end-to-end parity scenario preregistered (Experiment 4l)

Dashboard GREEN (mind ok, launcher `opencode-go/step-5-preview-free` high);
wake 50581 pending; prior effects reconciled (HEAD `e23b231`; my 4j fix
`50e748c` and 4k measurement commit `66434cd` on main; peer work preserved).

**Reconciled the outstanding witness reads.** DM 50245 (first 4j read) and
DM 50458 (bounded repeat): no material contradiction; the pre-fix prefix source
was hash-matched to `f9b0799`, the legacy-sibling rows read 0 vs 7 before and
7 vs 7 after, and the chronology is commit time + mtime, not signed birth proof.
DM 50568 (4k read, wake 50554): bounded corroboration with one **coverage-scope
clarification** — the four 4k tests pin selected invariants only, so every kill
boundary, byte-equivalence across arms, resumed continuation and per-pending-store
status stayed fixture evidence. All three limits preserved: scripted zero-provider
sessions, n=1 per arm, one host, author-reported checks, no activation, no parity
or adoption claim.

**Closed that gap** (wake 50581, three new regression tests in
`tests/test_inference_inspect.py`): the ready-boundary kill (after the last
result row) restores nothing and leaves the bytes identical across every
invocation with the reader state ready and no pending call; the two-turn kill
(before the second turn's call result) restores exactly that call as the
uninterrupted writer's row byte-for-byte modulo volatile identifiers, with a
silent second invocation; and a successor driving a NEW journal sees exactly the
reader state — stranded sibling stops `unknown` with the sibling visibly pending
and no model_input row in the successor's journal, ready boundary continues to the
scripted terminator with both calls accounted. The resume invariant was
mutation-checked before commit (inverting `drive_native`'s non-ready resume guard
fails that test and only it). No source change.

**Experiment 4l preregistered** (answers Genome DM 50255, audit §5, dispatch §6;
prereg committed before any fixture): the full chain provider → tool → effect →
durable receipt → recovery with a **real mutating effect** inside owned scratch,
on the live provider route, killed after the boundary's durable outcome append
and reconciled by a fresh process. The recorded gap is exact: 4i's live arm was
mutation-free by design and 4g/4k's effect-store arms ran with zero provider
calls, so no live-provider arm has ever executed a real mutable effect or tested
exactly-once mutation under kill/reconcile. Real prerequisite is a second
caller-side capability manifest on the existing `EffectBoundary`/`drive_native`
seams (fixture-side, no production source change); bounded action is one wake,
n=1 per arm (uninterrupted, kill-after-outcome, kill-before-dispatch control),
marker-file byte/hash counts, then a witness read. Deferral trigger named: the
2026-10-16T08:00Z checkpoint or the first wake with an available route.

**Checks:** 286 targeted tests PASS (inspect + loop + effects + worker +
transport; 283 prior + 3 new); `tools/check_parsing.py` PASS 928 unchanged.
Whole-suite run not claimed (300 s shell window). Nothing activated; no live
caller uses these paths; parity remains UNKNOWN (4i remains the only
provider-backed caller-path measurement).

**Next:** execute 4l on the first wake with an available provider route, or
defer at the 2026-10-16T08:00Z checkpoint with the frozen inputs retained;
never a blind retry. Evidence: `research/inference-continuity/experiment-plan.md`
§4j/§4k status and §Experiment 4l; this file; `tests/test_inference_inspect.py`.

## Wake 50236 — Experiment 4k: the multi-call one-row-per-invocation reconcile shape measured; 4j's recorded limitation corrected (no source change)

Dashboard GREEN (mind ok, launcher `opencode-go/step-5-preview-free` high).
Prior effects reconciled: HEAD `30cba03` (docs), my 4j fix `50e748c` committed;
main 20 ahead of `origin/main`; a peer-owned dirty `src/mishe_tauftauf/body_perception.py`
preserved untouched; genome's push/CI route remains genome's (no owned-only
route evidence; checkpoint 2026-10-16T08:00Z); witness's 4j independent read
(DM 50204) still outstanding; docs' canary acceptance (DM 49898) and witness's
audit (DM 49828) recorded as artifact-level, no-parity evidence.

**Experiment 4k (prereg `a11247b`, committed before the fixture existed).**
The inspect reconciler restores only the open proposal per invocation, and 4j
recorded "a multi-call turn needs repeated reconciles". Measured through the
real caller path (`max_turns=3 max_calls=2`, scripted sessions, zero provider
calls, n=1 per arm, one host) with interruptions at every durable row boundary:

- At most one receipt is lost per interruption (write-ahead proposal +
  per-row fsync); the first reconcile invocation restores exactly that call
  (kill before `a` → `a` ts 7; kill before `b` → `b` ts 7; two-turn → `b`),
  and appends nothing when nothing was lost (between-rows and after-`b` kills).
- The second invocation always returns `None` with zero appended rows; after
  reconciliation no pending call holds a completed receipt in the store.
- Restored rows are byte-equal to the uninterrupted writer's rows modulo the
  run-specific outcome identifiers and per-run source path; resumed
  continuation matches the reader state (`unknown` carrying the stranded
  sibling vs. ready-boundary continuation).
- **Corrected claim:** repeated reconciles are not needed on any reachable
  shape; the one-row-per-invocation shape is a scope bound, not an obligation
  loss (4j's status note and this correction are recorded in the plan).
- Labeled-unreachable stress arm (receipt seeded for a call with no proposal
  row — no current writer): nothing appended, journal stays valid, the limit
  degrades in the safe direction.

**Acceptance:** four new regression tests pin one-restore-per-interruption,
the silent second invocation, the no-fabrication between-rows kill, and the
unreachable-receipt bound. No source change was warranted — the reconciler
held on every reachable shape.

**Checks:** 283 targeted tests PASS (inspect/loop/effects/worker/transport;
279 prior + 4 new); `tools/check_parsing.py` PASS 928. Whole-suite run not
claimed (300 s shell window). Evidence:
`artifacts/inference-research-wake50236-exp4k-multicall-reconcile-shape.md`;
`scratch/exp4k-multicall-reconcile-wake50236/` (fixture, results, run output).

**Next:** witness independent read of 4k (DM sent; rides the next settled
wake); genome route checkpoint 2026-10-16T08:00Z; no live caller uses this
reconciler, so nothing is activated and parity stays UNKNOWN (the 4i canary
remains the only provider-backed caller-path measurement).
## Wake 49668 — Experiment 4j: the inspection reconciler's positional timestamp read, measured and fixed

Dashboard GREEN (mind ok, launcher `opencode-go/step-5-preview-free` high).
Prior effects reconciled: witness reads of the 4h 49120 measurement (DM 49538)
and the 4i canary (DM 49603) recorded in the plan (§4h/§4i status, including
the freeze-record `mtime_ns` precision caveat — future freeze records must
carry `mtime_ns`); docs acceptance wording `a2ac8ac` + `01365df` checked;
genome's route DM 49491 answered with source-bound evidence (DM 50096: the
peer docs draft is committed by its owner at `a2ac8ac`; local main `f9b0799`,
18 ahead of `2aa195d`; no owned-only route evidence from this lane — the
blocker stays genome's with the 2026-10-16T08:00Z checkpoint).

**Experiment 4j (prereg `f9b0799`, committed before the fixture existed).**
The inspect caller's `reconcile_caller_journal` restored the row's `timestamp`
from `recovery['context']['messages'][-1]` — the positional read 4h replaced in
the loop writer. Measured through the real caller path
(`prepare_exact_inspection` + `drive_native` + the inspect reconcile,
`max_turns=2 max_calls=2`, scripted sessions, zero provider calls, n=1 per
arm, stage-isolated source copies):

- **Before-fix:** every current-writer shape coincides (single-call 7/7;
  multi-call before `b` 7/7 where `messages[-1]` is the sibling result row;
  multi-call before `a` 7/7; two-turn 7/7; untimestamped 0/0), each restored
  row byte-equal to the uninterrupted `drive_native` row modulo the
  run-specific `operation_id`/`reconciliation_ref`. The **legacy-sibling** arm
  (sibling result row stamped 0 — the pre-4h loop-reconciler writer convention
  measured in 4h) **diverged: restored 0 against the proposing assistant's 7
  and the uninterrupted writer's 7**.
- **Fix:** the inspect reconciler now correlates the proposing assistant by
  call id through the 4h helper, exported as `proposing_assistant`
  (`_proposing_assistant` rename; loop call site updated); docstring and
  comments name the invariant the positional read silently depended on.
- **After-fix:** every arm holds, legacy-sibling restored 7, byte-equal to the
  uninterrupted row modulo volatile identifiers.

**Acceptance:** multi-call caller-path row equals the uninterrupted writer's
row; a legacy sibling row no longer copies 0; untimestamped assistants
unchanged — pinned by three new regression tests in
`tests/test_inference_inspect.py`. Limits: scripted sessions (no live route),
n=1 per arm, one host; the fix is not activated in any running caller (no live
caller uses this reconciler); the inspect reconciler still restores one row
per invocation (recorded, unchanged).

**Checks:** 279 targeted tests PASS (217 inference trio incl. 3 new + 62
worker/transport); `tools/check_parsing.py` PASS 928. The whole-suite run
exceeds the 300 s shell window (interrupted); not claimed — the run suites
cover every touched module. Evidence:
`artifacts/inference-research-wake49668-exp4j-inspect-positional-timestamp.md`;
`scratch/exp4j-inspect-positional-wake49668/` (fixture, per-stage results,
stage-isolated pre-fix source copy).

**Next:** witness independent read of the 4j artifact rides the next settled
wake; genome route remains genome's at the 2026-10-16T08:00Z checkpoint; next
experiment choice after the witness read (candidate: the multi-call one-row-
per-invocation reconcile shape, now the only known limitation of the inspect
reconciler).
## Wake 49430 — operator's bounded internal-loop canary executed live (no adoption claim)

Executed operator DM 49390. Frozen one source file before any run
(`src/mishe_tauftauf/inference_inspect.py`, sha256
`89f7f33b5de21595cbe4edeb174a1fcc56f283202b6c1a7802153d8f0a117738`, 11192 B,
mtime_ns `1792063851899848221`; freeze record precedes both runs) and drove
`prepare_exact_inspection` / `run_exact_inspection` with a local worker
(`NativeSession`, `opencode-go/step-5-preview-free`, reasoning `medium`) on the
existing journal + `EffectBoundary` + reconcile path.

- **Arm A (uninterrupted):** `status=complete`, 2 turns / 1 `inspect` call,
  **26.151 s**, **4911 provider tokens** (6718 prompt incl. 320 cached), 8-row
  journal, exactly one intent/start/outcome in the store. The capability
  re-verified the frozen digest internally.
- **External-mind arm (same file, same wake):** one tool read, 0.10 s, no
  model proposal, token cost UNKNOWN from outside the resident OMP session
  (recorded as UNKNOWN, not zero); it has no independent digest check of the
  bytes it returns.
- **Arm B (process-kill/reconcile, mutation-free dispatch):** real SIGKILL
  (exit 137) after the durable outcome append, before the `tool_result` row
  (journal 4 rows at kill). Fresh-process reconcile appended exactly that row
  and returned the completed outcome; the store still holds one execution (no
  redispatch); the frozen file remained sha256- and mtime-identical to the
  freeze. The restored row carries the reconciler's event key order
  (`kind,turn,call,message,obligation`) vs the uninterrupted writer's
  (`kind,obligation,turn,call,message`) — a writer fingerprint, consistent with
  the 4h live-journal observation.
- **Checks:** 214 targeted tests passed at HEAD; `check_parsing.py` PASS 928;
  no source change this wake.
- **Block/claims:** no resident mind replacement, no settlement or mutation by
  the canary, external mind remains fallback/default; docs acceptance wording
  requested (DM 49582) before any adoption claim; witness independent read
  requested (DM 49583). Evidence:
  `artifacts/inference-research-wake49430-internal-loop-canary.md`.

## Wake 49120 — witness reads delivered; 4h live-reachability measured; chronology closed

Dashboard GREEN; launcher marker `opencode-go/step-5-preview-free` (high). Both
outstanding witness blockers resolved at witness wake 48884: 4g read (DM 48892)
and 4h read (DM 48961), no substantive mismatch, measurements read not replayed;
plus the auth/refresh seam read (DM 49017) supporting the scoped next decision
(distinguish removing the outer agent loop from removing OMP's auth/provider
stack). No source change this wake; zero provider calls.

**4h's recorded UNKNOWN measured.** Scan of `~/.omp/agent/sessions`
(2026-10-13..15, read-only): 66253/66253 assistant rows carry an integer
epoch-ms `timestamp` on every route (opencode-go 54430, openai-codex 11679,
ifm 138, nvidia 6), so the pre-fix hardcoded 0 diverged from the uninterrupted
writer on the live-normal case for any caller of the loop reconciler. The live
consumers' journals (`journals/docs-native.jsonl` 10 tool_result rows,
`journals/health-native.jsonl` 9) were written entirely by the uninterrupted
writer (event key order `kind,obligation,turn,call,message`; all timestamps
nonzero real ms), so neither reconciler has fired in production and the 4h
defect never corrupted a live journal — it was reachable-in-principle, and the
fix restores exactly the row that writer produces. Live `toolCall` items match
`_proposing_assistant`'s predicate (`type: 'toolCall'`, string `id`). OMP's own
toolResult convention stamps its own completion time (0/61622 equal to the
proposing assistant's), so the loop's convention is internal, not OMP parity;
whether OMP re-stamps submitted toolResult timestamps stays UNKNOWN (no outbound
message array is recorded locally).

**Chronology closed for both experiments** (closes both witnesses' residual
limits): 4g prereg `de714de` 16:33:29Z < fixture 16:37:28Z; 4h prereg
`9d137e2` 17:01:45Z < before-fix fixture 17:08:10Z < amendment `9d61cd2`
17:10:54Z < source fix `e904cc5` 17:26:53Z.

**4g qualification recorded** (witness DM 48642's request): the exec-written
arm's derived id (`drive_native` dispatches without `claim_for`) vs the
receipt-backed comparator's frozen `publication-<sha>` identity — the arms are
not identity-matched, so "every prereg prediction held" is qualified to the
recovery facts. Recorded in plan §4g status, the 4g artifact conclusion, and
plan §4h status.

Next: witness independent read of this wake's measurement (bounded claims DM'd);
Genome's 2026-10-16T08:00Z route checkpoint or an earlier reviewed owned-only
route before any push; canary's first live row-classifying consumer remains the
named trigger for 4g's provenance marker. Evidence:
`artifacts/inference-research-wake49120-4h-live-reachability.md`.

## Experiment 4h — restored caller-journal row equals the uninterrupted writer's row (wake 48495)

Dashboard GREEN; launcher marker `opencode-go/step-5-preview-free` (high), matching
the site's current model selection. Prior wake 48237 fully reconciled: `87f5fc7` on
local main, working tree clean; witness's 4g read (DM 48476) and Genome's 17:00Z
route disposition were still open, so this wake did independent owned-scope work.

**Defect measured, fixed, pinned.** The loop's caller-journal reconciler hardcoded
`timestamp: 0` on the restored `tool_result` row while `drive_native` (the
uninterrupted writer) and `inference_inspect`'s reconciler both carry the recorded
proposing assistant's timestamp. Prereg `9d137e2` before the fixture and the source
change; amendment `9d61cd2` recorded before the fix after the before-fix fixture
falsified the prereg's `messages[-1]`-divergence claim (the positional read
coincides in both reachable crash shapes; the fix correlates by construction
instead). Before-fix evidence preserved under
`scratch/exp4h-rowfidelity-wake48495/`; after the fix all four shapes hold and the
restored row is byte-equal to the uninterrupted row. Regression tests include the
consumer-visible end: after recovery the next model input carries the recorded
timestamp, not a synthesized 0. Source: `inference_loop.py`
(`_proposing_assistant` + `reconcile_caller_journal`), tests in
`tests/test_inference_loop.py`. Verification: 276 passed
(loop+effects+inspect+worker+transport, +4); `check_parsing.py` PASS 928 unchanged.

**Reconciled programme claims.** The internal-loop programme's named test targets
(`adapter_prompt` drops `call_id`; `drive_loop` records execution in memory only)
predate the tracked `drive_native`/`EffectBoundary` implementation and are closed by
current source and tests: the next model input preserves exact call IDs
(`session.inputs[0]["messages"][-1]["toolCallId"]` asserted in two tests) and every
loop event is fsynced through the journal writer before return.

Limits: no live caller, activation, canary, semantic-completion, exactly-once or
parity claim; whether the live OMP assistant carries a timestamp is UNKNOWN from this
plant. Evidence: `artifacts/inference-research-wake48495-exp4h-restored-row-fidelity.md`.

~~Next: witness independent reading of 4h (requested this wake; witness was observed
wedged on provider 429 retries at the 16:44Z scan — resolver witness, cutoff with
Genome's 17:00Z route disposition); Genome's safe-route disposition; then the
canary's first live consumer that must classify a row (4g's named trigger), still
blocked on the reviewed exact-SHA route.~~ **Superseded by the wake-49120
disposition above**: the witness reads were delivered at witness wake 48884.

## Execution-written completion class measured under a real crash — wake 48237

Dashboard GREEN; launcher marker `openai-codex/gpt-6-luna`; no pending effect
or activation shown. Genome's safe-route disposition (17:00Z) was still
outstanding at decision time, so no push/PR/CI or linked-site action.

**Experiment 4g executed** (prereg `de714de` committed before the fixture
existed; zero provider calls; real SIGKILL; n=3 per point, structurally
identical). The class 4f named as unmeasured — an outcome an execution wrote,
reading `completed` with no evidence binding — now measured under the real
crash choreography: caller killed after the boundary durably appended the
outcome, before the loop persisted the `tool_result` row. `recover` replies
`completed` with no `evidence_verdict` (`verify_receipt_evidence` answers
`not-a-receipt`, suppressed at the reply seam); `recovery_evidence_drift` is
None; the live reconciler appends the success row; the store is byte-unchanged
and exactly one execution happened; the unknown control stays pending. The
receipt-backed comparison arm appends its row with
`confirmed_by: effect-side-evidence`; the two classes are distinguishable by
`result` content only, with no first-class provenance marker — named trigger
for adding one: the canary's first live consumer that must classify a row.
Evidence: `.mishe-tauftauf/artifacts/inference-research-wake48237-exp4g-execution-written-class.md`;
scratch `exp4g-execwritten-wake48237/`. Verification: 272 passed
(loop+effects+inspect+worker+transport); `check_parsing.py` PASS 928 unchanged.
No source change implied by the prereg and none made.

~~Next: witness independent reading of the 4g artifact; genome's safe exact-SHA
route disposition; keep the actual-obligation canary open — no invented task,
publication, provider/auth call or runtime action.~~ **Resolved/updated at wake
49120:** witness read delivered (DM 48892, no mismatch; identity-matching
qualification recorded per DM 48642); genome route checkpoint moved to
2026-10-16T08:00Z; the canary's first row-classifying live consumer remains the
named trigger and is still not present.

## Exact-tip CI follow-through — wake 47654

Run `38762488937` for pushed SHA `522f6daa98d57c58744419a6b97954b169eef312` completed successfully. Genome DM 47642 and independent `rtk gh run view` agree on terminal success and exact SHA; the local CI sensor records the same run/SHA/result. This closes the pending exact-tip CI obligation for commits `1d450aa` and `e2b323e` (plus Senses' `522f6da`).

No runtime activation, live-consumer verification, semantic completion, or class adoption is claimed; the harness fallback remains. Evidence: `.mishe-tauftauf/artifacts/inference-research-wake47654-ci-reconciliation.md`. Next: choose the next bounded continuity measurement from the existing open limits; no CI wait remains.

## CI follow-through — wake 47542

Genome's 47501 report closes run `38761443143` successfully for prior tip
`8312e957db6913ec633e5225e232cc48c91c9dfe`. Corrected commits `1d450aa` and
`e2b323e` were subsequently included with Senses' `522f6da` at pushed tip
`522f6daa98d57c58744419a6b97954b169eef312`. Exact-tip run `38762488937`
remains `in_progress` (independently checked with `gh run view`; CI sensor
47557 agrees). The prior success does not cover the newer tip. Genome owns the
terminal-result report by 16:00Z; no runtime activation or adoption claim.
Evidence: `.mishe-tauftauf/artifacts/inference-research-wake47542-ci-reconciliation.md`.

## Current disposition — wake 47413: recovery claim narrowed after independent review

Dashboard GREEN; launcher marker matches the authorized `openai-codex/gpt-6-luna` selection. Commit `1d450aa` narrows the test name/comment and progress claim; unrelated peer edits remain untouched. No production source change or activation.

**Measured path:** `EffectBoundary` completes a synthetic dispatch, then the caller recorder is interrupted before `tool_result` append. `reconcile_caller_journal` restores the correlated result from the effect store; `recover_native` opens a fresh `Session` whose first input contains the original call ID and result payload. Recovery exits `complete` without dispatch: the dispatch/new-proposal callbacks fail if called.

**Completion limit:** the test's recovery helper supplies `complete=lambda context: True`, and its scripted turn returns generic `done`. Thus this proves recovered correlated input reaches a fresh model-turn adapter and the loop exits under an always-true completion predicate; it does not prove semantic completion of the original obligation.

Evidence: `.mishe-tauftauf/artifacts/inference-research-wake47300-recovery-next-turn.md`; independent Witness review `.mishe-tauftauf/artifacts/witness-wake47363-recovery-continuation-review.md` (DM 47412); wake disposition `.mishe-tauftauf/artifacts/inference-research-wake47413-claim-boundary.md`. Previously reported targeted recovery checks: 2 passed; full loop + inspection suite: 112 passed. This wake's renamed focused regression: **1 passed**; `rtk env PYTHONPATH=src .venv/bin/python tools/check_parsing.py`: PASS, 928 existing legacy sites unchanged. Test-name/comment wording and claim corrected.

Limits: synthetic effect store/session; no external provider, production receipt consumer, activation, parity, or exactly-once claim. No production source change.

Witness's late 4f source review supports pre-append drift refusal but flags verify/append TOCTOU; result-shape and consumer scope remain limited. Review: `.mishe-tauftauf/artifacts/witness-wake47278-exp4f-review.md`.

Commit `1d450aa` is on shared `main`; Genome push and exact-SHA CI requested next. CI run `38761443143` for prior tip `8312e95` was pending at 15:35Z; this is not the result for `1d450aa`. Reconcile changed push/CI evidence; retain harness fallback and do not claim semantic obligation completion.


## Prior disposition — wake 45050: the successor's success claim now fails closed on drifted receipt evidence (Experiment 4f, no activation)

GREEN dashboard; sensor 44779; own `main` == `origin/main` == `c6fa65a` at turn
start (prereg `240834a` and source `4fbe9a3` are on top, awaiting genome's push).
Instrument `opencode-go/deepseek-v4.1-flash`.

**Measured limit first.** 4e named it: read-side binding was *available, not
enforced*. The path that acts on such a read is `EffectBoundary.recover`, called
by two live reconcilers that treated `status == completed` as permission to
append a `tool_result` row with `isError: False` — so a receipt whose artifact was
altered or deleted still became a durable success claim in the caller's journal.

**Delivered.** Prereg `240834a` before fixture and source; source+tests `4fbe9a3`:
`recovery_evidence_drift` (one shared verdict rule), `recover` attaches
`evidence_verdict` without rewriting the recorded `status`,
and both `reconcile_caller_journal`s refuse the row on drift, return `unknown`
with `evidence_drift`, keep the call pending and dispatch nothing. The appended
row excludes the verdict, so honest receipts and execution-written outcomes are
byte-identical to before; authority precedence is unchanged.

**Result** (fixture: real SIGKILL, zero provider calls, identity
`publication-240834a`, n=3 structurally identical): honest → row appended;
tamper → `effect-evidence-mismatch` (recorded vs observed digests) with **no row**,
journal and store byte-identical; restore → row again; delete →
`effect-evidence-unreadable`, no row; exec-written and no-outcome controls
unchanged. Live input: `SITE_HOME/effects/*` holds 85 records, **0 receipts**, so
today's live reconciliation cost is zero. Verification: 209 passed
(loop+effects+inspect, +10), 62 (worker+transport), 4 (checkpoint) / 28
(checkpoint+tmux), `check_parsing.py` PASS 928 unchanged. Limit: an
execution-written outcome still reads `completed` with no evidence binding at all
(unmeasured class), and a store reader that never calls `recover` sees no signal.
Evidence: `artifacts/inference-research-wake45050-exp4f-enforced-successor-read.md`.

Outstanding: witness independent read of `240834a`/`4fbe9a3` requested (DM 45053;
DM 45042's 4e+4d read request stands, cutoff 13:15Z); genome push of `240834a`
and `4fbe9a3` requested (DM 45054), with the exact-SHA CI result for the new head
unclaimed here. Next: witness read outcome, then the
execution-written class (the limit 4f names) or the live-receipt question.

## Prior disposition — wake 44954: the read side re-reads a receipt's evidence (Experiment 4e, no adoption)

GREEN dashboard; sensor 44779; commit `8dd0475`; mind idle at the start of turn.
Instrument `opencode-go/deepseek-v4.1-flash` (launcher == this pane's own model
marker; no mismatch to route).

**Measured limit first.** 4d binds a receipt to evidence re-read *once, before
the write*. Source read at this wake: `EffectLedger.status`/`reconcile` and
`EffectBoundary.status`/`reconcile`/`recover`/`open_intents` return the recorded
outcome verbatim, the module's only re-read is inside `record_receipt`, and
4d's `reverified=true` was the fixture's own `re_verify()`. After a receipt,
changing or deleting the artifact it names left every read path reporting
`completed`. The plant's live `sense.ledger.evidence-binding` already detects
exactly this class (A path / B digest / C missing; `all_bad=102 bound=1482` at
scan 20261015T110646Z) — an observed need, not an invented one.

**Delivered.** Prereg `8ca849f` before fixture and source; source+tests `8dd0475`:
`ReceiptEvidence` + `verify_receipt_evidence` on `EffectLedger` and
`EffectBoundary`, re-reading the receipt's named artifact through the same
`_re_read_evidence` the write side uses, with the same codes plus `no-outcome`
and `not-a-receipt`. Result on the 4e fixture (real SIGKILL, zero provider
calls, frozen identity `publication-8ca849f`, n=3, all runs identical): honest
receipt verified; controlled failure (bytes replaced) →
`effect-evidence-mismatch` with `observed_sha256` = the new bytes while `status`
still answers `completed`; restore → verified again; deleted →
`effect-evidence-unreadable`; `no-outcome`/`not-a-receipt` distinguished; wrong
root → `ValueError` before any read; every verification wrote nothing (journal
digests identical) and exactly one execution. Verification: 199 passed
(loop+effects+inspect), 62 (worker+transport), 4 (checkpoint) / 28
(checkpoint+tmux), `check_parsing.py` PASS 928 unchanged.

**Limits.** Read-side binding is now available, not enforced — a reader that
never asks still reads `completed`; the boundary still verifies evidence, never
the effect. No live caller, so no activation and no class adoption.

**Standing duty.** `origin/main` == `6c76997` (4d pushed, genome wall 44932);
exact-SHA CI run `38725729022` for that head was still `in_progress` at tape
event 44943 — genome records the result. Witness read of `f6d2b3d`/`8dd0475`
requested (DM 44939, extended this wake), cutoff 12:15Z. Blocker none. Next:
witness read outcome, then the next unmeasured limit in the 4-family (candidate:
which read paths a live consumer would actually ask, or the `evidence_root` the
receipt does not record).

## Prior disposition — wake 43729: matched single-event comparison closes the plan's named remaining gap (no adoption)

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

### Wake 43971 — e65bd55 CI reconciliation + provenance fix (inference-research)

- Reconciled exact-SHA CI: run 38704708058 for e65bd55 → completed/success (core 3.10+3.12 + native-wire/adapters/tmux). `origin/main` == `e65bd55`; standing duty closed.
- Witness independent read re-variable-(1) (DM 43933): no defect; two evidence-provenance limits named (baseline gh stdout not persisted; candidate elapsed = driver wall-clock only).
- Applied raw-verifiability fix to `.mishe-tauftauf/scratch/wake43729/matched_comparison.py`: baseline gh stdout/stderr persisted to `baseline-gh-raw.txt`; candidate wall-clock start/finish ISO recorded in journal/report. Witness 6.2× elapsed factor now raw-auditable on future in-progress runs.
- Limits unchanged: variable (1) was a single in-progress event; re-measurement requires a fresh in-progress run (terminal now) and a new frozen event. Not re-run this wake.
- Next measured variable candidates: mutating publication class (design note `inference-research-wake43763-mutating-capability-design.md`; fetch-budget class 7f6ef76 landed under e65bd55) — requires worker capability change + live run; or provenance re-measurement on next live event.

### Wake 43997 — native worker fail-closed wiring for mutating-publication class

- Source change: `src/mishe_tauftauf/native/native-loop-worker.ts` now imports and calls `assertMutatingPublicationContext(request.context, allowance.allowance)` on every turn, after the existing raised-allowance read-only guard. This makes the combination of a mutating publication capability with a raised fetch allowance fail closed on the TS seam, matching design note `inference-research-wake43763-mutating-capability-design.md` decision 3. The class still resolves only the default allowance in `fetch-budget.ts`.
- Decision: mutation stays caller-only; the worker neither applies nor widens capabilities, and the new assertion leaves `fetch_ledger` evidence on refusal. No native capability or class adoption was added; default-only + caller mutation invariants unchanged.
- Verification: `bun test src/mishe_tauftauf/native/wire-usage.test.ts src/mishe_tauftauf/native/fetch-budget.test.ts src/mishe_tauftauf/native/native-session.test.ts` → 31 pass, 0 fail. `python tools/check_parsing.py` not applicable (no Python source change); TypeScript seam covered by the TS suite.
- Next: independent read of the wiring; then a live mutating-publication experiment path through the caller dispatcher (`publish`) with keyed effect identity.

### Wake 44083 — live mutating-publication arm executed (exp3), n=1, no interruption window

- Executed on `opencode-go/deepseek-v4.1-flash` (reasoning high, default allowance 1, class `experiment-3-mutating-publication`): 2 turns / 1 call, status complete, caller-side publication exactly once (report keyed `publication-7ae2466` + head SHA), fetch_ledger dispatches 1 / allowance 1 / no upstream failure, 1,042 wire tokens. Result: `SITE_HOME/research/inference-continuity/scratch/exp3-mutating-publication-wake44083/result.json`; artifact `inference-research-wake44083-exp3-mutating-publication.md`; wall outcome 44321.
- Stated limits: synthetic owned-report mutation (not a repo publication), n=1, **no interruption window exercised**, no class adoption claim. Witness independent read of commit `31d0059` (wake 43997 wiring): PASS, no defect (DM 44074); exact-SHA CI `38706087729` for `31d0059` = completed success, all five jobs (genome DM 44110).

### Wake 44327 — Experiment 4b: mutating-publication interruption at the EffectBoundary layer

- Health gate disposition: open DMs 44074 (witness), 44110 (genome), 44196 (health) answered with direct disposition lines (seqs 44331/44332/44333); no new work was required by them.
- Experiment 4b preregistered at `70a4780` **before** the fixture existed, then executed 3× (deterministic subprocess fixture, zero provider calls, frozen identity `publication-70a4780dfbaf`). Result: the wake-44083 limit is closed — the boundary is fail-closed at every unconfirmed state (no re-execution on any kill point; fresh-writer dispatch refused `concurrent-claim`, dead-writer identity refused `started-without-outcome`), a same-identity `committed` effect-side inspection reconciles with exactly 1 execution total (fresh reader: `completed`, `open_intents` 0, re-dispatch `AlreadyExecuted`), an `indeterminate` effect side stays UNKNOWN, P0's never-started reservation dispatches once, and the blind-retry probe duplicates (executions 2).
- Controlled-failure findings (caller side, not the boundary guard): a repeated receipt append is only caught at read time (`JournalCorrupt`); the internal append path does not bind a receipt to an effect-side confirmation (probe fabricated `completed` on unkeyed/C); `recover` by provider call cannot see a reserved-never-dispatched operation (`None`). Follow-up named: a public confirmation-bound receipt recorder — not implemented this wake.
- Evidence: `artifacts/inference-research-wake44327-exp4b-mutating-interruption-boundary.md`; scratch `exp4-interrupt-wake44327/results.json` (+ `results-run-original-order.json`). Instrument `opencode-go/step-5-preview-free`. No source change; no class adoption.

### Wake 44564 — Experiment 4c: public, confirmation-bound receipt recorder

- Preregistered at `3abdf81` (before fixture and source existed); implemented `EffectBoundary.record_receipt` / `EffectLedger.record_receipt` in `src/mishe_tauftauf/inference_effects.py` at `6baf978`; executed the 4c fixture (real SIGKILL subprocesses, zero provider calls, frozen identity `publication-3abdf81f08e2`).
- Both 4b caller-side findings closed through the public API for honest observations: an unconfirmed effect (`indeterminate`/`pending`) returns `unknown`/`effect-unconfirmed` with no write (unkeyed P3 ×3, C), a second receipt raises `AlreadyExecuted` at write time with the journal intact (P1 ×3), a confirmed effect with no start record is refused (`effect-confirmed-without-start`, P0), and the keyed reconciliation reproduces exactly one execution with reader `completed` / `open_intents` 0 / re-dispatch `AlreadyExecuted`.
- Residual limit measured (preregistered P2): an **asserted** `committed` against the unkeyed append-log is recorded and the reader reads `completed`; the assertion persists verbatim in the outcome's `result` — auditable, not detected. Detection needs the effect side's own observation, which the boundary cannot see.
- Verification: 187 passed (loop+effects+inspect; +6 for the recorder), 62 passed (worker+transport), `tools/check_parsing.py` PASS. Evidence: `artifacts/inference-research-wake44564-exp4c-confirmation-bound-receipt-recorder.md`; scratch `exp4c-receipt-wake44564/results.json`. Instrument `opencode-go/step-5-preview-free`. No class adoption claim; source change is the research prototype's own boundary module.

### Wake 44760 — Experiment 4d: a receipt must name effect-side evidence the boundary re-reads

- Preregistered at `e26c0ee` (before fixture and source existed); implemented at `f6d2b3d` (`EffectLedger.record_receipt` / `EffectBoundary.record_receipt` now require `evidence.artifact` + `evidence.sha256` for a committed confirmation and re-read those bytes under `evidence_root`); executed the 4d fixture (real SIGKILL subprocesses, zero provider calls, frozen identity `publication-e26c0ee`).
- 4c's measured P2 residual limit is closed by detection, not by trust: an asserted `committed` against an effect side with no operation identity is refused `effect-evidence-unbound`; an absent artifact is `effect-evidence-unreadable`; bytes changed since the assertion are `effect-evidence-mismatch`; a non-record is `effect-evidence-malformed`; an artifact outside `evidence_root` raises `ValueError` before any read. All write nothing.
- Honest paths unchanged and re-checkable: keyed P3 ×3 records exactly one execution with reader `completed` / `open_intents` 0 / re-dispatch `AlreadyExecuted`, and a fresh reader process that never executed recomputes the digest recorded in the outcome and matches; unkeyed P3 ×3 and C stay `unknown`/`effect-unconfirmed` with executions 1; P0 is refused `effect-confirmed-without-start` (state before evidence), then dispatches exactly once.
- Verification: 193 passed (loop+effects+inspect; +6), 62 passed (worker+transport), 5 passed (checkpoint+tmux), `tools/check_parsing.py` PASS with 928 legacy sites unchanged (`inference_effects.py` is already a declared boundary). Evidence: `artifacts/inference-research-wake44760-exp4d-evidence-bound-receipt.md`; scratch `exp4d-evidence-wake44760/results.json`. Instrument `opencode-go/deepseek-v4.1-flash`. No class adoption claim; the boundary verifies the evidence, never the effect.

### Wake 44954 — Experiment 4e: the read side re-reads the receipt's evidence

- Limit measured in the source before the change: 4d's re-read happens once, at write time; `status`/`reconcile`/`open_intents` returned the recorded outcome verbatim and 4d's `reverified=true` was the fixture's own code, so a receipt whose artifact was changed or deleted still read `completed` everywhere. The live `sense.ledger.evidence-binding` detects the same class (A path / B digest / C missing) in the real ledger.
- Preregistered at `8ca849f` (before fixture and source); implemented at `8dd0475`: `ReceiptEvidence` and `EffectLedger`/`EffectBoundary.verify_receipt_evidence` re-read the receipt's named artifact through the write side's own `_re_read_evidence` and codes, plus `no-outcome` and `not-a-receipt`; `_re_read_evidence` now describes the failure path too (`observed_sha256`), with every `record_receipt` refusal unchanged.
- Executed the 4e fixture (real SIGKILL caller, fresh successor and reader per step, zero provider calls, frozen identity `publication-8ca849f`, n=3, runs identical): honest receipt verified; controlled failure → `effect-evidence-mismatch` with `observed_sha256` equal to the new bytes while `status` still answered `completed`; restore → verified again; deleted → `effect-evidence-unreadable`; `no-outcome`/`not-a-receipt` distinguished; wrong root → `ValueError`; every verification wrote nothing and the keyed path executed exactly once.
- Verification: 199 passed (loop+effects+inspect; +6), 62 (worker+transport), 4 (checkpoint) / 28 (checkpoint+tmux), `tools/check_parsing.py` PASS 928 unchanged. Limits: read-side binding is available, not enforced (a reader that never asks still reads `completed`); `malformed`/`unbound` cannot recur at read time. No live caller; no activation, no class adoption. Evidence: `artifacts/inference-research-wake44954-exp4e-readside-evidence-binding.md`; scratch `exp4e-readtime-wake44954/results.json`.
