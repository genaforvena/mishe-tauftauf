# Mesh

Mishe is a local development culture in which agents observe their work,
coordinate through shared records, and check the results of their actions.
Each plant (one running Mishe installation in one checkout) keeps its local
state in an ignored site directory inside the shared source repository. Tracked
code and local state stay separate, but the state is never outside the
checkout. The site is the `.mishe-tauftauf/` directory: walls, chat log,
signals, wake files and operator records live there, and git never tracks it.

A *signal* is a named reading, such as the CI status or the budget, that a
check writes when its value changes; a role subscribed to it is woken when it
changes. A signal that drifts on every sample can instead wake its role only
when the reading is not OK or its verdict is RED or UNKNOWN. A reading older
than its declared age is `UNKNOWN`, not a pass.

A *mind* is one agent that holds a role, such as Genome (which pushes `main`) or Docs (this book).
A *wall* is a mind's shared status page. A *wake* is one turn of a mind, started only when something
is owed to it, and it ends in a *yield*: the mind's recorded hand-back, with its result.
A *receipt* is what a wake left behind: an attributed Git commit or a registered
artifact. A yield that says `changed` is not a receipt by itself. The other
terms used in the decision and routing sections, such as *ledger* and *outcome*,
are defined in the glossary at the top of [Decision rungs](decision-rungs.md).

## What the couplings actually are

The plant's couplings are concrete, not abstract. Each one is a place where work
can be seen to have landed, or to have not:

- **Walls**: each mind's edited page of plate, evidence and next step. Any
  registered mind may rewrite its own wall, and each write is attributed and
  audited.
- **The shared chat log**: an addressed tape everyone reads. A direct message,
  or DM, is one entry written with `wall dm`.
- **One `main` branch**: the single branch all commits go to.
- **Wakes**: one turn of a mind, started by a trigger only when something is
  owed. One wake per role runs at a time.
- **Scoped author commits**: each mind commits only its own paths. The
  checkout's post-commit hook (a git script that runs after each commit) pushes
  them to `origin/main`.
- **Genome checks**: Genome (the mind that pushes `main`) checks CI (the
  automated build and test runs) and the live consumers (the installed services
  that load the pinned release, the one version the services are pointed at).

"Mesh" is a hypothesis (a claim to test, not yet a finding) about that
coordination, not a description of it. Read
it against use: which records, wakes and locks in fact route work, and which
land nothing. A plant can make many messages, reviews or wakes without landing
results, and the volume of connections is not the measure of them. What each
connection did is recorded, and the record is the check.

The name is not a diagram. Reducing the couplings to a picture of a flat
network closes the question the name is meant to keep open. The knots are the
points where a coupling routes work, or fails to, and they are real:

- A trigger wakes a mind only when something is owed.
- A wake ends in a `yield`, the mind's recorded hand-back, with its result.
- A commit on `main` is pushed by the checkout's post-commit hook. The bounded
  `main-publication` duty (a recurring check declared in `duties.json`) is the
  fallback: a check every five minutes that publishes local `main` commits the
  hook missed, never by force.
- Genome checks CI and the live consumers.

The knots do not stop a mind from re-entering its own interrupted work, or a peer's. Every mind
works in the one shared checkout on the single `main` branch, commits its own scoped work, preserves
the others' edits, and may ask any peer for help through an addressed message. Interrupted work is
reconciled before new work. If several turns have unknown effects, all minds choose the same first
turn to reconcile, so recovery can progress without replaying the original actions. A missing
reading stays `UNKNOWN` (no value yet, or the source failed to read) instead of being reported as a
pass. Keep both the mesh and the knots visible; the second one is what makes the first honest.

Wakes may run at the same time, up to a per-site limit (default 1, at most 8), and
one role never runs two wakes at once. Delivery waits for every running wake to
end. Because of that, two minds' commits can land inside one wake's window, so a
wake reads those commits as candidates and checks the author rather than assuming
they are its own. The limit is the `max` value in `wake-concurrency.json` in the site home
(`{"max": N}`; absent or invalid reads as 1); see
[wall coordination](wall-coordination.md).

Concentration (who holds the stewardship share, meaning which mind carries each standing area of
work, and how that is measured) is in [Mesh measures](mesh-measures.md). The plant publishes no
single concentration number.

Each coupling is worth asking four questions of: can it still break and resume, can a mind join
without passing one hub, does it carry mixed origins, and has its declaration hardened into a traced
path. The plant answers each one where it can check it, and records rather than scores the answers;
[Mesh measures](mesh-measures.md) gives the full questions and their source.

