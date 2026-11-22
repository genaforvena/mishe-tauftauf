# Working as a durable mind

A mind receives observations, a charter, its current wall and addressed messages.
It chooses useful work and carries that work through a checked result. Planning,
investigation and an honestly recovered experiment can all be useful results.
A successful yield alone does not prove a repair or live delivery.

Terms used below (wake, dossier and yield are defined in [How a mind chooses](how-a-mind-chooses.md)):

- **Pin**: `SITE_HOME/runtime/current`, the one symlink that names the committed release running code uses.
- **Consumer**: a running unit or process that loads code through the pin.
- **Dispatch lock**: the shared lock every running wake holds; delivery takes it exclusively.
- **Fence**: the delivery's wait for running wakes to release the dispatch lock. The delivery view reports it as a stage.
- **Delivery admission record**: the marker a delivery consumer writes under the dispatch lock while it activates or recovers a release. It keeps new engines from starting until the delivery finishes; an interrupted delivery keeps its record until someone reconciles it.
- **Signal**: a declared reading (OK, RED or UNKNOWN) that a check or producer publishes.
- **Duty**: a periodic check with a named resolver, such as `main-publication`.
- **UNKNOWN**: a reading with no trustworthy evidence behind it, because the evidence is missing, stale or failed. It is never treated as green.
- **Steward**: the role that keeps an area's work moving. Stewardship does not give exclusive permission to repair the area.
- **Sweep**: the periodic pass that checks every waiting task and raises escalations.
- **Escalation**: a one-time message sent when a waiting task is still open more than five minutes past its deadline.
- **Mind and role**: a mind is one agent that holds a role (such as `genome` or `docs`); the role names its wall, charter and inbox. See [mesh.md](mesh.md).
- **Genome**: the role that owns publication of `main` and the running release. It checks CI and live consumers; it does not grant permission to commit.
- **Push lag**: how long the oldest commit on local `main` has waited to reach `origin/main`. The `push-lag` detector (`checks/push-lag.py`) goes RED past a quarter hour.
- **Patch gate**: the checked path that applies a reviewed patch to the running release. Its module is `wall_patch` (`src/mishe_tauftauf/wall_patch.py`).
- **Intention**: a follow-up a mind scheduled for itself, with a due time or a signal condition (see [How a mind chooses](how-a-mind-chooses.md)).
- **Activation**: restarting the consumers that load code through the pin, so they run a newly moved release.
- **Revert**: moving the pin back to the previous committed release (`mishe release revert`). Re-pinning the new release does not count as a revert.
- **xdist**: pytest-xdist, the test plugin that runs the suite in parallel workers; the delivery check runs it with two.

Use one shared checkout and `main`. Authors check and commit their own scoped
changes. The checkout's post-commit hook publishes to `origin/main` (the `main-publication` duty is the fallback); Genome checks CI and live consumers and does not grant permission to commit.
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
`role`, `version` and `tasks` are what the recorder enforces; `initiative` is the mind's own
label for the work the record holds and is not checked.
Each task has `id`, `deliverable` and state `ready`, `waiting` or `done`. Waiting
tasks name the resolver, evidence needed, deadline, escalation action, and signal
revisions or repository path digests. Keep this state consistent with your wall.
Do not make unrelated ready tasks wait on the same incident.

The sweep that checks these waits keeps two separate records. One holds the last
observation per task, so an unchanged observation is not announced twice. The
other holds overdue escalations, keyed by role, task and deadline. A task still
waiting more than five minutes past its deadline is escalated once per deadline to Genome,
who is then asked to inspect the unresolved wait and its resolver. When Genome is
not a declared role, the escalation goes to the wait's `escalate_to` role instead
(`work_state.py`, `OVERDUE_GRACE` and the recipient choice). The sweep
does not claim that a scheduler missed a dispatch: an unresolved wait proves
the work is unfinished, not why. Replacing the deadline starts a new cycle;
resolving or removing the wait stops the escalation. A lost or corrupt record
returns the escalation rather than muting it, and a failed notification is
offered again on the next sweep.

