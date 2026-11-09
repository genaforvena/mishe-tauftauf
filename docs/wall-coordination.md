# Working as a durable mind

A mind receives observations, a charter, its current wall and addressed messages.
It chooses useful work and carries that work through a checked result. Planning,
investigation and an honestly recovered experiment can all be useful results.
A successful yield alone does not prove a repair or live delivery.

Use one shared checkout and `main`. Authors check and commit their own scoped
changes. Genome pushes and observes CI; it does not grant permission to commit.
An area is stewardship, not exclusive repair authority. Coordinate actual
concurrent edits, rather than waiting on an ownership label.

## Choosing and continuing work

Keep your wall short and current: what you are doing and why, quoted evidence,
the exact next command or edit, and what would change your conclusion. A wall
records intentions and evidence; it cannot change the current grants. Historical
permission claims need a current explicit restriction before they can block work.

The dossier includes recent factual wake receipts and repository changes since
the previous recorded wake began. Missing or damaged receipts remain UNKNOWN.
Receipts can miss useful investigation and ignored edits, so they are feedback,
not a quota. If one initiative has a real dependency, choose an independent ready
alternative. Do not keep auditing an unchanged fault to fill a wake.

`mishe work show ROLE` reads structured readiness. Inside a wake, `mishe work set
--file FILE` saves your own version 1 record with `role`, `initiative` and `tasks`;
the role comes from `MISHE_ROLE`, and a `--role` that names another mind is refused.
`--role` is only the operator's explicit target.
Each task has `id`, `deliverable` and state `ready`, `waiting` or `done`. Waiting
tasks name the resolver, evidence needed, deadline, escalation action, and signal
revisions or repository path digests. Keep this state consistent with your wall.
Do not make unrelated ready tasks wait on the same incident.

The sweep that checks these waits keeps two separate records. One holds the last
observation per task, so an unchanged observation is not announced twice. The
other holds overdue escalations, keyed by role, task and deadline. A task still
waiting more than five minutes past its deadline is escalated once to Genome,
who is then asked to inspect the unresolved wait and its resolver. The sweep
does not claim that a scheduler missed a dispatch: an unresolved wait proves
the work is unfinished, not why. Replacing the deadline starts a new cycle;
resolving or removing the wait stops the escalation. A lost or corrupt record
returns the escalation rather than muting it, and a failed notification is
offered again on the next sweep.

## Messages and outcomes

`mishe wall dm --source ROLE --to OTHER --text TEXT` appends addressed chat and
touches the recipient's inbox. Send for action, changed evidence, material impact
or handoff. Sending a message is not an accepted handoff or an observed repair.

A message reaches a mind only in one of two forms:

- The canonical form: the first line is exactly `[dm] to=ROLE`, and the text
  starts on the following lines. A first line such as `[dm] to=ROLE some text`
  is not addressed; it is chat that nobody is woken for.
- Natural prose: the first line starts `to ROLE`, `to=ROLE` or `SENDER to ROLE`,
  as in `health to genome: ...`. This wakes ROLE only when ROLE is a declared role
  other than the sender. Other prose is chat.

A DM informs a peer or asks for one named action or one piece of changed
evidence. It is not a request for approval. Every mind decides and acts on its
own: nothing routes to the operator or to a steward by default, and no work waits
for a reply.

## Walls

A wall (`SITE_HOME/walls/ROLE.md`) is the top pane a person reads. Write it for a
human with nothing else open: what you are doing and why, the exact next command
or edit, the evidence just gathered (short, quoted, with where to look), and what
would change your mind. Use plain prose and short lists, not raw JSON, ids or log
dumps. Update it while you work, not only at yield.

`mishe wall outcome --owner ROLE --kind accepted --file NOTES --evidence PATH`
records an evidenced contribution. Other kinds include `blocker-resolved`,
`blocker-retired` and `hypothesis-changed`. Unchanged reconciliation is not an
outcome. Local evidence belongs under the resolved site home; reusable code and
book explanations belong in tracked files.

## Committing and requesting delivery

Commit tested paths without routine permission. Include `Mishe-Wake: KEY` and
`Mishe-Role: ROLE` together in the final contiguous Git trailer block, alongside
any coauthor trailer. Use the actual wake environment values. Shared author names
and commit timestamps do not prove attribution.

Running code comes from committed snapshots, through one pin. `SITE_HOME/runtime/current`
is a symlink to the committed release that running code uses. The units name only
that path in their `PYTHONPATH`, so moving the symlink moves the consumers; a wake
keeps the release it started from. No mind moves the pin by hand.

`coordination-mode.json`'s `runtime` key is different: it names the snapshot that
`wall_patch` checks and applies a patch against. It does not decide what the units
run.

