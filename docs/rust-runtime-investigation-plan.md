# Rust runtime migration initiative

## Current owner instruction: implementation and observed live delivery

The owner has superseded the original planning-only assignment. The dedicated
**inner `mishe-self-development-current:rust-plan` mind, using Claude Haiku**,
owns investigation, implementation, scoped commits, delivery coordination,
actual-consumer observation and continuing migration. `7:omp` is only completing
the autonomy setup/handoff; it is not the migration owner. The remaining
investigation sections below describe the initial stage, not a restriction on
the newly authorized implementation. No further routine implementation approval
is required. Apply the existing delivery rules: one path for every change, with
the scheduler, dispatch, settlement, locking, protocol, authorization and delivery
changes keeping their deterministic activation, revert and reapply checks.

Drive meaningful responsibilities toward small composable POSIX commands,
actually replacing selected Python implementations with Rust and retiring their
obsolete paths. Keep exactly one authoritative live implementation per
responsibility. Use measured benefits and preserve existing contracts, evidence,
UNKNOWN states, containment, reconciliation and file/systemd coordination.
A first running Rust core slice within one day is the owner's target; report
feasibility and evidence rather than promising the entire core in that time.

Carry each completed scoped change through the existing committed-snapshot
release mechanism to an observed live consumer; code or tests alone are not
completion. Do not activate untested or unfinished edits. Distinguish committed,
reviewed, activated, observed and recovered states. Continue autonomous bounded
work and follow-up without waiting for another implementation assignment.

The owner accepts bounded operational risk. This does not remove protected host
limits, the fresh review that follows delivery, or deterministic activation,
revert and reapply checks. Coordinate the current release writer and overlapping
unit edits rather than moving pins or regenerating units from a second path.

`0:op` is asked to maintain recovery coverage if a migration failure prevents
Mishe restoring itself. Recovery is not complete with a one-time revival:
preserve the failure, diagnose the failed recovery boundary, repair the reusable
self-restoration path through the proper delivery gate, and exercise controlled
recurrence plus missing/stale detector behavior. Name the monitor, resolver,
bounded detection interval and next recovery observation. No weakening host
guards, blindly replaying unknown effects or claiming unobserved recovery.

Autonomy setup must hand off the existing interactive session without concurrent
interactive/autonomous migration engines. Preserve evidence and the inner window.
Verify the Haiku pin, real wake, canonical wall/yield, scheduled continuation,
expiring signals and bounded failure routing on the running consumer. `7:omp`
finishes this setup with the existing delivery owner, then hands ownership to
this inner mind and reports the verified result to `0:op` and the requester.

## Continuing sensing responsibility

The resident rust-plan mind owns observing the existing runtime first, then
extending those observations as it replaces responsibilities with Rust. Reuse
existing senses before creating new bounded deterministic producers. Coordinate
shared acquisition with Senses; rust-plan remains responsible for interpretation,
regressions, live consumer evidence and useful migration-specific coverage.

Its canonical top pane must expose the relevant current observations with age,
source/deployed version and baseline comparison where meaningful: CPU, peak RSS,
latency, invocation/failure frequency, resource pressure, authoritative consumer
implementation and recovery status. A fresh wall or plan is not performance or
delivery evidence. If the normal pane only summarizes signal names/states,
add bounded readable detail through the existing presentation path; keep expensive
sampling out of rendering and avoid a new polling framework.

Every migration slice includes tested observation coverage, real running caller
measurements before/after, explicit bounds/thresholds with measurement limits,
expiring UNKNOWN on missing/stale/broken input, a named failure resolver and
bounded routing, controlled detector-failure exercise and observed recovery.
Evolve subscriptions and detectors with the actual responsibility. Retire old
coverage only after equivalent replacement coverage is tested. Avoid numerical
noise causing wake storms; keep samples as evidence and use meaningful state
changes/thresholds for autonomous wake triggers. Do not mark the initiative done
until actual autonomous wake/yield/follow-up and top-pane observations are seen.

## Initial investigation brief (historical scope, superseded above)

This authorizes investigation and a written migration proposal only. It does not
authorize implementing Rust, changing production consumers, or activating releases.

## Assignment and order

1. `7:omp` owns execution of this investigation brief and coordinates with `0:op`.
   First read the operator dashboard, current walls, AGENTS.md, and live recovery
   status. Establish available resource headroom and overlapping work. Do not run
   stress tests, build toolchains, or interfere with recovery or other windows.