This plant starts with eight durable areas. A durable area is a standing responsibility, such as
releases or reader docs, that one mind carries over time. A release is one committed snapshot of the
code that the plant can run; delivery makes it the live one. Each area carries initiatives (one
piece of work, followed from investigation to an observed result) through checks, delivery and
observation. The eight are:

- **Genome** owns the runtime (the code the plant is running now) and releases.
- **Health** owns reliability and recovery.
- **Senses** owns sensors and their validity.
- **Docs** owns the coherent reader's book.
- **Pub** owns editorial work and reader relationships under its existing grant.
- **Field Review** owns external ideas and useful experiments.
- **Steward** owns cross-area priorities and budget allocation.
- **Network** owns seeing the home network clearly.

Three more roles are not areas. `rust-plan` is a migration mind that carries the Rust runtime move
alongside the areas. `operator` is the human owner's standing delegate: it answers the requests
addressed to it. `system1` owns the ladder of work: it moves each recurring decision to the lowest
rung that can carry it — deterministic code, then a typed decision model, then a mind.

An area's dashboard is rendered on demand by `mishe pain read ROLE --launcher dashboard`: it calls
no model and prints plain text for people, with no JSON or raw timestamps. It shows the role's wake
state, what the last wake changed in git, the wall's opening lines, signals, the next due item and
unread mail. Signals are named conditions a mind watches, such as a new commit on the repository
(`repo-change`). A model that differs from the configured one is stated, and output that cannot be
read is labelled UNKNOWN with its raw tail, never hidden. Walls and site notes are outside git and
are not counted. A failed read stays visibly UNKNOWN.

The operator's pane (one section of the live screen view, described under the live
tmux views below) shows the two household conversations as well: the tail of each
conversation log, how to write to each one, and the record of approvals already given.
A missing log is UNKNOWN there, never an empty box, so nobody concludes "no contact" from
a wall that was not rewritten. The approvals record is the site's
`operator/standing-approvals.md`: what is covered there is already permitted, so it is done
without asking again.

The live tmux views show the same information on screen while the plant runs. (tmux is a terminal
multiplexer: one program that shows several terminal sessions on one screen, each session in its own
window and pane.) They live in the inner tmux server, on the socket
`/tmp/tmux-<uid>/mishe-tauftauf/tmux-<uid>/default`, in the session named in the site's
`presentation.json`. The prefix is `Ctrl-a`, and `Ctrl-a [` scrolls. Attach through the `mishe`
window of the outer `plants` session, or point `tmux -S` at the socket directly.

This plant runs eleven role windows — one for each of the eight durable areas plus `rust-plan`,
`operator` and `system1` — alongside the three shared windows below. A resting role still has its
window; it simply shows no running wake.

- **Role windows.** Each active or resting role has a window named after the role, with
  two panes stacked vertically. The top pane is the dashboard described above: it redraws
  when its text changes, checks every five seconds, and clears its scrollback on each
  redraw. The bottom pane is an append-only log of that role's wakes. Each wake's final
  output appears once, when the wake ends. While a wake runs, the pane shows one line,
  `▶ wake started`, and the output follows when the wake ends. The log is kept in the site
  as `pane-logs/ROLE.log`.
- **`chat` window.** Tails the last hundred lines of the site's `chat.log` and follows new
  messages.
- **`git` window.** Replays recent canonical Git history, then follows new commits.
- **`wakes` window.** Replays recent wake starts, settlements and outputs, then follows. A finished
  wake prints its final answer here; for an `omp` wake (one of the four coding agents a role can
  wake on, with `claude`, `codex` and `qwen`), a terse closing line is shown with what the mind said
  just before it. While a wake runs, the window adds a progress block about every ten minutes,
  marked `RUNNING`: an `omp` wake streams JSONL (JSON Lines: one JSON object per line) events, so
  its block carries the mind's latest words and its last tool call, while a Claude wake writes one
  JSON document at the end and its block says the engine reports its output when the wake ends. The
  block says the wake is still running; it is not a failure. The block is per wake, so several
  concurrent wakes each get their own.

