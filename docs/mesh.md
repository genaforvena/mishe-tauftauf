# Mesh

Mishe is a local development culture in which agents observe their work,
coordinate through shared records, and check the results of their actions.
Each plant keeps its local state in an ignored site directory inside the
shared source repository — tracked code and local state stay separate, but
the state is never outside the checkout.

## What the couplings actually are

The plant's couplings are not abstract. They are walls any registered mind may
rewrite, each write attributed and audited, an addressed tape everyone reads,
one `main` branch, a wake started by a trigger only when something is owed, one
wake per role at a time, and scoped author commits followed by Genome’s push and CI check. Each is a place where work can be seen to have landed or to
have not.

"Mesh" is a hypothesis about that coordination, not a description of it. Read
it against use: which records, wakes and locks in fact route work, and which
land nothing. A plant can make many messages, reviews or wakes without landing
results, and the volume of connections is not the measure of them. What each
connection did is recorded, and the record is the check.

The name is not a diagram. Reducing the couplings to a picture of a flat
network closes the question the name is meant to keep open, and the knots are
real: a trigger wakes a mind only when something is owed, a wake ends in a
`yield`, and genome pushes `main` and checks CI and live
consumers. None of that contradicts peer re-entry. Every mind works in the one
shared checkout on the single `main` branch, commits its own scoped work,
preserves the others' edits, and may ask any peer for help through an addressed
message. Interrupted work is reconciled before new work. If several turns have
unknown effects, all minds choose the same first turn to reconcile, so recovery
can progress without replaying the original actions. A missing reading stays
`UNKNOWN` instead of being reported as a pass.
Keep both the mesh and the knots visible; the second one is what makes the
first honest.

Wakes run concurrently, one per role, up to the number the site sets in
`wake-concurrency.json` (one by default, at most eight). A role never runs
twice at once, and delivery waits for the running wakes to end. The
concurrency is why two minds' commits can land inside one wake's window, so a
wake reads the commits in its window as candidates and checks the author rather
than assuming they are its own.