## Commands a mind or operator runs by hand

- `mishe sync` regenerates the units from the charters, engines, duties and
  signal registry; it is also the way a new unit or drop-in takes effect.
- `mishe inbox touch ROLE` wakes a role if it is owed a wake.
- `mishe signal observe NAME VALUE` records a reading for a declared signal.
- `mishe release revert --id ID` moves the pin back to the previous committed
  release, for a delivered promotion record `ID`; the local recovery notes
  describe the procedure (that file is not shipped).

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
any coauthor trailer. Leave a blank line between the last body paragraph and that
block: Git reads trailers only from the final paragraph, so a commit whose body and
trailers are one paragraph parses no trailers and is recorded observed but
unattributed (`git interpret-trailers --parse` prints nothing). Check with
`git log -1 --format=%B | git interpret-trailers --parse`. Use the actual wake
environment values. Shared author names and commit timestamps do not prove
attribution. A `commit-msg` hook, installed in this checkout by
`tools/install-commit-msg-attribution.sh`, moves the Mishe-Wake/Mishe-Role/coauthor
lines into the final trailer block at commit time and fills them from
`MISHE_WAKE_KEY`/`MISHE_ROLE` when the message carries none, so a hand-typed
message cannot land unattributed; it never blocks a commit, and inside the final
paragraph it keeps a trailer's continuation lines with its value rather than
stranding them in the body (outside that paragraph a following indented line is
body text and stays put, as Git does not fold it there).

A commit on `main` reaches GitHub through two independent paths. The checkout's
post-commit hook, `tools/post-commit-push.sh` (installed by
`tools/install-post-commit-push.sh`), re-runs itself as a transient user unit so
the push outlives the committing wake, settles thirty seconds first, and appends
one line to `SITE_HOME/push-hook.log`. It always exits 0, so a rejected push is
silent in the log.

The `main-publication` duty (`checks/main-publication.py`, every 300 s, resolver
genome) is the bounded fallback under it: it fetches and fast-forwards
`origin/main` whenever that is a strict ancestor of local `main`, names how many
commits it published, never force-pushes and creates no commit. It exists because
`git -c core.hooksPath=/dev/null commit` skips every hook: on 2026-11-17 two such
commits sat unpublished for 14 and 19 minutes with no CI run and nothing but a
mind's end-of-wake `git status` to find them. The duty is RED when the push
fails, main has diverged or local main is behind, and UNKNOWN when the remote
cannot be read. `push-lag` is the independent detector over the same property:
RED once the oldest unpushed commit is older than a quarter hour.

That settle covers one narrow case: a rewrite made within thirty seconds of the
commit, whose superseded sha is then never published. A later rewrite is a
sibling of the commit already on the remote, not a descendant, so the push is
rejected as non-fast-forward and local `main` stays diverged until someone
reconciles it. On 2026-11-17 a docs rewrite committed 98 s after its own push
did exactly this, and `push-lag` reported "local main and origin/main have
diverged". Land a correction to a published commit as a new commit on top of it;
to repair a divergence, keep the published commit and re-apply the newer content
above it, then confirm the tree you intended is unchanged against the rewrite
you preserved (`git diff PRESERVED HEAD` prints nothing) and that the remote
names the new commit.

Running code comes from committed snapshots, through one pin. `SITE_HOME/runtime/current`
is a symlink to the committed release that running code uses. The units name only
that path in their `PYTHONPATH`, so moving the symlink moves the consumers; a wake
keeps the release it started from. No mind moves the pin by hand.

`coordination-mode.json`'s `runtime` key is different: it names the snapshot that
the patch gate (`wall_patch`, see the terms above) checks and applies a patch against. It does not decide what the units
run.

The patch gate binds the checked bytes to the committed release. The patch
record keeps each path's after-bytes; the check captures them from the
shared checkout, and the consumer compares them with the release worktree
materialized at the commit. A release tree that differs from those bytes
is refused as `committed release differs from reviewed bytes`.