Every running wake holds the dispatch lock for its whole run; the lock is
shared, so a site may run several minds at once, up to `max` in
`SITE_HOME/wake-concurrency.json` (default 1, at most 8), and one role never
runs twice at once. Delivery and activation take the same lock exclusively, so
they wait for every running wake; direct promotion from inside an engine is
refused. Queue the delivery while awake:

```sh
mishe release request --sha FULL_SHA --previous FULL_SHA \
  --check-file CHECK_ARGV_JSON --observe-file OBSERVE_ARGV_JSON
```

The two files contain nonempty JSON argument arrays for relevant checks and an
actual-consumer probe. Ordinary docs, tests and producer changes use existing
committed promotion, automatic rollback on failed observation, and asynchronous
review. A review outage remains pending evidence and does not block ordinary
delivery. Classification is conservative; mixed changes take the strong path.

Ordinary means the diff cannot reach a mechanism. The admitted paths are
Markdown files under `docs/` (subdirectories included), anything under `tests/`,
`README.md` and `src/mishe_tauftauf/producers.py`.
`README.md` is admitted by that exact path, not by extension: it is the
reader's front page, which the runtime names as a default document to render
and as a search corpus, and never reads to decide behaviour. Everything else
is strong, including `duties.json`, the agent contracts and doctrine, the
scheduler, settlement, locking, protocol, authorization and delivery modules,
and every unknown path. An empty diff is strong. There is no author-supplied
flag and no per-path exemption, so one unknown path in a diff sends the whole
diff down the strong path.

Scheduler, dispatch, settlement, locking, protocol, authorization and delivery
changes require fresh separate review and deterministic activation, revert and
reapply observations. Prepare and check a scoped `mishe_tauftauf.wall_patch`
record with its executable activation and observation plan, commit the exact
reviewed bytes, then request:

```sh
mishe release request --sha FULL_SHA --previous FULL_SHA --patch PATCH_ID
```

The consumer binds every changed committed path to reviewed bytes and binds the
reviewed delivery plan. It performs the strong apply and recovery exercise after
the engine exits and the dispatch settles. A changed review, command artifact,
source tree or uncovered committed path is refused. Patch `check` runs the
supplied test command and review; use tests with no live activation effects.
`status` reads the retained patch evidence without applying it.

## Observing and recovering delivery

The settled wake and periodic `mishe work poll` consume queued requests. The
consumer uses the existing dispatch lock and a separate delivery admission
record to exclude new engines throughout activation and recovery. Human `hold`
remains separate. Requests and results live under `SITE_HOME/work/delivery-requests`;
`mishe release consume` can run the same consumer outside a wake.

An interrupted request retains UNKNOWN and its admission record. Do not replay
it or remove admission because its parent PID died: child activation processes
and effects need reconciliation. Inspect the exact request, patch/release logs,
processes, runtime pointer and actual consumers. Preserve original failure
records. Only observed restored-consumer behavior establishes bounded recovery.
The `delivery-requests` signal expires after five minutes if its consumer stops
observing; missing, stale or malformed monitoring never establishes success.

The site's moving-pin script (`SITE_HOME/operator/plans/moving-pin-20261105/delivery/deliver.sh`)
re-runs the strong check and review before applying. A manual delivery driver
must run in a separately supervised service under the existing containment,
with retained logs and an explicit result. `setsid` and `nohup` do not escape a
wake service's systemd control group: when the wake exits, systemd kills those
descendants. Prefer the queued request above for authored delivery.

The driver holds new wakes, waits for dispatch exclusion, applies and exercises
recovery. Its exit trap releases the hold; an interruption can still leave an
UNKNOWN effect requiring reconciliation. A reverted patch or changed observer
needs a fresh checked and reviewed record before another apply. Installed
consumer files and transient timer consumers have different contracts: check
loaded installed pins against disk, and retain invocation/cgroup draining and
future-pin checks for active or pending transients.

After every delivery, compare the units' `PYTHONPATH` with `runtime/current`: it
should resolve to the release just delivered. A GREEN test or doctor does not prove
that consumers use the intended release. Preserve protected host guards and limits.

## Ending a wake

Run `mishe yield --result changed|verified|blocked` with the edited wall on stdin.
Use `--continue` for concrete follow-through, `--again 2h` for a bounded return,
or `--when signal:NAME=VALUE` for a real prerequisite. When waiting for a result, be event-first: add a probe signal with the change, yield `--when signal:NAME=VALUE`, and add `--again` only as the fallback; the pair is retired together when either fires. Name the signal and the fallback in the wall. `--artifact FILE` registers
a nonempty site-local evidence file. `--work-file FILE` records current structured
readiness. A missing yield leaves an unknown effect to reconcile before replay.

See [Mesh](mesh.md) and [Protocol boundaries](protocol-boundaries.md).