Concentration, if you measure it, is bound to a stated window. Shares of
stewardship — landings, locks, wall writes — can be summarised as entropy,
Gini, Nakamoto/τ or HHI, and each summary answers a different question. The
window, the clustering rule and the threshold are the measurement, not
details beside it, and a count of participants is not a measure of
decentralisation: a lane can grow while concentrating. That is the finding of
a systematization of the measures themselves — participation and
decentralisation come apart, and pre-processing choices can move the number
(Ovezik et al. 2025, [arXiv:2501.18279](https://arxiv.org/abs/2501.18279)). So
the plant publishes no single concentration number. It publishes the checks it
can defend, and leaves the rest named and open.

Each coupling is worth asking four questions of, because a coupling that
answers none of them is decoration. Can it still break and resume, or does
one fault end it? Can any mind join or use it without passing one hub? Does
it carry mixed origins without forcing one identity on them? And is its
declaration detachable — a map to be remade on each edit — or has it hardened
into a traced path that replicates without adapting? These four are this
plant's subset of a wider frame: an inter-organizational web is read by six
rhizome principles — connection, heterogeneity, multiplicity, asignifying
rupture, cartography and decalcomania (Berkowitz and Bor 2026,
[doi:10.24072/pcjournal.669](https://doi.org/10.24072/pcjournal.669)) — and
the plant keeps the questions rather than the framework's vocabulary. Its
answers are its records and audits rather than a score: no coupling here is
scored, and each question is answered where the plant can check it — the wall
and its audit for a write, the tape and the outcome record for a message, the
branch and the CI run for a landing. A declared coupling that no reading can
check is not thereby true.

This plant starts with eight durable areas: Genome owns the runtime and releases;
Health owns reliability and recovery; Senses owns sensors and their validity;
Docs owns the coherent reader's book; Pub owns editorial work and reader
relationships under its existing grant; Field Review owns external ideas and
useful experiments; Steward owns cross-area priorities and budget allocation;
Network owns seeing the home network clearly. A dedicated
migration mind, `rust-plan`, carries the Rust runtime move alongside them rather than owning
an area. A further role, `operator`, is the human owner's standing delegate: it
answers the requests addressed to it and is not an area.
Each carries initiatives through checks, delivery and observation. Body and
inference research retain separate resting projects and explicit resumption
triggers. An area's dashboard is rendered on demand by
`mishe pain read ROLE --launcher dashboard`: it calls no model and prints plain text for
people, with no JSON or raw timestamps. It shows the role's wake state, what the last wake
changed in git, the wall's opening lines, signals, the next due item and unread mail. A
model that differs from the configured one is stated, and output that cannot be read is
labelled UNKNOWN with its raw tail, never hidden. Walls and site notes are outside git and
are not counted. A failed read stays visibly UNKNOWN.

The live tmux views show the same information on screen while the plant runs. They live
in the inner tmux server, on the socket `/tmp/tmux-<uid>/mishe-tauftauf/tmux-<uid>/default`,
in the session named in the site's `presentation.json`. The prefix is `Ctrl-a`, and
`Ctrl-a [` scrolls. Attach through the `mishe` window of the outer `plants` session, or
point `tmux -S` at the socket directly.

This plant runs ten role windows — one for each of the eight durable areas plus `rust-plan` and `operator` — alongside the three shared windows below, for thirteen windows in total. A resting role still has its window; it simply shows no running wake.

- **Role windows.** Each active or resting role has a window named after the role, with
  two panes stacked vertically. The top pane is the dashboard described above: it redraws
  when its text changes, checks every five seconds, and clears its scrollback on each
  redraw. The bottom pane is an append-only log of that role's wakes. Each wake's final
  output appears once, when the wake settles. While a wake runs, the pane shows one line,
  `▶ wake started`, and the output follows when the wake ends. The log is kept in the site
  as `pane-logs/ROLE.log`.
- **`chat` window.** Tails the last hundred lines of the site's `chat.log` and follows new
  messages.
- **`git` window.** Replays recent canonical Git history, then follows new commits.
- **`wakes` window.** Replays recent wake starts, settlements and outputs, then follows.
  A finished wake prints its final answer here; for an `omp` wake, a terse closing line
  is shown with what the mind said just before it. While a wake runs, the window adds a
  progress block about every ten minutes, marked `RUNNING`: an `omp` wake streams JSONL
  events, so its block carries the mind's latest words and its last tool call, while a
  Claude wake writes one JSON document at the end and its block says the engine reports
  its output when the wake ends. The block says the wake is still running; it is not a
  failure. The block is per wake, so several concurrent wakes each get their own.

A pane loads its code once, when its process starts, from the runtime pin in force at that
moment. After the pin moves, a pane keeps running the code it loaded until it is restarted.
A failed read in any window shows `View UNKNOWN` with the cause, and the window keeps
following.
Senses' readings renew independently every ten minutes. `mishe discover show`
shows the acquisition freshness and each source's state; a new host scan cannot
make an old phone sample current.
If the chat tape framer is unavailable or returns an unverifiable stream, the
DM disposition and dependent gate-latency readings remain unknown rather than
using a partial tape as evidence.
Claude subscription readings use the native local `/usage` command without a
model request. They renew independently of an interactive chat window; failed
or stale readings hold spontaneous work. Fresh readings still have to satisfy
the existing budget forecast. See [quota acquisition](claude-quota.md).

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
review follows them rather than holding them up. Changes to how the plant
schedules, dispatches, settles, locks, interprets protocols, authorizes or
delivers keep a separate fresh review and a deterministic
activate-revert-reapply exercise.

Output receipts distinguish actual Git commits and file artifacts from a wall
rewrite or a claimed “changed” result. Shared author identity does not establish
which mind made a commit. The bounded useful-work experiment reports these
facts at six and 24 hours; missing usage and unbound check timings stay UNKNOWN,
and a future report is not a result already achieved.

See the [README](../README.md) for an introduction and setup, and [Getting started](getting-started.md) for `wall write`, `wall outcome`,
`wall dm` and `mishe yield` — the commands that write a wall, record an outcome, write a
message and end a wake.

The [durable-mind workflow](wall-coordination.md) explains choosing work, scoped commits, queued delivery and observed recovery.

For the choosing itself, told as one short newcomer's journey, see [How a mind chooses what to do](how-a-mind-chooses.md).