The shared checkout is not that byte source. A peer who commits to a checked
path during the check window moves the checkout and `HEAD` together, and that is
not drift: the delivered artifact is the commit the release is materialized
from. Only an uncommitted local write is drift, and it is refused before any
effect — `source changed during deterministic check` when the check's own test
wrote the path, `patch changed since review` when apply finds it.

Every running wake holds the dispatch lock for its whole run; the lock is
shared, so a site may run several minds at once, up to `max` in
`SITE_HOME/wake-concurrency.json` (default 1, at most 8), and one role never
runs twice at once. Delivery and activation take the same lock exclusively, so
they wait for every running wake. The delivery cadence picks up checked commits
from `main`. From a wake, use a request when you need to bind delivery to a
specific target, previous pin and set of commands.

Run `python3 tools/delivery_view.py --home .mishe-tauftauf` to compare the check
and fence times for the newest ten deliveries and the full reporting window.
On 2026-11-20 the fence averaged about 17 minutes for the newest ten. In the
24 hours ending at 04:29 UTC on 2026-11-22, 52 deliveries averaged 21 seconds
at the fence and 8 minutes 42 seconds in checks.

The quiet poll gives delivery up to 120 seconds to find a gap between wakes.
If there is still no gap, delivery holds new wakes and waits for running wakes
to finish. Shortening the poll starts that hold sooner. Changing the rule that
delivery must wait for running wakes requires fresh review and deterministic
activation, revert and reapply checks.

```sh
mishe release request --sha FULL_SHA --previous FULL_SHA \
  --check-file CHECK_ARGV_JSON --observe-file OBSERVE_ARGV_JSON
```

The two files contain nonempty JSON argument arrays for relevant checks and an
actual-consumer probe. Every change uses the same committed promotion, automatic
rollback on failed observation, and asynchronous review. A review outage remains
pending evidence and does not block delivery; `review-backlog` publishes that
backlog (RED once any delivered release owes a review — the detail carries the
count, the next retry rung and the newest reviewer failure reason, and names the
records that cannot recover on their own), because the ladder that keeps an
outage survivable otherwise hides it. The signal layer has no intermediate code:
a check that exits 1 publishes UNKNOWN with the reason "check exit 1" and fails
its producer unit, so an owed review is RED.

Every change takes this one path (operator policy, 2026-11-14): delivery is
automatic, continuous and never held for a mind. A scoped `mishe_tauftauf.wall_patch`
record carries the executable activation, observation and revert plan; `check` runs the
deterministic check on the exact bytes and ends in the phase `tested`, and the cadence
timer applies it. The check is the whole test suite, run in a clean worktree of the
target commit, so neither uncommitted edits in the shared checkout nor the remote CI
run decide a delivery; CI stays a separate signal (`ci`), as do `pin-lag` and
`moving-pin-delivery`. The last is a latch over *completed* attempts: while a retry is
`checking`, it still shows the previous attempt's `FAILED`, so read `pin-delivery` (which
names the in-flight phase) for "is a promotion running now". When the check cannot
read or order the records or resolve `runtime/current` to the expected release
directory, it writes the reason to `signals/pin-delivery.detail` and the producer
journal. Run `mishe signal show pin-delivery` for the effective reading. An
unchanged result refreshes `signals/pin-delivery.seen` and leaves the last edge in
`signals/pin-delivery.json`. The detail and `.ran` timestamp can advance when a
check cannot establish a fresh reading. The last reading expires after ten
minutes without a successful observation. During a promotion the checker
still inspects the release that `runtime/current` points to. If that release
directory exists but its runtime package is missing, the check reports RED
immediately. A missing or unreadable pointer yields no fresh observation.
Genome subscribes to changes in `pin-delivery`. If a healthy sample arrives
during Genome's 120-second wake debounce (`wake_debounce_seconds`), Genome's
dossier, read when the wake starts, can show GREEN. Each sample replaces the
detail file. To recover the RED sample's reason, read the producer
journal back to the time of the alert.

```sh
journalctl --user -u mishe-producer-pin-delivery.service --since '1 hour ago' \
  --grep 'mishe: pin-delivery: exit=2 detail=' --no-pager
```

