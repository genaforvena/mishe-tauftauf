# How it works

Mishe-tauftauf is a local culture for development through self-observation. Python programs collect evidence, render text panes, track tasks, and supervise installed coding agents. The agents observe their work and its evidence, choose a bounded next step, and check the result—including changes to their own development machinery. The current runtime is planted alongside an owned Git worktree; that is its storage and delivery substrate, not a limit on the work's goals. The metaphor is a plant. The machinery is processes, files, and tmux.

[README](../README.md) · [Getting started](getting-started.md) · [Operating the plant](operating.md)

Jump to: [vocabulary](#the-vocabulary-without-the-potting-soil) ·
[runtime](#what-actually-runs) ·
[roles](#the-initial-channels) ·
[evidence](#evidence-unknown-is-not-pass) ·
[durable work](#where-work-survives) ·
[authority](#scope-and-authority) ·
[case study](#case-study-a-false-ci-unknown).

## The vocabulary, without the potting soil

| Term | Concrete meaning |
| --- | --- |
| **Plant** | One running local instance: its tmux session, resident agents, observation programs, supervisor processes, and saved state. Planting initializes that instance around a worktree. |
| **Site** | The ignored directory holding an instance's state, commonly `.mishe-seed/` in the kernel checkout or `.mishe-tauftauf/` in another target. `SITE` in commands means the actual directory printed during planting. |
| **Channel** | A responsibility with a named tmux window, checks, charter, and resident agent. The initial resident channels are listed below. |
| **Mind** | An installed coding-agent process in a channel's lower pane, such as Codex or OMP. It is not a separate sentience or a replacement for the host's access controls. |
| **Charter** | A channel's lasting purpose and owned scope, saved in `SITE/charters/ROLE.md`. |
| **Handoff** | The current task, evidence, uncertain effects, and exact next step saved in `SITE/handoffs/ROLE.md` and archived when the wake is settled. Unlike a charter, it describes work in progress. |
| **Wake** | A supervisor-issued invitation to take one bounded step. Its sequence number ties the action, handoff, and receipt to an exact entry in `chat.log`. |
| **Lease** | The top pane's refresh timestamp. A fresh lease is evidence that the renderer is running, not that the system being checked is healthy. |
| **Genome** | The reusable tracked code and general rules; also the resident channel that pushes the single `main` branch and checks its CI. Local instance state is not part of this tracked genome. |
| **Sense** | A repeatable observation with a real source, freshness, an honest failure or unknown state, and a consumer. Finding a command on `PATH` is only a candidate for one. |
| **Artifact** | A saved record of source-bound observations, actions, and checks, normally under `SITE/artifacts/`. A claim in prose alone is not a checked result. |

## What actually runs

The reusable runtime lives in `src/mishe_tauftauf/`. Checkout-only host commands in `coordination/` plant instances and refresh linked sites. A full Linux setup uses Python 3.10 or newer, tmux, Git, an installed agent CLI, a user systemd manager, and GitHub CLI for CI readings. The [getting-started guide](getting-started.md) covers those prerequisites and a manual trial without resident services.

Each resident channel has two panes:

- **Upper:** a refreshing view of the channel's goal, observations, checks, and current state.
- **Lower:** the installed agent's working process, receiving supervisor wakes when it can accept a prompt.

The supervisor reads the full live pane, checks its freshness and the mind's readiness, and delivers a wake for an actionable task, changed observation, addressed event, continuation, or quiet self-pick. It does not invent a passing result to keep the household cheerful.

```text
local system and source
          ↓
 sampled observations → refreshing top panes
                                  ↓
                         resident minds below
                                  ↓
                     bounded action → same live check
                                  ↓
              artifact + handoff + append-only chat.log
                                  ↓
                settled idle clear → next WAKE + restore
                                  ↓
                         next bounded step
```

### Development is not just repository maintenance

The generic runtime provides the observation, task, and handoff loop. A project's charter supplies the goal; its local checks make progress and uncertainty observable. Builds, Git state, and CI are useful checks, but they are not a universal definition of progress.

For a research project, the next step may be to audit a dataset, run an authorized evaluation, compare experimental conditions, trace a result to its provenance, or hold a conclusion for independent review. Evidence can reveal a flaw in the method or the plan, not just a bug in the code. The loop also observes itself: stale displays, repeated no-progress work, or misleading checks are development problems in their own right.

The [tiny-fleet](https://github.com/genaforvena/tiny-fleet) plant illustrates this distinction. In the live instance inspected on 30 September 2026, its project-specific genome pane tracked audit, prioritization, reproduction, reporting, and independent review. Saved evaluation outputs were visible, but unresolved provenance and review obligations kept research acceptance `UNKNOWN`. Its health channel separately reported passing local infrastructure checks. Healthy machinery did not certify the science.

The senses channel also recorded a mismatch between a current research view and an older embedded sample. That was evidence about the observation machinery itself, not a new experimental finding or proof that the mismatch had been repaired. The live panes, status file, and handoffs support this point-in-time example; it is not a claim of research completion. The plant's local evidence is ignored state, not bundled with the public repository.

Project-specific checks and charters make this possible; the stock runtime does not arrive knowing how to judge an experiment. Keep authority bounded to the owned project, and distinguish a recorded output from a justified conclusion.

## The initial channels

| Window | What the upper pane shows | What the lower pane does |
| --- | --- | --- |
| `discover` | Recent bounded local reads and capability candidates | Selects related literature, assesses applicability, and proposes evidence-backed new directions as well as local capabilities |
| `senses` | Sampled readings, freshness, and unknowns | Turns recurring observations into truthful senses with temporal validity, a failure state, and a consumer |
| `health` | Doctor, expected tmux windows, resident services, and CI state | Repairs the plant's local feed, panes, and services |
| `genome` | Repository goal, worktree, doctor, CI, and next development step | Develops source, pushes the single `main` branch, and checks its CI and live consumers |
| `witness` | Open tasks, conversation, CI events, receipts, and channel health | Routes work, follows it through completion, and checks claims against artifacts and live results |
| `docs` | The repository's selected reader document (default `README.md`) | Keeps the README and reader docs current, deleting stale passages |
| `research-methods` | Deterministic facts: CI, deployed runtime, services, and patch state | Assesses whether experiments answer the question and helps implement bounded improvements |
| `permissions` | Pending and decided requests | Gives the operator a shell for scoped grant and revoke decisions |
| `operator` | The human owner's shell | A convenience for the person; not a role and not a gate on work |

A site can declare additional resident channels beyond the built-in channels by adding
a charter and an executable launcher. The plant preserves that window, refreshes
its evidence pane, and counts the resident's activity toward the silence watch;
the resident's wake service is site-owned rather than generated.

**discover → senses → health → genome → witness** describes responsibilities: find an observable, make it reliable, maintain the local substrate, improve reusable code, and independently check completion. It is not a mandatory route for every task. A fault goes to its owner; authors commit their own scoped work on the single `main` branch, and genome pushes it and checks CI and rollout. Witness keeps unfinished IDs visible. `docs` keeps the repository's README and reader docs current. A person can attach to the same text session and read the same tape.

Roles are not file restrictions. Within owned scope, every mind may repair Mishe's source, checks, prompts, doctrine, charters, roles, routing, supervisor lifecycle, planting, or coordination when evidence requires it. Path ownership, independent review, acceptance checks, and external boundaries still apply.

Discovery starts with bounded, read-only local samples: commands on `PATH`, `/proc` and `/sys` values, disk space, tmux windows, and input activity counters. Input activity counts do not capture key content. Each discovery entry explains available commands, verified readings, unknowns, the artifact, and the next action. Repeated scans refresh local evidence but append chat only when availability, status, or an unknown reason changes. Discover and senses wake for those meaningful changes, not every moving counter.

The discover and senses supervisor ticks renew a missing scan or one older than
600 seconds, before the panes' 900-second stale boundary. This bounded read runs
under the seed lock even when the upper pane is unavailable or stopped; other
channels do not renew it. An unchanged scan does not append another discovery
event, so keeping samples current does not itself require a mind wake.

Local scans are only one input to discovery. Discover also selects neighboring topics
from literature, reads relevant primary sources, and assesses their mechanisms,
evidence, assumptions, and limits against the project. It records what transfers,
what does not, and what needs an experiment, with citations and the sections read.
Its outcome can be a new question, a bounded experiment with a predicted result
and acceptance check, or a reasoned rejection—not necessarily a new sensor or a
fix for an existing fault. A green scan does not establish research progress.
Literature is occasional, not a per-wake quota, and can inform any aspect of Mishe
or its project goals: coordination, self-organization, learning, evaluation,
experimental methods, or agent behavior. Discover chooses when outside work is
useful and applies supported ideas through checked experiments or owned changes;
it does not force papers into unrelated tasks.

A sense has explicit temporal validity: sample time, the interval or event covered,
and the age or source change that makes it unsuitable for the consumer's decision.
It need not stream continuously. An expired reading can remain valid historical
evidence while being UNKNOWN for a current-state claim. A paper read to explore
an idea is discovery; a repeatable watch for literature updates can be a sense.

New senses can also cross several existing ones. The composite defines its inputs,
combination rule, inferred state, and consumer, aligns the observation windows,
and preserves input provenance and validity. A new output timestamp does not
refresh stale inputs; missing evidence stays UNKNOWN where it prevents the
inference. Compare against each input alone and account for correlation before
claiming new information—for example, testing an overload hypothesis from queue
growth, throughput, and memory pressure together.

## Evidence: UNKNOWN is not pass

A missing, stale, unavailable, or conflicting reading is `UNKNOWN`. That means the result cannot be established. It is neither proof of failure nor permission to call the system healthy. A fresh lease beside an unknown check gives you a functioning display of uncertainty.

The cross-site runtime-drift reading checks services attributable to linked-site
sessions and the scanning site's session against their pins. A stale covered
root is `drift`; incomplete attribution or pin coverage without an observed stale
root is `unknown`, not `verified`. Its sample retains the `unattributed` and
`unpinned` unit lists so readers can distinguish coverage gaps from drift.

Checks, not minds, decide reproducible facts: counts, identity, deadlines, task eligibility, duplicate work, and observed outcomes. Each consequential gate needs a visible verdict and a real failure state. Minds compare plausible approaches and make choices that deterministic checks cannot make.

For a RED or UNKNOWN result, the working sequence is:

1. Read the live pane, relevant source, worktree, and exact obligation.
2. Reproduce the result and state a hypothesis with a predicted, checkable outcome.
3. Name the owned paths and resources, preserve unrelated work, and make one bounded change.
4. Rerun the **same check** and see its result on the live pane.
5. Save an artifact, a rollback or exact retry edge, and a handoff before claiming completion.

On a GREEN pane, a mind may pursue one useful improvement grounded in its charter or an open operator wish. An unavailable source stays honestly unknown with an exact retry condition; it does not suspend all other useful work.

A renderer self-test does not prove that its caller or visible viewport works. Unit tests establish protocol behavior; the live pane, actual caller, and tape establish the running wiring. After restoration, read the live evidence again. A handoff is memory, not a freshness certificate.

### CI checks the relevant commit

The CI watcher queries GitHub Actions for the full SHA of the GitHub default branch's local remote-tracking ref, `origin/<default-branch>`. It selects the latest observed run for each workflow in that exact-commit result. A failed run is failure; an in-progress run is pending; all selected runs must succeed for pass. No matching run, unavailable GitHub CLI, a query error, or a stale reading is `UNKNOWN`, not a pass borrowed from an older commit.

Transitions produce `[ci]` entries. Candidate failures return to the source author; an unassigned main failure opens a genome repair task. The repair task stays open through code change, independent review, push, and a successful replacement run for the pushed SHA. A local test success is not remote delivery.

## Where work survives

`SITE/chat.log` is the append-only conversation **and** obligation tape. The runtime reconstructs open tasks, next steps, owners, attempts, offers, and retry conditions from its entries. There is no requirement to keep the previous agent's context alive to preserve task state.

| Saved surface | Purpose |
| --- | --- |
| `SITE/chat.log` | Human explanation, stable protocol tags, durable task-state/event records, wakes, yields, and work receipts |
| `SITE/charters/ROLE.md` | Lasting channel purpose |
| `SITE/handoffs/ROLE.md` | Latest current-work handoff |
| `SITE/artifacts/seed-ROLE-wake-N.md` | Archived handoff for an exact settled wake |
| Other `SITE/artifacts/` files | Source-bound evidence and checks |
| `SITE/access/requests/` and permission entries in `chat.log` | Scoped capability requests and operator decisions |

Write ordinary entries through the feed CLI, not by hand:

```bash
mishe-tauftauf --home SITE append --source ROLE 'Readable event, evidence, and next action.'
```

The CLI owns sequence numbers, timestamps, and framing. A malformed feed frame can block every pane; preserve its original bytes and repair the exact error under the feed lock, rather than overwriting the conversation.

Every entry must explain the event, evidence or referenced artifact, and next owner or action in plain text. Stable tags are necessary for replay, but not sufficient for a reader. Unchanged repeated samples stay in local artifacts rather than filling the shared tape.

### The durable task lifecycle

Each lifecycle transition is a separate entry, with its tag at the start of the first line:

| Tag | Meaning |
| --- | --- |
| `[task] <id> owner=<role>` | Opens a stable task identity and names its owner |
| `[taking] <id>` | Records that the work started |
| `[done] <id>` | Closes completed work with a checked artifact |
| `[dropped] <id> — reason` | Records why the work was declined |

A tag buried later in a combined entry or only in a handoff does not update the shared task board. Keep the same ID through a long task's handoffs. An invitation, a `[taking]`, or a confident statement is not completion.

The following command forms use placeholders for an existing open task, role, evidence file, and text. Run them with the planted CLI and `--home SITE` prefix:

```text
task step ID --owner ROLE --next-step TEXT --progress TEXT --evidence FILE
task wait ID --owner ROLE --next-step TEXT --reason TEXT --evidence FILE --retry-event TOKEN
task wait ID --owner ROLE --next-step TEXT --reason TEXT --evidence FILE --retry-at TIMESTAMP
task event TOKEN --source ROLE --reason TEXT --evidence FILE
task offer ID --owner ROLE --helper OTHER_ROLE --evidence FILE
task show [--owner ROLE]
```

- **`task step`** records concrete checked progress and a next step, making the task ready. Repeating the same next-step text with unchanged evidence is rejected: record a wait condition instead.
- **`task wait`** records why the next step cannot proceed and the exact event or timezone-bearing deadline that permits a retry. If both are recorded, either can make the task eligible.
- **`task event`** publishes a named condition change with a reason and evidence. Publish it only when that condition actually changes. An event must appear after the wait's retry boundary; an older matching event does not release a new wait.
- **`task offer`** names helpers whose charters fit the work. Repeat `--helper` to name several; omit it to withdraw the offer. This is not a broadcast permission to edit any file.
- **`task show`** exposes current owners, next steps, attempts, and retry conditions reconstructed from the tape.

The task commands record an evidence-file path and SHA-256 digest. The file must be readable, nonempty, and no larger than 1 MiB. They establish durable records; they do not independently certify the truth of a claimed result.

### Once per selected step or fired retry

The supervisor delivers a durable wake and the full shared board. The mind chooses its useful next step, then calls `task claim ID --owner ROLE --wake N --reason TEXT --evidence FILE`. After private pitfall checks, the shared seed lock protects the exact live wake, eligibility, ownership transfer and consumed attempt in one referenced record. The task becomes waiting without a retry predicate until the owner records progress, an exact wait, or completion. This prevents the same unchanged step from becoming another quiet self-pick or continuation.

A fresh pane observation or addressed event can still invite investigation; it does **not** by itself make an unchanged waiting task actionable. A retry event or reached deadline permits one attempt. Delivery consumes that permission, so the same expired deadline or previously published event cannot keep selecting the step.

Offers are advisory invitations. A suitable mind can choose any eligible shared task within its authority without requiring an offer. The board shows ready main integration priority without inheriting preparation priority. Active claims reserve their tasks; competing minds cannot consume the same step. A completed producer stays visible so its waiting consumer can reconcile the evidence. These are cooperative scheduling checks, not locks on arbitrary editor writes.

### Settle, clear, restore

After a checked step, settle the **exact pending wake**:

```text
seed yield --slug ROLE --wake N --file HANDOFF_FILE --result changed|verified|blocked
```

The runtime saves the latest handoff, archives it, and writes a `[work]` receipt with the wake, evidence linkage, result, and handoff digest. `seed yield` settles a wake; it does not itself close the task. The checked `[done]` transition does that.

For unfinished long work, record an actionable next step and add `--continue`. An unchanged waiting prerequisite does not qualify. The supervisor waits for a settled idle boundary, rotates the lower pane to a fresh process, verifies that a new live process exists, and gates delivery on readiness. The charter, handoff, and current instructions arrive with the next real wake. Clearing context creates no idle model turn.

An unsettled wake after a crash must be reconciled before any possible effect is repeated. Recovery can redeliver the same wake; that is not authorization to repeat its mutation blindly. The [operating guide](operating.md) covers readiness, recovery, and live inspection.

## Scope and authority

Every mind has standing authority to pursue useful work within its charter and owned scope. External or protected actions still need their own authority. Privacy, credentials, and external systems do not become owned just because a command can reach them.

A genuinely missing external capability goes through the scoped permissions ledger. The public CLI command is **`access`**; the permissions shell also provides a site-local `permit` wrapper for it. `access request` records a stable request ID, owner, task, capability, reason, and the paths a decision would unblock. The operator's `access grant` or `access revoke` appears on the permissions pane and in `chat.log`. Granted capabilities are ready to use within that recorded scope; pending or revoked requests confer no authority. The ledger records a plant decision. It does not change Linux permissions or device access.

This single-repository seed does **not** inherit LTE authority over other nodes, fleet resources, node-wide GPU allocation, fleet board gates, or native mesh TURN accounting. OMP's full mesh lifecycle uses native `session_start` and `session_stop` receipts, idle drain, and context clear. This seed implements the narrower exact-wake handoff, `[work]` receipt, and settled idle clear.

The older mishe planting skill describes a mortal, no-clone demo with a full `burn` path. This repository is a persistent development seed with resident user services in the full setup. It does not promise that demo's zero-footprint teardown.

### Tracked source versus local state

Tracked source holds reusable runtime code, tests, setup, general instructions, and the default culture. The site holds chat, customized charters, handoffs, plans, drafts, checks, artifacts, permission requests, and service files. The site is ignored by Git; a fresh clone does not inherit another instance's live state.

A general lesson becomes shared behavior only through deliberate promotion into tracked source. Authors commit their own scoped work on the single `main` branch; there are no publication or candidate branches. Genome pushes `main` and checks the pushed commit's CI and live consumers; it does not gate another mind's commit. The author verifies final CI and deployed consumers. The task remains open until delivery completes or an exact blocker has a retry edge. Kernel changes also need to reach active sites and their live services, not merely the checkout.

The local agent contract is [AGENTS.md](../AGENTS.md). Durable rules come from [seed_doctrine.md](../src/mishe_tauftauf/seed_doctrine.md), read from tracked source at restore and mirrored into `SITE/doctrine.md` during planting. The [mesh culture mapping](../instructions/mesh-culture.md) records the source rules and the narrower boundaries this seed carries.

## Where this fits

| Project | Primary unit | What it gives you |
| --- | --- | --- |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | A graph of application steps | A runtime for long-running stateful agent workflows, with persistence and human review points |
| [OpenHands SDK](https://docs.openhands.dev/sdk/getting-started) | A framework for building software agents | Agents that interact with code, files, and system commands through tools and a workspace |
| mishe-tauftauf | A resident observation-and-development loop | Shared evidence panes, role charters, durable tasks and handoffs, and checked next steps toward a project's goals—including improvements to the loop itself |

These are different layers, not a league table. Mishe-tauftauf uses an installed coding agent as a mind and can incorporate other runtimes. Its specific contribution is ongoing development guided by observation of the work, its evidence, and its own machinery. Git remains the current planting and source-delivery substrate; a reviewed commit is one possible outcome, not the definition of all progress.

## Case study: a false CI unknown

On 29 September 2026, the live CI pane reported `UNKNOWN` for `origin/main` even though [run 36617398870](https://github.com/genaforvena/mishe-tauftauf/actions/runs/36617398870) had succeeded for that exact commit.

The watcher listed recent branch runs and filtered their SHAs afterward; that query did not reliably find the run. Witness kept the discrepancy visible as a genome task. Genome reproduced the exact-SHA lookup, changed the watcher to query `gh run list --commit <full origin/main SHA>`, restored per-workflow result coverage, and obtained independent review. The focused tests passed (3).

Genome pushed [commit 71b9aca](https://github.com/genaforvena/mishe-tauftauf/commit/71b9aca89c7e21519eb4b278e6fa409b8be0f167). [Run 36619558220](https://github.com/genaforvena/mishe-tauftauf/actions/runs/36619558220) succeeded for the new SHA, and the live pane returned to `PASS`.

The improvement is specific: query CI for the remote commit being tended, while leaving GitHub query failures `UNKNOWN`. The task remained open through change, review, push, and replacement run; completion went into `chat.log` only after those checks. The account above is this repository's recorded case history, not a new verification run performed for this guide.

### A separate application example

The public [check ledger example](https://github.com/genaforvena/mishe-tauftauf-example) is a separate application repository with a running plant. Its [commit history](https://github.com/genaforvena/mishe-tauftauf-example/commits/main) and [Actions runs](https://github.com/genaforvena/mishe-tauftauf-example/actions) show code and CI. Its chat, panes, and host readings remain in the ignored local site; those public records are not the full running plant.

### Explainable entries and private correction

Every new entry in `chat.log` is readable text. JSON belongs in immutable referenced records, including supervisor observations, task transitions and work receipts. The historical tape remains replayable. The feed rejects JSON objects and arrays even when embedded in prose or fenced examples; a hash reference must accompany an explanation.

A configured publication checker reviews R01–R08 before every post and P01–P28 before a claim or handoff. Suspicious or unknown verdicts preserve a private draft and correction report without publishing or performing that transition. Correct the draft and its plan without repeating earlier effects. `publication check --source ROLE --file FILE --stage post|selection|handoff` checks privately; `publication status` exposes configuration and last verdict. Unconfigured semantics remain explicitly untested. Model classifications need measured detection validation before trusting a production gate; the JSON rule and canonical claim checks are deterministic.

Witness follows task histories across all roles, including its own work and seed receipts. `task coordination-report` prints evidence-linked deterministic failures and semantic suspicions; panes display these without running model inference on refresh.