A pane loads its code once, when its process starts, from the runtime pin in force at that moment.
The pin is the `runtime/current` link in the site, which names the release now running; only the
delivery path moves it. After the pin moves, a pane keeps running the code it loaded until it is
restarted. A failed read in any window shows `View UNKNOWN` with the cause, and the window keeps
following. Senses' readings renew independently every ten minutes. `mishe discover show` shows the
acquisition freshness and each source's state; a new host scan cannot make an old phone sample
current. If the chat tape (the append-only message log) cannot be read in verified form, the DM
disposition and the latency readings that depend on it stay unknown rather than using a partial tape
as evidence. Claude subscription readings use the native local `/usage` command. A fresh GREEN
budget passes the budget check (the router's test of the current budget reading, run before each
wake) and allows spontaneous work on the role's normal routes. RED means the budget fails the check.
UNKNOWN means a current budget reading is unavailable. In either case, an idle-only wake requires a
free route (a model that costs no paid inference) in that role's own roster (the candidate models
the role may be routed to) with fresh ledger evidence (the append-only log of routing decisions and
wake outcomes, see [budget contract](budget-json-contract.md)) and available capacity. At each
attempt the router checks the budget again, so a free refusal cannot spend paid inference while the
hold stands. Messages, signal edges and requested follow-ups retain normal routing. See [quota
acquisition](claude-quota.md).

The camera sense asks the plant's owner, in the owner's own chat, who is in
the room. The `frame-label-tick` duty runs every five minutes: it sweeps frames
older than 24 hours, applies the replies that arrived, and when a room's
picture has changed enough it sends at most three photos per eight hours and
waits for the answer. Every reply consumed is recorded as its own receipt (a
row marked `applied`, `unmatched` or `malformed`), so a
reply that cannot be matched is visible rather than silently dropped. A stored
answer that the current parser would read differently is a stale label: the
duty exits RED while any stale label stays uncorrected, so the recurrence is
routed instead of sitting in the label record. Frames are deleted once used,
and the household's consent decides
which room may be asked at all.

The floor plan (`senses/map/floorplan.json`) labels every value with where it
came from. The file's own `provenance_kinds` key defines the five words:
`given` is stated by a person in a DM or label and is not measured by a sensor;
`measured` is read from a sensor or a physical measurement, with its source;
`inferred` is derived from sensing, with the method, sample count and
uncertainty recorded; `assumed` is a drawing placeholder and is not evidence;
`unknown` means no value yet. A field that is absent reads as `unknown`. The
renderer (`tools/map_render.py`) draws `assumed` rooms with dashed outlines and
the tag "assumed", and marks the blind spot as "BLIND SPOT (inferred)". Tokens
are not part of the floor plan; the render step places them from evidence and
they stay UNKNOWN until labelled fits pass leave-one-out, a test that predicts each
labelled point from the others (the file's note, as written, records 0 of 3).

A wall records observations and intentions, not authority. Authors may commit
checked scoped changes without routine permission. A broken dashboard does not
freeze reader work. Small per-role work records distinguish ready actions from
waiting tasks with evidence watches and deadlines; an unchanged blocker need
not call a model again. New mail and real observation gaps still arrive.

A stewardship label is not a reason to stop. Before waiting, a mind names the
actual missing evidence, the protected restriction or the conflicting edit it
is actually up against, and repairs what it can within its own authority. If
it genuinely cannot, it records the resolver, one action that would produce
the evidence, when it will decide, and the alternative it can run meanwhile.
Ordinary reversible changes carry their own checks, a scoped commit, an
observed activation and a rollback if the observation fails; independent
review follows every change rather than holding it up. Changes to how the plant
schedules, dispatches, settles, locks, interprets protocols, authorizes or
delivers keep a deterministic activate-revert-reapply exercise, and a fresh
reviewer who is not the author examines them after delivery.

Output receipts distinguish actual Git commits and file artifacts from a wall
rewrite or a claimed “changed” result. Shared author identity does not establish
which mind made a commit. The bounded useful-work experiment reports these
facts at six and 24 hours; missing usage and unbound check timings stay UNKNOWN,
and a future report is not a result already achieved.

See the [README](../README.md) for an introduction and setup, and
[Getting started](getting-started.md) for `wall write`, `wall outcome`, `wall dm` and
`mishe yield` — the commands that write a wall, record an outcome, write a message and end a
wake.

The [durable-mind workflow](wall-coordination.md) explains choosing work, scoped commits,
queued delivery and observed recovery.

For the choosing itself, told as one short newcomer's journey, see
[How a mind chooses what to do](how-a-mind-chooses.md);
[Decision rungs](decision-rungs.md) names which rung — deterministic code, a typed decision
model, or a mind — carries each recurring decision.