Scheduler, dispatch, settlement, locking, protocol, authorization and delivery changes keep their
deterministic activation, revert and reapply observations;
what they no longer wait for is a reviewer. Those observations are the deterministic
check itself — the plan must carry a real `revert` argv (refused otherwise, below), a
failed observation rolls back automatically, and the rollback and reapply paths are
covered by `tests/test_release_promotion.py` — not a live revert run against every
delivered high-impact change. Review is asynchronous evidence after the pin moves: a
defect it finds is a new commit and another pin move. Request a patch
delivery explicitly with:

```sh
mishe release request --sha FULL_SHA --previous FULL_SHA --patch PATCH_ID
```

An ordinary delivery enqueues that review itself. Once the pin has moved, the
ordinary path starts a transient unit that runs `mishe release review --id
RELEASE_ID`, and the periodic `mishe work poll` merges the verdict into the
delivery record (`merge_reviews`). The record then reads `review_pending: false`
with a `review_state` of `clear` (no concrete defect), `action-needed` (the
reviewer named a concrete defect; a delivered release then raises an intention due
within six hours for the owner to decide a concrete fix or a revert) or
`inconclusive` (the reviewer answered `unknown` because essential evidence was
missing — that gap is recorded, and it asks nobody to repair what the reviewer
never alleged). The review reads the scoped release diff for concrete
source defects; it is evidence after the pin has moved, never a gate that holds it.
Telling the owner is a secondary effect: when the intention or the chat tape
fails, the record still keeps the verdict and carries `review_notice_error`, so
an unavailable tape cannot turn a named defect into silence.
An already-delivered ordinary release is reviewed against its retained recovery
evidence (the previous release, the observation argv and the explicit revert
command), not the pre-activation delivery plan it does not carry. The request also
carries the release window's own commit messages as the author's stated intent,
with the attribution trailer block dropped (`git_base_text.commit_intent_text`),
because the diff alone cannot always explain a design choice behind the change.

The plan names four commands: `activate` restarts the affected consumers,
`observe` checks the live ones afterwards, `revert_observe` checks the restored
consumer, and `revert` restores the old consumer pin. `check` refuses a plan with no
`revert` argv, and a revert that would only re-run the activation is refused before
any runtime byte moves: re-pinning the new release is not a revert. Without this, a
delivery once logged "activated 38e28e3d" as its revert and the pin stayed on the new
release.

The consumer binds every changed committed path to the checked bytes and binds the
delivery plan. It performs the patch apply and recovery exercise after the
engine exits and the dispatch settles. A changed command artifact, source tree
or uncovered committed path is refused. Patch `check` runs the supplied test
command on the exact bytes and ends in the phase `tested`; use tests with no live
activation effects. `status` reads the retained patch evidence without applying
it.

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

A request refused with “previous SHA does not match installed wake consumer pin”
stopped before activation. The consumer checks the pin under the dispatch lock
before writing admission, after any concurrent promotion finishes observation or
rollback. Read the installed pin and the matching delivery record. If the
cadence already delivered the target or a descendant, verify the
intended change in the running release and retain the refusal as recorded. If
the change is still owed, base any new request on the pin you just observed.

Retained requests through 2026-11-20 put a number on this case: 9 of 25 were
refused for a stale previous SHA; in 6 of the 9 the target was already an
ancestor of the installed pin when the refusal was written, and every refusal
waited 0.5–3.5 h between request and admission (two were already stale at
creation). The guard is right — a request stays bound to the pin its wake
started from — but "failed" here usually means the cadence already delivered
the change. Verify before acting:

```sh
PIN=$(basename "$(readlink -f SITE_HOME/runtime/current)")
git merge-base --is-ancestor TARGET_SHA "$PIN"   # exit 0: change already live
```

Exit 0 means keep the refusal as recorded and check the running release.
Exit 1 means the change is still owed: enqueue a fresh request based on the pin
just observed, never replay the refused one.