2. After those checks, **7:omp creates the dedicated tmux window** for this work.
   Choose an unused index and a clear name such as `rust-plan`; preserve existing
   panes. Assign **Claude Haiku**, not a local model or Codex substitute. Verify
   the selected provider/model from the session, and record the exact selection
   and window in the site-local wall. If unavailable, report that fact to the
   requesting session rather than silently substituting. Keep one worker, bounded
   output and no ongoing polling loop. Pass this document and the original user
   requirements to the worker. Window creation belongs to 7:omp, not the author
   of this brief.
3. Haiku performs the investigation below; 7:omp checks the cited source and
   evidence, resolves omissions, and commits the resulting documentation on main.
   Inform 0:op when the window starts and when the final artifact is committed;
   communicate material contention or missing evidence promptly. Operator recovery
   is context to preserve, not a routine approval gate.

## Outputs and evidence

Update this brief's checkboxes as work completes. Create
`docs/rust-runtime-migration-plan.md` as the final,
evidence-based proposal. Keep raw measurements, commands, transcripts and local
notes under a directory resolved from `$MISHE_SEED_HOME` or the live site home;
never commit runtime artifacts. Record progress, exact next action and evidence
in the dedicated worker's site-local wall. Follow the normal yield protocol if
executing as a Mishe wake.

### 1. Map the real running system

- [ ] Record checkout commit, dirty paths, live committed snapshot and any
  differences. Initial reconnaissance saw main at `3ca418c4`, with unrelated
  untracked `checks/work_balance.py` and `tests/test_work_balance.py`; refresh
  these facts rather than treating them as current. Preserve others' work.
- [ ] Inspect `pyproject.toml`, `bin/mishe`, `src/mishe_tauftauf/`, `checks/`,
  `tools/`, relevant tests, `parsing-policy.json`, `docs/mesh.md`,
  `docs/wall-coordination.md`, `docs/protocol-boundaries.md`, and recovery docs.
  Locate installed unit fragments/drop-ins and effective settings using bounded
  `systemctl --user cat/show` reads. Record ExecStart, frequency, timeout,
  KillMode, memory/CPU/task constraints and environment for actual consumers.
- [ ] Trace CLI imports, feed parsing/indexing/streaming/verification, signals,
  subprocess wrappers, dispatch/locking/settlement/reconciliation, host probes,
  and release paths. Starting points include `feed.py`, `feed_errors.py`,
  `core/dispatch_record.py`, `signal_store.py`, `live_signal.py`,
  `release_boundary.py`, `release_promotion.py`, `release_readiness.py`, and
  `release_retention.py`. Follow actual call sites; these are not target choices.
- [ ] Read recent history and incident evidence for architectural changes and
  observed stalls, OOMs, startup costs or repeated work. Distinguish current
  failures from historical ones. Resource contention and a recent recovery were
  visible during this handoff; verify status with 0:op before measurements.

Acceptance: a concise source-and-consumer map with file references, deployment
provenance, unit constraints and explicit UNKNOWNs. No inferred live success.

### 2. Establish a cheap baseline before ranking migration targets

- [ ] Measure representative read-only CLI invocations from the actual pinned
  snapshot: elapsed/user/system time, peak RSS and import cost. First inspect
  commands for side effects; use Python import-time tracing only on a safe path.
  Bound repetitions (initially three per command), distinguish first observed
  run from warm repeats, and retain all samples and measurement overhead.
- [ ] Use existing service accounting/journals to estimate execution frequency,
  aggregate CPU, peak memory and failure frequency before adding measurements.
  Read bounded excerpts. Do not clear caches, force timer runs, kill consumers,
  install dependencies or load local models to obtain a benchmark.
- [ ] On preserved copies only, measure representative feed size, parse/verify
  cost and scaling. Record sample provenance, hardware/load and measurement
  limitations. Treat insufficient measurements as UNKNOWN, not proof of benefit.
- [ ] Separate demonstrated costs, causal hypotheses and language-independent
  fixes (for example unnecessary imports, full rescans or scheduling frequency).
  Compare plausible Python simplification against a Rust boundary before choosing.

Acceptance: reproducible bounded commands and numerical baseline table; no claim
of Rust speedup without measured evidence. Stop measurement if headroom is poor;
document the missing sample and safe trigger for collecting it later.

### 3. Select a sequence, including the option to keep Python

- [ ] Rank candidates by measured aggregate cost, reliability benefit, boundary
  clarity, migration/recovery risk and maintenance/build cost. Include rejected
  candidates and reasons. Do not assume dispatch or the CLI should move first.
- [ ] Select the safest first meaningful candidate only after baselining. A
  small read-only responsibility may be preferable if evidence supports it;
  a Python-only improvement or no migration is an acceptable conclusion.
