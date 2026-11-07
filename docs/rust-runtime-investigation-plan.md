# Rust runtime migration initiative

## Current owner instruction: implementation and observed live delivery

The owner has superseded the original planning-only assignment. The dedicated
**inner `mishe-self-development-current:rust-plan` mind, using Claude Haiku**,
owns investigation, implementation, scoped commits, delivery coordination,
actual-consumer observation and continuing migration. `7:omp` is only completing
the autonomy setup/handoff; it is not the migration owner. The remaining
investigation sections below describe the initial stage, not a restriction on
the newly authorized implementation. No further routine implementation approval
is required. Apply the existing ordinary/strong-change delivery rules.

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
limits, separate fresh strong-change review, or deterministic activation,
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
- [ ] Apply existing ordinary versus strong-change policy. Scheduler, dispatch,
  settlement, locking, protocol, authorization and delivery/bootstrap changes
  require separate fresh review and deterministic activation/revert/reapply
  checks. Define these future checks without executing or weakening them.

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
fresh review, containment or activation/revert/reapply checks. Define precisely
what that first slice owns; do not imply a full runtime rewrite. Include a
24-hour critical path with effort estimates, prerequisites, actual-consumer
acceptance and rollback, plus an explicit go/no-go if the evidence does not
support safe delivery in that time. This investigation remains planning only;
implementation requires a separate assignment after the proposal.