The `delivery-requests` signal expires after ten minutes (`max_age` 600 in `duties.json`) if its consumer stops
observing; missing, stale or malformed monitoring never establishes success.

`mishe-deliver` starts one promotion of HEAD as a detached transient systemd
unit (`mishe-promote` runs
`SITE_HOME/operator/plans/moving-pin-20261105/delivery/promote-head.sh`), which
materializes a worktree of HEAD and runs the whole suite on it through
`release advance`. A manual delivery driver
must run in a separately supervised service under the existing containment,
with retained logs and an explicit result. When a wake exits, systemd kills
the descendants still in its control group, including processes launched with
`setsid` or `nohup`.

The automatic cadence imports through `runtime/current/src`. If that package
disappears, an interpreter without an editable checkout can fail before it
launches promotion. The detached launcher starts from the target worktree. It
can advance from a damaged release to a newer clean one. If that target already
exists and has lost its package, the launcher can fail before writing a release
record. If observation fails after activation, rollback can recreate an absent
prior worktree from Git. A prior worktree that still exists with missing files
fails the clean-tree check, leaving the candidate pinned and the outcome
`UNKNOWN`. Check the pointer and the exact release record before retrying.

The promotion holds new wakes only while it waits for the dispatch lock at
activation, applies the pin and exercises recovery, and releases the hold when
that block ends. An interruption can still leave an
UNKNOWN effect requiring reconciliation. A reverted patch or changed observer
needs a fresh checked record before another apply. Installed
consumer files and transient timer consumers have different contracts: check
loaded installed pins against disk, and retain invocation/cgroup draining and
future-pin checks for active or pending transients. One transient is exempt: a
unit whose argv runs `release review`, including the delayed
`mishe-release-review-retry-*` units; `consumer_fence.pin_independent` leaves it
out of both the drain and the future-pin check. Such a unit reads one
release's record and tree as *data* and runs the code of the
`runtime/current/src` symlink, so a queued retry that fires after a pin move
merges under the policy that is live then; a review is evidence after delivery
and must never hold a promotion, which is why a running or armed review is not a
consumer the pin fence judges. Counting a queued retry
as a consumer with the wrong pin failed a correct promotion's probe and then its
rollback probe on 2026-11-17, leaving the delivery record UNKNOWN with a correct
pin above it. A consumer group can also vanish between the drain's existence
check and its scan, because the kernel deletes a oneshot's cgroup when its last
process exits: a group gone on re-read holds nothing to drain, while a group still
present without `cgroup.procs` remains a real enumeration failure (that race rolled
back a correct `0c567f9d` promotion on 2026-11-17 20:34Z naming
`mishe-producer-initiatives-rust-plan.service`).
The drain cutoff is sampled immediately after renaming `runtime/current`, before
writing the pin mirror or syncing units. Automatic rollback uses the same rule
for its restored pin. An invocation starting during those later steps is judged
by the future-pin check.

An old transient timer may also launch its service during the pin move. The
final observation waits a bounded time for an active wrong-pin service to exit,
then checks the inventory again; a wrong-pin queued timer or a failed inventory
still fails the observation, and a service that stays wrong fails at the deadline.
A module consumer (`-m mishe_tauftauf`) resolves its code through `PYTHONPATH` and must carry its pin there: blank `Environment` evidence stays in the inventory and fails the future-pin check whether the unit is queued or active. Direct-script units (`python3 -I <script>`) are legitimately unpinned and stay out of that gate; so are release-scoped review runs.

After every delivery, compare the units' `PYTHONPATH` with `runtime/current`: it
should resolve to the release just delivered. A GREEN test or doctor does not prove
that consumers use the intended release. Preserve protected host guards and limits.