- [ ] Define independently deliverable future steps. For every step name source
  paths, callers, exact interface/state contracts, obsolete machinery removed,
  required tests, numerical benefit threshold derived from baseline, invariants,
  actual-running-consumer observation, rollback trigger and recovery procedure.
  Mark dependencies and explicit abandon conditions. Do not invent signatures
  or precise performance claims without source evidence.

Each successful future step must leave exactly one authoritative implementation
of each responsibility and retire its predecessor. Temporary isolated validation
is allowed; permanent shadow execution, compatibility runtimes and duplicate
paths are not. Preserve file coordination and systemd, human/agent CLI contracts,
protocols, historical records, durable effects, explicit UNKNOWN, bounded work,
containment, and reconciliation before replay. Keep Python where simpler. Avoid
resident services, IPC frameworks and speculative refactoring.

### 4. Specify failure, release and recovery acceptance before migration

- [ ] For each proposed boundary describe termination before/after durable writes,
  process handoff/exec failure, child exit propagation, stdout/stderr contracts,
  systemd timeout/SIGTERM/SIGKILL and descendant containment, lock lifetime,
  partial/truncated/malformed records, rename/fsync durability and interrupted
  updates. State which behavior already exists and which remains unproven.
- [ ] Plan controlled fault tests on isolated fixtures, including replay after
  unknown effects. Require recurrence detection, named resolver and bounded
  routing interval; exercise missing/stale/broken monitoring as UNKNOWN or RED.
  Never erase original failure evidence or substitute one passing test for
  actual consumer observation. Do not perform live fault injection in this task.
- [ ] Specify committed-source-to-binary provenance, pinned Rust/toolchain and
  dependencies, host target/linking assumptions, build resource budget, artifact
  integrity, release layout, interrupted-build/promotion behavior, pin consistency,
  prior artifact retention and rollback without rebuilding on the live host.
  Distinguish build reproducibility from merely recording source provenance.
- [ ] Apply existing delivery policy. There is one path for every change, and no
  path classification. Scheduler, dispatch, settlement, locking, protocol,
  authorization and delivery/bootstrap changes require deterministic
  activation/revert/reapply checks and a fresh review after delivery. Define these
  future checks without executing or weakening them.

### 5. Review and finish the planning deliverable

- [ ] 7:omp verifies source references, baseline provenance, all nine architectural
  constraints, measurable acceptance, retirement and recovery for every proposed
  step, and conditions for abandoning Rust when cost/benefit fails.
- [ ] Check document whitespace and diff, stage only the planning documents and
  commit on main. No runtime edits, Cargo scaffolding or activation. Report the
  commit and final document to the requesting session and 0:op, with remaining
  UNKNOWNs clearly distinguished from conclusions. End at this planning boundary;
  later agents need a separate implementation assignment.

The final proposal must explain why the sequence is justified at Mishe's current
scale, how ongoing development continues between steps, and what would change
the recommendation. Prefer deleting machinery over accumulating it.

## Additional direction from the owner

Prefer independent pieces that are, in essence, POSIX commands: useful bounded
programs composed through arguments, files, streams and exit statuses. Assess
this as a general Mishe design preference, including Python commands; it is not
a reason to split every function into a process. Choose meaningful responsibilities
and measure process/serialization cost. Preserve signal handling, containment,
durable effects and clear error/UNKNOWN contracts across command boundaries.

## Owner clarification: real replacement and a one-day target

The intended direction is a small, composable POSIX-style Mishe with worthwhile
responsibilities actually migrated to Rust, not a Rust sidecar or permanent
parallel implementation. Each proposed cutover must name the authoritative
consumer, the predecessor it retires, and removal of obsolete paths. Preserve
Python where justified and reject unsafe or unhelpful migration candidates.

The owner would like a running first Rust core slice within one day. Treat this
as a planning target, not a guaranteed deadline or permission to skip baseline,
containment or activation/revert/reapply checks, or the fresh review that
follows delivery. Define precisely what that first slice owns; do not imply a
full runtime rewrite. Include a 24-hour critical path with effort estimates,
prerequisites, actual-consumer acceptance and rollback, plus an explicit
go/no-go if the evidence does not support safe delivery in that time. This
investigation remains planning only; implementation requires a separate
assignment after the proposal.

### 10.69 The scan index replaces the corpus parse (2026-11-18)