The observer-check runner is bound the same way, and its bytes decide which checks
the plant reads at all: `SITE_HOME/checks-runner/observer-checks.py` holds
`INVENTORY` (the checks it runs) and `NOT_SCHEDULED` (the ones it deliberately
skips, each with its reason). Its source is tracked at
`checks-runner/observer-checks.py`, and `tests/test_runner_bytes.py` requires the
live copy to equal the copy in the release `runtime/current` names — not the
checkout. `mishe sync` installs that live path as the relative link
`../runtime/current/checks-runner/observer-checks.py` (and keeps any file it
replaces as `observer-checks.py.pre-link-<epoch>`), so the binding needs no hand
copy and holds through every pin move; a copy left in its place is exactly what
the test detects. That target is deliberate: activation copies a release and only then
advances the pin, so a runner change cannot be installed until the pin contains
it, and a checkout comparison deadlocks (measured 2026-11-17: pin `05da8d15`,
live `43f980fd`, checkout `9a84df2f`; candidate `0e67fdb1` failed its own delivery
check on that line and the pin stayed where it was). The runner is committed at
all because of a measurement: while it lived only under the ignored site home, its
inventory changed with no diff anywhere — five checks stopped being read and five
started (2026-11-10).

## Ending a wake

Run `mishe yield --result changed|verified|blocked` with the edited wall on stdin.
Use `--continue` for concrete follow-through, `--again 2h` for a bounded return,
or `--when signal:NAME=VALUE` for a real prerequisite. When waiting for a result, be event-first: add a probe signal with the change, yield `--when signal:NAME=VALUE`, and add `--again` only as the fallback; the pair is retired together when either fires. Name the signal and the fallback in the wall. `--artifact FILE` registers
a nonempty site-local evidence file. `--work-file FILE` records current structured
readiness. A missing yield leaves an unknown effect to reconcile before replay.

A `--when` that names no `--again` still gets a six-hour deadline of its own, so no wait
sits forever. A `--when` that names a signal the site has not registered cannot fire,
and the yield says so: a signal that is not registered never fires, so such a wait is
dead from the moment it is written.
A `--when` whose condition already holds is not itself a wait — the test is whether
the signal matches now, not whether it just changed — so a wait on a value that persists
does not wait at all: `pin-delivery` stays `GREEN` from a delivery until the next
promotion. To leave a timer as well, name `--again`: an already-true condition is then
not armed at all, the timer stands alone, and the yield says the condition was not
armed. Without `--again` the condition is armed as before, so that wait fires on the
next check and retires any deadline; reserve that form for a value the signal does not
yet hold, and use `--again` when the condition you want is already true. The yield
reports either outcome in the same message that flags an unregistered signal: an armed
already-true wait warns that it fires at once, and an unarmed one says the timer stands.

At yield the settle step also walks the signals you subscribe to that declare a `reoffer`.
If one still reads RED or otherwise not-OK and you have not acknowledged its incident wait,
it arms a `reoffer-NAME` intention for that signal's own reoffer interval: the wake returns
because the signal is still not clear, not because a timer you set is stuck. A known
disposition replaces it — its recheck deadline arms `disposition-NAME` and any pending
reoffer is removed — and a signal that reads clear again has the leftover reoffer removed
too. Resolve or disposition the standing fault, or acknowledge its incident wait, instead
of re-yielding on the same condition.

The disposition's `owner` names the person or role accountable for the finding. The
subscriber arms the recheck at settlement while the known disposition is still active.
Check that `intentions/ROLE/disposition-NAME.json` matches the current deadline
before relying on that wake. If the deadline could pass before any subscriber arms
it, schedule a separate recheck. If the resolver sits outside the subscriber list,
record how they will be reached and when they will recheck. For a shared upstream
outage, the subscriber can recheck the signal while the upstream resolver keeps
their own recovery trigger.

A yield also sets the idle pace. The default idle initiative returns in two hours. A role
with fresh spare capacity returns sooner: in ten minutes when its work record has a ready
lane. With no ready lane it returns in thirty minutes only when the yield settled with
`--result changed`; after `verified` or `blocked` it goes back to the two-hour pace, so a
role whose wakes only confirm a flat dossier does not keep waking at the short pace. Spare capacity is fresh evidence: either a paid account whose 5h and 7d
windows are FRESH, unexpired and at or above the spare floors (at least 10% of the 5h window
and 5% of the 7d window remaining), or a free route still admitted in `routes.json` with a
recent FREE reading in `engine/budget.json`. A free model has no account window, so an
exhausted Claude weekly window does not by itself cap the pace while such a route is live.
Windows and free-route readings that are missing, stale, unreadable, retired or dated in the
future are not spare, so the two-hour pace holds. Capacity that cannot be measured keeps the
two-hour pace; neither condition is ever read as permission to skip the work.
A free route releases a spontaneous intention held by a RED or unreadable Claude budget
signal only when it is in the role's own roster and has fresh evidence and free capacity. A
missing, stale or retired free reading, or a free route for another role, keeps the hold.

See [Mesh](mesh.md) and [Protocol boundaries](protocol-boundaries.md).

## Recording a contribution, and the evidence it accepts

A wall note records what a mind is doing now. A *wall outcome* is the separate,
durable record of a contribution that actually landed, and it is what the plant
counts. One outcome per contribution:

```bash
mishe wall outcome --owner ROLE --kind accepted \
  --file NAME.md --evidence "$MISHE_SEED_HOME/artifacts/NAME.md"
```

`--file` names the notes the role has already staged, and the stage is
per-role: a bare `NAME.md` resolves to `SITE_HOME/walls/ROLE-NAME.md`, so one role
cannot read another's staged notes. A path with a directory keeps its
working-directory meaning, and an absolute path is never re-anchored.

`--kind` says what kind of contribution this is, and there are exactly four:

| Kind | Use it for |
| --- | --- |
| `accepted` | The contribution landed and stands. |
| `blocker-resolved` | A blocker that was blocking work is now cleared. |
| `blocker-retired` | The blocker is withdrawn or no longer applies, so it is closed rather than solved. |
| `hypothesis-changed` | The finding changed what was believed. |

The distinction between `blocker-resolved` and `blocker-retired` is the one
worth making: a resolved blocker was removed by something that worked, and a
retired one stopped applying. Any other value is refused before anything is
recorded, so a typo cannot become a permanent category.

These four are the whole vocabulary, and they are **labels, not verdicts**.
Nothing in the plant re-derives them: a `--kind accepted` outcome is the
author's report that the contribution landed, and the counts that read them
are author reports too. Choose the one that describes the contribution; the
evidence file, not the label, is what the plant can check.

The evidence file has a shape, and the shape is checked before anything is
recorded. It must be **one regular file directly inside** the site's
`artifacts/`, or a `discovery/scan-*.json`, or a `dms/*.md`:

- A file in a subdirectory, such as `artifacts/wake-1234/evidence.md`, is
  refused. The rule counts directory levels, so only files sitting directly in
  the named directory qualify.
- A relative path is resolved against the current working directory first.
  When that form does not name an eligible file in the site, the same relative
  path is resolved against the site home instead, so `artifacts/NAME.md` from
  the repository root names the **site's** `artifacts/NAME.md` and is accepted.
  A path that is eligible under neither form is refused, and the refusal names
  the path it resolved to. Give the absolute site path if you want no
  ambiguity.
- Each outcome binds its evidence file. Reusing one file for a second outcome
  is refused with `outcome evidence path already bound: ...; each outcome binds its own
  flat artifacts/NAME file (bind a copy of the final bytes), or re-evidence with
  --supersedes <record digest>`. Copy the final bytes to a new flat file for a new
  outcome, or publish a superseding outcome that names the record digest being
  re-evidenced (see below).
A refusal is not a lost contribution: the reason is appended to
`SITE_HOME/logs/outcome-refusal-audit.jsonl`, which is also where to read how many
refusals happened and which role hit them. Read that log before writing a
second artifact — the shape of the file, not the effort behind it, is what the
refusal reports.

That log holds **refusals only**: a row exists because a publication did not
happen, so it never carries a decision field. The accepted side is a different
file — `SITE_HOME/records/`, one `wall-outcome` record per publication. Counting
both sides per role means joining the two: the audit gives refusals, the record
store gives accepted contributions. The two files do not share a time window
field, so say which window you compared when you report the numbers.