**A/B first, on the live corpus and pin `76c4256b`, same host guard:** parsing
receipts 2.044 CPU-s, indexed reading 0.0167 CPU-s, 2,139 identical rows (first
2026-10-19T23:59:48Z, last 2026-11-18T10:17:19Z; artifact
`artifacts/rust-plan-20261107/check_scan_index.py`). That is ~2.0 CPU-s per
readings scan, once per 600 s — the slice clears its own ≥1 CPU-s bar, and it
retires a cost that grows with the corpus because nothing prunes receipts.

**Shape (commit `8c568a59`).** `discovery.scan()` writes the receipt, then appends
one line to `discovery/scan-index.jsonl`: the receipt's filename stem, its
`created`, and every verified observation whose sample coerces to an int. The
receipt stays the record. `read_scan_series` answers from the index when it
accounts for every receipt (by filename stem, so no receipt is opened) and
otherwise parses the receipts once and rewrites the index — a missing, short,
torn, malformed or older-format index rebuilds instead of dropping a scan, and a
line whose receipt is gone is ignored. Both paths take their rows from one
`_verified_samples` map, so they cannot drift. An append failure is recorded as
`scan_index=failed: …` in `discovery/attempt.json` and never fails a published
receipt.

The index is 441 KB for 2,139 receipts (183 bytes/line), written in one
`O_APPEND` write; the reader's remaining cost is the directory listing plus
441 KB of JSON, both constant in the corpus size. Seven tests in
`tests/test_scan_series_index.py` pin the differential equality, the fast path
(receipts unreadable but still listed), and every rebuild trigger.

**Peak RSS is UNKNOWN here.** `ru_maxrss` in this container starts from the
parent's high-water mark: a bare `python3` already reports 463,772 KB, so no RSS
delta from these runs means anything. The live producer's own `MemoryPeak` after
delivery is the observation that settles it.

**Delivery, first attempt (2026-11-18).** The request for `8c568a59` failed its
check on one test unrelated to this change:
`tests/test_engine_progress_deadline.py::test_an_engine_waiting_on_a_live_child_is_not_idle`,
cut at "exceeded 1s (no output for 2.5s)" with 5,398 tests passing. Both
live-child fixtures in that file started the engine silent and expected the fork
to be visible inside `idle_gap`, so their verdict rode on interpreter-start
latency; the fork is visible in 0.04–0.10 s on an idle host and the file passed
12/12 three times at that checkout. `90d0f135` forks the child before the first
output line in both fixtures (assertions and engine behaviour unchanged), after
a deterministic reproduction: delaying the fork past `idle_gap` gives the gate's
exact message at 2.53 s, forking first exits 0 at 5.11 s. Delivery was then
re-requested for `90d0f135`, previous pin `76c4256b`.

**Delivered and observed (2026-11-18 11:32Z).** Release `90d0f135…` reached the
pin with `activated: true`, `observed: true` and consumer `mishe-arm.service`
`result: success`; its check was 5,399 passed, 39 skipped in 842 s. The live
observation printed "index-backed series live: 2144 rows, 0.0169 CPU-s, 2144
receipts covered" against 2.044 CPU-s to parse the same corpus before the slice.
At 11:39Z the live host had 2,144 receipts and 2,144 index lines (2,139 lines
against 2,143 receipts at 11:14Z), so the producer rebuilt and keeps the index
current. The stale prediction that `attempt.json` would gain a `scan_index`
field was wrong: it records only created/scan_id/stage/status; row parity with
the receipts is the live check.

**Peak RSS, observed after delivery (2026-11-18 12:13Z).** The live
`mishe-producer-readings.service` journal now carries the systemd peak the
earlier wake lacked. Before pin `90d0f135` (08:04–11:25Z), 18 samples ran
881.1–882.1 MB with one 956.5 MB outlier at 10:07Z; after it (11:35–12:08Z),
four samples ran 882.1–882.4 MB. The slice retired scan-read CPU, not producer
peak memory; the earlier in-process `ru_maxrss` gap stays unusable, but the
producer-level RSS UNKNOWN is closed. A fresh pinned-bytes observation at
12:11Z (`PYTHONPATH=runtime/current/src`) had `discovery._disk_trend` return
`state=verified samples=2148 span_h=708.14` in 0.0212 CPU-s, with
`read_scan_series` at 2,148 rows / 0.0187 CPU-s and 2,148 receipts against
2,148 index lines. The newest receipt names producer
`releases/90d0f135…/src/mishe_tauftauf@90d0f135…`, so the running consumer uses
the delivered bytes.

The further test-only fix `b18af2ac` passed its own check (`code 0`, 11:28Z) but
is stranded in `delivery-busy`: its recorded previous pin `76c4256b` was
superseded by `90d0f135`. Its tree also carries other minds' in-flight commits,
including the codex-engine change, so re-requesting it from this role would
promote work outside this slice. Genome owns that disposition.


### 10.70 The live role read is not the rebuild (2026-11-23)

The forced fold that `checks/rust-measurement.py` times is the cache-miss price,
which a wake does not normally pay: `role_entries` serves the persisted role
cache and extends it by the bytes appended since. The check now runs a second,
separate-process `live` reading per role and records both. At pin `e85d4411`
(16:48Z, load1 8.18, the check's own host guard passed) the run was GREEN:

- senses: forced reader 4.734 + children 4.557 = 9.291 CPU-s; live 0.170 CPU-s;
  36,388 entries, `view_digest` equal.
- genome: forced reader 4.484 + children 3.958 = 8.443 CPU-s; live 0.111 CPU-s;
  30,340 entries, `view_digest` equal.

The differential is the digest equality: a cached view that disagrees with the
rebuilt tape is RED (`disagree`), and `tests/test_rust_measurement.py` pins that
case with a stale-cache fixture. The 33 measurement tests pass.

A `disagree` line also says `tape moved during the pair: True|False`, from the
tape's inode, size and mtime read before and after the forced and live folds
(commit a9a40c2c). `True` means the two views were read from different tape
states, a sampling race rather than a cache fault; the verdict is still RED
either way, so read the field before blaming the cache.

What this settles: on a cache hit the role read costs about 0.1–0.2 CPU-s, so no
Rust seam exists in the role read on that path. What it does not settle: how often
a real wake misses the cache. The live reading is taken on a hit at this pin, and
the history carries no cache-miss counter, so the frequency stays UNKNOWN. The safe
trigger to collect it is a role-cache miss counter in the feed, read with the
`live` reading on the next pin change.

### 10.71 Every role cache is a hit-extend at 01:50Z (2026-11-25)

10.70 left the cache-miss frequency UNKNOWN and named a role-cache miss counter as
the safe trigger. A cheaper deterministic snapshot answers the narrower question
first — is any role paying the cold full fold right now? — without a counter or a
fold. The probe
`.mishe-tauftauf/artifacts/rust-plan-20261107/probe_role_cache_state.py` opens the
live tape under the shared lock and calls `_load_role_cache` for each role, the
same predicate `role_entries` uses, and classifies the result.

At 01:50Z, 30/30 role caches are HIT-extend, 0 MISS. Every cached offset is behind
the live offset (e.g. rust-plan +487 B, genome +14,184 B, omp +32,284,437 B), so a
fresh reader extends a valid projection and never rebuilds. The matching live
reading at this pin (`checks/rust-measurement.py --roles rust-plan`) is 0.007 CPU-s
with `view equal`, against 3.547 reader + 3.336 children = 6.884 CPU-s for the
forced fold over the same 2,156 entries. The cold full fold — the only path where a
Rust seam could pay — is not being paid at this snapshot.

What this settles: the live role read is on the extend path across all roles now,
so the costliest seam is not the role read. What it does not settle: the frequency
over time; one snapshot is not a rate. The 10.70 counter stays the trigger for the
rate, and this probe is the bounded action that shows the current state is all-hit.

### 10.72 The cache-miss rate is zero over 475 readings (2026-11-28)

10.70 left the cache-miss frequency UNKNOWN and named a role-cache miss counter
as the safe trigger. The check now records `role_cache_miss` per reading (both
forced and live) and appends it to `rust-measurement-history.jsonl`, so the rate
is answerable from the history without a new counter in the feed.

At pin `c281acbc` (2026-11-28 10:38Z, host load1 10.03, memory pressure 0.00,
MemAvailable 12.1 GB), a fresh run of `checks/rust-measurement.py` was GREEN:
senses reader 3.876 + children 4.929 = 8.805 CPU-s, 111 MB, 36,389 entries,
live 0.123 CPU-s, view equal, cache hit; genome reader 3.616 + children 3.832 =
7.448 CPU-s, 107 MB, 30,344 entries, live 0.088 CPU-s, view equal, cache hit;
binaries 8 checked. The history then carried 475 readings (forced and live),
0 misses.

What this settles: the cold full fold is not being paid by any role in the
history; every live reading is a cache hit. The 10.70 counter is therefore not
needed as a new feed counter — the history's per-reading flag answers the rate.
What it does not settle: a miss could still occur on a role that is not in the
default `--roles` set (senses, genome), or on a tape state the current
snapshot does not cover. The 10.71 probe remains the bounded action for the
current state; the history is the rate.
